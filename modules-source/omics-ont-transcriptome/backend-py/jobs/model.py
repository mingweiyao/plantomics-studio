"""任务模型与状态机

任务是模块的核心抽象。每个长耗时操作都包装成 Job:
  - ONT basecall / NanoFilt / Pychopper / minimap2 / Pinfish / StringTie / ...

状态机:
    pending -> running -> (completed | failed | cancelled | interrupted)

持久化:
  - $MODULE_DATA_DIR/jobs/<job_id>/job.json     <- 元信息 + 进度
  - $MODULE_DATA_DIR/jobs/<job_id>/log.txt      <- 完整日志(append-only)
  - 用户指定的 output_path                       <- 实际产物

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


# ONT 全长转录组任务类型
class JobKind(str, Enum):
    BASECALL = "basecall"                      # Dorado/Guppy basecalling
    NANOFILT = "nanofilt"                      # NanoFilt QC + NanoStat
    PYCHOPPER = "pychopper"                    # 全长 read 鉴定
    MINIMAP2_ALIGN = "minimap2_align"          # minimap2 比对
    PINFISH = "pinfish"                        # Pinfish 转录本组装
    STRINGTIE = "stringtie"                    # StringTie 冗余去除
    GFFCOMPARE = "gffcompare"                  # 新转录本发现
    TRANSDECODER = "transdecoder"              # CDS 预测
    ANNOT_7DB = "annot_7db"                    # 7 数据库功能注释
    SALMON_QUANT = "salmon_quant"              # Salmon 定量
    SUPPA2 = "suppa2"                          # SUPPA2 可变剪接
    FUSION = "fusion"                          # 融合基因检测
    SSR = "ssr"                                # SSR 分析
    TF = "tf"                                  # 转录因子鉴定


@dataclass
class JobProgress:
    """任务进度。模块的 R/Python runner 调 update_progress() 来写。"""
    pct: int = 0           # 0-100
    stage: str = ""        # 当前阶段描述,例如 "比对中(2/8)"
    detail: str = ""       # 长描述,例如 "Dorado basecalling sample ABC"
    indeterminate: bool = False  # 无法估进度时前端显示流动动画
    heartbeat: str = ""    # 心跳时间戳(ISO8601 UTC)
    step: str = ""         # 一键流程当前所处的"流程节点 id"


@dataclass
class Job:
    id: str
    kind: str              # JobKind.value
    project_id: str        # 该任务所属的项目

    params: dict[str, Any] = field(default_factory=dict)

    output_path: str = ""
    output_subdir: str = ""

    status: str = JobStatus.PENDING.value
    progress: JobProgress = field(default_factory=JobProgress)
    error: Optional[str] = None

    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

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
        return self.status in (
            JobStatus.COMPLETED.value,
            JobStatus.FAILED.value,
            JobStatus.CANCELLED.value,
            JobStatus.INTERRUPTED.value,
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jobs_dir(module_data_dir: Path) -> Path:
    d = module_data_dir / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def job_file(module_data_dir: Path, job_id: str) -> Path:
    d = jobs_dir(module_data_dir) / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "job.json"


def log_file(module_data_dir: Path, job_id: str) -> Path:
    d = jobs_dir(module_data_dir) / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "log.txt"


def save_job(module_data_dir: Path, job: Job) -> None:
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
    import shutil
    d = jobs_dir(module_data_dir) / job_id
    if d.exists():
        shutil.rmtree(d)
        return True
    return False


def append_log(module_data_dir: Path, job_id: str, line: str) -> None:
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
