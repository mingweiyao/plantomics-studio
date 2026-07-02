"""Pychopper full-length transcript identification runner.

Identifies full-length Nanopore cDNA reads using Pychopper:
  1. pychopper - classifies reads as full-length, chimeric, or primer-only
  2. Extracts full-length reads with correct strand orientation

Parameters:
  fastq_files: [str]   - Input FASTQ files (NanoFilt filtered)
  sample_name: str     - Sample name (default: auto from first file)
  strategy: str        - Pychopper strategy: 'pychopper' | 'pychopper_v2' (default: 'pychopper_v2')
  min_length: int      - Minimum full-length read length (default: 50)
  max_length: int      - Maximum full-length read length (default: 5000)
  threads: int         - CPU threads (default: 8)

Outputs (to output_dir/<sample_name>/):
  full_length.fq.gz        - Full-length classified reads
  rescued.fq.gz            - Rescued reads (with orientation correction)
  non_full_length.fq.gz    - Non-full-length reads
  stats.txt                - Classification statistics
  pychopper_summary.json   - Machine-readable summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class PychopperRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files", [])
        sample_name = p.get("sample_name", "")
        strategy = p.get("strategy", "pychopper_v2")
        min_length = int(p.get("min_length", 50))
        max_length = int(p.get("max_length", 5000))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq_files:
            raise ValueError("fastq_files 列表为空")

        if not sample_name:
            sample_name = Path(fastq_files[0]).stem.split(".")[0]

        out_dir = Path(self.output_dir()) / sample_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Merge input FASTQs if multiple
        merged_fq = out_dir / "merged_input.fq.gz"
        if len(fastq_files) == 1:
            merged_fq = Path(fastq_files[0])
            self.log(f"使用单文件输入: {merged_fq}")
        else:
            self.update(pct=5, stage="合并输入文件")
            self.run_command([
                "bash", "-c",
                f"cat {' '.join(fastq_files)} > '{merged_fq}'",
            ])

        # ---- Pychopper ----
        self.update(pct=15, stage="Pychopper 全长分类", indeterminate=True)

        fl_fq = out_dir / "full_length.fq.gz"
        resc_fq = out_dir / "rescued.fq.gz"
        non_fl_fq = out_dir / "non_full_length.fq.gz"
        stats_file = out_dir / "stats.txt"

        cmd = [
            "cdna_classifier.py" if strategy == "pychopper_v2" else "pychopper",
            "-r", str(out_dir),
            "-S", str(stats_file),
            "-m", str(min_length),
            "-M", str(max_length),
            "-t", str(threads),
            "-w", str(fl_fq),
            "-u", str(non_fl_fq),
            "-q", str(resc_fq),
            str(merged_fq),
        ]

        # pychopper v2: cdna_classifier.py
        # pychopper original: pychopper
        self.run_command(cmd, indeterminate=True,
                         heartbeat_stage="Pychopper")

        # Count full-length reads
        fl_reads = 0
        if fl_fq.exists():
            try:
                import gzip
                with gzip.open(fl_fq, "rt", errors="ignore") as fh:
                    fl_reads = sum(1 for ln in fh if ln.startswith("@"))
            except Exception:
                pass

        # Rescue reads if any
        rescued_reads = 0
        if resc_fq.exists():
            try:
                import gzip
                with gzip.open(resc_fq, "rt", errors="ignore") as fh:
                    rescued_reads = sum(1 for ln in fh if ln.startswith("@"))
            except Exception:
                pass

        # Parse stats
        stats_text = ""
        if stats_file.exists():
            stats_text = stats_file.read_text(encoding="utf-8", errors="ignore")

        summary = {
            "sample_name": sample_name,
            "total_full_length": fl_reads,
            "rescued": rescued_reads,
            "strategy": strategy,
            "min_length": min_length,
            "max_length": max_length,
            "outputs": {
                "full_length": str(fl_fq) if fl_fq.exists() else "",
                "rescued": str(resc_fq) if resc_fq.exists() else "",
                "non_full_length": str(non_fl_fq) if non_fl_fq.exists() else "",
                "stats": str(stats_file) if stats_file.exists() else "",
            },
        }
        (out_dir / "pychopper_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Pychopper 完成: {fl_reads} 条全长, "
                 f"{rescued_reads} 条矫正 → {out_dir} ===")


if __name__ == "__main__":
    PychopperRunner.main()
