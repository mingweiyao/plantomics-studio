"""Salmon quantification runner for Tail Iso-seq.

Quantifies transcript expression from long-read data using Salmon.

Parameters:
  transcripts_fa: str    - Transcript sequences FASTA
  reads_files: [str]     - Input read files (FASTQ)
  lib_type: str          - Library type (default: "A")
  sample_name: str       - Sample name
  index_dir: str         - Pre-built Salmon index (optional)
  threads: int           - CPU threads (default: 8)

Outputs (to output_dir/<sample_name>/):
  salmon_index/            - Salmon index
  salmon_quant/quant.sf    - Quantification results
  salmon_summary.json      - Summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class SalmonQuantRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        transcripts_fa = p.get("transcripts_fa", "")
        reads_files = p.get("reads_files", [])
        lib_type = p.get("lib_type", "A")
        sample_name = p.get("sample_name", "sample")
        index_dir = p.get("index_dir", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not transcripts_fa or not Path(transcripts_fa).exists():
            raise FileNotFoundError(f"转录本 FASTA 不存在: {transcripts_fa}")
        if not reads_files:
            raise ValueError("reads_files 列表为空")

        out_dir = Path(self.output_dir()) / sample_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build or use existing index
        idx_dir = out_dir / "salmon_index"
        if index_dir and Path(index_dir).exists():
            idx_dir = Path(index_dir)
        elif idx_dir.exists() and list(idx_dir.iterdir()):
            pass
        else:
            self.update(pct=5, stage="Salmon 索引", indeterminate=True)
            idx_dir.mkdir(parents=True, exist_ok=True)
            self.run_command([
                "salmon", "index", "-t", str(transcripts_fa),
                "-i", str(idx_dir), "-p", str(threads), "-k", "31",
            ], indeterminate=True, heartbeat_stage="Salmon index")

        # Quantify
        self.update(pct=40, stage="Salmon 定量", indeterminate=True)
        quant_dir = out_dir / "salmon_quant"
        quant_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "salmon", "quant",
            "-i", str(idx_dir),
            "-l", lib_type,
            "-r", reads_files[0],
            "-o", str(quant_dir),
            "-p", str(threads),
            "--validateMappings",
        ]
        self.run_command(cmd, indeterminate=True,
                         heartbeat_stage=f"Salmon quant")

        # Parse results
        quant_sf = quant_dir / "quant.sf"
        n_transcripts = 0
        expressed_gt_1 = 0
        if quant_sf.exists():
            with open(quant_sf, encoding="utf-8") as fh:
                fh.readline()  # skip header
                for line in fh:
                    cols = line.strip().split("\t")
                    if len(cols) >= 4:
                        n_transcripts += 1
                        if float(cols[3]) >= 1.0:
                            expressed_gt_1 += 1

        summary = {
            "sample_name": sample_name,
            "n_transcripts_quantified": n_transcripts,
            "n_expressed_tpm_gt_1": expressed_gt_1,
        }
        (out_dir / "salmon_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Salmon 定量完成: {n_transcripts} 转录本, "
                 f"{expressed_gt_1} 表达(TPM>1) → {quant_dir} ===")


if __name__ == "__main__":
    SalmonQuantRunner.main()
