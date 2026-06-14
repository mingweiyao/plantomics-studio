"""Job 调度器:管理并发、启动 runner 子进程、跟踪状态。

设计:
- 用户提交任务 → enqueue
- 调度器看 running 数 < max_concurrent 就 dequeue 一个 → 启动 runner
- runner 是独立子进程(R 或 Python),有自己的 PID
- runner 写 progress 到 jobs/<id>/job.json,主进程定期读
- 取消任务:发 SIGTERM 给 runner pid,5 秒不退就 SIGKILL
- 主进程崩溃:启动时把所有 running 改成 interrupted

为啥 runner 是独立子进程而不是线程?
- R 的 plumber 是单线程,跑 DESeq2 时整个进程被占
- 子进程能干净取消(发信号)
- 一个任务 OOM 不影响其他任务
"""
import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .model import (
    Job, JobStatus, JobKind,
    save_job, load_job, list_jobs, append_log,
)
from .resources import CpuBudget
from runners.dispatcher import dispatch_runner

logger = logging.getLogger(__name__)


class JobManager:
    """单例,在模块 main.py 里通过 get() 取。"""
    
    def __init__(self, module_data_dir: Path, max_concurrent: int = 2,
                 total_threads: int | None = None):
        self.data_dir = module_data_dir
        self.max_concurrent = max_concurrent
        # 全局 CPU 预算:把"总线程数"和"并行数"绑定,对外给出每任务线程配额。
        # 这样保证显示("N 线程 / M 并行")与实际 CPU 占用一致,
        # 不再出现 N 个任务各占满线程把机器超额订阅、被 OS 平分的情况。
        self.cpu_budget = CpuBudget(total_threads=total_threads,
                                    max_parallel=max_concurrent)
        
        # 内存状态
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        # job_id -> Process
        
        self._dispatcher_task: Optional[asyncio.Task] = None
        self._stopping = False
    
    async def start(self):
        """启动后台调度循环。"""
        # 崩溃恢复:把所有 RUNNING 改成 INTERRUPTED
        self._recover_interrupted()
        
        # 启动调度循环
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())
        logger.info(
            f"JobManager 启动 (max_concurrent={self.max_concurrent}, "
            f"data_dir={self.data_dir})"
        )
    
    async def stop(self):
        """优雅关闭:取消所有 running 任务。"""
        self._stopping = True
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
        
        # 杀掉所有 running 子进程
        for jid in list(self._processes.keys()):
            await self._kill_process(jid, force=True)
    
    def _recover_interrupted(self):
        """启动时,把所有 status=RUNNING 的任务改成 INTERRUPTED。"""
        for job in list_jobs(self.data_dir):
            if job.status == JobStatus.RUNNING.value:
                job.status = JobStatus.INTERRUPTED.value
                job.error = "进程意外重启,任务被中断"
                save_job(self.data_dir, job)
                logger.warning(f"恢复:任务 {job.id} ({job.kind}) 标记为 interrupted")
    
    # ─────────────────────────────────────────────────────
    # API
    # ─────────────────────────────────────────────────────
    
    def submit(self, kind: str, project_id: str, params: dict,
                output_path: str) -> Job:
        """提交任务。立刻返回(任务进 pending 队列)。"""
        if kind not in {k.value for k in JobKind}:
            raise ValueError(f"未知的任务类型: {kind}")
        
        job = Job.new(kind=kind, project_id=project_id, params=params,
                      output_path=output_path)
        save_job(self.data_dir, job)
        append_log(self.data_dir, job.id,
                   f"任务已提交 (kind={kind}, project={project_id})")
        logger.info(f"提交任务: {job.id} ({kind})")
        return job
    
    def get(self, job_id: str) -> Optional[Job]:
        return load_job(self.data_dir, job_id)
    
    def list(self, project_id: Optional[str] = None) -> list[Job]:
        return list_jobs(self.data_dir, project_id)
    
    async def cancel(self, job_id: str) -> bool:
        """取消任务。
        
        - 如果还在 pending,直接改状态
        - 如果在 running,发信号终止子进程
        """
        job = self.get(job_id)
        if not job:
            return False
        if job.is_terminal:
            return False
        
        if job.status == JobStatus.PENDING.value:
            job.status = JobStatus.CANCELLED.value
            job.finished_at = _now()
            save_job(self.data_dir, job)
            append_log(self.data_dir, job_id, "任务在排队时被取消")
            return True
        
        if job.status == JobStatus.RUNNING.value:
            await self._kill_process(job_id, force=False)
            # 等子进程报告状态变更,但加个超时保险
            for _ in range(10):  # 最多等 5 秒
                await asyncio.sleep(0.5)
                latest = self.get(job_id)
                if latest and latest.is_terminal:
                    return True
            # 实在不退,强行标记
            job.status = JobStatus.CANCELLED.value
            job.finished_at = _now()
            save_job(self.data_dir, job)
            return True
        
        return False
    
    def update_concurrency(self, n: int):
        """运行时调节最大并发数。同步更新 CPU 预算的并行数,
        于是每任务线程配额 = 总预算 // 并发数 也随之变化。"""
        self.max_concurrent = max(1, n)
        self.cpu_budget.update(max_parallel=self.max_concurrent)
        logger.info(f"max_concurrent 改为 {n},每任务配额={self.cpu_budget.quota_for()}")

    def update_cpu_budget(self, total_threads: int):
        """运行时调节总线程预算。"""
        self.cpu_budget.update(total_threads=total_threads)
    
    # ─────────────────────────────────────────────────────
    # 调度循环
    # ─────────────────────────────────────────────────────
    
    async def _dispatcher_loop(self):
        """每秒检查一次:有空位 + 有 pending 的任务,就启动新的。"""
        try:
            while not self._stopping:
                try:
                    await self._dispatch_tick()
                except Exception as e:
                    logger.exception(f"调度循环出错: {e}")
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
    
    async def _dispatch_tick(self):
        # 清理已完成的子进程引用
        for jid in list(self._processes.keys()):
            proc = self._processes[jid]
            if proc.returncode is not None:
                self._processes.pop(jid, None)
        
        # 数 running 任务
        running_count = len(self._processes)
        if running_count >= self.max_concurrent:
            return
        
        # 找最早的 pending
        all_jobs = self.list()
        pending = [j for j in all_jobs if j.status == JobStatus.PENDING.value]
        if not pending:
            return
        # 按创建时间排序(最早的先跑)
        pending.sort(key=lambda j: j.created_at)
        
        # 启动一个
        for job in pending:
            if running_count >= self.max_concurrent:
                break
            try:
                await self._launch(job)
                running_count += 1
            except Exception as e:
                logger.exception(f"启动 {job.id} 失败")
                job.status = JobStatus.FAILED.value
                job.error = f"启动失败: {e}"
                job.finished_at = _now()
                save_job(self.data_dir, job)
                append_log(self.data_dir, job.id, f"启动失败: {e}")
    
    async def _launch(self, job: Job):
        """启动 runner 子进程。"""
        # 不再套 <job_id>_<kind>_<ts>/ 子目录 — 用户希望输出直接进
        # output_path(它已经是项目工作目录的标准子文件夹,如 raw/ qc/)。
        # runner 自己负责按样本/accession 进一步分子目录。
        # 重跑时同名样本会覆盖,但任务历史保留参数和日志。
        out_root = Path(job.output_path)
        out_root.mkdir(parents=True, exist_ok=True)
        out_dir = out_root
        
        job.output_subdir = str(out_dir)
        job.status = JobStatus.RUNNING.value
        job.started_at = _now()
        save_job(self.data_dir, job)
        
        append_log(self.data_dir, job.id,
                   f"启动 runner,输出目录 {out_dir}")
        
        # 让 runners.dispatcher 决定怎么启动这种 kind 的任务
        # 注入本任务的线程配额(全局预算 // 并发数),runner 据此 clamp 线程数
        thread_quota = self.cpu_budget.quota_for(running_now=len(self._processes))
        proc = await dispatch_runner(job, self.data_dir,
                                     thread_quota=thread_quota)
        job.pid = proc.pid
        save_job(self.data_dir, job)
        
        self._processes[job.id] = proc
        logger.info(f"已启动 {job.id} ({job.kind}), pid={proc.pid}")
        
        # spawn 监控任务
        asyncio.create_task(self._watch_process(job.id, proc))
    
    async def _watch_process(self, job_id: str,
                              proc: asyncio.subprocess.Process):
        """等子进程结束,根据退出码更新任务状态。"""
        rc = await proc.wait()
        # runner 应该自己更新过 status,我们读最新状态
        job = self.get(job_id)
        if not job:
            return
        if not job.is_terminal:
            # runner 死了但没改状态(崩溃 / 被杀)
            if rc == 0:
                job.status = JobStatus.COMPLETED.value
            else:
                job.status = JobStatus.FAILED.value
                if not job.error:
                    job.error = f"runner 退出码 {rc}(无明确错误信息)"
            job.finished_at = _now()
            save_job(self.data_dir, job)
            append_log(self.data_dir, job_id,
                       f"runner 退出 (rc={rc}), 状态 = {job.status}")
        else:
            append_log(self.data_dir, job_id,
                       f"runner 退出 (rc={rc}), 任务已结束")
        
        # 失败时把 stdio_log 内容贴进主日志(最关键的诊断信息)
        if rc != 0:
            stdio_log_path = (self.data_dir / "jobs" / job_id /
                                "runner_stdio.log")
            if stdio_log_path.exists() and stdio_log_path.stat().st_size > 0:
                try:
                    with open(stdio_log_path, encoding="utf-8",
                                errors="replace") as f:
                        stdio_content = f.read().strip()
                    if stdio_content:
                        append_log(
                            self.data_dir, job_id,
                            f"\n=== runner 标准输出/错误 ===\n{stdio_content}"
                        )
                except Exception as e:
                    logger.warning(f"读 stdio_log 失败: {e}")
        
        self._processes.pop(job_id, None)
    
    async def _kill_process(self, job_id: str, force: bool = False):
        """杀子进程。force=True 直接 SIGKILL,否则先 SIGTERM。"""
        proc = self._processes.get(job_id)
        if not proc:
            return
        if proc.returncode is not None:
            return
        
        try:
            if force:
                proc.kill()
            else:
                proc.terminate()
                # 给 5 秒优雅退出
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                    return
                except asyncio.TimeoutError:
                    proc.kill()
        except ProcessLookupError:
            pass


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────
# 单例(模块进程内)
# ─────────────────────────────────────────────────────

_manager: Optional[JobManager] = None


def init(module_data_dir: Path, max_concurrent: int = 2,
         total_threads: int | None = None) -> JobManager:
    global _manager
    _manager = JobManager(module_data_dir, max_concurrent,
                          total_threads=total_threads)
    return _manager


def get() -> JobManager:
    if _manager is None:
        raise RuntimeError("JobManager 未初始化")
    return _manager
