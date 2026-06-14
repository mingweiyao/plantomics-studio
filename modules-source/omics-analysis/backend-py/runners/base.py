"""所有 Python runner 的通用基类。

设计要点:
  1. **流式日志**:subprocess.Popen + 实时按行读 stdout/stderr 写到 log.txt,
     用户可以实时看到进度(不用等命令跑完才显示)。
     原来用 capture_output=True 等命令跑完才一次性写,大输出还撑爆内存。
  
  2. **多样本并行**:run_in_parallel(func, items, workers) 用线程池跑独立子任务。
     每个子任务内部还是子进程(底层工具的多线程靠工具自己)。
     用法见 fastp_runner / fastqc_runner / sra_download_runner。
  
  3. **取消支持**:接 SIGTERM,正在跑的子进程也会被 terminate。
"""
import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import traceback
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Callable, Iterable, Any

# 把 backend-py 加进 sys.path,这样 from jobs... 能 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs.model import (
    Job, JobStatus, JobProgress,
    save_job, load_job, append_log,
)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class BaseRunner(ABC):
    """子类只需要实现 run()。"""
    
    def __init__(self):
        self.job: Optional[Job] = None
        self.data_dir: Optional[Path] = None
        self._cancelled = False
        # 当前跑的所有子进程,用于 SIGTERM 时一起 kill
        self._active_procs: list[subprocess.Popen] = []
        self._procs_lock = threading.Lock()
        # ── 进度作用域(修复 pipeline 里子 runner 进度倒退跳变)──
        # 子 runner 用 update(pct=0..100) 写"本步骤内的局部百分比",
        # 经由作用域映射成全局 [_pct_base, _pct_base+_pct_span] 区间。
        # 默认 0..100,即"局部=全局",单独跑时行为不变。
        # pipeline 在调每个子 runner 前 push_scope(base, span),子 runner 的
        # 0..100 就被压缩进对应的全局区间,进度条只会前进不会倒退。
        self._pct_base: float = 0.0
        self._pct_span: float = 100.0
        # ── CPU 配额 ──
        # JobManager 通过环境变量注入"本任务的线程配额",runner 一律以此为上限。
        # 没有注入(单元测试 / 直接命令行跑)时为 None,表示不限制。
        env_q = os.environ.get("PLANTOMICS_JOB_THREADS")
        self._thread_quota: Optional[int] = (
            max(1, int(env_q)) if env_q and env_q.isdigit() else None
        )
    
    @abstractmethod
    def run(self):
        """子类实现实际工作。"""
        ...
    
    def update(self, pct: int, stage: str = "", detail: str = "",
               indeterminate: bool = False):
        """更新进度。

        pct 是"本作用域内的局部百分比 0..100",会按当前作用域映射成全局百分比。
        单独跑时作用域是 0..100,映射后仍是原值;在 pipeline 里被 push_scope 后,
        会被压缩进对应的全局区间(于是不会从 75% 倒退回 12%)。

        indeterminate=True 时前端显示流动动画(用于无法估算进度的长步骤)。
        """
        if not self.job:
            return
        local = max(0, min(100, int(pct)))
        actual = self._pct_base + local / 100.0 * self._pct_span
        actual = int(max(0, min(100, round(actual))))
        self.job.progress = JobProgress(
            pct=actual, stage=stage, detail=detail,
            indeterminate=bool(indeterminate),
            heartbeat=_now_iso(),
        )
        save_job(self.data_dir, self.job)

    # ── 进度作用域 ──────────────────────────────
    def push_scope(self, base: float, span: float):
        """把后续 update(0..100) 映射到全局 [base, base+span]。

        返回 (旧 base, 旧 span),供 pop_scope 还原。pipeline 编排器这样用:

            old = self.push_scope(45, 30)   # STAR 阶段占全局 45%~75%
            self._run_subrunner(StarAlignRunner, ...)
            self.pop_scope(old)
        """
        old = (self._pct_base, self._pct_span)
        # 嵌套作用域:在当前作用域之内再切一刀
        self._pct_base = old[0] + base / 100.0 * old[1]
        self._pct_span = span / 100.0 * old[1]
        return old

    def pop_scope(self, old):
        self._pct_base, self._pct_span = old

    # ── CPU 配额 ────────────────────────────────
    # 线程模型(项目级 compute):
    #   project.compute.threads      = 单任务核数(per-task,显式传进每个 job 的 threads 参数)
    #   project.compute.parallel_jobs = 同时跑几个任务(= JobManager.max_concurrent)
    # 因此"单任务线程数"是用户在项目里显式设的值,runner 必须如实使用,
    # 不再用"总预算 ÷ 并行数"去二次切分(那会把用户设的值改小)。
    # 这两个方法保留接口、改为如实透传;真正的并发上限由 parallel_jobs 控制。
    def effective_threads(self, requested: int) -> int:
        """单进程多线程工具的线程数 = 项目设定的单任务核数,如实使用。"""
        return max(1, int(requested))

    def effective_parallel_alloc(self, requested_parallel: int,
                                 requested_threads_per: int) -> tuple[int, int]:
        """多样本并行工具:按调用方给的值透传(默认调用方已用项目 compute 设好)。"""
        return max(1, int(requested_parallel)), max(1, int(requested_threads_per))

    def heartbeat(self, stage: str = "", detail: str = "",
                  indeterminate: bool = True):
        """只刷新心跳(和可选的 stage/detail),不改 pct。

        用于长步骤中途"我还活着"的信号。pct 保持当前值,
        indeterminate 默认 True 让前端显示流动动画。
        """
        if not self.job:
            return
        cur = self.job.progress
        self.job.progress = JobProgress(
            pct=cur.pct,
            stage=stage or cur.stage,
            detail=detail or cur.detail,
            indeterminate=indeterminate,
            heartbeat=_now_iso(),
        )
        save_job(self.data_dir, self.job)
    
    def log(self, line: str):
        if not self.job:
            return
        append_log(self.data_dir, self.job.id, line)
    
    def output_dir(self) -> Path:
        return Path(self.job.output_subdir)
    
    def is_cancelled(self) -> bool:
        return self._cancelled
    
    def run_command(self, cmd: list, timeout: Optional[int] = None,
                     cwd: Optional[str] = None,
                     env: Optional[dict] = None,
                     indeterminate: bool = False,
                     heartbeat_stage: str = "") -> int:
        """流式执行命令,实时把 stdout/stderr 写到 job log。

        返回 returncode。非 0 时 raise CalledProcessError。

        indeterminate=True:这是一个无法估算进度的长步骤(STAR 比对单样本、
        samtools sort 等)。流式读输出的同时,每隔几秒刷新一次进度心跳,
        前端据此显示"流动动画 + 还活着",不会看起来卡死。
        heartbeat_stage:心跳时显示的阶段文字(默认沿用当前 stage)。

        会在子进程中提升 RLIMIT_NOFILE(open files)到硬上限,避免高线程
        STAR/samtools 这种大量并发文件句柄的工具因为 ulimit -n 默认 1024 而崩。
        """
        cmd_str = ' '.join(str(c) for c in cmd)
        self.log(f"$ {cmd_str}")

        if indeterminate:
            # 进入长步骤:先打一拍心跳,前端立刻切到流动动画
            self.heartbeat(stage=heartbeat_stage, indeterminate=True)
        import time as _time
        last_beat = _time.monotonic()
        HEARTBEAT_EVERY = 3.0  # 秒
        
        # preexec_fn:在 fork 之后、exec 之前跑,只影响子进程
        def _raise_nofile():
            try:
                import resource
                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                # 提到硬上限或 65536(取大),不动系统硬上限
                target = max(soft, min(hard, 65536) if hard > 0 else 65536)
                if hard > 0:
                    target = min(target, hard)
                if target > soft:
                    resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
            except Exception:
                # 限制不上就算了,不阻断子进程启动
                pass
        
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=cwd,
                env=env,
                preexec_fn=_raise_nofile,
            )
        except FileNotFoundError as e:
            self.log(f"!! 命令找不到: {e}")
            raise
        
        # 注册到活跃进程列表
        with self._procs_lock:
            self._active_procs.append(proc)
        
        try:
            # 流式读输出,实时写日志
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\n").rstrip("\r")
                if line:
                    self.log(f"  {line}")
                # 长步骤:定期刷新心跳(pct 不变,但证明任务还活着 + 维持流动动画)
                if indeterminate:
                    now = _time.monotonic()
                    if now - last_beat >= HEARTBEAT_EVERY:
                        self.heartbeat(
                            stage=heartbeat_stage,
                            detail=line[:120] if line else "",
                            indeterminate=True,
                        )
                        last_beat = now
                # 检查取消
                if self._cancelled:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    self.log(f"!! 任务被取消,已终止子进程")
                    raise InterruptedError("任务取消")
            
            # 等结束 + 拿 rc
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                raise
        finally:
            with self._procs_lock:
                if proc in self._active_procs:
                    self._active_procs.remove(proc)
        
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
        return rc
    
    def run_in_parallel(self, func: Callable[[Any], None],
                          items: Iterable, workers: int,
                          desc: str = "处理"):
        """并行跑 func(item) — 用线程池。
        
        - func: 接受单个 item 的函数(应该 return None,出错抛异常)
        - items: 可迭代
        - workers: 并发数
        - desc: 给日志用的描述
        
        进度:每完成一个就 update。
        异常:如果某个 item 失败,记日志继续,最后汇总
        """
        items_list = list(items)
        total = len(items_list)
        if total == 0:
            return
        
        workers = max(1, min(workers, total))
        self.log(f"=== {desc}: {total} 个任务,并行数 {workers} ===")
        
        completed = 0
        completed_lock = threading.Lock()
        errors = []
        
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(func, item): item for item in items_list}
            
            for fut in as_completed(futures):
                item = futures[fut]
                with completed_lock:
                    completed += 1
                    pct = int(completed / total * 100)
                
                try:
                    fut.result()
                    self.log(f"  ✓ [{completed}/{total}] {item}")
                except Exception as e:
                    errors.append((item, e))
                    self.log(f"  ✗ [{completed}/{total}] {item}: {e}")
                
                self.update(
                    pct=pct,
                    stage=f"{desc} {completed}/{total}",
                )
                
                if self._cancelled:
                    # 取消剩余
                    for f in futures:
                        f.cancel()
                    break
        
        if errors:
            err_summary = "; ".join(f"{i}: {e}" for i, e in errors[:3])
            if len(errors) > 3:
                err_summary += f" ... 共 {len(errors)} 个错误"
            raise RuntimeError(f"{desc}有失败: {err_summary}")
    
    # ─────────────────────────────────────────
    # 入口
    # ─────────────────────────────────────────
    
    @classmethod
    def main(cls):
        parser = argparse.ArgumentParser()
        parser.add_argument("--job-id", required=True)
        parser.add_argument("--data-dir", required=True)
        args = parser.parse_args()
        
        data_dir = Path(args.data_dir)
        job = load_job(data_dir, args.job_id)
        if not job:
            print(f"job {args.job_id} 不存在", file=sys.stderr)
            sys.exit(1)
        
        runner = cls()
        runner.job = job
        runner.data_dir = data_dir
        
        # 信号:SIGTERM 触发取消,active_procs 一起 kill
        def on_signal(signum, frame):
            runner._cancelled = True
            runner.log(f"收到信号 {signum},准备退出")
            with runner._procs_lock:
                for p in runner._active_procs:
                    try:
                        p.terminate()
                    except Exception:
                        pass
        
        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        
        runner.log(f"runner 启动 ({cls.__name__}, pid={os.getpid()})")
        try:
            runner.run()
            if runner._cancelled:
                job.status = JobStatus.CANCELLED.value
                runner.log("任务已取消")
            else:
                job.status = JobStatus.COMPLETED.value
                runner.log("任务完成")
            from datetime import datetime, timezone
            job.finished_at = datetime.now(timezone.utc).isoformat()
            save_job(data_dir, job)
            sys.exit(0)
        except Exception as e:
            tb = traceback.format_exc()
            job.status = JobStatus.FAILED.value
            job.error = f"{type(e).__name__}: {e}"
            from datetime import datetime, timezone
            job.finished_at = datetime.now(timezone.utc).isoformat()
            save_job(data_dir, job)
            runner.log(f"!! 任务失败: {e}")
            runner.log(tb)
            sys.exit(1)
