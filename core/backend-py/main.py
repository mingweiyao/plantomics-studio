"""PlantOmics Studio 主程序后端

只做:
  - 项目 CRUD
  - 参考资源 CRUD
  - 模块管理(列出已装、安装、卸载)
  - 模块生命周期(启动/停止子进程)
  - 模块 API 转发(/modules/<id>/* → 模块自己的进程)

不做:
  - 任何具体组学分析
  - 任何"样本表"、"对比组"等组学概念
"""
import argparse
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api import projects, modules, proxy
from module_runtime.registry import ModuleRegistry
from module_runtime.lifecycle import LifecycleManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时:发现并启动所有已装模块。退出时:停止所有模块。"""
    logger.info("===== PlantOmics Studio 主程序后端启动 =====")
    logger.info(f"  data_dir: {app.state.data_dir}")
    logger.info(f"  modules_dir: {app.state.modules_dir}")

    registry: ModuleRegistry = app.state.registry
    lifecycle: LifecycleManager = app.state.lifecycle

    # 扫描已装模块
    discovered = registry.discover(app.state.modules_dir)
    logger.info(f"发现 {len(discovered)} 个已装模块: {[m.id for m in discovered]}")

    # 启动模块进程
    for mod in discovered:
        try:
            await lifecycle.start_module(mod)
            logger.info(f"  ✓ {mod.id} 启动成功")
        except Exception as e:
            mod.status = "error"
            mod.error = str(e)
            logger.exception(f"  ✗ {mod.id} 启动失败: {e}")

    yield

    # 关闭时停止所有模块
    logger.info("===== 主程序后端关闭中,停止所有模块 =====")
    await lifecycle.stop_all()


def create_app(data_dir: Path, modules_dir: Path,
               core_version: str) -> FastAPI:
    app = FastAPI(
        title="PlantOmics Studio",
        version=core_version,
        lifespan=lifespan,
    )

    # 应用状态
    app.state.data_dir = data_dir
    app.state.modules_dir = modules_dir
    app.state.core_version = core_version
    app.state.registry = ModuleRegistry()
    app.state.lifecycle = LifecycleManager(app.state.registry)

    # 确保目录
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "projects").mkdir(exist_ok=True)
    (data_dir / "modules").mkdir(exist_ok=True)
    (data_dir / "logs").mkdir(exist_ok=True)

    # 主程序自己的 API
    app.include_router(projects.router, prefix="/projects", tags=["projects"])
    app.include_router(modules.router, prefix="/modules-mgmt", tags=["modules-mgmt"])
    
    # /modules/<id>/* 是 catch-all,转发到模块进程
    app.include_router(proxy.router, prefix="/modules", tags=["proxy"])

    @app.get("/")
    async def root():
        return {
            "app": "PlantOmics Studio",
            "version": core_version,
            "modules_loaded": len(app.state.registry.list_ready()),
        }

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def all_errors(request: Request, exc: Exception):
        logger.exception(f"未捕获的错误处理 {request.url}: {exc}")
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", type=Path,
                        default=Path.home() / ".plantomics")
    parser.add_argument("--modules-dir", type=Path,
                        default=Path("/opt/plantomics-studio/modules"))
    parser.add_argument("--core-version", type=str, default="1.0.0")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    import uvicorn
    app = create_app(args.data_dir, args.modules_dir, args.core_version)
    uvicorn.run(app, host="127.0.0.1", port=args.port,
                log_level=args.log_level)


if __name__ == "__main__":
    main()
