"""TransDecoder CDS prediction runner for ONT transcriptome data.

Predicts coding regions (CDS) from transcript sequences using TransDecoder.
Supports two input modes:
  1. candidate_gtf + genome_fasta: extract transcript sequences via gffread
  2. transcript_fasta: use pre-existing transcript FASTA directly

Parameters:
  candidate_gtf:   str  - Candidate transcript GTF (from novel transcript step)
  genome_fasta:    str  - Reference genome FASTA (needed for gffread)
  transcript_fasta: str - Alternative: pre-extracted transcript FASTA
  min_orf_aa:      int  - Minimum ORF length in amino acids (default 50)
  single_best:     bool - Only retain single best ORF per transcript (default True)

Outputs (to output_dir/):
  transcripts.fa                    - Transcript sequences (from gffread)
  transcripts.fa.transdecoder.pep   - Predicted protein sequences
  transcripts.fa.transdecoder.cds   - Predicted CDS sequences
  transcripts.fa.transdecoder.gff3  - CDS annotations in GFF3
  transcripts.fa.transdecoder.bed   - CDS annotations in BED
  transdecoder_summary.json         - Prediction summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class TransdecoderRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        candidate_gtf = p.get("candidate_gtf", "")
        genome_fasta = p.get("genome_fasta", "")
        transcript_fasta = p.get("transcript_fasta", "")
        min_orf_aa = int(p.get("min_orf_aa", 50))
        single_best = bool(p.get("single_best", True))

        # Validate params: need either transcript_fasta or candidate_gtf+genome_fasta
        if transcript_fasta:
            if not Path(transcript_fasta).exists():
                raise FileNotFoundError(
                    f"转录本 FASTA 不存在: {transcript_fasta}")
            self.log(f"使用预提取的转录本 FASTA: {transcript_fasta}")
            use_gffread = False
        elif candidate_gtf and genome_fasta:
            if not Path(candidate_gtf).exists():
                raise FileNotFoundError(f"候选 GTF 不存在: {candidate_gtf}")
            if not Path(genome_fasta).exists():
                raise FileNotFoundError(f"参考基因组 FASTA 不存在: {genome_fasta}")
            use_gffread = True
        else:
            raise ValueError(
                "必须提供 transcript_fasta, 或同时提供 candidate_gtf + genome_fasta")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Step 1: Extract transcript sequences via gffread ----
        if use_gffread:
            self.update(pct=5, stage="gffread 提取",
                        detail="从 GTF 提取转录本序列",
                        indeterminate=True)
            trans_fa = out_dir / "transcripts.fa"
            self.run_command([
                "gffread",
                "-w", str(trans_fa),
                "-g", genome_fasta,
                candidate_gtf,
            ], indeterminate=True, heartbeat_stage="gffread 提取")

            if not trans_fa.exists() or trans_fa.stat().st_size == 0:
                raise RuntimeError("gffread 未提取到转录本序列")
            n_seqs = 0
            with open(trans_fa, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(">"):
                        n_seqs += 1
            self.log(f"  -> {trans_fa} ({n_seqs} 条转录本)")
        else:
            # Copy or symlink the provided FASTA
            trans_fa = out_dir / "transcripts.fa"
            src = Path(transcript_fasta)
            if src.resolve() != trans_fa.resolve():
                trans_fa.write_bytes(src.read_bytes())
            n_seqs = 0
            with open(trans_fa, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(">"):
                        n_seqs += 1
            self.log(f"使用提供的 FASTA: {trans_fa} ({n_seqs} 条转录本)")

        # ---- Step 2: TransDecoder.LongOrfs ----
        self.update(pct=25, stage="TransDecoder.LongOrfs",
                    detail=f"min_orf={min_orf_aa}aa",
                    indeterminate=True)

        self.run_command([
            "TransDecoder.LongOrfs",
            "-t", str(trans_fa),
            "-m", str(min_orf_aa),
        ], indeterminate=True, heartbeat_stage="TransDecoder.LongOrfs")

        # Check LongOrfs output
        longorfs_dir = trans_fa.parent / (trans_fa.name + ".transdecoder_dir")
        if not longorfs_dir.exists():
            # TransDecoder sometimes puts output next to the input
            longorfs_dir = trans_fa.parent / "transdecoder_dir"
        self.log(f"TransDecoder.LongOrfs 输出目录: {longorfs_dir}")

        # ---- Step 3: TransDecoder.Predict ----
        self.update(pct=55, stage="TransDecoder.Predict",
                    detail="预测 CDS", indeterminate=True)

        predict_cmd = [
            "TransDecoder.Predict",
            "-t", str(trans_fa),
        ]
        if single_best:
            predict_cmd.append("--single_best_only")

        self.run_command(predict_cmd, indeterminate=True,
                         heartbeat_stage="TransDecoder.Predict")

        # ---- Step 4: Count predicted ORFs ----
        self.update(pct=85, stage="收集结果",
                    detail="统计预测的 ORF", indeterminate=True)

        pep_file = trans_fa.parent / (trans_fa.name + ".transdecoder.pep")
        cds_file = trans_fa.parent / (trans_fa.name + ".transdecoder.cds")
        gff3_file = trans_fa.parent / (trans_fa.name + ".transdecoder.gff3")
        bed_file = trans_fa.parent / (trans_fa.name + ".transdecoder.bed")

        n_orfs = 0
        if pep_file.exists() and pep_file.stat().st_size > 0:
            with open(pep_file, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(">"):
                        n_orfs += 1
            self.log(f"  -> {pep_file} ({n_orfs} 条预测的蛋白质序列)")
        else:
            self.log("!! TransDecoder.Predict 未生成 .pep 文件")

        n_cds = 0
        if cds_file.exists() and cds_file.stat().st_size > 0:
            with open(cds_file, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(">"):
                        n_cds += 1
            self.log(f"  -> {cds_file} ({n_cds} 条 CDS 序列)")

        # ---- Summary ----
        summary = {
            "candidate_gtf": candidate_gtf if use_gffread else "",
            "genome_fasta": genome_fasta if use_gffread else "",
            "transcript_fasta": str(trans_fa),
            "min_orf_aa": min_orf_aa,
            "single_best_only": single_best,
            "n_transcripts_input": n_seqs,
            "n_predicted_orfs": n_orfs,
            "n_predicted_cds": n_cds,
            "output_files": {
                "pep": str(pep_file) if pep_file.exists() else "",
                "cds": str(cds_file) if cds_file.exists() else "",
                "gff3": str(gff3_file) if gff3_file.exists() else "",
                "bed": str(bed_file) if bed_file.exists() else "",
            },
        }
        (out_dir / "transdecoder_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== TransDecoder 完成: {n_orfs} 个 ORF 预测 "
                 f"({n_seqs} 条转录本) → {out_dir} ===")


if __name__ == "__main__":
    TransdecoderRunner.main()
