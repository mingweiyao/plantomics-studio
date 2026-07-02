"""miRDeep2.pl prediction runner - miRNA 鉴定。

对每个样本运行 miRDeep2.pl:
  miRDeep2.pl collapsed.fa genome.fa arf_file mature.fa [other_mature.fa] [precursor.fa] -t species

参数:
  samples: [dict]         - [{name, collapsed_fa, arf}]
  genome_fasta: str       - 基因组 FASTA 路径
  mature_mirna_fa: str    - 已知成熟 miRNA FASTA (miRBase)
  other_mature_fa: str    - 其他已知成熟 miRNA FASTA (可选)
  precursor_mirna_fa: str - 前体 miRNA FASTA (可选)
  species: str            - 'animal' 或 'plant',默认 'animal'

产出(每样本):
  <output>/<name>/
    mirdeep2/                     - miRDeep2 输出目录
    mirdeep2_result.csv           - 解析后的预测结果
    novel_mirnas.fa               - 预测的新 miRNA 序列
    known_mirnas.csv              - 已知 miRNA 表达
"""
import csv
import re
from pathlib import Path

from runners.base import BaseRunner


class Mirdeep2Runner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        samples = params.get("samples", [])
        genome_fasta = params.get("genome_fasta", "")
        mature_fa = params.get("mature_mirna_fa", "")
        other_mature = params.get("other_mature_fa", "")
        precursor_fa = params.get("precursor_mirna_fa", "")
        species = params.get("species", "animal")

        if not samples:
            raise ValueError("需要 samples")
        if not genome_fasta:
            raise ValueError("需要 genome_fasta")
        if not mature_fa:
            raise ValueError("需要 mature_mirna_fa (miRBase)")

        genome_path = Path(genome_fasta)
        mature_path = Path(mature_fa)
        if not genome_path.exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")
        if not mature_path.exists():
            raise FileNotFoundError(f"mature FASTA 不存在: {mature_fa}")

        out_dir = self.output_dir()
        total = len(samples)

        self.log(f"=== miRDeep2 预测: {total} 个样本 ===")
        self.log(f"  物种类型: {species}")

        for i, sample in enumerate(samples):
            if self._cancelled:
                raise InterruptedError("任务取消")

            name = sample.get("name", f"sample_{i}")
            collapsed_fa = sample.get("collapsed_fa", "")
            arf_file = sample.get("arf", "")

            if not collapsed_fa:
                self.log(f"  跳过 {name}: 无 collapsed_fa")
                continue
            if not arf_file:
                self.log(f"  跳过 {name}: 无 arf 文件")
                continue

            cf_path = Path(collapsed_fa)
            arf_path = Path(arf_file)
            if not cf_path.exists():
                self.log(f"  跳过 {name}: collapsed_fa 不存在 ({collapsed_fa})")
                continue
            if not arf_path.exists():
                self.log(f"  跳过 {name}: arf 不存在 ({arf_file})")
                continue

            pct = int(10 + 80 * i / total)
            self.update(pct=pct, stage=f"miRDeep2 预测 {i+1}/{total}: {name}")

            sample_dir = out_dir / name
            sample_dir.mkdir(parents=True, exist_ok=True)

            # miRDeep2 会写很多文件到当前工作目录,我们在样本目录下跑
            # 构建命令
            cmd = [
                "miRDeep2.pl",
                str(cf_path),
                str(genome_path),
                str(arf_path),
                str(mature_path),
            ]
            if other_mature and Path(other_mature).exists():
                cmd.append(str(other_mature))
            else:
                cmd.append("none")
            if precursor_fa and Path(precursor_fa).exists():
                cmd.append(str(precursor_fa))
            else:
                cmd.append("none")

            cmd += ["-t", species]

            self.log(f"  [{name}] 运行 miRDeep2.pl")
            self.log(f"  命令: {' '.join(cmd)}")

            # miRDeep2 自身创建 mirdeep2_output_* 目录,我们在样本目录下跑
            # 以隔离不同样本的输出
            self.run_command(
                cmd,
                cwd=str(sample_dir),
                timeout=86400,  # miRDeep2 可能很慢
                indeterminate=True,
                heartbeat_stage=f"miRDeep2 {name}",
            )

            # 解析 miRDeep2 输出
            self.update(pct=pct + 5, stage=f"解析结果 {i+1}/{total}: {name}")
            self._parse_results(sample_dir, name)

        self.update(pct=95, stage="汇总")
        self._write_summary(out_dir, samples)
        self.update(pct=100, stage="完成")

    def _parse_results(self, sample_dir: Path, name: str):
        """解析 miRDeep2 输出,提取预测结果。"""
        # miRDeep2 在 sample_dir 下生成: miRDeep2_output_<date>/
        # 其中包含 result_*.csv, novel_mirnas.fa 等
        output_dirs = sorted(sample_dir.glob("miRDeep2_output_*"))
        if not output_dirs:
            self.log(f"  没找到 miRDeep2 输出目录,可能运行失败")
            return

        latest = output_dirs[-1]
        self.log(f"  解析: {latest.name}")

        # 找结果 CSV
        result_csv = latest / "result_*.csv"
        import glob
        csv_files = sorted(latest.glob("result_*.csv"))
        if csv_files:
            # 复制结果到样本目录
            import shutil
            for rf in csv_files:
                dest = sample_dir / f"{name}_mirdeep2_{rf.name}"
                shutil.copy2(rf, dest)
                self.log(f"  预测结果: {dest.name}")

        # 找 novel miRNAs
        novel_fa = latest / "novel_mirnas.fa"
        if novel_fa.exists():
            import shutil
            dest = sample_dir / f"{name}_novel_mirnas.fa"
            shutil.copy2(novel_fa, dest)
            # 统计 novel miRNA 数
            n = 0
            with open(dest) as f:
                for line in f:
                    if line.startswith(">"):
                        n += 1
            self.log(f"   novel miRNAs: {n}")

        # 找已知 miRNA 表达
        known_csv = latest / "miRDeep2_known_miRNAs.csv"
        if known_csv.exists():
            import shutil
            dest = sample_dir / f"{name}_known_miRNAs.csv"
            shutil.copy2(known_csv, dest)
            # 统计行数
            with open(dest) as f:
                n = sum(1 for _ in f) - 1  # 减表头
            self.log(f"   已知 miRNAs: {n}")

    def _write_summary(self, out_dir: Path, samples: list[dict]):
        """写汇总文件,列出所有样本的预测统计。"""
        rows = []
        for sample in samples:
            name = sample.get("name", "?")
            sample_dir = out_dir / name
            novel = sample_dir / f"{name}_novel_mirnas.fa"
            known = sample_dir / f"{name}_known_miRNAs.csv"
            n_novel = 0
            n_known = 0
            if novel.exists():
                with open(novel) as f:
                    n_novel = sum(1 for l in f if l.startswith(">"))
            if known.exists():
                with open(known) as f:
                    n_known = sum(1 for _ in f) - 1
            rows.append({"sample": name, "known_mirnas": n_known,
                         "novel_mirnas": n_novel})

        if rows:
            summary = out_dir / "mirdeep2_summary.tsv"
            with open(summary, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["sample", "known_mirnas",
                                                    "novel_mirnas"],
                                   delimiter="\t")
                w.writeheader()
                w.writerows(rows)
            self.log(f"汇总: {summary}")


if __name__ == "__main__":
    Mirdeep2Runner.main()
