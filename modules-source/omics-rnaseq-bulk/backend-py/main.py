"""omics-rnaseq-bulk 模块 - Python 后端

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

MODULE_ID = "omics-rnaseq-bulk"
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

    @app.post("/submit/sra-download")
    async def submit_sra(req: SubmitJobBaseRequest):
        """SRA 处理(下载+解压)。
        
        params 三选一(或组合):
          accessions: [str]    - 要下载的 accession 列表
          sra_files:  [str]    - 已有 .sra 文件路径列表(只解压)
          scan_dir:   str      - 扫描这个目录,自动发现 .sra
          threads:    int      - 默认 8
        """
        p = req.params
        if not (p.get("accessions") or p.get("sra_files") or p.get("scan_dir")):
            raise HTTPException(400,
                "至少需要 accessions / sra_files / scan_dir 之一")
        return _submit(JobKind.SRA_DOWNLOAD.value, req)

    @app.post("/submit/sra-extract")
    async def submit_sra_extract(req: SubmitJobBaseRequest):
        """只解压本地的 .sra,不下载。
        
        params:
          sra_files: [str] (必需)
          threads: int (默认 8)
        """
        if not req.params.get("sra_files") and not req.params.get("scan_dir"):
            raise HTTPException(400, "params.sra_files 或 scan_dir 必填")
        return _submit(JobKind.SRA_EXTRACT.value, req)

    @app.post("/submit/fastqc")
    async def submit_fastqc(req: SubmitJobBaseRequest):
        """params: {fastq_files: [str], summary_label: 'raw'|'trimmed'}

        按 summary_label 区分过滤前/后质控,落到独立的 job kind,
        让两步在前端各自独立(状态、勾选、参数互不影响)。
        """
        if not req.params.get("fastq_files"):
            raise HTTPException(400, "params.fastq_files 必填")
        label = (req.params.get("summary_label") or "raw").lower()
        kind = JobKind.FASTQC_TRIMMED.value if label == "trimmed" else JobKind.FASTQC_RAW.value
        return _submit(kind, req)

    @app.post("/submit/fastp")
    async def submit_fastp(req: SubmitJobBaseRequest):
        """params: {samples: [{name, r1, r2?}], 各种过滤参数}"""
        if not req.params.get("samples"):
            raise HTTPException(400, "params.samples 必填")
        return _submit(JobKind.FASTP.value, req)

    @app.post("/submit/star-index")
    async def submit_star_index(req: SubmitJobBaseRequest):
        """params: {fasta, gtf?, threads, sjdbOverhang}"""
        if not req.params.get("fasta"):
            raise HTTPException(400, "params.fasta 必填(基因组 FASTA 路径)")
        return _submit(JobKind.STAR_INDEX.value, req)

    @app.post("/submit/star-align")
    async def submit_star_align(req: SubmitJobBaseRequest):
        """params: {index_root|index_dir, samples, threads}
        index_root 是 star_index/ 根目录(自动按读长选子索引);
        index_dir 是某个具体索引目录(向后兼容)
        """
        p = req.params
        if not (p.get("index_root") or p.get("index_dir")):
            raise HTTPException(
                400,
                "params 需要 index_root(推荐)或 index_dir(老格式)"
            )
        if not p.get("samples"):
            raise HTTPException(400, "params.samples 必填")
        return _submit(JobKind.STAR_ALIGN.value, req)

    @app.post("/submit/feature-counts")
    async def submit_fc(req: SubmitJobBaseRequest):
        """params: {bam_files, gtf, paired, strand, threads}"""
        if not req.params.get("bam_files"):
            raise HTTPException(400, "params.bam_files 必填")
        if not req.params.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        return _submit(JobKind.FEATURE_COUNTS.value, req)

    @app.post("/submit/library-qc")
    async def submit_library_qc(req: SubmitJobBaseRequest):
        """Qualimap 文库质量评估。params: {bam_files, gtf, sample_names, paired, java_mem}"""
        if not req.params.get("bam_files"):
            raise HTTPException(400, "params.bam_files 必填")
        if not req.params.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        return _submit(JobKind.LIBRARY_QC.value, req)

    @app.post("/submit/new-transcripts")
    async def submit_new_transcripts(req: SubmitJobBaseRequest):
        """StringTie 新转录本发现。params: {bam_files, gtf, sample_names, strand, threads}"""
        if not req.params.get("bam_files"):
            raise HTTPException(400, "params.bam_files 必填")
        if not req.params.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        return _submit(JobKind.NEW_TRANSCRIPTS.value, req)

    @app.post("/submit/alt-splicing")
    async def submit_alt_splicing(req: SubmitJobBaseRequest):
        """rMATS 可变剪接。params: {bam_files_g1, bam_files_g2, gtf, read_length, paired, threads}"""
        if not req.params.get("bam_files_g1") or not req.params.get("bam_files_g2"):
            raise HTTPException(400, "params.bam_files_g1 / bam_files_g2 必填")
        if not req.params.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        return _submit(JobKind.ALT_SPLICING.value, req)

    @app.post("/submit/lncrna")
    async def submit_lncrna(req: SubmitJobBaseRequest):
        """lncRNA 预测(CPC2 + PLEK 取非编码交集)。
        params: {candidate_gtf, genome_fasta, min_length?, threads?}"""
        if not req.params.get("candidate_gtf"):
            raise HTTPException(400, "params.candidate_gtf 必填(用新转录本步骤的 merged.gtf)")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.LNCRNA.value, req)

    @app.post("/submit/merge-counts")
    async def submit_merge_counts(req: SubmitJobBaseRequest):
        """合并多个单样本 counts 为一个矩阵。
        params:
          counts_dir:    扫这个目录下所有 *.tsv (优先级低)
          counts_files:  显式指定要合并的文件列表 (优先级高)
          output_name:   输出文件名,默认 counts_merged.tsv
        """
        p = req.params
        if not (p.get("counts_dir") or p.get("counts_files")):
            raise HTTPException(400, "需要 counts_dir 或 counts_files 之一")
        return _submit(JobKind.MERGE_COUNTS.value, req)

    @app.post("/submit/normalize")
    async def submit_normalize(req: SubmitJobBaseRequest):
        """TPM/FPKM/CPM。
        params:
          mode: "matrix"(默认)| "per_sample"
          counts_file:  矩阵模式用这个(单文件,多列样本)
          counts_files: per_sample 模式用这个(多个单样本文件)
          gtf:          TPM/FPKM 必需
          methods:      ["TPM", "FPKM", "CPM"]
        """
        p = req.params
        if not (p.get("counts_file") or p.get("counts_files")):
            raise HTTPException(400, "需要 counts_file 或 counts_files")
        return _submit(JobKind.NORMALIZE.value, req)

    @app.post("/submit/data-volume-stats")
    async def submit_data_volume_stats(req: SubmitJobBaseRequest):
        """测序数据量统计(解析 fastp JSON,报告 5.1.1)。params: {trimmed_dir?}"""
        return _submit(JobKind.DATA_VOLUME_STATS.value, req)

    @app.post("/submit/align-stats")
    async def submit_align_stats(req: SubmitJobBaseRequest):
        """比对率统计(解析 STAR Log.final.out,报告 5.2.1)。params: {aligned_dir?}"""
        return _submit(JobKind.ALIGN_STATS.value, req)

    @app.post("/submit/transdecoder")
    async def submit_transdecoder(req: SubmitJobBaseRequest):
        """新转录本编码区预测(TransDecoder)。
        params: {candidate_gtf, genome_fasta, min_orf_aa?, single_best?}"""
        if not req.params.get("candidate_gtf"):
            raise HTTPException(400, "params.candidate_gtf 必填(用新转录本步骤的 merged.gtf)")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.TRANSDECODER.value, req)

    # ─── 一键运行(上游)────────────────────────
    @app.post("/submit/pipeline-upstream")
    async def submit_pipeline_upstream(req: SubmitJobBaseRequest):
        """一键跑上游(到 counts_merged.tsv)。
        params:
          workdir, fasta, gtf 必填
          fastp / star_align / feature_counts: 子任务的参数(可选)
        """
        p = req.params
        if not p.get("workdir"):
            raise HTTPException(400, "需要 workdir")
        if not p.get("gtf"):
            raise HTTPException(400, "需要 gtf")
        return _submit(JobKind.PIPELINE_UPSTREAM.value, req)

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
