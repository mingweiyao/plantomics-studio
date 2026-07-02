"""根据 Job.kind 启动合适的 runner 子进程。

设计:
- Python 跑的:用模块自己的 env 启动一个 Python 子进程,
  调底层命令行工具(prefetch / fastp / bowtie / miRDeep2 ...)
- R 跑的:启动 Rscript 子进程跑 .R 文件

每个 runner 收到 --job-id,从 job.json 读 params,
跑完更新 status/progress 写回 job.json。
"""
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from jobs.model import Job, JobKind, log_file

logger = logging.getLogger(__name__)


# 模块根目录
MODULE_ROOT = Path("/opt/plantomics-studio/modules/omics-mirna")
# 开发模式下根可能是别的位置 - 由 main.py 启动时通过 set_module_root 修正
_module_root_override: Optional[Path] = None


def set_module_root(path: Path):
    global _module_root_override
    _module_root_override = path


def module_root() -> Path:
    return _module_root_override or MODULE_ROOT


# ---- Kind -> Runner 映射 -----------------------------------------------

# Python runners(底层调命令行工具)
PY_RUNNERS = {
    JobKind.SRA_DOWNLOAD.value: "sra_download_runner",
    JobKind.SRA_EXTRACT.value: "sra_download_runner",
    JobKind.FASTQC.value: "fastqc_runner",
    JobKind.FASTQC_RAW.value: "fastqc_runner",
    JobKind.FASTQC_TRIMMED.value: "fastqc_runner",
    JobKind.FASTP.value: "fastp_runner",
    JobKind.BOWTIE_ALIGN.value: "bowtie_align_runner",
    JobKind.MIRDEEP2.value: "mirdeep2_runner",
    JobKind.QUANTIFIER.value: "quantifier_runner",
    JobKind.MERGE_COUNTS.value: "merge_counts_runner",
    JobKind.NORMALIZE.value: "normalize_runner",
}

# R runners(调 Rscript 跑 .R 脚本)
R_RUNNERS = {
    JobKind.DIFF_EXPRESSION.value: "run_diff_expression.R",
    JobKind.TARGET_PREDICTION.value: "run_target_prediction.R",
    JobKind.ENRICHMENT.value: "run_enrichment.R",
    JobKind.CLUSTERING.value: "run_clustering.R",
    JobKind.COEXPRESSION.value: "run_coexpression.R",
}


async def dispatch_runner(job: Job, data_dir: Path,
                          thread_quota: Optional[int] = None
                          ) -> asyncio.subprocess.Process:
    """根据 job.kind 启动 runner 子进程,返回 Process。

    被 JobManager._launch 调用。runner 自己更新 job.json 的 status/progress。
    thread_quota:本任务可用的线程配额(全局 CPU 预算 // 并发数),
    通过环境变量 PLANTOMICS_JOB_THREADS 传给 runner。
    """
    env = _make_env(job, data_dir, thread_quota=thread_quota)

    if job.kind in PY_RUNNERS:
        return await _spawn_python_runner(job, data_dir, PY_RUNNERS[job.kind], env)
    elif job.kind in R_RUNNERS:
        return await _spawn_r_runner(job, data_dir, R_RUNNERS[job.kind], env)
    else:
        raise ValueError(f"没有为 kind={job.kind} 注册 runner")


def _open_log_for_subprocess(data_dir: Path, job_id: str):
    """为子进程的 stdout/stderr 打开 job log 文件(append 模式)。"""
    log_path = log_file(data_dir, job_id)
    return open(log_path, "ab")


async def _spawn_python_runner(job: Job, data_dir: Path,
                               runner_module: str, env: dict
                               ) -> asyncio.subprocess.Process:
    """启动 Python runner 子进程。"""
    py = module_root() / "env/bin/python3"
    backend_dir = module_root() / "backend-py"

    if not py.exists():
        raise FileNotFoundError(f"模块 env 缺 python3: {py}")

    cmd = [
        str(py), "-m", f"runners.{runner_module}",
        "--job-id", job.id,
        "--data-dir", str(data_dir),
    ]
    logger.info(f"启动 Python runner: {' '.join(cmd)}")

    log_fh = _open_log_for_subprocess(data_dir, job.id)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(backend_dir),
        env=env,
        stdout=log_fh,
        stderr=log_fh,
    )
    log_fh.close()
    return proc


async def _spawn_r_runner(job: Job, data_dir: Path,
                          script_name: str, env: dict
                          ) -> asyncio.subprocess.Process:
    """启动 Rscript runner 子进程。"""
    rscript = module_root() / "env/bin/Rscript"
    scripts_dir = module_root() / "backend-r" / "scripts"
    backend_r_dir = module_root() / "backend-r"

    if not rscript.exists():
        # 可能 Rscript 在 PATH 里
        rscript = Path("Rscript")

    cmd = [
        str(rscript), str(scripts_dir / script_name),
        "--job-id", job.id,
        "--data-dir", str(data_dir),
    ]
    logger.info(f"启动 R runner: {' '.join(cmd)}")

    log_fh = _open_log_for_subprocess(data_dir, job.id)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(backend_r_dir),
        env=env,
        stdout=log_fh,
        stderr=log_fh,
    )
    log_fh.close()
    return proc


def _make_env(job: Job, data_dir: Path,
              thread_quota: Optional[int] = None) -> dict:
    """构造 runner 子进程的环境变量。"""
    env = dict(os.environ)
    # 把 env/bin 放到 PATH 最前
    env_bin = str(module_root() / "env/bin")
    env["PATH"] = env_bin + ":" + env.get("PATH", "")
    # 模块数据目录(runner 用来读 job.json)
    env["MODULE_DATA_DIR"] = str(data_dir)
    # backend-py 在 PYTHONPATH 里
    env["PYTHONPATH"] = str(module_root() / "backend-py")
    # 本任务线程配额
    if thread_quota is not None:
        env["PLANTOMICS_JOB_THREADS"] = str(max(1, int(thread_quota)))
    # 如果要 R 端没法读到代理设置(代理污染 localhost),unset
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(k, None)
    return env
