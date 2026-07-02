"""根据 Job.kind 启动合适的 runner 子进程。

设计:
- Python 跑的(SRA/QC/对齐/量化):用模块自己的 env 启动一个 Python 子进程,
  调底层命令行工具(prefetch / fastqc / STAR ...)
- R 跑的(标准化/差异/富集/WGCNA 等):启动 Rscript 子进程跑 .R 文件

每个 runner 收到 --job-id,从 job.json 读 params,
跑完更新 status/progress 写回 job.json。

**关键修复**:之前用 `stdout=DEVNULL stderr=DEVNULL`,启动错误(R 找不到包、
路径错等)被吞掉,前端只看到 rc!=0 但日志空白。改成把 stdio 都接到
job log 文件 — 启动期错误能被前端"日志"看到。
"""
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from jobs.model import Job, JobKind, log_file

logger = logging.getLogger(__name__)


# 模块根目录(env、scripts 等)
MODULE_ROOT = Path("/opt/plantomics-studio/modules/omics-ont-lncrna")
# 开发模式下根可能是别的位置 - 由 main.py 启动时通过 set_module_root 修正
_module_root_override: Optional[Path] = None


def set_module_root(path: Path):
    global _module_root_override
    _module_root_override = path


def module_root() -> Path:
    return _module_root_override or MODULE_ROOT


# ─────────────────────────────────────────────────────
# Kind → Runner 映射
# ─────────────────────────────────────────────────────

# Python runners(底层调命令行工具 / 纯 Python 计算)
PY_RUNNERS = {
    JobKind.BASECALL.value: "basecall_runner",
    JobKind.NANOFILT.value: "nanofilt_runner",
    JobKind.PYCHOPPER.value: "pychopper_runner",
    JobKind.RRNA_REMOVE.value: "rrna_remove_runner",
    JobKind.MINIMAP2_ALIGN.value: "minimap2_align_runner",
    JobKind.PINFISH.value: "pinfish_runner",
    JobKind.STRINGTIE.value: "stringtie_runner",
    JobKind.GFFCOMPARE.value: "gffcompare_runner",
    JobKind.TRANSDECODER.value: "transdecoder_runner",
    JobKind.ANNOT_7DB.value: "annot_7db_runner",
    JobKind.LNCRNA_IDENTIFY.value: "lncrna_identify_runner",
    JobKind.LNCRNA_CLASSIFY.value: "lncrna_classify_runner",
    JobKind.SALMON_QUANT.value: "salmon_quant_runner",
    JobKind.SUPPA2.value: "suppa2_runner",
    JobKind.FUSION.value: "fusion_runner",
    JobKind.SSR.value: "ssr_runner",
    JobKind.TF.value: "tf_runner",
}

async def dispatch_runner(job: Job, data_dir: Path,
                          thread_quota: Optional[int] = None
                          ) -> asyncio.subprocess.Process:
    """根据 job.kind 启动 runner 子进程,返回 Process。

    被 JobManager._launch 调用。runner 自己更新 job.json 的 status/progress。
    thread_quota:本任务可用的线程配额(全局 CPU 预算 // 并发数),
    通过环境变量 PLANTOMICS_JOB_THREADS 传给 runner;runner(base.py)据此把
    线程数 clamp 到配额内,保证全机器不超额订阅。
    本模块只有 Python runner;下游分析(用 R)已迁到 omics-analysis 模块。
    """
    env = _make_env(job, data_dir, thread_quota=thread_quota)

    if job.kind in PY_RUNNERS:
        return await _spawn_python_runner(job, data_dir, PY_RUNNERS[job.kind], env)
    else:
        raise ValueError(f"没有为 kind={job.kind} 注册 runner")


def _open_log_for_subprocess(data_dir: Path, job_id: str):
    """为子进程的 stdout/stderr 打开 job log 文件(append 模式)。
    
    让子进程写的所有 print/error 都进 job log,前端"日志"页能看到。
    """
    log_path = log_file(data_dir, job_id)
    return open(log_path, "ab")


async def _spawn_python_runner(job: Job, data_dir: Path,
                                 runner_module: str, env: dict
                                 ) -> asyncio.subprocess.Process:
    """启动 Python runner 子进程。
    
    调用 `python -m runners.<runner_module> --job-id <id> --data-dir <dir>`
    """
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
    log_fh.close()  # 进程已经接管 fd,我们关掉自己的句柄
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
    # 本任务线程配额:runner 据此 clamp 线程数,避免并发任务超额订阅 CPU
    if thread_quota is not None:
        env["PLANTOMICS_JOB_THREADS"] = str(max(1, int(thread_quota)))
    # 如果要 R 端没法读到代理设置(代理污染 localhost),unset
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(k, None)
    return env
