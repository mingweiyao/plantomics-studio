"""omics-ont-translatome 模块 - Python 后端

ONT 全长翻译组分析:
从 ONT raw data 到全长序列鉴定、转录本组装、新转录本发现、
表达定量、可变剪接分析、融合基因、SSR 及转录因子分析，
并提供 Ref vs All 双流程对比功能。

启动方式:由主程序通过 LifecycleManager 启动。
"""
import argparse
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs import manager as job_manager
from jobs.model import JobKind, read_log
from runners import dispatcher

logger = logging.getLogger(__name__)

MODULE_ID = "omics-ont-translatome"
MODULE_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("MODULE_DATA_DIR", "."))
    data_dir.mkdir(parents=True, exist_ok=True)
    max_concurrent = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
    budget_env = os.environ.get("PLANTOMICS_CPU_BUDGET")
    total_threads = int(budget_env) if budget_env and budget_env.isdigit() else None
    mgr = job_manager.init(data_dir, max_concurrent=max_concurrent,
                           total_threads=total_threads)
    await mgr.start()
    install_dir = os.environ.get("MODULE_INSTALL_DIR")
    if install_dir:
        dispatcher.set_module_root(Path(install_dir))
    else:
        prod = Path("/opt/plantomics-studio/modules") / MODULE_ID
        if prod.exists():
            dispatcher.set_module_root(prod)
    logger.info(f"JobManager 已启动 (max_concurrent={max_concurrent})")
    yield
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

    class SubmitJobBaseRequest(BaseModel):
        project_id: str
        output_path: str
        params: dict[str, Any] = {}

    def _submit(kind: str, req: SubmitJobBaseRequest) -> dict:
        if not req.output_path:
            raise HTTPException(400, "output_path 必填")
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

    # ── 1. 碱基识别 ──────────────────────────────
    @app.post("/submit/basecall")
    async def submit_basecall(req: SubmitJobBaseRequest):
        """Dorado/Guppy basecalling (pod5/fast5 -> fastq)。"""
        if not req.params.get("input_dir"):
            raise HTTPException(400, "params.input_dir 必填(raw data 目录)")
        return _submit(JobKind.BASECALL.value, req)

    # ── 2. NanoFilt 质控 ─────────────────────────
    @app.post("/submit/nanofilt")
    async def submit_nanofilt(req: SubmitJobBaseRequest):
        """NanoFilt QC + NanoStat 统计。"""
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填(输入 fastq 文件)")
        return _submit(JobKind.NANOFILT.value, req)

    # ── 3. Pychopper 全长鉴定 ────────────────────
    @app.post("/submit/pychopper")
    async def submit_pychopper(req: SubmitJobBaseRequest):
        """Pychopper 全长 read 鉴定和引物修剪。"""
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        return _submit(JobKind.PYCHOPPER.value, req)

    # ── 4. minimap2 比对 ──────────────────────────
    @app.post("/submit/minimap2-align")
    async def submit_minimap2_align(req: SubmitJobBaseRequest):
        """minimap2 比对 (-ax splice -uf -k14)。"""
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        if not req.params.get("index"):
            raise HTTPException(400, "params.index 必填(参考基因组)")
        return _submit(JobKind.MINIMAP2_ALIGN.value, req)

    # ── 5. Pinfish 转录本组装 ────────────────────
    @app.post("/submit/pinfish")
    async def submit_pinfish(req: SubmitJobBaseRequest):
        """Pinfish 一致性转录本组装(4步流程)。"""
        if not req.params.get("bam"):
            raise HTTPException(400, "params.bam 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.PINFISH.value, req)

    # ── 6. StringTie 冗余去除 ─────────────────────
    @app.post("/submit/stringtie")
    async def submit_stringtie(req: SubmitJobBaseRequest):
        """StringTie 合并及冗余去除 (--conservative -L -R)。"""
        p = req.params
        if not p.get("input_gtfs") and not p.get("gtf_list_file"):
            raise HTTPException(400, "params.input_gtfs 或 gtf_list_file 必填")
        return _submit(JobKind.STRINGTIE.value, req)

    # ── 7. gffcompare 新转录本 ────────────────────
    @app.post("/submit/gffcompare")
    async def submit_gffcompare(req: SubmitJobBaseRequest):
        """gffcompare 新转录本发现及分类 (-R -C -K -M)。"""
        p = req.params
        if not p.get("query_gtf"):
            raise HTTPException(400, "params.query_gtf 必填")
        if not p.get("reference_gtf"):
            raise HTTPException(400, "params.reference_gtf 必填")
        return _submit(JobKind.GFFCOMPARE.value, req)

    # ── 8. TransDecoder 编码区预测 ────────────────
    @app.post("/submit/transdecoder")
    async def submit_transdecoder(req: SubmitJobBaseRequest):
        """TransDecoder CDS 预测 (-m 50 --single_best_only)。"""
        p = req.params
        if not (p.get("candidate_gtf") or p.get("transcript_fasta")):
            raise HTTPException(400, "params.candidate_gtf 或 transcript_fasta 必填")
        if p.get("candidate_gtf") and not p.get("genome_fasta"):
            raise HTTPException(400, "有 candidate_gtf 时 genome_fasta 必填")
        return _submit(JobKind.TRANSDECODER.value, req)

    # ── 9. 7 数据库注释 ──────────────────────────
    @app.post("/submit/annot-7db")
    async def submit_annot_7db(req: SubmitJobBaseRequest):
        """7 数据库功能注释 (diamond -> Nr/Uniprot, hmmscan -> Pfam, kofam_scan -> KEGG)。"""
        if not req.params.get("pep_fasta"):
            raise HTTPException(400, "params.pep_fasta 必填")
        return _submit(JobKind.ANNOT_7DB.value, req)

    # ── 10. Salmon 定量 ──────────────────────────
    @app.post("/submit/salmon-quant")
    async def submit_salmon_quant(req: SubmitJobBaseRequest):
        """Salmon 转录本定量。"""
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        if not req.params.get("index"):
            raise HTTPException(400, "params.index 必填(Salmon 索引)")
        return _submit(JobKind.SALMON_QUANT.value, req)

    # ── 11. SUPPA2 可变剪接 ─────────────────────
    @app.post("/submit/suppa2")
    async def submit_suppa2(req: SubmitJobBaseRequest):
        """SUPPA2 可变剪接分析 (7 类型 + 差异)。"""
        p = req.params
        if not p.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        if not p.get("tpm_file"):
            raise HTTPException(400, "params.tpm_file 必填")
        return _submit(JobKind.SUPPA2.value, req)

    # ── 12. 融合基因检测 ──────────────────────────
    @app.post("/submit/fusion")
    async def submit_fusion(req: SubmitJobBaseRequest):
        """融合转录本检测。"""
        p = req.params
        if not p.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        if not p.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.FUSION.value, req)

    # ── 13. SSR 分析 ─────────────────────────────
    @app.post("/submit/ssr")
    async def submit_ssr(req: SubmitJobBaseRequest):
        """SSR(微卫星)分析。"""
        if not req.params.get("fasta"):
            raise HTTPException(400, "params.fasta 必填")
        return _submit(JobKind.SSR.value, req)

    # ── 14. 转录因子鉴定 ─────────────────────────
    @app.post("/submit/tf")
    async def submit_tf(req: SubmitJobBaseRequest):
        """转录因子鉴定 (PlantTFDB / AnimalTFDB)。"""
        if not req.params.get("pep_fasta"):
            raise HTTPException(400, "params.pep_fasta 必填")
        return _submit(JobKind.TF.value, req)

    # ── 15. Ref vs All 双流程对比 ⭐ ──────────────
    @app.post("/submit/ref-vs-all")
    async def submit_ref_vs_all(req: SubmitJobBaseRequest):
        """Ref vs All 双流程对比分析。
        params:
          ref_gtf: str        - 参考注释 GTF
          ref_pep: str        - 参考流程蛋白序列(可选)
          full_gtf: str       - 全流程的组装 GTF
          full_pep: str       - 全流程的蛋白序列(可选)
          full_annot: str     - 全流程的注释结果 TSV(可选)
          genome_fasta: str   - 参考基因组(可选)
          output_prefix: str  - 输出前缀
          threads: int        - 默认 8
        """
        p = req.params
        if not p.get("ref_gtf"):
            raise HTTPException(400, "params.ref_gtf 必填")
        if not p.get("full_gtf"):
            raise HTTPException(400, "params.full_gtf 必填")
        return _submit(JobKind.REF_VS_ALL.value, req)

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
