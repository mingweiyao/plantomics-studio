"""omics-analysis 模块 - Python 后端

启动方式:由主程序通过 LifecycleManager 启动,环境变量包括:
  MODULE_PY_PORT       要监听的端口
  MODULE_R_PORT        本模块 R 后端的端口(用于 Python 转 R)
  PLANTOMICS_DATA_DIR  主程序数据目录(只读)
  MODULE_DATA_DIR      模块自己的数据目录(可写)
  PLANTOMICS_CORE_API  主程序 API 地址

提供两类端点:
  1. **任务相关** /jobs - 提交、查询、取消任务(异步)
  2. **同步轻查询** /tools/list 等 - 不需要异步的小操作
"""
import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse
from pydantic import BaseModel

# 让 jobs/ runners/ 能 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs import manager as job_manager
from jobs.model import JobKind, read_log
from runners import dispatcher

logger = logging.getLogger(__name__)

MODULE_ID = "omics-analysis"
MODULE_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化 JobManager
    data_dir = Path(os.environ.get("MODULE_DATA_DIR", "."))
    data_dir.mkdir(parents=True, exist_ok=True)
    
    max_concurrent = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
    # 总线程预算:默认探测本机逻辑核心数;可用 PLANTOMICS_CPU_BUDGET 覆盖。
    budget_env = os.environ.get("PLANTOMICS_CPU_BUDGET")
    total_threads = int(budget_env) if budget_env and budget_env.isdigit() else None
    mgr = job_manager.init(data_dir, max_concurrent=max_concurrent,
                           total_threads=total_threads)
    await mgr.start()
    
    # 让 dispatcher 知道模块根(支持开发模式)
    install_dir = os.environ.get("MODULE_INSTALL_DIR")
    if install_dir:
        dispatcher.set_module_root(Path(install_dir))
    else:
        # 默认假设是 deb 装的位置
        prod = Path("/opt/plantomics-studio/modules") / MODULE_ID
        if prod.exists():
            dispatcher.set_module_root(prod)
    
    logger.info(f"JobManager 已启动 (max_concurrent={max_concurrent})")

    # 可插拔分析注册表:只扫描用户分析目录 ~/.plantomics/.../analyses/。
    # 模块本身**不内置任何分析/画图代码**,也不播种任何示例 ——
    # 一切分析都由用户通过"新增"向导添加(R 代码 + 示例输入 + 预览图),
    # 落盘到扫描目录,重启/重装后依然在。
    try:
        from analysis_registry import AnalysisRegistry
        app.state.analysis_registry = AnalysisRegistry()
    except Exception as e:
        logger.warning("分析注册表初始化失败: %s", e)
        from analysis_registry import AnalysisRegistry
        app.state.analysis_registry = AnalysisRegistry()

    yield
    
    # 关闭
    await mgr.stop()
    logger.info("JobManager 已停止")


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"PlantOmics Module: {MODULE_ID}",
        version=MODULE_VERSION,
        lifespan=lifespan,
    )

    # ─── 基础信息 ───────────────────────────────
    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "module_id": MODULE_ID,
            "version": MODULE_VERSION,
        }

    @app.get("/info")
    async def info():
        return {
            "module_id": MODULE_ID,
            "version": MODULE_VERSION,
            "py_port": int(os.environ.get("MODULE_PY_PORT", 0)),
            "r_port": int(os.environ.get("MODULE_R_PORT", 0)),
            "data_dir": os.environ.get("PLANTOMICS_DATA_DIR", ""),
            "module_data_dir": os.environ.get("MODULE_DATA_DIR", ""),
            "core_api": os.environ.get("PLANTOMICS_CORE_API", ""),
            "supported_jobs": [k.value for k in JobKind],
        }

    # ─── 任务管理(通用)───────────────────────
    
    class JobsListResponse(BaseModel):
        jobs: list[dict]

    @app.get("/jobs", response_model=JobsListResponse)
    async def list_jobs(project_id: Optional[str] = None):
        mgr = job_manager.get()
        jobs = mgr.list(project_id=project_id)
        return {"jobs": [j.to_dict() for j in jobs]}

    @app.get("/jobs/{job_id}")
    async def get_job(job_id: str):
        mgr = job_manager.get()
        job = mgr.get(job_id)
        if not job:
            raise HTTPException(404, f"job 不存在: {job_id}")
        return job.to_dict()

    @app.get("/jobs/{job_id}/log", response_class=PlainTextResponse)
    async def get_job_log(job_id: str, tail: Optional[int] = None):
        """读任务日志。tail=N 只读最后 N 行。"""
        mgr = job_manager.get()
        job = mgr.get(job_id)
        if not job:
            raise HTTPException(404, f"job 不存在: {job_id}")
        return read_log(mgr.data_dir, job_id, tail_lines=tail)

    @app.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        mgr = job_manager.get()
        ok = await mgr.cancel(job_id)
        if not ok:
            raise HTTPException(400, "无法取消(任务不存在或已结束)")
        return {"cancelled": job_id}

    @app.delete("/jobs/{job_id}")
    async def delete_job(job_id: str):
        from jobs.model import delete_job, load_job
        mgr = job_manager.get()
        job = mgr.get(job_id)
        if not job:
            raise HTTPException(404, f"job 不存在: {job_id}")
        if not job.is_terminal:
            raise HTTPException(400, "进行中的任务不能删,请先取消")
        delete_job(mgr.data_dir, job_id)
        return {"deleted": job_id}

    @app.get("/concurrency")
    async def get_concurrency():
        mgr = job_manager.get()
        # 一并返回 CPU 预算,前端照这个显示("N 线程 / M 并行 → 每任务 K 线程")
        return {
            "max_concurrent": mgr.max_concurrent,
            **mgr.cpu_budget.describe(),
        }

    class SetConcurrencyRequest(BaseModel):
        max_concurrent: int
        total_threads: int | None = None

    @app.put("/concurrency")
    async def set_concurrency(req: SetConcurrencyRequest):
        mgr = job_manager.get()
        mgr.update_concurrency(req.max_concurrent)
        if req.total_threads is not None:
            mgr.update_cpu_budget(req.total_threads)
        return {
            "max_concurrent": mgr.max_concurrent,
            **mgr.cpu_budget.describe(),
        }

    # ─── 提交任务的端点(每种 kind 一个)───────
    # 这些端点只做"参数校验 + submit",真正的工作由 runner 子进程做。

    class SubmitJobBaseRequest(BaseModel):
        project_id: str
        output_path: str          # 用户指定的输出根目录
        params: dict[str, Any] = {}

    def _submit(kind: str, req: SubmitJobBaseRequest) -> dict:
        if not req.output_path:
            raise HTTPException(400, "output_path 必填")
        # 校验输出目录可写
        try:
            out = Path(req.output_path)
            out.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise HTTPException(400, f"输出路径不可用: {e}")
        
        mgr = job_manager.get()
        try:
            job = mgr.submit(
                kind=kind,
                project_id=req.project_id,
                params=req.params,
                output_path=req.output_path,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return job.to_dict()

    # ─── 可插拔分析:列出 / 预览 / 示例 / 运行 / 新增 ───────────
    @app.get("/analyses")
    async def list_analyses():
        """所有已注册的分析(前端据此渲染卡片 + 自动生成参数表单)。"""
        from analysis_registry import DATASET_TYPES
        reg = app.state.analysis_registry
        return {"analyses": reg.list_manifests(), "dataset_types": DATASET_TYPES}

    @app.post("/analyses/rescan")
    async def rescan_analyses():
        reg = app.state.analysis_registry
        reg.rescan()
        return {"analyses": reg.list_manifests()}

    @app.get("/analyses/{analysis_id}/preview")
    async def analysis_preview(analysis_id: str):
        reg = app.state.analysis_registry
        folder = reg.folder_of(analysis_id)
        if not folder:
            raise HTTPException(404, "分析不存在")
        png = folder / "preview.png"
        if not png.exists():
            raise HTTPException(404, "无预览图")
        return FileResponse(str(png), media_type="image/png")

    @app.get("/analyses/{analysis_id}/preview-b64")
    async def analysis_preview_b64(analysis_id: str):
        """预览图的 base64 版(给前端走 JSON 代理用,二进制没法过代理)。"""
        import base64
        reg = app.state.analysis_registry
        folder = reg.folder_of(analysis_id)
        if not folder:
            raise HTTPException(404, "分析不存在")
        png = folder / "preview.png"
        if not png.exists():
            return {"preview": None}
        b64 = base64.b64encode(png.read_bytes()).decode("ascii")
        return {"preview": f"data:image/png;base64,{b64}"}

    @app.get("/analyses/{analysis_id}/examples/{filename}")
    async def analysis_example(analysis_id: str, filename: str):
        reg = app.state.analysis_registry
        folder = reg.folder_of(analysis_id)
        if not folder:
            raise HTTPException(404, "分析不存在")
        f = folder / "examples" / Path(filename).name
        if not f.exists():
            raise HTTPException(404, "示例文件不存在")
        return FileResponse(str(f), filename=f.name)

    class RunAnalysisRequest(BaseModel):
        analysis_id: str
        project_id: str
        output_path: str
        inputs: dict[str, str] = {}
        params: dict[str, Any] = {}

    @app.post("/run")
    async def run_analysis(req: RunAnalysisRequest):
        """运行一个分析(通用,走 RUN_ANALYSIS job + analysis_runner)。"""
        reg = app.state.analysis_registry
        man = reg.get(req.analysis_id)
        if not man:
            raise HTTPException(404, f"分析不存在: {req.analysis_id}")
        job_params = {
            "analysis_folder": man["folder"],
            "analysis_id": req.analysis_id,
            "inputs": req.inputs,
            "analysis_params": req.params,
        }
        base = SubmitJobBaseRequest(
            project_id=req.project_id, output_path=req.output_path, params=job_params
        )
        return _submit(JobKind.RUN_ANALYSIS.value, base)

    @app.post("/analyses")
    async def create_analysis(
        id: str = Form(...),
        label: str = Form(...),
        category: str = Form("plot"),
        accepts: str = Form(""),          # 逗号分隔的数据类型
        params_json: str = Form("[]"),     # 参数定义(YAML/JSON 数组)
        code: UploadFile = File(...),       # 用户的 R 代码
        preview: Optional[UploadFile] = File(None),
        examples: list[UploadFile] = File([]),
    ):
        """新增分析("新增"向导提交):把 R 代码 + 示例文件 + 预览图写进用户分析目录。"""
        import re
        import shutil as _sh
        import yaml as _yaml
        from analysis_registry import USER_ANALYSES_DIR

        if not re.match(r"^[A-Za-z0-9_-]+$", id):
            raise HTTPException(400, "id 只能含字母/数字/下划线/连字符")
        folder = USER_ANALYSES_DIR / id
        if folder.exists():
            raise HTTPException(409, f"分析 {id} 已存在")
        folder.mkdir(parents=True)
        try:
            code_body = (await code.read()).decode("utf-8", errors="ignore")
            # 用户代码若没写 @plantomics-analysis 头部,用表单元数据自动补一个
            if "@plantomics-analysis" not in code_body:
                try:
                    plist = _yaml.safe_load(params_json) or []
                except Exception:
                    plist = []
                hdr = ["#' @plantomics-analysis", f"#' id: {id}",
                       f"#' label: {label}", f"#' category: {category}"]
                acc = [a.strip() for a in accepts.split(",") if a.strip()]
                if acc:
                    hdr.append("#' accepts: [" + ", ".join(acc) + "]")
                if isinstance(plist, list) and plist:
                    hdr.append("#' params:")
                    for p in plist:
                        hdr.append("#'   - " + _yaml.dump(
                            p, default_flow_style=True, allow_unicode=True).strip())
                code_body = "\n".join(hdr) + "\n\n" + code_body
            (folder / "analysis.R").write_text(code_body, encoding="utf-8")

            if preview is not None and preview.filename:
                (folder / "preview.png").write_bytes(await preview.read())
            if examples:
                ex_dir = folder / "examples"
                ex_dir.mkdir(exist_ok=True)
                for f in examples:
                    if f and f.filename:
                        (ex_dir / Path(f.filename).name).write_bytes(await f.read())

            reg = app.state.analysis_registry
            reg.rescan()
            man = reg.get(id)
            if not man:
                raise HTTPException(400, "新增的分析无法解析(检查 R 代码头部与 run 函数)")
            return man
        except HTTPException:
            _sh.rmtree(folder, ignore_errors=True)
            raise
        except Exception as e:
            _sh.rmtree(folder, ignore_errors=True)
            raise HTTPException(500, f"新增分析失败: {e}")

    class CreateAnalysisJsonRequest(BaseModel):
        id: str
        label: str
        category: str = "plot"
        accepts: list[str] = []
        params: list[dict] = []
        code: str
        preview_b64: Optional[str] = None        # data URL 或纯 base64
        examples: list[dict] = []                  # [{name, content_b64}]

    @app.post("/analyses-json")
    async def create_analysis_json(req: CreateAnalysisJsonRequest):
        """新增分析(JSON 版,给前端"新增"向导用——文件用 base64 传,走 JSON 代理)。"""
        import re
        import base64
        import shutil as _sh
        import yaml as _yaml
        from analysis_registry import USER_ANALYSES_DIR

        def _b64bytes(s: str) -> bytes:
            if not s:
                return b""
            if s.strip().startswith("data:") and "," in s:
                s = s.split(",", 1)[1]
            return base64.b64decode(s)

        if not re.match(r"^[A-Za-z0-9_-]+$", req.id):
            raise HTTPException(400, "id 只能含字母/数字/下划线/连字符")
        folder = USER_ANALYSES_DIR / req.id
        if folder.exists():
            raise HTTPException(409, f"分析 {req.id} 已存在")
        folder.mkdir(parents=True)
        try:
            code_body = req.code
            if "@plantomics-analysis" not in code_body:
                hdr = ["#' @plantomics-analysis", f"#' id: {req.id}",
                       f"#' label: {req.label}", f"#' category: {req.category}"]
                if req.accepts:
                    hdr.append("#' accepts: [" + ", ".join(req.accepts) + "]")
                if req.params:
                    hdr.append("#' params:")
                    for p in req.params:
                        hdr.append("#'   - " + _yaml.dump(
                            p, default_flow_style=True, allow_unicode=True).strip())
                code_body = "\n".join(hdr) + "\n\n" + code_body
            (folder / "analysis.R").write_text(code_body, encoding="utf-8")

            if req.preview_b64:
                (folder / "preview.png").write_bytes(_b64bytes(req.preview_b64))
            if req.examples:
                ex_dir = folder / "examples"
                ex_dir.mkdir(exist_ok=True)
                for ex in req.examples:
                    name = Path(str(ex.get("name", "example"))).name
                    (ex_dir / name).write_bytes(_b64bytes(str(ex.get("content_b64", ""))))

            reg = app.state.analysis_registry
            reg.rescan()
            man = reg.get(req.id)
            if not man:
                raise HTTPException(400, "新增的分析无法解析(检查 R 代码头部与 run 函数)")
            return man
        except HTTPException:
            _sh.rmtree(folder, ignore_errors=True)
            raise
        except Exception as e:
            _sh.rmtree(folder, ignore_errors=True)
            raise HTTPException(500, f"新增分析失败: {e}")

    @app.delete("/analyses/{analysis_id}")
    async def delete_analysis(analysis_id: str):
        """删除一个分析文件夹(只能删用户目录里的)。"""
        import shutil as _sh
        reg = app.state.analysis_registry
        folder = reg.folder_of(analysis_id)
        if not folder:
            raise HTTPException(404, "分析不存在")
        _sh.rmtree(str(folder), ignore_errors=True)
        reg.rescan()
        return {"ok": True}

    # ── 内置分析端点已全部移除 ──
    # 本模块是纯插件宿主:分析一律走 /analyses(列表/新增/删除)+ /run(运行)。
    # 不再有 /submit/* 、/species/* 、/templates/* 这些写死的分析端点。

    # ─── 错误处理 ──────────────────────────────
    @app.exception_handler(Exception)
    async def all_errors(request, exc):
        if isinstance(exc, HTTPException):
            raise
        logger.exception("未捕获错误: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("MODULE_PY_PORT", 0)))
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    if not args.port:
        raise SystemExit("没有指定 --port,且环境变量 MODULE_PY_PORT 也是 0")

    logging.basicConfig(
        level=args.log_level.upper(),
        format=f"[{MODULE_ID}-py] %(asctime)s [%(levelname)s] %(message)s",
    )

    logger.info(f"===== 模块 {MODULE_ID} v{MODULE_VERSION} Python 后端启动 =====")
    logger.info(f"  py_port={args.port}")
    logger.info(f"  r_port={os.environ.get('MODULE_R_PORT', '(none)')}")
    logger.info(f"  data_dir={os.environ.get('PLANTOMICS_DATA_DIR', '(none)')}")
    logger.info(f"  module_data_dir={os.environ.get('MODULE_DATA_DIR', '(none)')}")

    import uvicorn
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=args.port,
                log_level=args.log_level)


if __name__ == "__main__":
    main()
