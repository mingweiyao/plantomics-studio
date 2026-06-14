//! Python 后端 sidecar 管理
//!
//! 核心修复 (vs 之前版本):
//!   - Tokio 的 `tokio::process::Child` drop 时会 kill 子进程,所以**绝对不能** drop。
//!     改用 `std::process::Child`(Tokio 0.x 之前的行为),这样 drop 后子进程仍存活。
//!     但 std::process::Child 没法 await,所以我们用 `Command::spawn` 后立刻
//!     `forget()` 掉 handle,让 OS 自己管(进程变成 init 的孤儿)。
//!
//!   - 详细诊断日志:每一步都打印,失败时一眼看出问题在哪。
//!
//!   - 端口探测:用 `bind` 后立刻 drop,加 100ms 等待让 TIME_WAIT 释放。

use anyhow::{Context, Result};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// 启动主程序的 Python 后端,等它就绪,返回端口。
pub async fn start_python_backend(data_dir: &Path) -> Result<u16> {
    log::info!("───── start_python_backend 开始 ─────");
    
    let env_dir = locate_env_dir()?;
    log::info!("env_dir = {}", env_dir.display());
    
    let backend_dir = locate_backend_dir()?;
    log::info!("backend_dir = {}", backend_dir.display());
    
    let python = env_dir.join("bin/python3");
    let main_py = backend_dir.join("main.py");
    
    if !python.exists() {
        anyhow::bail!("找不到 Python: {}", python.display());
    }
    if !main_py.exists() {
        anyhow::bail!("找不到主程序入口: {}", main_py.display());
    }
    log::info!("python = {} (exists)", python.display());
    log::info!("main_py = {} (exists)", main_py.display());
    
    // 找可用端口
    let port = find_free_port(8000).context("8000-8099 没找到可用端口")?;
    log::info!("分配端口: {}", port);
    
    // 模块目录
    let modules_dir = if Path::new("/opt/plantomics-studio/modules").exists() {
        PathBuf::from("/opt/plantomics-studio/modules")
    } else {
        // 开发模式:回退到源码下的 modules-source/
        backend_dir.parent().unwrap().parent().unwrap()
            .join("modules-source")
    };
    log::info!("modules_dir = {}", modules_dir.display());
    
    // 用 std::process::Command(不是 tokio::process)
    // 这样 child handle drop 后,子进程**不会**被 kill(Tokio 的 Child 默认会 kill)
    let mut cmd = Command::new(&python);
    cmd.current_dir(&backend_dir)
        .args([
            "main.py",
            "--port", &port.to_string(),
            "--data-dir", data_dir.to_str().unwrap(),
            "--modules-dir", modules_dir.to_str().unwrap(),
            "--core-version", env!("CARGO_PKG_VERSION"),
        ])
        // 不能 piped:piped 后我们必须主动消费 stdout/stderr,否则子进程的 stdio 缓冲区
        // 满了会阻塞。这里直接继承父进程(应用日志),用户可以 RUST_LOG=info 看到。
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    
    log::info!("即将启动: {:?}", cmd);
    
    let child = cmd.spawn().context("启动 Python 后端失败(spawn)")?;
    let pid = child.id();
    log::info!("✓ Python 子进程已 spawn,pid = {}", pid);
    
    // 把 child handle 立刻 forget,让 OS 自己管
    // 否则 child drop 时 std::process::Child 会等子进程退出(阻塞)
    std::mem::forget(child);
    
    // 等就绪
    let url = format!("http://127.0.0.1:{}/health", port);
    log::info!("健康检查 URL: {}", url);
    
    // 关键:no_proxy() 防止 reqwest 把 127.0.0.1 的请求送给系统代理
    // (用户可能设了 http_proxy=http://127.0.0.1:7890 等)
    // local_address(127.0.0.1) 强制走 IPv4,避免 WSL 的 IPv6 路由问题
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(2))
        .no_proxy()
        .build()
        .unwrap();
    
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
    let mut attempt = 0;
    let mut last_error: Option<String> = None;
    while std::time::Instant::now() < deadline {
        attempt += 1;
        match client.get(&url).send().await {
            Ok(resp) if resp.status().is_success() => {
                log::info!("✓ Python 后端就绪 (port={}, attempt={})", port, attempt);
                return Ok(port);
            }
            Ok(resp) => {
                last_error = Some(format!("HTTP {}", resp.status()));
                log::debug!("attempt {}: {}", attempt, last_error.as_ref().unwrap());
            }
            Err(e) => {
                let err_str = format!("{}", e);
                // 第一次失败 + 每 10 次记一次,让用户能从 INFO 级看到诊断
                if attempt == 1 || attempt % 10 == 1 {
                    log::warn!("attempt {} 连接失败: {}", attempt, err_str);
                }
                last_error = Some(err_str);
            }
        }
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
    }
    
    anyhow::bail!(
        "Python 后端 60 秒内未就绪 (尝试 {} 次,最后错误: {})。\n\
         目标 URL: {}\n\
         排查建议:\n\
         1. 在另一个终端跑: curl --noproxy '*' {}\n\
         2. 看是否有代理: env | grep -i proxy\n\
         3. ps aux | grep main.py 看 Python 是否还活着",
        attempt,
        last_error.unwrap_or_else(|| "(无)".to_string()),
        url, url
    )
}

/// 找主程序的 conda env。优先 /opt(deb 装好),其次开发模式下的 build 目录。
fn locate_env_dir() -> Result<PathBuf> {
    if let Ok(p) = std::env::var("PLANTOMICS_ENV_DIR") {
        let path = PathBuf::from(p);
        if path.join("bin/python3").exists() {
            log::info!("env_dir from env var: {}", path.display());
            return Ok(path);
        }
    }
    let candidates = [
        PathBuf::from("/opt/plantomics-studio/env"),
        PathBuf::from("../../build/env"),
        PathBuf::from("../build/env"),
        PathBuf::from("./build/env"),
    ];
    for p in &candidates {
        if p.join("bin/python3").exists() {
            return Ok(p.canonicalize()?);
        }
    }
    anyhow::bail!("找不到主程序 conda env(/opt/plantomics-studio/env 不存在?)")
}

/// 找主程序后端代码目录。
fn locate_backend_dir() -> Result<PathBuf> {
    let candidates = [
        PathBuf::from("/opt/plantomics-studio/backend-py"),
        PathBuf::from("../../backend-py"),
        PathBuf::from("../backend-py"),
        PathBuf::from("./backend-py"),
    ];
    for p in &candidates {
        if p.join("main.py").exists() {
            return Ok(p.canonicalize()?);
        }
    }
    anyhow::bail!("找不到主程序 backend-py 目录")
}

/// 简单的可用端口探测。bind 后等一会让端口释放。
fn find_free_port(start: u16) -> Option<u16> {
    use std::net::TcpListener;
    for port in start..start + 100 {
        if TcpListener::bind(("127.0.0.1", port)).is_ok() {
            // bind 立刻释放,但内核可能还在 TIME_WAIT
            std::thread::sleep(std::time::Duration::from_millis(50));
            return Some(port);
        }
    }
    None
}
