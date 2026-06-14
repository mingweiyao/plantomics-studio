"""模块注册表 - 内存中维护"已加载到主程序"的模块列表。

不持久化(主程序每次启动都会扫描 /opt/plantomics-studio/modules/ 重建)。
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class LoadedModule:
    """一个已加载的模块。"""
    id: str
    version: str
    install_dir: Path
    manifest: dict
    
    # 运行时状态
    status: str = "loading"   # loading | ready | error | disabled
    error: Optional[str] = None
    py_port: Optional[int] = None
    r_port: Optional[int] = None
    py_pid: Optional[int] = None
    r_pid: Optional[int] = None
    
    @property
    def has_python(self) -> bool:
        return "python" in self.manifest.get("runtime", {})
    
    @property
    def has_r(self) -> bool:
        return "r" in self.manifest.get("runtime", {})
    
    def to_public_dict(self) -> dict:
        """暴露给前端的视图。"""
        return {
            "id": self.id,
            "version": self.version,
            "manifest": self.manifest,
            "status": self.status,
            "error": self.error,
            "py_port": self.py_port,
            "r_port": self.r_port,
        }


class ModuleRegistry:
    def __init__(self):
        self._modules: dict[str, LoadedModule] = {}
        self._next_port = 8011
    
    def discover(self, modules_dir: Path) -> list[LoadedModule]:
        """扫描 modules_dir,加载所有 module.yaml,但不启动进程。
        
        启动进程由 LifecycleManager.start_module 做。
        """
        self._modules.clear()
        if not modules_dir.exists():
            logger.warning(f"模块目录不存在: {modules_dir}")
            return []
        
        for sub in sorted(modules_dir.iterdir()):
            if not sub.is_dir():
                continue
            manifest_path = sub / "module.yaml"
            if not manifest_path.exists():
                logger.warning(f"  跳过 {sub.name}:没有 module.yaml")
                continue
            
            try:
                with open(manifest_path) as f:
                    manifest = yaml.safe_load(f)
                
                mod_id = manifest["id"]
                version = manifest.get("version", "0.0.0")
                
                # ID 必须等于目录名(避免装错位置)
                if mod_id != sub.name:
                    logger.warning(
                        f"  跳过 {sub.name}:目录名与 module.yaml 的 id "
                        f"({mod_id})不一致"
                    )
                    continue
                
                mod = LoadedModule(
                    id=mod_id,
                    version=version,
                    install_dir=sub,
                    manifest=manifest,
                )
                self._modules[mod_id] = mod
                logger.info(f"  发现模块: {mod_id} v{version}")
            except Exception as e:
                logger.exception(f"  加载 {sub.name}/module.yaml 失败: {e}")
        
        return list(self._modules.values())
    
    def get(self, module_id: str) -> Optional[LoadedModule]:
        return self._modules.get(module_id)
    
    def list_all(self) -> list[LoadedModule]:
        return list(self._modules.values())
    
    def list_ready(self) -> list[LoadedModule]:
        return [m for m in self._modules.values() if m.status == "ready"]
    
    def allocate_port(self) -> int:
        """从 8011 起找一个可用端口。"""
        import socket
        for port in range(self._next_port, self._next_port + 100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", port))
                    self._next_port = port + 1
                    return port
                except OSError:
                    continue
        raise RuntimeError(f"找不到可用端口(8011-{self._next_port + 100})")
