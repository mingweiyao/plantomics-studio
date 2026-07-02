"""全局 CPU 预算分配器 — 修复"显示 25 线程 4 并行,实际平分到所有任务"的问题。

# 问题背景
原来的设计里有两个互不相关的旋钮:
  - JobManager.max_concurrent  →  同时跑几个任务(并行数)
  - 每个 job 的 threads / threads_per_sample × parallel  →  单任务线程数

二者完全独立、谁也不知道谁。于是 N 个并发任务各自申请满线程,机器被超额
订阅(over-subscription),操作系统只能把物理核心**时间片轮转**地分给所有
线程 —— 用户看到的就是"资源被平分到所有任务上",且界面显示的两个数字
和实际 CPU 占用根本对不上。

# 本模块做什么
把"总线程预算 B"做成一个真实、被强制执行的全局量,并和"并行数 P"挂钩:

    保证:任意时刻所有在跑任务的线程数之和 ≤ B,且并发任务数 ≤ P。

分配策略(静态均分,可预测、和用户心智模型一致):

    每个任务的线程配额 quota = max(1, B // P)

JobManager 启动每个 runner 子进程前,把 quota 通过环境变量
`PLANTOMICS_JOB_THREADS` 注入。runner 一律以 quota 为准 clamp 自己的线程数
(见 runners/base.py 的 effective_threads / effective_parallel_alloc)。

这样"B 线程 / P 并行"在界面上就能如实写成
"每任务 ⌊B/P⌋ 线程,最多 P 个并行" —— 显示与实际完全一致。

> 设计取舍:这里用"静态均分"而不是"令牌桶动态借还"。对桌面端、任务数不多
> 但每个都重(STAR/DESeq2/WGCNA)的场景,静态均分更可预测,也正好对应用户
> 期望的"把预算摊给并行槽"。接口上预留了 quota_for() 方法,将来要换成
> 令牌桶只需改这一个类,JobManager / runner 都不用动。
"""
import logging
import os

logger = logging.getLogger(__name__)


def detect_total_cpus() -> int:
    """探测本机逻辑核心数,作为默认 CPU 预算上限。"""
    try:
        # os.sched_getaffinity 更准(尊重 cgroup / taskset 限制),
        # 不是所有平台都有,退回 os.cpu_count()
        if hasattr(os, "sched_getaffinity"):
            return max(1, len(os.sched_getaffinity(0)))
    except Exception:
        pass
    return max(1, os.cpu_count() or 1)


class CpuBudget:
    """全局 CPU 预算 + 并行槽,二者绑定后对外给出"每任务线程配额"。

    线程安全:只在 JobManager(单线程 asyncio 事件循环)里读写,
    无需加锁;字段都是 int,赋值是原子的。
    """

    def __init__(self, total_threads: int | None = None,
                 max_parallel: int = 2):
        self.total_threads = int(total_threads or detect_total_cpus())
        self.max_parallel = max(1, int(max_parallel))
        logger.info(
            "CpuBudget 初始化: 总线程预算=%d, 最大并行=%d → 每任务配额=%d",
            self.total_threads, self.max_parallel, self.quota_for(),
        )

    def quota_for(self, running_now: int | None = None) -> int:
        """返回单个任务应得的线程配额。

        静态均分:budget // max_parallel,至少 1。
        (running_now 参数预留给将来的动态策略,当前忽略。)
        """
        return max(1, self.total_threads // self.max_parallel)

    def update(self, total_threads: int | None = None,
               max_parallel: int | None = None) -> None:
        """运行时调整预算 / 并行数。两者任一变化都会改变每任务配额。"""
        if total_threads is not None:
            self.total_threads = max(1, int(total_threads))
        if max_parallel is not None:
            self.max_parallel = max(1, int(max_parallel))
        logger.info(
            "CpuBudget 更新: 总线程预算=%d, 最大并行=%d → 每任务配额=%d",
            self.total_threads, self.max_parallel, self.quota_for(),
        )

    def describe(self) -> dict:
        """给前端用的状态快照 —— 界面就照这个显示,保证显示=实际。"""
        return {
            "total_threads": self.total_threads,
            "max_parallel": self.max_parallel,
            "threads_per_job": self.quota_for(),
        }
