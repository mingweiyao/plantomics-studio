"""fastp runner - 多样本并行质量过滤。

参数:
  samples: list[dict]
  qualified_quality_phred: 15  (-q)
  unqualified_percent_limit: 40 (-u)
  length_required: 30  (-l)
  adapter_sequence_r1: ""
  adapter_sequence_r2: ""
  threads_per_sample: int  - 每个 fastp 子进程的线程数,默认 4
  parallel: int            - 同时跑几个样本,默认 2
  threads:                 - 兼容老参数(单个值),会被解释为 threads_per_sample

产出: <output_subdir>/<name>/<name>.clean_*.fq.gz + json/html 报告
"""
from pathlib import Path
from runners.base import BaseRunner


class FastpRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        samples = params.get("samples", [])
        if not samples:
            raise ValueError("未提供 samples")

        q = int(params.get("qualified_quality_phred", 15))
        u = int(params.get("unqualified_percent_limit", 40))
        L = int(params.get("length_required", 30))
        adapter_r1 = params.get("adapter_sequence_r1", "")
        adapter_r2 = params.get("adapter_sequence_r2", "")
        threads_per = int(params.get(
            "threads_per_sample",
            params.get("threads", 4)
        ))
        parallel = int(params.get("parallel", 2))
        parallel, threads_per = self.effective_parallel_alloc(parallel, threads_per)

        out_dir = self.output_dir()

        def process_one(s):
            name = s.get("name") or "sample"
            r1 = s.get("r1")
            r2 = s.get("r2")

            if not r1 or not Path(r1).exists():
                raise FileNotFoundError(f"r1 不存在: {r1}")

            sample_dir = out_dir / name
            sample_dir.mkdir(parents=True, exist_ok=True)

            o1 = sample_dir / f"{name}.clean_1.fq.gz"
            cmd = [
                "fastp",
                "-i", str(r1),
                "-o", str(o1),
                "-q", str(q),
                "-u", str(u),
                "-l", str(L),
                "-w", str(threads_per),
                "--html", str(sample_dir / f"{name}.fastp.html"),
                "--json", str(sample_dir / f"{name}.fastp.json"),
            ]
            if r2 and Path(r2).exists():
                o2 = sample_dir / f"{name}.clean_2.fq.gz"
                cmd += ["-I", str(r2), "-O", str(o2)]
            if adapter_r1:
                cmd += ["--adapter_sequence", adapter_r1]
            if r2 and adapter_r2:
                cmd += ["--adapter_sequence_r2", adapter_r2]

            self.run_command(cmd, timeout=3600)

        self.run_in_parallel(
            func=process_one,
            items=samples,
            workers=parallel,
            desc=f"fastp 过滤(每样本 {threads_per} 线程,并行 {parallel} 样本)",
        )


if __name__ == "__main__":
    FastpRunner.main()
