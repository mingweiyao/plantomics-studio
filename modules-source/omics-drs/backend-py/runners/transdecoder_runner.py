"""TransDecoder CDS prediction runner for DRS transcripts.

Predicts coding regions from transcript sequences (e.g., Flair output):
  1. gffread -w transcripts.fa -g <genome> <input_gtf>
  2. TransDecoder.LongOrfs -t transcripts.fa -m <min_orf_aa>
  3. TransDecoder.Predict -t transcripts.fa [--single_best_only]

Parameters:
  candidate_gtf: str   - Input transcript GTF (from Flair collapse or gffcompare)
  genome_fasta: str    - Reference genome FASTA
  min_orf_aa: int     - Minimum ORF length in amino acids (default 100)
  single_best: bool   - Keep only single best ORF per transcript (default True)

Outputs (to output_dir/):
  transcripts.fa                         - Extracted transcript sequences
  transcripts.fa.transdecoder.pep        - Predicted protein sequences
  transcripts.fa.transdecoder.cds        - Predicted CDS sequences
  transcripts.fa.transdecoder.gff3       - CDS annotation in GFF3
  transcripts.fa.transdecoder.bed        - CDS annotation in BED
  transdecoder_summary.json              - Summary statistics
"""
import json
from pathlib import Path
import shutil

from runners.base import BaseRunner


class TransdecoderRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        cand = p.get("candidate_gtf")
        genome = p.get("genome_fasta")
        min_orf = int(p.get("min_orf_aa", 100))
        single_best = bool(p.get("single_best", True))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(
                f"候选转录本 GTF 不存在: {cand}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Tool pre-check
        missing = [t for t in ("TransDecoder.LongOrfs", "TransDecoder.Predict")
                   if not shutil.which(t)]
        if missing:
            raise FileNotFoundError(
                f"找不到 {', '.join(missing)}。请重建 conda 环境\n"
                "bash scripts/build-deb.sh (不要 --skip-env,不要复用旧 env 包)")

        # 1) Extract transcript sequences
        self.update(pct=10, stage="gffread 抽转录本序列")
        fa = out_dir / "transcripts.fa"
        self.run_command(["gffread", "-w", str(fa), "-g", genome, str(cand)])
        if not fa.exists() or fa.stat().st_size == 0:
            raise RuntimeError("gffread 没抽出转录本序列")

        # 2) TransDecoder.LongOrfs
        self.update(pct=35, stage="TransDecoder.LongOrfs", indeterminate=True)
        self.run_command(
            ["TransDecoder.LongOrfs", "-t", str(fa), "-m", str(min_orf)],
            cwd=str(out_dir), indeterminate=True,
            heartbeat_stage="TransDecoder.LongOrfs",
        )

        # 3) TransDecoder.Predict
        self.update(pct=65, stage="TransDecoder.Predict", indeterminate=True)
        cmd = ["TransDecoder.Predict", "-t", str(fa)]
        if single_best:
            cmd.append("--single_best_only")
        self.run_command(cmd, cwd=str(out_dir), indeterminate=True,
                         heartbeat_stage="TransDecoder.Predict")

        # Gather results
        pep = out_dir / "transcripts.fa.transdecoder.pep"
        cds = out_dir / "transcripts.fa.transdecoder.cds"
        gff3 = out_dir / "transcripts.fa.transdecoder.gff3"

        n_orf = 0
        if pep.exists():
            n_orf = sum(1 for ln in pep.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if ln.startswith(">"))

        summary = {
            "n_transcripts": sum(1 for ln in fa.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if ln.startswith(">")) if fa.exists() else 0,
            "n_orfs_predicted": n_orf,
            "min_orf_length": min_orf,
            "single_best_only": single_best,
            "output_files": {
                "pep": str(pep) if pep.exists() else "",
                "cds": str(cds) if cds.exists() else "",
                "gff3": str(gff3) if gff3.exists() else "",
            },
        }
        (out_dir / "transdecoder_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== TransDecoder 完成: {n_orf} 个 ORF → {out_dir} ===")


if __name__ == "__main__":
    TransdecoderRunner.main()
