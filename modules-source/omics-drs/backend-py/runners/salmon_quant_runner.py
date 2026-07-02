"""Salmon quantification runner for DRS transcripts.

Quantifies transcript expression from ONT DRS reads using Salmon
in alignment-based or quasi-mapping mode.

Parameters:
  transcripts_fa: str    - Transcript sequences FASTA (for index building)
  reads_files: [str]     - Input read files (FASTQ/FASTA)
  lib_type: str          - Library type (default: "A" for auto-detect)
  sample_name: str       - Sample name for output labelling
  index_dir: str         - Pre-built Salmon index directory (optional)
  extra_salmon: str      - Extra Salmon flags
  threads: int           - CPU threads (default 8)
  use_alignments: bool   - Use alignment-based quantification (default False)

Outputs (to output_dir/<sample_name>/):
  salmon_index/             - Salmon index (built or reused)
  quant.sf                  - Quantification file (TPM and counts)
  quant.genes.sf            - Gene-level quantification
  aux_info/                 - Auxiliary information
  salmon_summary.json       - Quantification summary
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
        extra_salmon = p.get("extra_salmon", "")
        use_alignments = bool(p.get("use_alignments", False))

        if not transcripts_fa or not Path(transcripts_fa).exists():
            raise FileNotFoundError(f"转录本 FASTA 不存在: {transcripts_fa}")
        if not reads_files:
            raise ValueError("reads_files 列表为空")
        for rf in reads_files:
            if not Path(rf).exists():
                raise FileNotFoundError(f"输入 reads 文件不存在: {rf}")

        out_dir = Path(self.output_dir()) / sample_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Step 1: Build or use existing Salmon index ----
        idx_dir = out_dir / "salmon_index"
        if index_dir and Path(index_dir).exists():
            idx_dir = Path(index_dir)
            self.log(f"使用已有索引: {idx_dir}")
        elif idx_dir.exists() and list(idx_dir.iterdir()):
            self.log(f"使用已有索引: {idx_dir}")
        else:
            self.update(pct=5, stage="Salmon 索引", indeterminate=True)
            idx_dir.mkdir(parents=True, exist_ok=True)
            self.run_command([
                "salmon", "index",
                "-t", str(transcripts_fa),
                "-i", str(idx_dir),
                "-p", str(threads),
                "-k", "31",
            ], indeterminate=True, heartbeat_stage="Salmon index")

        # ---- Step 2: Quantification ----
        self.update(pct=40, stage=f"Salmon 定量", indeterminate=True)

        # For DRS reads: typically single-end, but treat as SE
        quant_dir = out_dir / "salmon_quant"
        quant_dir.mkdir(parents=True, exist_ok=True)

        if use_alignments:
            # Alignment-based quantification (needs BAM)
            cmd = [
                "salmon", "quant",
                "-t", str(transcripts_fa),
                "-l", lib_type,
                "-a", reads_files[0],  # single BAM file
                "-o", str(quant_dir),
                "-p", str(threads),
            ]
        else:
            # Quasi-mapping based
            cmd = [
                "salmon", "quant",
                "-i", str(idx_dir),
                "-l", lib_type,
                "-r", reads_files[0],
                "-o", str(quant_dir),
                "-p", str(threads),
                "--validateMappings",
            ]
            # If multiple read files, supply them as a list
            if len(reads_files) > 1:
                # Single-end: multiple files for different samples isn't supported
                # Could use --mates1/--mates2 for paired-end
                pass

        if extra_salmon:
            cmd.extend(extra_salmon.split())

        self.run_command(cmd, indeterminate=True,
                         heartbeat_stage=f"Salmon quant {sample_name}")

        # ---- Parse Results ----
        self.update(pct=80, stage="解析定量结果")
        quant_sf = quant_dir / "quant.sf"

        n_transcripts = 0
        total_tpm = 0.0
        expressed_gt_1 = 0

        if quant_sf.exists():
            with open(quant_sf, encoding="utf-8") as fh:
                header = fh.readline()  # skip header
                for line in fh:
                    cols = line.strip().split("\t")
                    if len(cols) >= 4:
                        n_transcripts += 1
                        tpm = float(cols[3])
                        total_tpm += tpm
                        if tpm >= 1.0:
                            expressed_gt_1 += 1

        # Check for gene-level quantification
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
            "index": str(idx_dir),
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
