"""Salmon quantification runner for DRS transcripts.

Quantifies transcript expression from ONT DRS reads using Salmon
in quasi-mapping mode.

Parameters:
  fastq_files: [str]      - Input read files (FASTQ)
  transcriptome_fasta: str - Transcript sequences FASTA
  lib_type: str           - Library type (default: "A" for auto-detect)
  sample_name: str        - Sample name
  threads: int            - CPU threads (default 8)

Outputs (to output_dir/<sample>/):
  salmon_index/             - Salmon index
  salmon_quant/             - Quantification results
    quant.sf                - Transcript-level quantification
    quant.genes.sf          - Gene-level quantification
  salmon_summary.json       - Summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class SalmonQuantRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files", [])
        transcriptome_fasta = p.get("transcriptome_fasta", "")
        lib_type = p.get("lib_type", "A")
        sample_name = p.get("sample_name", "sample")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq_files:
            raise ValueError("fastq_files 列表为空")
        if not transcriptome_fasta or not Path(transcriptome_fasta).exists():
            raise FileNotFoundError(
                f"转录本 FASTA 不存在: {transcriptome_fasta}")
        for rf in fastq_files:
            if not Path(rf).exists():
                raise FileNotFoundError(f"输入文件不存在: {rf}")

        out_dir = Path(self.output_dir()) / sample_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build Salmon index
        idx_dir = out_dir / "salmon_index"
        if idx_dir.exists() and list(idx_dir.iterdir()):
            self.log(f"使用已有索引: {idx_dir}")
        else:
            self.update(pct=5, stage="Salmon 索引", indeterminate=True)
            idx_dir.mkdir(parents=True, exist_ok=True)
            self.run_command([
                "salmon", "index",
                "-t", str(transcriptome_fasta),
                "-i", str(idx_dir),
                "-p", str(threads),
                "-k", "31",
            ], indeterminate=True, heartbeat_stage="Salmon index")

        # Quantification
        self.update(pct=40, stage="Salmon 定量", indeterminate=True)
        quant_dir = out_dir / "salmon_quant"
        quant_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "salmon", "quant",
            "-i", str(idx_dir),
            "-l", lib_type,
            "-r", fastq_files[0],  # Single-end reads
            "-o", str(quant_dir),
            "-p", str(threads),
            "--validateMappings",
        ]
        self.run_command(cmd, indeterminate=True,
                         heartbeat_stage=f"Salmon quant {sample_name}")

        # Parse results
        self.update(pct=80, stage="解析定量结果")
        quant_sf = quant_dir / "quant.sf"
        n_transcripts = 0
        total_tpm = 0.0
        expressed_gt_1 = 0

        if quant_sf.exists():
            with open(quant_sf, encoding="utf-8") as fh:
                fh.readline()  # skip header
                for line in fh:
                    cols = line.strip().split("\t")
                    if len(cols) >= 4:
                        n_transcripts += 1
                        tpm = float(cols[3])
                        total_tpm += tpm
                        if tpm >= 1.0:
                            expressed_gt_1 += 1

        quant_genes = quant_dir / "quant.genes.sf"
        n_genes = 0
        if quant_genes.exists():
            n_genes = sum(1 for _ in open(quant_genes)) - 1

        summary = {
            "sample_name": sample_name,
            "n_transcripts_quantified": n_transcripts,
            "n_genes_quantified": max(0, n_genes),
            "n_expressed_tpm_gt_1": expressed_gt_1,
            "total_tpm": round(total_tpm, 2),
            "lib_type": lib_type,
            "quant_dir": str(quant_dir),
        }
        (out_dir / "salmon_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Salmon 定量完成: {n_transcripts} 个转录本, "
                 f"表达(TPM>1): {expressed_gt_1} 个 → {quant_dir} ===")


if __name__ == "__main__":
    SalmonQuantRunner.main()
