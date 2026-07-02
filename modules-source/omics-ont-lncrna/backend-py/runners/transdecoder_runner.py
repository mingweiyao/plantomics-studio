"""TransDecoder CDS prediction runner for ONT lncRNA data.

Predicts coding regions (CDS) from assembled transcripts using TransDecoder.

Parameters:
  candidate_gtf: str    - Candidate transcript GTF (e.g. merged.gtf) (required)
  genome_fasta: str      - Reference genome FASTA (required)
  min_orf_aa: int       - Minimum ORF length (aa), default 50
  single_best_only: bool- Keep only best ORF per transcript, default true

Outputs (to output_dir/):
  transcripts.fa                              - Extracted transcript sequences
  transcripts.fa.transdecoder.pep/.gff3/.cds  - Predicted CDS
  transdecoder_summary.json
"""
import json
from pathlib import Path
import shutil

from runners.base import BaseRunner


class TransdecoderRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        cand = p.get("candidate_gtf", "")
        genome = p.get("genome_fasta", "")
        min_orf = int(p.get("min_orf_aa", 50))
        single_best = bool(p.get("single_best_only", True))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(f"候选转录本 GTF 不存在: {cand}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Check tools
        for tool in ("TransDecoder.LongOrfs", "TransDecoder.Predict"):
            if not shutil.which(tool):
                raise FileNotFoundError(
                    f"找不到 {tool}, 请重建 conda 环境")

        # 1) Extract transcript sequences
        self.update(pct=10, stage="gffread 抽转录本序列")
        fa = out_dir / "transcripts.fa"
        self.run_command(["gffread", "-w", str(fa), "-g", genome, str(cand)])
        if not fa.exists() or fa.stat().st_size == 0:
            raise RuntimeError("gffread 没抽出转录本序列")

        # 2) LongOrfs
        self.update(pct=35, stage="TransDecoder.LongOrfs", indeterminate=True)
        self.run_command(
            ["TransDecoder.LongOrfs", "-t", str(fa), "-m", str(min_orf)],
            cwd=str(out_dir), indeterminate=True,
            heartbeat_stage="TransDecoder.LongOrfs")

        # 3) Predict
        self.update(pct=65, stage="TransDecoder.Predict", indeterminate=True)
        cmd = ["TransDecoder.Predict", "-t", str(fa)]
        if single_best:
            cmd.append("--single_best_only")
        self.run_command(cmd, cwd=str(out_dir), indeterminate=True,
                         heartbeat_stage="TransDecoder.Predict")

        pep = out_dir / "transcripts.fa.transdecoder.pep"
        n_orf = 0
        if pep.exists():
            n_orf = sum(1 for ln in pep.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if ln.startswith(">"))

        summary = {
            "n_orfs": n_orf,
            "min_orf_aa": min_orf,
            "single_best_only": single_best,
            "transcripts_fa": str(fa),
            "pep_file": str(pep) if pep.exists() else None,
        }
        (out_dir / "transdecoder_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== TransDecoder 完成: {n_orf} 个 ORF ===")


if __name__ == "__main__":
    TransdecoderRunner.main()
