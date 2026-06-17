"""新转录本编码区序列预测 runner(TransDecoder,对应报告 5.3.1 / 6.1.7)。

对"新转录本"步骤(StringTie)产出的 merged.gtf,先用 gffread 抽出转录本序列,
再用 TransDecoder 预测编码区(ORF):
  1. gffread -w transcripts.fa -g <genome> <merged.gtf>
  2. TransDecoder.LongOrfs -t transcripts.fa -m <min_orf_aa>
  3. TransDecoder.Predict   -t transcripts.fa [--single_best_only]

参数:
  candidate_gtf: str   - 候选转录本 GTF(用"新转录本"步骤的 merged.gtf)
  genome_fasta:  str   - 基因组 FASTA(抽转录本序列用,必填)
  min_orf_aa:    int   - 最短 ORF 长度(aa),默认 100(TransDecoder -m)
  single_best:   bool  - 每条转录本只保留最优 ORF,默认 True

产出(到 output_subdir):
  transcripts.fa                                   - 抽出的转录本序列
  transcripts.fa.transdecoder.pep/.gff3/.bed/.cds  - 预测的编码区
"""
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
                f"候选转录本 GTF 不存在(先跑新转录本步骤拿 merged.gtf): {cand}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # 工具预检:bioconda 的 transdecoder 偶尔没装进 env(或复用了旧的 env 包),
        # 这时直接给清晰可执行的提示,而不是等 gffread 跑完才在 LongOrfs 处抛裸 FileNotFoundError。
        missing = [t for t in ("TransDecoder.LongOrfs", "TransDecoder.Predict")
                   if not shutil.which(t)]
        if missing:
            raise FileNotFoundError(
                f"找不到 {', '.join(missing)}。这是模块 conda 环境里没装上 transdecoder 造成的,"
                "不是流程代码问题。env.yaml 里已列了 transdecoder,请重建模块环境后再试:"
                "运行 bash scripts/build-deb.sh,且不要加 --skip-env、也不要复用旧的 "
                "build/conda-env-packed.tar.gz(复用旧包不会安装新增的工具)。")

        # 1) 抽转录本序列
        self.update(pct=10, stage="gffread 抽转录本序列")
        fa = out_dir / "transcripts.fa"
        self.run_command(["gffread", "-w", str(fa), "-g", genome, str(cand)])
        if not fa.exists() or fa.stat().st_size == 0:
            raise RuntimeError("gffread 没抽出转录本序列,检查 GTF 与基因组是否匹配")

        # 2) LongOrfs(TransDecoder 在 cwd 下产出 <fa>.transdecoder_dir/)
        self.update(pct=35, stage="TransDecoder.LongOrfs", indeterminate=True)
        self.run_command(
            ["TransDecoder.LongOrfs", "-t", str(fa), "-m", str(min_orf)],
            cwd=str(out_dir), indeterminate=True,
            heartbeat_stage="TransDecoder.LongOrfs",
        )

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
        self.update(pct=100, stage="完成")
        self.log(f"=== TransDecoder 完成,预测到 {n_orf} 条编码区 → {out_dir} ===")


if __name__ == "__main__":
    TransdecoderRunner.main()
