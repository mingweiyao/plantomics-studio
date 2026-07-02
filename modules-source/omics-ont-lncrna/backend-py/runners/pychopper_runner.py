"""Pychopper runner for ONT full-length cDNA selection.

Identifies full-length reads from ONT cDNA sequencing data.

Parameters:
  fastq_files: list[str] - Input FASTQ files (required)
  Q:           int - Quality score threshold, default 7
  z:           int - Minimum length, default 50
  threads:     int - Number of threads, default 8

Outputs (to output_subdir):
  full_length.fastq  - Full-length reads
  report/            - Pychopper report
  stats.tsv          - Statistics
  pychopper_summary.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class PychopperRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files") or []
        Q = int(p.get("Q", 7))
        z = int(p.get("z", 50))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq_files:
            raise ValueError("未提供 FASTQ 文件列表")
        for f in fastq_files:
            if not Path(f).exists():
                raise FileNotFoundError(f"FASTQ 不存在: {f}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        self.update(pct=10, stage="Pychopper 全长筛选", indeterminate=True)
        cmd = [
            "pychopper",
            "-Q", str(Q),
            "-z", str(z),
            "-t", str(threads),
            "-m", "edlib",
            "-r", str(out_dir / "report"),
            "-S", str(out_dir / "stats.tsv"),
            "-o", str(out_dir / "full_length.fastq"),
        ] + [str(f) for f in fastq_files]
        self.run_command(cmd, indeterminate=True, heartbeat_stage="Pychopper")

        # Gather results
        full_len = out_dir / "full_length.fastq"
        n_full = 0
        if full_len.exists():
            n_full = sum(1 for _ in open(full_len, "r") if _.startswith("@"))

        stats = {}
        stats_file = out_dir / "stats.tsv"
        if stats_file.exists():
            for line in stats_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if "\t" in line:
                    k, v = line.split("\t", 1)
                    stats[k.strip()] = v.strip()

        summary = {
            "input_files": fastq_files,
            "Q": Q,
            "z": z,
            "full_length_reads": n_full,
            "stats": stats,
        }
        (out_dir / "pychopper_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== Pychopper 完成, 全长 reads: {n_full} ===")


if __name__ == "__main__":
    PychopperRunner.main()
