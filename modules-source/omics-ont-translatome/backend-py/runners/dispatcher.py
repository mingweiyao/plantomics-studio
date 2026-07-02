"""根据 Job.kind 启动合适的 runner 子进程。"""
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from jobs.model import Job, JobKind, log_file

logger = logging.getLogger(__name__)


MODULE_ROOT = Path("/opt/plantomics-studio/modules/omics-ont-translatome")
_module_root_override: Optional[Path] = None


def set_module_root(path: Path):
    global _module_root_override
    _module_root_override = path


def module_root() -> Path:
    return _module_root_override or MODULE_ROOT


PY_RUNNERS = {
    JobKind.BASECALL.value: "basecall_runner",
    JobKind.NANOFILT.value: "nanofilt_runner",
    JobKind.PYCHOPPER.value: "pychopper_runner",
    JobKind.MINIMAP2_ALIGN.value: "minimap2_align_runner",
    JobKind.PINFISH.value: "pinfish_runner",
    JobKind.STRINGTIE.value: "stringtie_runner",
    JobKind.GFFCOMPARE.value: "gffcompare_runner",
    JobKind.TRANSDECODER.value: "transdecoder_runner",
    JobKind.ANNOT_7DB.value: "annot_7db_runner",
    JobKind.SALMON_QUANT.value: "salmon_quant_runner",
    JobKind.SUPPA2.value: "suppa2_runner",
    JobKind.FUSION.value: "fusion_runner",
    JobKind.SSR.value: "ssr_runner",
    JobKind.TF.value: "tf_runner",
    JobKind.REF_VS_ALL.value: "ref_vs_all_runner",
}


async def dispatch_runner(job: Job, data_dir: Path,
                          thread_quota: Optional[int] = None
                          ) -> asyncio.subprocess.Process:
    env = _make_env(job, data_dir, thread_quota=thread_quota)

    if job.kind in PY_RUNNERS:
        return await _spawn_python_runner(job, data_dir, PY_RUNNERS[job.kind], env)
    else:
        raise ValueError(f"没有为 kind={job.kind} 注册 runner")


def _open_log_for_subprocess(data_dir: Path, job_id: str):
    log_path = log_file(data_dir, job_id)
    return open(log_path, "ab")


async def _spawn_python_runner(job: Job, data_dir: Path,
                                 runner_module: str, env: dict
                                 ) -> asyncio.subprocess.Process:
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


def _make_env(job: Job, data_dir: Path,
              thread_quota: Optional[int] = None) -> dict:
    env = dict(os.environ)
    env_bin = str(module_root() / "env/bin")
    env["PATH"] = env_bin + ":" + env.get("PATH", "")
    env["MODULE_DATA_DIR"] = str(data_dir)
    env["PYTHONPATH"] = str(module_root() / "backend-py")
    if thread_quota is not None:
        env["PLANTOMICS_JOB_THREADS"] = str(max(1, int(thread_quota)))
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(k, None)
    return env
