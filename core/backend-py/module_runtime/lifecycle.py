"""模块生命周期管理 - 启停模块的 Python/R 后端进程。

每个模块可以贡献:
  - Python 后端(独立进程,自己的 conda env)
  - R 后端(独立进程,自己的 conda env)

启动后通过 health check 等模块 ready,然后标记 status=ready。
"""
import asyncio
import logging
import os
import socket
from pathlib import Path
from typing import Optional

import httpx
import psutil

from .registry import LoadedModule, ModuleRegistry

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 60  # 秒。R 启动慢,30 秒经常不够


async def _forward_output(stream, prefix: str, level: str):
    """实时把子进程的 stdout/stderr 行转发到主程序 logger。
    
    必须**消费** PIPE,否则缓冲区(默认 64KB)满了之后,子进程的 write 会阻塞,
    然后整个子进程卡住。
    """
    if stream is None:
        return
    log_fn = {
        "info": logger.info,
        "warning": logger.warning,
        "error": logger.error,
    }.get(level, logger.info)
    
    while True:
        try:
            line = await stream.readline()
        except Exception:
            break
        if not line:
            break  # EOF
        try:
            text = line.decode("utf-8", errors="replace").rstrip()
        except Exception:
            text = repr(line)
        log_fn(f"[{prefix}] {text}")


class LifecycleManager:
    def __init__(self, registry: ModuleRegistry):
        self.registry = registry
        self._processes: dict[str, list[asyncio.subprocess.Process]] = {}
    
    async def start_module(self, mod: LoadedModule):
        """启动模块的所有后端进程,等就绪。"""
        runtime = mod.manifest.get("runtime", {})
        if not runtime:
            mod.status = "ready"  # 纯前端模块,没有后端
            logger.info(f"  {mod.id} 是纯前端模块,无需启动后端")
            return
        
        procs: list[asyncio.subprocess.Process] = []
        
        # Python 后端
        if "python" in runtime:
            py_proc, py_port = await self._start_python(mod)
            mod.py_port = py_port
            mod.py_pid = py_proc.pid
            procs.append(py_proc)
        
        # R 后端
        if "r" in runtime:
            r_proc, r_port = await self._start_r(mod)
            mod.r_port = r_port
            mod.r_pid = r_proc.pid
            procs.append(r_proc)
        
        self._processes[mod.id] = procs
        
        # 等待健康检查
        await self._wait_healthy(mod)
    
    async def _start_python(self, mod: LoadedModule):
        """启动模块的 Python 后端。"""
        runtime = mod.manifest["runtime"]["python"]
        entry = mod.install_dir / runtime["entry"]
        if not entry.exists():
            raise FileNotFoundError(f"模块入口不存在: {entry}")
        
        # 用模块自己的 env(关键!)
        env_python = mod.install_dir / "env" / "bin" / "python3"
        if not env_python.exists():
            raise FileNotFoundError(
                f"模块 env 缺失: {env_python}。模块 deb 安装可能不完整。"
            )
        
        port = self.registry.allocate_port()
        env = self._make_env(mod, py_port=port)
        
        cmd = [str(env_python), str(entry), "--port", str(port)]
        logger.info(f"启动 {mod.id} Python: {' '.join(cmd)}")
        
        # stdout/stderr 都接到 PIPE,然后 spawn task 实时读,转发到 logger
        # 不能用 inherit(asyncio 子进程没法 inherit),不能不读 PIPE(满了会阻塞)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 异步读子进程输出转发到主程序日志
        asyncio.create_task(_forward_output(proc.stdout, f"{mod.id}-py", "info"))
        asyncio.create_task(_forward_output(proc.stderr, f"{mod.id}-py", "warning"))
        return proc, port
    
    async def _start_r(self, mod: LoadedModule):
        """启动模块的 R 后端(plumber)。"""
        runtime = mod.manifest["runtime"]["r"]
        entry = mod.install_dir / runtime["entry"]
        if not entry.exists():
            raise FileNotFoundError(f"模块 R 入口不存在: {entry}")
        
        env_rscript = mod.install_dir / "env" / "bin" / "Rscript"
        if not env_rscript.exists():
            raise FileNotFoundError(f"模块 env 缺 Rscript: {env_rscript}")
        
        port = self.registry.allocate_port()
        env = self._make_env(mod, r_port=port)
        
        cmd = [str(env_rscript), str(entry)]
        logger.info(f"启动 {mod.id} R: {' '.join(cmd)} (port={port})")
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # 转发 R 的 stdout/stderr 到主程序日志
        asyncio.create_task(_forward_output(proc.stdout, f"{mod.id}-r", "info"))
        asyncio.create_task(_forward_output(proc.stderr, f"{mod.id}-r", "warning"))
        return proc, port
    
    def _make_env(self, mod: LoadedModule,
                   py_port: Optional[int] = None,
                   r_port: Optional[int] = None) -> dict:
        """为模块进程构造环境变量。
        
        关键:把模块的 env/bin 放到 PATH 最前,这样模块调子进程(R 调 Python)
        也能找到自己 env 里的工具,而不是系统的。
        """
        env = dict(os.environ)
        
        # 把模块 env/bin 放到 PATH 最前
        mod_env_bin = str(mod.install_dir / "env" / "bin")
        env["PATH"] = mod_env_bin + ":" + env.get("PATH", "")
        
        # 上下文协议
        if py_port:
            env["MODULE_PY_PORT"] = str(py_port)
        if r_port:
            env["MODULE_R_PORT"] = str(r_port)
        if mod.py_port and not py_port:
            env["MODULE_PY_PORT"] = str(mod.py_port)
        if mod.r_port and not r_port:
            env["MODULE_R_PORT"] = str(mod.r_port)
        
        # 数据目录
        data_dir = Path.home() / ".plantomics"
        env["PLANTOMICS_DATA_DIR"] = str(data_dir)
        env["MODULE_DATA_DIR"] = str(data_dir / "modules" / mod.id)
        Path(env["MODULE_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
        
        # 模块自身安装位置(供模块的 dispatcher 找 env/scripts/runners)
        env["MODULE_INSTALL_DIR"] = str(mod.install_dir)
        
        # 主程序 API 地址(模块用来调主程序)
        # 端口先用 0,后面真正启动 sidecar 时由 Rust 注入主程序端口
        env["PLANTOMICS_CORE_API"] = os.environ.get(
            "PLANTOMICS_CORE_API", "http://127.0.0.1:8000"
        )
        
        return env
    
    async def _wait_healthy(self, mod: LoadedModule):
        """等模块的后端进程就绪。"""
        manifest_runtime = mod.manifest.get("runtime", {})
        
        async def check(port: int, path: str = "/health") -> bool:
            url = f"http://127.0.0.1:{port}{path}"
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                try:
                    resp = await client.get(url)
                    return resp.status_code == 200
                except Exception:
                    return False
        
        # 收集要 ping 的端口
        targets = []
        if mod.py_port:
            py_path = manifest_runtime.get("python", {}).get("health_path", "/health")
            targets.append((mod.py_port, py_path, "python"))
        if mod.r_port:
            r_path = manifest_runtime.get("r", {}).get("health_path", "/health")
            targets.append((mod.r_port, r_path, "r"))
        
        deadline = asyncio.get_event_loop().time() + HEALTH_TIMEOUT
        ready = {t[2]: False for t in targets}
        
        while not all(ready.values()):
            if asyncio.get_event_loop().time() > deadline:
                missing = [k for k, v in ready.items() if not v]
                mod.status = "error"
                mod.error = f"等待 {missing} 后端就绪超时"
                raise TimeoutError(mod.error)
            
            for port, path, kind in targets:
                if not ready[kind] and await check(port, path):
                    ready[kind] = True
                    logger.info(f"  ✓ {mod.id} {kind} 后端就绪 (port={port})")
            
            if not all(ready.values()):
                await asyncio.sleep(0.5)
        
        mod.status = "ready"
    
    async def stop_module(self, module_id: str):
        """停止模块的所有进程。"""
        procs = self._processes.pop(module_id, [])
        for proc in procs:
            try:
                # 先 SIGTERM 优雅退出
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    # 强杀
                    proc.kill()
                    await proc.wait()
            except Exception as e:
                logger.warning(f"停止模块 {module_id} 进程失败: {e}")
        
        mod = self.registry.get(module_id)
        if mod:
            mod.status = "disabled"
            mod.py_pid = None
            mod.r_pid = None
    
    async def stop_all(self):
        for module_id in list(self._processes.keys()):
            await self.stop_module(module_id)
