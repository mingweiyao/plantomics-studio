"""omics-ont-lncrna 模块 - Python 后端

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

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

# 让 jobs/ runners/ 能 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jobs import manager as job_manager
from jobs.model import JobKind, read_log
from runners import dispatcher

logger = logging.getLogger(__name__)

MODULE_ID = "omics-ont-lncrna"
MODULE_VERSION = "1.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化 JobManager
    data_dir = Path(os.environ.get("MODULE_DATA_DIR", "."))
    data_dir.mkdir(parents=True, exist_ok=True)
    
    max_concurrent = int(os.environ.get("MAX_CONCURRENT_JOBS", "1"))
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

    @app.post("/submit/basecall")
    async def submit_basecall(req: SubmitJobBaseRequest):
        """Guppy basecalling.
        params: {fast5_dir, flowcell?, kit?, threads?}"""
        if not req.params.get("fast5_dir"):
            raise HTTPException(400, "params.fast5_dir 必填")
        return _submit(JobKind.BASECALL.value, req)

    @app.post("/submit/nanofilt")
    async def submit_nanofilt(req: SubmitJobBaseRequest):
        """NanoFilt QC.
        params: {fastq_files, min_qual=7, min_len=50}"""
        if not req.params.get("fastq_files"):
            raise HTTPException(400, "params.fastq_files 必填")
        return _submit(JobKind.NANOFILT.value, req)

    @app.post("/submit/pychopper")
    async def submit_pychopper(req: SubmitJobBaseRequest):
        """Pychopper full-length.
        params: {fastq_files, Q=7, z=50, threads?}"""
        if not req.params.get("fastq_files"):
            raise HTTPException(400, "params.fastq_files 必填")
        return _submit(JobKind.PYCHOPPER.value, req)

    @app.post("/submit/rrna-remove")
    async def submit_rrna_remove(req: SubmitJobBaseRequest):
        """rRNA removal.
        params: {fastq_files, rrna_db, threads?}"""
        if not req.params.get("fastq_files"):
            raise HTTPException(400, "params.fastq_files 必填")
        if not req.params.get("rrna_db"):
            raise HTTPException(400, "params.rrna_db 必填(rRNA FASTA 路径)")
        return _submit(JobKind.RRNA_REMOVE.value, req)

    @app.post("/submit/minimap2-align")
    async def submit_minimap2_align(req: SubmitJobBaseRequest):
        """minimap2 alignment.
        params: {fastq_files, genome_fasta, threads?}"""
        if not req.params.get("fastq_files"):
            raise HTTPException(400, "params.fastq_files 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.MINIMAP2_ALIGN.value, req)

    @app.post("/submit/pinfish")
    async def submit_pinfish(req: SubmitJobBaseRequest):
        """Pinfish consensus.
        params: {bam_files, genome_fasta, threads?}"""
        if not req.params.get("bam_files"):
            raise HTTPException(400, "params.bam_files 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.PINFISH.value, req)

    @app.post("/submit/stringtie")
    async def submit_stringtie(req: SubmitJobBaseRequest):
        """StringTie merge.
        params: {bam_files, gtf, threads?}"""
        if not req.params.get("bam_files"):
            raise HTTPException(400, "params.bam_files 必填")
        if not req.params.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        return _submit(JobKind.STRINGTIE.value, req)

    @app.post("/submit/gffcompare")
    async def submit_gffcompare(req: SubmitJobBaseRequest):
        """gffcompare novel transcript.
        params: {query_gtf, reference_gtf}"""
        if not req.params.get("query_gtf"):
            raise HTTPException(400, "params.query_gtf 必填")
        if not req.params.get("reference_gtf"):
            raise HTTPException(400, "params.reference_gtf 必填")
        return _submit(JobKind.GFFCOMPARE.value, req)

    @app.post("/submit/transdecoder")
    async def submit_transdecoder(req: SubmitJobBaseRequest):
        """TransDecoder CDS.
        params: {candidate_gtf, genome_fasta, min_orf_aa=50, single_best_only=true}"""
        if not req.params.get("candidate_gtf"):
            raise HTTPException(400, "params.candidate_gtf 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.TRANSDECODER.value, req)

    @app.post("/submit/annot-7db")
    async def submit_annot_7db(req: SubmitJobBaseRequest):
        """7 DB annotation.
        params: {transcripts_fasta, threads?}"""
        if not req.params.get("transcripts_fasta"):
            raise HTTPException(400, "params.transcripts_fasta 必填")
        return _submit(JobKind.ANNOT_7DB.value, req)

    @app.post("/submit/lncrna-identify")
    async def submit_lncrna_identify(req: SubmitJobBaseRequest):
        """lncRNA identification (CPC2 + PLEK).
        params: {candidate_gtf, genome_fasta, min_length=200, max_length=20000, threads?}"""
        if not req.params.get("candidate_gtf"):
            raise HTTPException(400, "params.candidate_gtf 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.LNCRNA_IDENTIFY.value, req)

    @app.post("/submit/lncrna-classify")
    async def submit_lncrna_classify(req: SubmitJobBaseRequest):
        """lncRNA classification.
        params: {lncrna_gtf, genome_fasta, annotation_gtf}"""
        if not req.params.get("lncrna_gtf"):
            raise HTTPException(400, "params.lncrna_gtf 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        if not req.params.get("annotation_gtf"):
            raise HTTPException(400, "params.annotation_gtf 必填")
        return _submit(JobKind.LNCRNA_CLASSIFY.value, req)

    @app.post("/submit/salmon-quant")
    async def submit_salmon_quant(req: SubmitJobBaseRequest):
        """Salmon quantification.
        params: {fastq_files, transcriptome_fasta, threads?}"""
        if not req.params.get("fastq_files"):
            raise HTTPException(400, "params.fastq_files 必填")
        if not req.params.get("transcriptome_fasta"):
            raise HTTPException(400, "params.transcriptome_fasta 必填")
        return _submit(JobKind.SALMON_QUANT.value, req)

    @app.post("/submit/suppa2")
    async def submit_suppa2(req: SubmitJobBaseRequest):
        """SUPPA2 alternative splicing.
        params: {counts_file, gtf, psi_file?}"""
        if not req.params.get("counts_file"):
            raise HTTPException(400, "params.counts_file 必填")
        if not req.params.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        return _submit(JobKind.SUPPA2.value, req)

    @app.post("/submit/fusion")
    async def submit_fusion(req: SubmitJobBaseRequest):
        """Fusion detection.
        params: {bam_files, genome_fasta, threads?}"""
        if not req.params.get("bam_files"):
            raise HTTPException(400, "params.bam_files 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.FUSION.value, req)

    @app.post("/submit/ssr")
    async def submit_ssr(req: SubmitJobBaseRequest):
        """SSR analysis.
        params: {transcripts_fasta, min_repeats?}"""
        if not req.params.get("transcripts_fasta"):
            raise HTTPException(400, "params.transcripts_fasta 必填")
        return _submit(JobKind.SSR.value, req)

    @app.post("/submit/tf")
    async def submit_tf(req: SubmitJobBaseRequest):
        """Transcription factor identification.
        params: {transcripts_fasta, plant_tf_db?}"""
        if not req.params.get("transcripts_fasta"):
            raise HTTPException(400, "params.transcripts_fasta 必填")
        return _submit(JobKind.TF.value, req)

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
