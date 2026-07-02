"""omics-mirna 模块 - Python 后端

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

MODULE_ID = "omics-mirna"
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

    # ---- 基础信息 -----------------------------------------------

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

    # ---- 任务管理(通用)-----------------------------------------

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

    # ---- 提交任务的端点(每种 kind 一个)--------------------------
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
          threads:    int      - 默认 4
          parallel:   int      - 默认 2
        """
        p = req.params
        if not (p.get("accessions") or p.get("sra_files") or p.get("scan_dir")):
            raise HTTPException(400,
                "至少需要 accessions / sra_files / scan_dir 之一")
        return _submit(JobKind.SRA_DOWNLOAD.value, req)

    @app.post("/submit/fastp")
    async def submit_fastp(req: SubmitJobBaseRequest):
        """params: {samples: [{name, r1, r2?}], 各种过滤参数}"""
        if not req.params.get("samples"):
            raise HTTPException(400, "params.samples 必填")
        return _submit(JobKind.FASTP.value, req)

    @app.post("/submit/fastqc")
    async def submit_fastqc(req: SubmitJobBaseRequest):
        """params: {fastq_files: [str], summary_label: 'raw'|'trimmed'}

        按 summary_label 区分过滤前/后质控。
        """
        if not req.params.get("fastq_files"):
            raise HTTPException(400, "params.fastq_files 必填")
        label = (req.params.get("summary_label") or "raw").lower()
        kind = JobKind.FASTQC_TRIMMED.value if label == "trimmed" else JobKind.FASTQC_RAW.value
        return _submit(kind, req)

    @app.post("/submit/bowtie-align")
    async def submit_bowtie_align(req: SubmitJobBaseRequest):
        """bowtie 比对 + miRDeep2 mapper.pl。

        params:
          genome_fasta: str   - 基因组 FASTA 路径(用于建索引)
          samples: [str|dict] - fastq 文件路径列表,或 [{name, fastq}]
          index_dir:  str     - bowtie 索引目录(可选,默认 output_path/index)
          threads:    int     - 每样本线程数(默认 4)
          parallel:   int     - 并行样本数(默认 1)
        """
        p = req.params
        if not p.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        if not p.get("samples"):
            raise HTTPException(400, "params.samples 必填")
        return _submit(JobKind.BOWTIE_ALIGN.value, req)

    @app.post("/submit/mirdeep2")
    async def submit_mirdeep2(req: SubmitJobBaseRequest):
        """miRDeep2 预测。

        params:
          samples: [dict]     - [{name, collapsed_fa, arf}]
          genome_fasta: str   - 基因组 FASTA 路径
          mature_mirna_fa: str   - 已知成熟 miRNA FASTA (miRBase)
          other_mature_fa: str   - 其他已知成熟 miRNA FASTA (可选)
          precursor_mirna_fa: str - 前体 miRNA FASTA (miRBase)
          species: str        - 'animal' 或 'plant',默认 'animal'
        """
        p = req.params
        if not p.get("samples"):
            raise HTTPException(400, "params.samples 必填")
        if not p.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        if not p.get("mature_mirna_fa"):
            raise HTTPException(400, "params.mature_mirna_fa 必填(来自 miRBase)")
        return _submit(JobKind.MIRDEEP2.value, req)

    @app.post("/submit/quantifier")
    async def submit_quantifier(req: SubmitJobBaseRequest):
        """miRNA 定量(quantifier.pl)。

        params:
          samples: [dict]     - [{name, collapsed_fa, arf?}]
          mature_mirna_fa: str   - 已知成熟 miRNA FASTA
          precursor_mirna_fa: str - 前体 miRNA FASTA (可选)
          threads: int        - 默认 4
        """
        p = req.params
        if not p.get("samples"):
            raise HTTPException(400, "params.samples 必填")
        if not p.get("mature_mirna_fa"):
            raise HTTPException(400, "params.mature_mirna_fa 必填")
        return _submit(JobKind.QUANTIFIER.value, req)

    @app.post("/submit/merge-counts")
    async def submit_merge_counts(req: SubmitJobBaseRequest):
        """合并多个单样本 miRNA counts 为一个矩阵。

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
        """CPM / RPM 标准化。

        params:
          counts_file:  输入的 counts 矩阵文件
          output_dir:   输出目录(默认 output_path)
          methods:      ["CPM", "RPM"] 子集,默认 ["CPM"]
        """
        p = req.params
        if not p.get("counts_file"):
            raise HTTPException(400, "需要 counts_file")
        return _submit(JobKind.NORMALIZE.value, req)

    @app.post("/submit/diff-expression")
    async def submit_diff_expression(req: SubmitJobBaseRequest):
        """DESeq2 差异表达分析(R 后端)。

        params:
          counts_file:    counts 矩阵文件
          metadata_file:  样本元数据文件(CSV/TSV)
          condition_col:  条件列名(默认 "condition")
          control:        对照组名
          treatment:      处理组名
          fdr_threshold:  FDR 阈值(默认 0.05)
          log2fc_threshold: |log2FC| 阈值(默认 1.0)
        """
        p = req.params
        if not p.get("counts_file"):
            raise HTTPException(400, "需要 counts_file")
        if not p.get("metadata_file"):
            raise HTTPException(400, "需要 metadata_file")
        return _submit(JobKind.DIFF_EXPRESSION.value, req)

    @app.post("/submit/target-prediction")
    async def submit_target_prediction(req: SubmitJobBaseRequest):
        """miRanda 靶基因预测(R 后端)。

        params:
          mirna_fasta: str   - miRNA 序列 FASTA
          utr_fasta:   str   - 3'UTR 序列 FASTA
          score_threshold: float - 默认 140
          energy_threshold: float - 默认 -20 (kcal/mol)
        """
        p = req.params
        if not p.get("mirna_fasta"):
            raise HTTPException(400, "需要 mirna_fasta")
        if not p.get("utr_fasta"):
            raise HTTPException(400, "需要 utr_fasta (3'UTR 序列)")
        return _submit(JobKind.TARGET_PREDICTION.value, req)

    @app.post("/submit/enrichment")
    async def submit_enrichment(req: SubmitJobBaseRequest):
        """GO/KEGG 富集分析(R 后端)。

        params:
          gene_list: [str]     - 靶基因列表
          organism: str        - 物种(如 "ath", "hsa", "mmu")
          pvalue_cutoff: float - 默认 0.05
          qvalue_cutoff: float - 默认 0.2
        """
        p = req.params
        if not p.get("gene_list"):
            raise HTTPException(400, "需要 gene_list")
        return _submit(JobKind.ENRICHMENT.value, req)

    @app.post("/submit/clustering")
    async def submit_clustering(req: SubmitJobBaseRequest):
        """miRNA 表达聚类(R 后端)。

        params:
          expression_file: str - 标准化表达矩阵文件
          n_clusters: int      - 聚类数(默认 4)
          distance: str        - 距离方法,默认 "euclidean"
        """
        p = req.params
        if not p.get("expression_file"):
            raise HTTPException(400, "需要 expression_file")
        return _submit(JobKind.CLUSTERING.value, req)

    @app.post("/submit/coexpression")
    async def submit_coexpression(req: SubmitJobBaseRequest):
        """miRNA-mRNA 共表达网络分析(R 后端)。

        params:
          mirna_expression_file: str - miRNA 表达矩阵
          mrna_expression_file: str  - mRNA 表达矩阵
          correlation_method: str    - 相关方法,默认 "spearman"
          cutoff: float              - 相关系数阈值,默认 0.7
          pvalue_cutoff: float       - P 值阈值,默认 0.05
        """
        p = req.params
        if not (p.get("mirna_expression_file") and p.get("mrna_expression_file")):
            raise HTTPException(400, "需要 mirna_expression_file 和 mrna_expression_file")
        return _submit(JobKind.COEXPRESSION.value, req)

    # ---- 错误处理 ----------------------------------------------

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
