use std::path::PathBuf;
use std::sync::Arc;
use tauri::Manager;
use tokio::sync::Mutex;

mod commands;
mod sidecar;

pub struct AppState {
    pub py_port: Arc<Mutex<Option<u16>>>,
    pub data_dir: PathBuf,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // ─────────────────────────────────────────────────────
    // Linux 图形环境兼容性
    // ─────────────────────────────────────────────────────
    // 默认禁用 WebKit2GTK 的 DMA-BUF 渲染,避免在原生 Ubuntu 桌面 / 虚拟机 / NVIDIA
    // 环境下因为 EGL 驱动不全而崩溃。设 PLANTOMICS_FORCE_GPU=1 可恢复 GPU 加速。
    #[cfg(target_os = "linux")]
    {
        if std::env::var("PLANTOMICS_FORCE_GPU").is_err() {
            if std::env::var("WEBKIT_DISABLE_DMABUF_RENDERER").is_err() {
                std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
            }
        }
    }

    env_logger::init();

    // 用户数据目录
    let data_dir = dirs_data_dir();
    std::fs::create_dir_all(&data_dir).ok();
    log::info!("data_dir: {}", data_dir.display());

    let state = AppState {
        py_port: Arc::new(Mutex::new(None)),
        data_dir: data_dir.clone(),
    };

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .manage(state)
        .setup(|app| {
            let state: tauri::State<AppState> = app.state();
            let py_port_arc = state.py_port.clone();
            let data_dir = state.data_dir.clone();

            // 后台启动 Python 后端
            tauri::async_runtime::spawn(async move {
                match sidecar::start_python_backend(&data_dir).await {
                    Ok(port) => {
                        log::info!("✓ Python 后端启动 (port={})", port);
                        let mut p = py_port_arc.lock().await;
                        *p = Some(port);
                    }
                    Err(e) => {
                        log::error!("✗ Python 后端启动失败: {:?}", e);
                    }
                }
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::get_backend_port,
            commands::core_call,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn dirs_data_dir() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join(".plantomics")
}
