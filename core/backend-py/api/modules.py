"""模块管理 API

- 列出已安装的模块(从 ModuleRegistry 取)
- 列出可下载的模块(从 modules.json 清单读)
- 触发模块下载 + 安装(调 pkexec apt install)
- 触发模块卸载(调 pkexec apt remove)
- 聚合"全部参考资源类型"(主程序内置 + 各模块声明的)
"""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel

from installer import catalog, downloader, apt_runner

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# 已安装模块
# ============================================================================

@router.get("/installed")
async def list_installed(request: Request):
    """列出当前已安装、已加载的模块。"""
    registry = request.app.state.registry
    return {
        "modules": [m.to_public_dict() for m in registry.list_all()]
    }


@router.get("/installed/{module_id}")
async def get_installed(module_id: str, request: Request):
    registry = request.app.state.registry
    mod = registry.get(module_id)
    if not mod:
        raise HTTPException(404, f"模块未安装或未加载: {module_id}")
    return mod.to_public_dict()


# ============================================================================
# 模块清单
# ============================================================================

@router.get("/catalog")
async def get_catalog(request: Request):
    items = catalog.load_catalog()
    registry = request.app.state.registry
    installed_ids = {m.id for m in registry.list_all()}

    enriched = []
    for item in items:
        d = dict(item)
        d["installed"] = item["id"] in installed_ids
        if d["installed"]:
            installed = registry.get(item["id"])
            d["installed_version"] = installed.version if installed else None
        enriched.append(d)

    return {"catalog": enriched}



# ============================================================================
# 安装/卸载(占位)
# ============================================================================

class InstallRequest(BaseModel):
    module_id: str


class InstallLocalRequest(BaseModel):
    deb_path: str
    password: Optional[str] = None  # 用户的 sudo 密码,通过应用内密码框输入


class UninstallRequest(BaseModel):
    password: Optional[str] = None


@router.post("/install")
async def install_module(req: InstallRequest, request: Request,
                          background: BackgroundTasks):
    items = catalog.load_catalog()
    target = next((i for i in items if i["id"] == req.module_id), None)
    if not target:
        raise HTTPException(404, f"模块清单里没有: {req.module_id}")
    raise HTTPException(
        501,
        "在线安装功能将在后续版本实装。请下载 deb 文件后用'从本地安装'。"
    )


@router.post("/install-local")
async def install_local(req: InstallLocalRequest, request: Request):
    """从本地 .deb 安装。
    
    密码处理(通过 password 字段):
      - 提供密码:用 sudo -S 走应用内密码框路径(主路径,WSL 友好)
      - 不提供:用 sudo -n 走 NOPASSWD 路径(对 sudoers 配了 NOPASSWD 的环境)
    
    装好后需要用户重启主程序才能加载新模块。
    """
    deb = Path(req.deb_path)
    if not deb.exists():
        raise HTTPException(404, f"文件不存在: {req.deb_path}")
    if deb.suffix != ".deb":
        raise HTTPException(400, "必须是 .deb 文件")
    
    success, log = await apt_runner.install_deb(deb, password=req.password)
    if not success:
        raise HTTPException(500, {
            "error": "install_failed",
            "message": log if len(log) < 200 else "安装失败,详见日志",
            "log": log,
        })
    
    return {
        "success": True,
        "message": f"模块已装。请重启 PlantOmics Studio 使其加载。",
        "log": log,
    }


@router.post("/uninstall/{module_id}")
async def uninstall_module(module_id: str, req: UninstallRequest, request: Request):
    """卸载模块,需要管理员密码。"""
    registry = request.app.state.registry
    if not registry.get(module_id):
        raise HTTPException(404, f"模块未安装: {module_id}")
    
    package_name = apt_runner.module_id_to_package_name(module_id)
    success, log = await apt_runner.remove_package(package_name, password=req.password)
    if not success:
        raise HTTPException(500, {
            "error": "uninstall_failed",
            "message": log if len(log) < 200 else "卸载失败,详见日志",
            "log": log,
        })
    
    return {
        "success": True,
        "message": f"模块已卸载。请重启 PlantOmics Studio 让变更生效。",
        "log": log,
    }

