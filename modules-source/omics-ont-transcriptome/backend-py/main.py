"""omics-ont-transcriptome 模块 - Python 后端

ONT 全长转录组分析:
从 ONT raw data 到全长序列鉴定、转录本组装、新转录本发现、
表达定量、可变剪接分析、融合基因、SSR 及转录因子分析。

启动方式:由主程序通过 LifecycleManager 启动,环境变量包括:
  MODULE_PY_PORT       要监听的端口
  MODULE_R_PORT        本模块 R 后端的端口(用于 Python 转 R)
  PLANTOMICS_DATA_DIR  主程序数据目录(只读)
  MODULE_DATA_DIR      模块自己的数据目录(可写)
  PLANTOMICS_CORE_API  主程序 API 地址

提供两类端点:
  1. 任务相关 /jobs - 提交、查询、取消任务(异步)
  2. 同步轻查询 /tools/list 等 - 不需要异步的小操作
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

MODULE_ID = "omics-ont-transcriptome"
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
        """Dorado/Guppy basecalling (pod5/fast5 -> fastq)。
        params:
          input_dir: str      - pod5/fast5 所在目录
          model: str           - 模型名称或路径(dna_r10.4.1_e8.2_400bps_hac@v4.2.0)
          kit: str             - 试剂盒名(SQK-LSK114)
          sample_name: str     - 样本名
          output_format: str   - fastq(默认) / cram / bam
          skip_qscore: bool    - 跳过碱基质量评分,默认 False
          trim: bool           - 自动切 adapter,默认 True
          device: str          - cuda:0(默认) / cpu
          batchsize: int       - 每批 read 数,默认 256
          threads: int         - 默认 8
        """
        p = req.params
        if not p.get("input_dir"):
            raise HTTPException(400, "params.input_dir 必填(raw data 目录)")
        return _submit(JobKind.BASECALL.value, req)

    # ── 2. NanoFilt 质控 ─────────────────────────
    @app.post("/submit/nanofilt")
    async def submit_nanofilt(req: SubmitJobBaseRequest):
        """NanoFilt QC + NanoStat 统计。
        params:
          fastq: str        - 输入 fastq(或目录)
          q: int            - 最低质量值,默认 7
          min_length: int   - 最短序列长度,默认 50
          max_length: int   - 最长序列长度,默认 0(不限制)
          headcrop: int     - 从 5' 端切除碱基数,默认 0
          tailcrop: int     - 从 3' 端切除碱基数,默认 0
          output_prefix: str - 输出文件名前缀
        """
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填(输入 fastq 文件)")
        return _submit(JobKind.NANOFILT.value, req)

    # ── 3. Pychopper 全长鉴定 ────────────────────
    @app.post("/submit/pychopper")
    async def submit_pychopper(req: SubmitJobBaseRequest):
        """Pychopper 全长 read 鉴定和引物修剪。
        params:
          fastq: str            - 输入 fastq
          output_prefix: str    - 输出文件前缀
          min_length: int       - 最短全长长度,默认 50
          max_length: int       - 最长全长长度,默认 0(不限制)
          q: int                - 最低质量,默认 7
          primer_scheme: str    - 引物方案(auto / custom)
          threads: int          - 默认 8
        """
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        return _submit(JobKind.PYCHOPPER.value, req)

    # ── 4. minimap2 比对 ──────────────────────────
    @app.post("/submit/minimap2-align")
    async def submit_minimap2_align(req: SubmitJobBaseRequest):
        """minimap2 比对 (-ax splice -uf -k14)。
        params:
          fastq: str           - query fastq
          index: str           - 参考基因组索引(.mmi)或 FASTA
          output_bam: str      - 输出 BAM 路径(可选,默认 <name>.sorted.bam)
          extra_opts: str      - 额外参数(追加到命令行末尾)
          sort_memory: str     - samtools sort 内存,默认 2G
          threads: int         - 默认 8
        """
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        if not req.params.get("index"):
            raise HTTPException(400, "params.index 必填(参考基因组)")
        return _submit(JobKind.MINIMAP2_ALIGN.value, req)

    # ── 5. Pinfish 转录本组装 ────────────────────
    @app.post("/submit/pinfish")
    async def submit_pinfish(req: SubmitJobBaseRequest):
        """Pinfish 一致性转录本组装(4步流程)。
        params:
          bam: str              - 经过排序的比对 BAM
          genome_fasta: str     - 参考基因组 FASTA
          annotation_gtf: str   - 参考注释 GTF(可选)
          min_coverage: float   - spliced_bam2gff 最小覆盖,默认 0.1
          min_cluster_size: int - cluster_gff 最小聚类大小,默认 5
          threads: int          - 默认 8
        """
        if not req.params.get("bam"):
            raise HTTPException(400, "params.bam 必填")
        if not req.params.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.PINFISH.value, req)

    # ── 6. StringTie 冗余去除 ─────────────────────
    @app.post("/submit/stringtie")
    async def submit_stringtie(req: SubmitJobBaseRequest):
        """StringTie 合并及冗余去除 (--conservative -L -R)。
        params:
          input_gtfs: [str]    - 输入的 GTF 文件列表(Pinfish 输出)
          annotation_gtf: str  - 参考注释 GTF(可选,用于合并)
          reference_gtf: str   - 参考注释 GTF(用于 -G 引导合并)
          merge_only: bool     - 只做 merge 不做 redundancy removal
          threads: int         - 默认 8
        """
        p = req.params
        if not p.get("input_gtfs") and not p.get("gtf_list_file"):
            raise HTTPException(400, "params.input_gtfs 或 gtf_list_file 必填")
        return _submit(JobKind.STRINGTIE.value, req)

    # ── 7. gffcompare 新转录本 ────────────────────
    @app.post("/submit/gffcompare")
    async def submit_gffcompare(req: SubmitJobBaseRequest):
        """gffcompare 新转录本发现及分类 (-R -C -K -M)。
        params:
          query_gtf: str       - 待比较的 GTF(例如 StringTie merged)
          reference_gtf: str   - 参考注释 GTF
          prefix: str          - 输出前缀
          extra_opts: str      - 额外参数
        """
        p = req.params
        if not p.get("query_gtf"):
            raise HTTPException(400, "params.query_gtf 必填")
        if not p.get("reference_gtf"):
            raise HTTPException(400, "params.reference_gtf 必填")
        return _submit(JobKind.GFFCOMPARE.value, req)

    # ── 8. TransDecoder 编码区预测 ────────────────
    @app.post("/submit/transdecoder")
    async def submit_transdecoder(req: SubmitJobBaseRequest):
        """TransDecoder CDS 预测 (-m 50 --single_best_only)。
        params:
          transcript_fasta: str  - 转录本 FASTA(可以是 gffread 提取的)
          genome_fasta: str      - 基因组 FASTA(用于 gffread 提取)
          candidate_gtf: str     - 候选 GTF(从新转录本步骤来)
          min_orf_aa: int        - 最短 ORF 长度,默认 50
          single_best: bool      - 只保留最优 ORF,默认 True
        """
        p = req.params
        if not (p.get("candidate_gtf") or p.get("transcript_fasta")):
            raise HTTPException(400, "params.candidate_gtf 或 transcript_fasta 必填")
        if p.get("candidate_gtf") and not p.get("genome_fasta"):
            raise HTTPException(400, "有 candidate_gtf 时 genome_fasta 必填(gffread 提取序列用)")
        return _submit(JobKind.TRANSDECODER.value, req)

    # ── 9. 7 数据库注释 ──────────────────────────
    @app.post("/submit/annot-7db")
    async def submit_annot_7db(req: SubmitJobBaseRequest):
        """7 数据库功能注释 (diamond -> Nr/Uniprot, hmmscan -> Pfam, kofam_scan -> KEGG)。
        params:
          pep_fasta: str         - 蛋白质序列(TransDecoder 输出)
          cds_fasta: str         - CDS 序列(可选)
          nr_db: str             - NR 数据库路径
          uniprot_db: str        - UniProt 数据库路径
          pfam_db: str           - Pfam HMM 数据库路径
          kofam_db: str          - KEGG kofam 数据库目录
          eggnog_db: str         - eggNOG 数据库路径(可选)
          go_obo: str            - go.obo 路径(可选)
          threads: int           - 默认 8
          evalue: float          - DIAMOND e-value 阈值,默认 1e-5
        """
        if not req.params.get("pep_fasta"):
            raise HTTPException(400, "params.pep_fasta 必填")
        return _submit(JobKind.ANNOT_7DB.value, req)

    # ── 10. Salmon 定量 ──────────────────────────
    @app.post("/submit/salmon-quant")
    async def submit_salmon_quant(req: SubmitJobBaseRequest):
        """Salmon 转录本定量。
        params:
          fastq: str              - 输入 fastq 文件
          index: str              - Salmon 索引目录
          lib_type: str           - 文库类型,默认 A
          output_dir: str         - 输出目录
          extra_opts: str         - 额外参数
          threads: int            - 默认 8
        """
        if not req.params.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        if not req.params.get("index"):
            raise HTTPException(400, "params.index 必填(Salmon 索引)")
        return _submit(JobKind.SALMON_QUANT.value, req)

    # ── 11. SUPPA2 可变剪接 ─────────────────────
    @app.post("/submit/suppa2")
    async def submit_suppa2(req: SubmitJobBaseRequest):
        """SUPPA2 可变剪接分析 (7 类型 + 差异)。
        params:
          gtf: str                - 注释 GTF
          tpm_file: str           - TPM 表达矩阵
          condition_file: str     - 条件分组文件(差异分析用)
          output_prefix: str      - 输出前缀
          as_types: [str]         - 剪接类型列表,默认全部(SE/SS/MXE/A5/A3/AF/AL)
          psi_threshold: float    - PSI 差异阈值,默认 0.1
          pval_threshold: float   - p-value 阈值,默认 0.05
        """
        p = req.params
        if not p.get("gtf"):
            raise HTTPException(400, "params.gtf 必填")
        if not p.get("tpm_file"):
            raise HTTPException(400, "params.tpm_file 必填")
        return _submit(JobKind.SUPPA2.value, req)

    # ── 12. 融合基因检测 ──────────────────────────
    @app.post("/submit/fusion")
    async def submit_fusion(req: SubmitJobBaseRequest):
        """融合转录本检测。
        params:
          fastq: str              - 输入 fastq
          genome_fasta: str       - 参考基因组
          annotation_gtf: str     - 注释 GTF
          method: str             - 检测方法(artic / pizza)
          extra_opts: str         - 额外参数
          threads: int            - 默认 8
        """
        p = req.params
        if not p.get("fastq"):
            raise HTTPException(400, "params.fastq 必填")
        if not p.get("genome_fasta"):
            raise HTTPException(400, "params.genome_fasta 必填")
        return _submit(JobKind.FUSION.value, req)

    # ── 13. SSR 分析 ─────────────────────────────
    @app.post("/submit/ssr")
    async def submit_ssr(req: SubmitJobBaseRequest):
        """SSR(微卫星)分析。
        params:
          fasta: str              - 输入序列 FASTA(转录本或基因组)
          min_repeats: dict       - 各重复单元最小重复次数,{1:10, 2:6, 3:5, 4:5, 5:5, 6:5}
          output_prefix: str      - 输出前缀
          method: str             - 检测工具(MISA / SSRIT)
        """
        if not req.params.get("fasta"):
            raise HTTPException(400, "params.fasta 必填")
        return _submit(JobKind.SSR.value, req)

    # ── 14. 转录因子鉴定 ─────────────────────────
    @app.post("/submit/tf")
    async def submit_tf(req: SubmitJobBaseRequest):
        """转录因子鉴定 (PlantTFDB / AnimalTFDB)。
        params:
          pep_fasta: str          - 蛋白质序列 FASTA
          organism: str           - 物种(plant / animal)
          db_dir: str             - 数据库目录
          evalue: float           - HMMER e-value 阈值,默认 1e-5
          output_prefix: str      - 输出前缀
          threads: int            - 默认 8
        """
        if not req.params.get("pep_fasta"):
            raise HTTPException(400, "params.pep_fasta 必填")
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
