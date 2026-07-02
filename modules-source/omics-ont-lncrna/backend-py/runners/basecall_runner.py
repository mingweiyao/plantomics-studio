"""Guppy basecalling runner for ONT raw data.

Reads ONT raw signal (fast5) files and converts to basecalled FASTQ.

Parameters:
  fast5_dir: str   - Directory containing fast5 files (required)
  config:    str   - Guppy config file, default "dna_r9.4.1_450bps_hac.cfg"
  flowcell:  str or None - Flowcell type (optional)
  kit:       str or None - Library kit (optional)
  threads:   int   - Number of callers/runners, default 4

Outputs (to output_subdir):
  guppy_out/          - Guppy output directory
  all_passed.fastq.gz - Concatenated passed reads
  basecall_summary.json
"""
import gzip
import json
import shutil
from pathlib import Path

from runners.base import BaseRunner


class BasecallRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fast5_dir = p.get("fast5_dir")
        config = p.get("config", "dna_r9.4.1_450bps_hac.cfg")
        flowcell = p.get("flowcell")
        kit = p.get("kit")
        threads = self.effective_threads(int(p.get("threads", 4)))

        if not fast5_dir or not Path(fast5_dir).is_dir():
            raise FileNotFoundError(
                f"fast5 目录不存在: {fast5_dir}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        guppy_out = out_dir / "guppy_out"

        # Build guppy_basecaller command
        cmd = [
            "guppy_basecaller",
            "-i", str(fast5_dir),
            "-s", str(guppy_out),
            "--config", config,
            "-r",
            "--num_callers", str(threads),
            "--gpu_runners_per_device", str(threads),
        ]
        if flowcell:
            cmd += ["--flowcell", flowcell]
        if kit:
            cmd += ["--kit", kit]

        self.update(pct=10, stage="Guppy basecalling", indeterminate=True)
        self.run_command(cmd, indeterminate=True, heartbeat_stage="Guppy basecalling")

        # Concatenate passed reads using Python
        self.update(pct=80, stage="合并 passed FASTQ")
        pass_dir = guppy_out / "pass"
        all_passed = out_dir / "all_passed.fastq.gz"

        n_passed_files = 0
        if pass_dir.is_dir():
            fastq_files = sorted(pass_dir.glob("*.fastq"))
            if fastq_files:
                self.log(f"  合并 {len(fastq_files)} 个 fastq 文件到 {all_passed}")
                with gzip.open(all_passed, "wt", encoding="utf-8", errors="ignore") as gzout:
                    for f in fastq_files:
                        shutil.copyfileobj(open(f, "r", encoding="utf-8", errors="ignore"), gzout)
                n_passed_files = len(fastq_files)
            else:
                gz_files = sorted(pass_dir.glob("*.fastq.gz"))
                if gz_files:
                    self.log(f"  合并 {len(gz_files)} 个 gz 文件到 {all_passed}")
                    with gzip.open(all_passed, "wb") as gzout:
                        for f in gz_files:
                            with gzip.open(f, "rb") as gzin:
                                shutil.copyfileobj(gzin, gzout)
                    n_passed_files = len(gz_files)
                else:
                    self.log("  pass 目录下没有找到 fastq 文件")

        # Collect stats
        n_failed_files = 0
        fail_dir = guppy_out / "fail"
        if fail_dir.is_dir():
            n_failed_files = len(list(fail_dir.glob("*.fastq*")))

        summary = {
            "config": config,
            "flowcell": flowcell,
            "kit": kit,
            "threads": threads,
            "n_passed_fastq": n_passed_files,
            "n_failed_fastq": n_failed_files,
            "guppy_output": str(guppy_out),
            "all_passed_fastq": str(all_passed) if all_passed.exists() else None,
        }
        (out_dir / "basecall_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== Guppy basecalling 完成, passed: {n_passed_files}, failed: {n_failed_files} ===")


if __name__ == "__main__":
    BasecallRunner.main()
