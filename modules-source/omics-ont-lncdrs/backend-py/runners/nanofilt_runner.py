"""NanoFilt QC runner for ONT Direct RNA Sequencing data.

Filters ONT reads using NanoFilt (q>=10, len>=50), then generates quality
statistics with NanoStat.

Parameters:
  fastq_files: [str]   - Input FASTQ file paths
  sample_names: [str]  - Optional sample names (default from filenames)
  min_qual: int        - Minimum average quality score (default 10)
  min_len: int         - Minimum read length (default 50)
  max_len: int         - Maximum read length (default 0 = no limit)
  headcrop: int        - Trim N bases from read start (default 0)
  tailcrop: int        - Trim N bases from read end (default 0)
  threads: int         - Parallel processing threads (default 4)

Outputs (per sample to output_dir/<sample>/):
  <sample>.fastq.gz        - Filtered reads
  <sample>_nanostat.txt    - NanoStat summary
  nanofilt_summary.json    - Per-sample filtering summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class NanofiltRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files", [])
        sample_names = p.get("sample_names", [])
        min_qual = int(p.get("min_qual", 10))
        min_len = int(p.get("min_len", 50))
        max_len = int(p.get("max_len", 0))
        headcrop = int(p.get("headcrop", 0))
        tailcrop = int(p.get("tailcrop", 0))
        threads = self.effective_threads(int(p.get("threads", 4)))

        if not fastq_files:
            raise ValueError("fastq_files 列表为空")

        if not sample_names or len(sample_names) != len(fastq_files):
            sample_names = [Path(f).stem.split(".")[0] for f in fastq_files]

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        sample_results = []
        n = len(fastq_files)

        for i, (fq, name) in enumerate(zip(fastq_files, sample_names)):
            if not Path(fq).exists():
                self.log(f"!! 跳过 {name}: {fq} 不存在")
                continue

            self.update(pct=int(80 * i / n), stage=f"NanoFilt 过滤 ({i + 1}/{n})",
                        detail=name)

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            filtered_fq = sample_dir / f"{name}.fastq.gz"

            # Build NanoFilt command
            cmd = ["NanoFilt"]
            if min_qual > 0:
                cmd.extend(["-q", str(min_qual)])
            if min_len > 0:
                cmd.extend(["-l", str(min_len)])
            if max_len > 0:
                cmd.extend(["--maxlength", str(max_len)])
            if headcrop > 0:
                cmd.extend(["--headcrop", str(headcrop)])
            if tailcrop > 0:
                cmd.extend(["--tailcrop", str(tailcrop)])

            # Pipe: cat fq | NanoFilt | gzip > output
            self.run_command(
                ["bash", "-c",
                 f"cat '{fq}' | {' '.join(cmd)} | gzip > '{filtered_fq}'"],
                indeterminate=True,
                heartbeat_stage=f"NanoFilt {name}",
            )

            if not filtered_fq.exists() or filtered_fq.stat().st_size == 0:
                self.log(f"!! {name}: 过滤后为空,跳过统计")
                continue

            # NanoStat summary
            self.update(pct=int(80 * i / n + 10),
                        stage=f"NanoStat 统计 ({i + 1}/{n})", detail=name)
            nanostat_out = sample_dir / f"{name}_nanostat.txt"
            self.run_command([
                "NanoStat", "--fastq", str(filtered_fq),
                "--outdir", str(sample_dir),
                "--name", f"{name}_nanostat.txt",
                "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage=f"NanoStat {name}")

            # Count reads
            read_count = 0
            try:
                import gzip
                with gzip.open(filtered_fq, "rt", errors="ignore") as fh:
                    for line in fh:
                        if line.startswith("@"):
                            read_count += 1
            except Exception:
                pass

            sample_results.append({
                "sample": name,
                "input_fastq": fq,
                "filtered_fastq": str(filtered_fq),
                "read_count": read_count,
                "nanostat": str(nanostat_out) if nanostat_out.exists() else "",
            })

        summary = {
            "n_samples": len(sample_results),
            "filter_params": {
                "min_qual": min_qual,
                "min_len": min_len,
                "max_len": max_len,
                "headcrop": headcrop,
                "tailcrop": tailcrop,
            },
            "samples": sample_results,
        }
        (out_dir / "nanofilt_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        total_reads = sum(s.get("read_count", 0) for s in sample_results)
        self.update(pct=100, stage="完成")
        self.log(f"=== NanoFilt 完成: {len(sample_results)} 个样本, "
                 f"总计 {total_reads} 条 reads → {out_dir} ===")


if __name__ == "__main__":
    NanofiltRunner.main()
