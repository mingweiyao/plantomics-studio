"""任务模型与状态机

任务是模块的核心抽象。每个长耗时操作都包装成 Job:
  - SRA 下载 / FastQC / fastp / STAR / featureCounts / 标准化

状态机:
    pending → running → (completed | failed | cancelled | interrupted)

持久化:
  - $MODULE_DATA_DIR/jobs/<job_id>/job.json     ← 元信息 + 进度
  - $MODULE_DATA_DIR/jobs/<job_id>/log.txt      ← 完整日志(append-only)
  - 用户指定的 output_path                       ← 实际产物

"interrupted" 是恢复后的状态:模块进程意外重启时,把所有 running 改成
interrupted,用户能自己决定重启或删除。
"""
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"          # 已提交,排队等待
    RUNNING = "running"          # 正在执行
    COMPLETED = "completed"      # 成功结束
    FAILED = "failed"            # 报错
    CANCELLED = "cancelled"      # 用户取消
    INTERRUPTED = "interrupted"  # 被进程崩溃中断


# 上游任务类型
class JobKind(str, Enum):
    SRA_DOWNLOAD = "sra_download"
    SRA_EXTRACT = "sra_extract"     # 只解压本地 .sra 文件,不下载
    FASTQC = "fastqc"
    FASTP = "fastp"
    STAR_ALIGN = "star_align"
    STAR_INDEX = "star_index"
    FEATURE_COUNTS = "feature_counts"
    MERGE_COUNTS = "merge_counts"   # 合并多个单样本 counts 文件
    IMPORT_COUNTS = "import_counts"
    NORMALIZE = "normalize"
    # ─── 一键运行 ───
    PIPELINE_UPSTREAM = "pipeline_upstream"
    PIPELINE_DOWNSTREAM = "pipeline_downstream"
    # ─── 下游分析 ───
    DEG_DESEQ2 = "deg_deseq2"
    DEG_EDGER = "deg_edger"
    PLOT_PCA = "plot_pca"
    PLOT_CORR = "plot_corr"
    PLOT_VOLCANO = "plot_volcano"
    PLOT_MA = "plot_ma"
    PLOT_DEG_HEATMAP = "plot_deg_heatmap"
    ENRICHMENT = "enrichment"           # 统一 ORA + GSEA × 任意物种 × 任意本体论
    WGCNA = "wgcna"
    # ─── 物种数据库管理 ───
    BUILD_SPECIES = "build_species"

    # 通用可插拔分析(用户丢进 analyses/ 的自描述 R 脚本)
    RUN_ANALYSIS = "run_analysis"


@dataclass
class JobProgress:
    """任务进度。模块的 R/Python runner 调 update_progress() 来写。"""
    pct: int = 0           # 0-100
    stage: str = ""        # 当前阶段描述,例如 "比对中(2/8)"
    detail: str = ""       # 长描述,例如 "STAR aligning sample SRR1234"
    # 不确定进度:某些步骤(STAR 单样本比对、DESeq()、WGCNA blockwiseModules)
    # 是一个长时间的单次调用,中途无法估算百分比。这种情况把 indeterminate=True,
    # 前端渲染"流动/脉冲"动画而不是冻在某个固定宽度,避免看起来卡死。
    # 步骤结束后再设回 False 并给出确定的 pct。
    indeterminate: bool = False
    # 心跳时间戳(ISO8601 UTC)。长任务即使 pct 不变也会定期刷新它,
    # 前端可据此判断"任务还活着"。
    heartbeat: str = ""


@dataclass
class Job:
    id: str
    kind: str              # JobKind.value
    project_id: str        # 该任务所属的项目
    
    # 用户提交的参数(每种 kind 不同)
    params: dict[str, Any] = field(default_factory=dict)
    
    # 输出目录(用户指定的根)。任务实际产物会放在 output_path/<id>_<kind>_<ts>/
    output_path: str = ""
    output_subdir: str = ""  # 真实写入的目录,在 runner 启动时确定
    
    # 状态
    status: str = JobStatus.PENDING.value
    progress: JobProgress = field(default_factory=JobProgress)
    error: Optional[str] = None
    
    # 时间戳
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    
    # 进程信息(运行时)
    pid: Optional[int] = None
    
    @classmethod
    def new(cls, kind: str, project_id: str, params: dict[str, Any],
             output_path: str) -> "Job":
        return cls(
            id=str(uuid.uuid4())[:12],
            kind=kind,
            project_id=project_id,
            params=params,
            output_path=output_path,
            created_at=_now(),
        )
    
    def to_dict(self) -> dict:
        d = asdict(self)
        return d
    
    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        progress_data = d.pop("progress", {}) or {}
        if isinstance(progress_data, dict):
            progress = JobProgress(**progress_data)
        else:
            progress = JobProgress()
        return cls(progress=progress, **d)
    
    @property
    def is_terminal(self) -> bool:
        """终态:不会再变了。"""
        return self.status in (
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            JobStatus.INTERRUPTED.value,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ============================================================================
# 持久化 IO
# ============================================================================

def jobs_dir(module_data_dir: Path) -> Path:
    """jobs 目录位置。"""
    d = module_data_dir / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_file(module_data_dir: Path, job_id: str) -> Path:
    """job.json 路径。"""
    d = jobs_dir(module_data_dir) / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "job.json"


def log_file(module_data_dir: Path, job_id: str) -> Path:
    """log.txt 路径。"""
    d = jobs_dir(module_data_dir) / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "log.txt"


def save_job(module_data_dir: Path, job: Job) -> None:
    """原子写。先写 .tmp 再 rename,避免读到半截文件。"""
    f = job_file(module_data_dir, job.id)
    tmp = f.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(job.to_dict(), fh, indent=2, ensure_ascii=False)
    tmp.replace(f)


def load_job(module_data_dir: Path, job_id: str) -> Optional[Job]:
    f = job_file(module_data_dir, job_id)
    if not f.exists():
        return None
    try:
        with open(f, encoding="utf-8") as fh:
            return Job.from_dict(json.load(fh))
    except Exception as e:
        logger.exception(f"读 job {job_id} 失败: {e}")
        return None


def list_jobs(module_data_dir: Path,
               project_id: Optional[str] = None) -> list[Job]:
    """列出所有任务,默认按创建时间倒序。"""
    out = []
    base = jobs_dir(module_data_dir)
    for sub in base.iterdir():
        if not sub.is_dir():
            continue
        f = sub / "job.json"
        if not f.exists():
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                job = Job.from_dict(json.load(fh))
        except Exception:
            continue
        if project_id and job.project_id != project_id:
            continue
        out.append(job)
    out.sort(key=lambda j: j.created_at, reverse=True)
    return out


def delete_job(module_data_dir: Path, job_id: str) -> bool:
    """删除 job 记录(不删除产物文件)。"""
    import shutil
    d = jobs_dir(module_data_dir) / job_id
    if d.exists():
        shutil.rmtree(d)
        return True
    return False


def append_log(module_data_dir: Path, job_id: str, line: str) -> None:
    """追加日志一行(线程安全够用,不上锁)。"""
    f = log_file(module_data_dir, job_id)
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {line.rstrip()}\n")


def read_log(module_data_dir: Path, job_id: str,
              tail_lines: Optional[int] = None) -> str:
    f = log_file(module_data_dir, job_id)
    if not f.exists():
        return ""
    with open(f, encoding="utf-8") as fh:
        if tail_lines is None:
            return fh.read()
        lines = fh.readlines()
        return "".join(lines[-tail_lines:])
