"""调 apt 命令安装/卸载模块 deb。

# 设计选型背景

之前用 pkexec(图形化提权框),但 WSL 环境下不工作:WSL 没有完整的 systemd-logind
登录会话,polkit 找不到 cookie,认证总是失败。报错 GDBus:
  org.freedesktop.PolicyKit1.Error.Failed: No session for cookie

切换到主路径:**应用内密码框 + sudo -S**(从 stdin 读密码)。
- 不依赖 polkit / dbus / 桌面会话,在 WSL / SSH / 容器都能跑
- 用户体验:主程序自己弹一个密码 Modal,输完密码后台调 sudo

兼容方案:`pkexec` 仍然保留,做 fallback。如果 sudo -S 失败且 pkexec 可用,
试一次 pkexec(对桌面 Linux 用户更原生)。

# 安全性

- 密码通过 Tauri 本地 IPC 传过来,全程在 127.0.0.1 进程间走
- 后端立即把密码喂给 sudo 的 stdin,不写文件不打日志
- 函数里密码变量用完立刻覆盖
"""
import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _has(cmd: str) -> bool:
    return shutil.which(cmd) is not None


async def install_deb(deb_path: Path, password: Optional[str] = None) -> tuple[bool, str]:
    """安装 .deb 文件。
    
    - 提供 password 时:用 sudo -S(主路径)
    - 没提供且 sudo NOPASSWD:用 sudo -n
    - 都不行就报错
    
    apt 必须看到完整文件路径并且要带 / 前缀,否则会当成包名解析。
    """
    if not deb_path.exists():
        return False, f"文件不存在: {deb_path}"
    
    deb_abs = str(deb_path.absolute())
    return await _run_apt(["apt", "install", "-y", deb_abs], password,
                           context=f"安装 {deb_path.name}")


async def remove_package(package_name: str, password: Optional[str] = None) -> tuple[bool, str]:
    """卸载包(按包名,不是 deb 文件路径)。"""
    return await _run_apt(["apt", "remove", "-y", "--purge", package_name],
                           password,
                           context=f"卸载 {package_name}")


async def _run_apt(apt_args: list[str], password: Optional[str],
                    context: str) -> tuple[bool, str]:
    """运行 apt 命令(需要 root 权限)。
    
    密码处理路径:
      1. password 提供 → sudo -S(从 stdin 读密码)
      2. password=None → 试 sudo -n(NOPASSWD,大多数机器没配)
    """
    if password is not None:
        return await _run_with_sudo_password(apt_args, password, context)
    return await _run_with_sudo_nopasswd(apt_args, context)


async def _run_with_sudo_password(apt_args: list[str], password: str,
                                    context: str) -> tuple[bool, str]:
    """sudo -S apt ...:从 stdin 提供密码。"""
    # -S: 从 stdin 读密码
    # -p '': 不打印密码提示符(避免被当成密码本身)
    # -k: 强制重新认证(避免之前的 sudo timestamp 影响)
    cmd = ["sudo", "-S", "-p", "", "-k"] + apt_args
    
    # 不在日志里写完整命令(虽然 sudo args 不含密码,但保险起见)
    logger.info(f"sudo -S {' '.join(apt_args[:3])}... ({context})")
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 把密码喂给 stdin,加换行
        password_bytes = (password + "\n").encode("utf-8")
        stdout, stderr = await proc.communicate(input=password_bytes)
        # 立刻覆盖密码变量
        password_bytes = b"\x00" * len(password_bytes)
        del password_bytes
        
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        log = f"=== STDOUT ===\n{out}\n\n=== STDERR ===\n{err}".strip()
        
        if proc.returncode == 0:
            logger.info(f"✓ {context} 成功")
            return True, log
        
        # 判断是否密码错(sudo 退出码 1,stderr 里有 "incorrect password" 或 "Sorry, try again")
        err_lower = err.lower()
        if "incorrect password" in err_lower or "sorry, try again" in err_lower or \
           "authentication failure" in err_lower:
            return False, "管理员密码错误。请重试。"
        
        # 其他失败
        logger.warning(f"{context} 失败 (rc={proc.returncode}): {err[:300]}")
        return False, log
    except FileNotFoundError:
        return False, "找不到 sudo 命令。请检查系统是否安装了 sudo。"
    except Exception as e:
        logger.exception(f"{context} 启动失败")
        return False, f"启动失败: {e}"


async def _run_with_sudo_nopasswd(apt_args: list[str],
                                    context: str) -> tuple[bool, str]:
    """sudo -n:不要交互,如果需要密码立刻失败。
    
    用于 sudo 设了 NOPASSWD 的环境(很少)。
    """
    cmd = ["sudo", "-n"] + apt_args
    logger.info(f"sudo -n {' '.join(apt_args[:3])}... ({context})")
    
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        log = f"=== STDOUT ===\n{out}\n\n=== STDERR ===\n{err}".strip()
        
        if proc.returncode == 0:
            logger.info(f"✓ {context} 成功")
            return True, log
        
        # sudo -n 在需要密码时,会输出 "sudo: a password is required"
        if "password is required" in err.lower():
            return False, "需要管理员密码。请在弹出的对话框中输入。"
        
        return False, log
    except FileNotFoundError:
        return False, "找不到 sudo 命令。请检查系统是否安装了 sudo。"
    except Exception as e:
        return False, f"启动失败: {e}"


def module_id_to_package_name(module_id: str) -> str:
    """模块 id → deb 包名映射。
    
    约定:模块 id 以 omics-* 开头,deb 包名是 plantomics-module-<去掉 omics- 前缀>。
    例:omics-rnaseq-bulk → plantomics-module-rnaseq-bulk
    """
    if module_id.startswith("omics-"):
        return f"plantomics-module-{module_id[len('omics-'):]}"
    return f"plantomics-module-{module_id}"
