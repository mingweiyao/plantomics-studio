"""miRNA quantification runner - 量化 miRNA 表达。

使用 miRDeep2 quantifier.pl 对每个样本量化 miRNA 表达。

参数:
  samples: [dict]         - [{name, collapsed_fa, arf?}]
  mature_mirna_fa: str    - 已知成熟 miRNA FASTA
  precursor_mirna_fa: str - 前体 miRNA FASTA (可选)
  threads: int            - 默认 4

产出(每样本):
  <output>/<name>/
    mirnas_expression.html     - 表达报告
    mirnas_expression.csv      - 表达矩阵(csv)
    mirnas_counts_per_precursor.html
  <output>/quantifier_summary.tsv
"""
from pathlib import Path

from runners.base import BaseRunner


class QuantifierRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        samples = params.get("samples", [])
        mature_fa = params.get("mature_mirna_fa", "")
        precursor_fa = params.get("precursor_mirna_fa", "")
        threads = int(params.get("threads", 4))

        if not samples:
            raise ValueError("需要 samples")
        if not mature_fa:
            raise ValueError("需要 mature_mirna_fa")
        if not Path(mature_fa).exists():
            raise FileNotFoundError(f"mature FASTA 不存在: {mature_fa}")

        threads = self.effective_threads(threads)
        out_dir = self.output_dir()
        total = len(samples)

        self.log(f"=== miRNA 定量: {total} 个样本 ===")
        self.log(f"  mature FASTA: {mature_fa}")

        for i, sample in enumerate(samples):
            if self._cancelled:
                raise InterruptedError("任务取消")

            name = sample.get("name", f"sample_{i}")
            collapsed_fa = sample.get("collapsed_fa", "")

            if not collapsed_fa or not Path(collapsed_fa).exists():
                self.log(f"  跳过 {name}: collapsed_fa 不存在或未提供")
                continue

            pct = int(10 + 80 * i / total)
            self.update(pct=pct, stage=f"定量 {i+1}/{total}: {name}")

            sample_dir = out_dir / name
            sample_dir.mkdir(parents=True, exist_ok=True)

            # quantifier.pl 参数:
            #   -p <precursor.fa>  前体 miRNA 序列
            #   -m <mature.fa>     成熟 miRNA 序列
            #   -r <collapsed.fa>  去冗余的 reads
            #   -t <species>       物种类型
            #   -y <date>          日期字符串
            #   -c                 干净模式 (不清除中间文件)
            #   -g 0               不生成表达图

            cmd = [
                "quantifier.pl",
                "-p", str(precursor_fa) if precursor_fa and Path(precursor_fa).exists() else "none",
                "-m", str(mature_fa),
                "-r", str(collapsed_fa),
                "-t", "all",       # 所有物种类型
                "-c",              # 保留中间文件
                "-g", "0",         # 不生成图(加快速度)
            ]

            self.log(f"  [{name}] quantifier.pl 运行中")
            self.run_command(
                cmd,
                cwd=str(sample_dir),
                timeout=14400,
                indeterminate=True,
                heartbeat_stage=f"定量 {name}",
            )

            # quantifier.pl 在当前目录输出:
            #   mirnas_expression.html / mirnas_expression.csv
            # 把它们整理到样本子目录
            self._move_output(sample_dir, name)

        self.update(pct=95, stage="汇总")
        self._write_summary(out_dir, samples)
        self.update(pct=100, stage="完成")

    def _move_output(self, sample_dir: Path, name: str):
        """把 quantifier.pl 输出移到样本目录。"""
        import shutil

        # quantifier.pl 在当前目录写入的文件
        output_files = [
            "mirnas_expression.html",
            "mirnas_expression.csv",
            "mirnas_counts_per_precursor.html",
            "expression_analysis.html",
            "miRNA_mature_to_pre_mapping.txt",
            "miRNA_mature.fa",
            "miRNA_pre_mature.fa",
        ]

        moved = []
        for fname in output_files:
            src = sample_dir / fname
            if src.exists():
                dest = sample_dir / f"{name}_{fname}"
                shutil.move(str(src), str(dest))
                moved.append(dest.name)

        # 也移动可能的子目录
        for subdir in ["bed", "tmp"]:
            sd = sample_dir / subdir
            if sd.is_dir():
                dest = sample_dir / f"{name}_{subdir}"
                shutil.move(str(sd), str(dest))
                moved.append(dest.name)

        if moved:
            self.log(f"  [{name}] 输出: {', '.join(moved)}")
        else:
            self.log(f"  [{name}] 未找到 quantifier.pl 输出文件")

    def _write_summary(self, out_dir: Path, samples: list[dict]):
        """汇总每个样本的定量结果统计。"""
        rows = []
        for sample in samples:
            name = sample.get("name", "?")
            sample_dir = out_dir / name
            csv_file = sample_dir / f"{name}_mirnas_expression.csv"
            if csv_file.exists():
                with open(csv_file) as f:
                    n = sum(1 for _ in f) - 1  # 减表头
                rows.append({"sample": name, "mirnas_quantified": max(0, n)})

        if rows:
            summary = out_dir / "quantifier_summary.tsv"
            with open(summary, "w") as f:
                f.write("sample\tmirnas_quantified\n")
                for r in rows:
                    f.write(f"{r['sample']}\t{r['mirnas_quantified']}\n")
            self.log(f"定量汇总: {summary}")


if __name__ == "__main__":
    QuantifierRunner.main()
