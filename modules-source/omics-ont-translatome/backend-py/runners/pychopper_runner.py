"""Pychopper full-length read identification and trimming runner.

Identifies and trims full-length cDNA reads from ONT transcriptome data
using Pychopper (cdna_classifier.py).

Parameters:
  fastq:          str - Input FASTQ file path
  output_prefix:  str - Prefix for output files
  min_length:     int - Minimum read length (default 50)
  max_length:     int - Maximum read length (default 0 = no limit)
  q:              int - Minimum average quality (default 7)
  primer_scheme:  str - Primer scheme: auto (default) / pcs110 / pcs109 / pcs111
  threads:        int - CPU threads (default 8)

Outputs (to output_dir/):
  <output_prefix>.full_length.fastq.gz  - Full-length reads
  <output_prefix>.rejected.fastq.gz     - Rejected reads
  <output_prefix>_report.pdf            - Visual summary report
  <output_prefix>_report.tsv            - Tabular summary
  pychopper_summary.json                - Pipeline summary
"""
import json
import shutil
from pathlib import Path

from runners.base import BaseRunner


class PychopperRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq = p.get("fastq", "")
        output_prefix = p.get("output_prefix", "pychopper")
        min_length = int(p.get("min_length", 50))
        max_length = int(p.get("max_length", 0))
        q = int(p.get("q", 7))
        primer_scheme = p.get("primer_scheme", "auto")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq or not Path(fastq).exists():
            raise FileNotFoundError(f"输入 FASTQ 不存在: {fastq}")

        # Tool precheck
        if not shutil.which("cdna_classifier.py"):
            raise FileNotFoundError(
                "找不到 cdna_classifier.py (Pychopper)。请确保模块 conda 环境"
                "中已安装 pychopper。")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        full_len_fq = out_dir / f"{output_prefix}.full_length.fastq.gz"
        rejected_fq = out_dir / f"{output_prefix}.rejected.fastq.gz"
        report_pdf = out_dir / f"{output_prefix}_report.pdf"
        report_tsv = out_dir / f"{output_prefix}_report.tsv"

        # ---- Step 1: cdna_classifier.py ----
        self.update(pct=5, stage="Pychopper 分类", indeterminate=True)

        # cdna_classifier.py -r report.pdf -S report.tsv -t threads
        #   -w full_len.fq -u rejected.fq input.fq
        cmd = [
            "cdna_classifier.py",
            "-r", str(report_pdf),
            "-S", str(report_tsv),
            "-t", str(threads),
            "-w", str(full_len_fq),
            "-u", str(rejected_fq),
        ]
        if min_length > 0:
            cmd.extend(["-m", str(min_length)])
        if max_length > 0:
            cmd.extend(["-M", str(max_length)])
        if q > 0:
            cmd.extend(["-q", str(q)])
        if primer_scheme and primer_scheme != "auto":
            cmd.extend(["-s", primer_scheme])

        cmd.append(fastq)

        self.run_command(
            cmd,
            indeterminate=True,
            heartbeat_stage="cdna_classifier.py",
        )

        # ---- Count results ----
        n_full = 0
        if full_len_fq.exists():
            try:
                import gzip
                with gzip.open(full_len_fq, "rt", errors="ignore") as fh:
                    for line in fh:
                        if line.startswith("@"):
                            n_full += 1
            except Exception:
                pass

        n_rejected = 0
        if rejected_fq.exists():
            try:
                import gzip
                with gzip.open(rejected_fq, "rt", errors="ignore") as fh:
                    for line in fh:
                        if line.startswith("@"):
                            n_rejected += 1
            except Exception:
                pass

        # ---- Parse TSV report if present ----
        stats = {}
        if report_tsv.exists():
            try:
                lines = report_tsv.read_text(
                    encoding="utf-8", errors="ignore").splitlines()
                for line in lines:
                    if "\t" in line:
                        k, v = line.split("\t", 1)
                        stats[k.strip()] = v.strip()
            except Exception:
                pass

        summary = {
            "input_fastq": fastq,
            "full_length_fastq": str(full_len_fq) if full_len_fq.exists() else "",
            "rejected_fastq": str(rejected_fq) if rejected_fq.exists() else "",
            "report_pdf": str(report_pdf) if report_pdf.exists() else "",
            "report_tsv": str(report_tsv) if report_tsv.exists() else "",
            "n_full_length_reads": n_full,
            "n_rejected_reads": n_rejected,
            "stats": stats,
            "primer_scheme": primer_scheme,
        }
        (out_dir / "pychopper_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Pychopper 完成: {n_full} 条全长 reads, "
                 f"{n_rejected} 条被丢弃 → {out_dir} ===")


if __name__ == "__main__":
    PychopperRunner.main()
