"""merge_counts runner - 把多个单样本 miRNA counts 合并成矩阵。

参数:
  counts_dir:    扫这个目录下所有 *expression.csv 或 *.tsv (优先级低)
  counts_files:  显式指定要合并的文件列表 (优先级高)
  output_name:   输出文件名,默认 "counts_merged.tsv"

单样本文件格式: 每行 miRNA_id\tcount (2 列)
合并后格式: miRNA_id\tsample1\tsample2\t... (outer join,缺失补 0)

输出:
  <output_subdir>/<output_name>
  <output_subdir>/merge_meta.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


EXCLUDED_BASENAMES = {
    "counts_merged.tsv",
    "merge_meta.json",
}


class MergeCountsRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        counts_dir = params.get("counts_dir")
        counts_files = params.get("counts_files") or []
        output_name = params.get("output_name") or "counts_merged.tsv"

        # 决定要合并哪些文件
        files: list[Path] = []
        if counts_files:
            files = [Path(f) for f in counts_files]
            self.log(f"用户指定 {len(files)} 个文件")
        elif counts_dir:
            d = Path(counts_dir)
            if not d.is_dir():
                raise FileNotFoundError(f"counts_dir 不是目录: {counts_dir}")
            # 找所有 expression.csv(quantifier.pl 输出) 和 .tsv
            all_files = sorted(d.glob("*expression*.csv")) + sorted(d.glob("*.tsv"))
            files = [
                p for p in all_files
                if p.name not in EXCLUDED_BASENAMES
                and p.name != output_name
            ]
            self.log(f"从 {counts_dir} 扫到 {len(files)} 个 counts 文件")
        else:
            raise ValueError("必须提供 counts_dir 或 counts_files 之一")

        if not files:
            raise ValueError("没有要合并的文件")

        self.log("=== merge_counts 开始 ===")
        self.update(pct=20, stage="读各样本 counts")

        # miRNA_id -> {sample_name: count_str}
        gene_data: dict[str, dict[str, str]] = {}
        sample_order: list[str] = []

        for i, f in enumerate(files):
            if not f.exists():
                self.log(f"跳过(不存在): {f}")
                continue

            self.update(
                pct=int(20 + 50 * i / len(files)),
                stage=f"读 {i+1}/{len(files)}: {f.name}",
            )

            # 从文件名提取样本名(取第一个点之前的部分)
            sample = f.stem
            # 去掉 mirnas_expression 之类的前缀
            for prefix in ["mirnas_expression", "expression", "counts"]:
                if sample.startswith(prefix + "_"):
                    sample = sample[len(prefix) + 1:]
                    break
                if sample == prefix:
                    sample = "sample"
                    break

            # 处理重复样本
            if sample in sample_order:
                self.log(f"警告:样本 {sample} 重复,以最新为准")
            else:
                sample_order.append(sample)

            # 读 counts 文件
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fp:
                    # 第一行可能是表头,也可能直接是数据
                    first_line = fp.readline().strip()
                    header = first_line.split("\t")
                    if len(header) >= 2 and header[0].lower() in (
                            "miRNA_id", "mirna_id", "mirna", "gene_id",
                            "geneid", "id"):
                        # 有表头,继续读数据
                        pass
                    else:
                        # 没有表头,第一行就是数据
                        self._add_count_line(gene_data, sample, header)

                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        cols = line.split("\t")
                        if len(cols) < 2:
                            continue
                        self._add_count_line(gene_data, sample, cols)

                self.log(f"  {f.name} -> 样本 {sample}")
            except Exception as e:
                self.log(f"  读 {f.name} 失败: {e}")

        if not sample_order:
            raise ValueError("没有读到任何有效样本")

        n_mirnas = len(gene_data)
        n_samples = len(sample_order)
        self.log(f"合并后: {n_mirnas} miRNA x {n_samples} 样本")

        self.update(pct=80, stage="写出合并矩阵")

        out_dir = self.output_dir()
        out_path = out_dir / output_name
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("miRNA_id\t" + "\t".join(sample_order) + "\n")
            for mirna_id in sorted(gene_data.keys()):
                row = gene_data[mirna_id]
                vals = [row.get(s, "0") for s in sample_order]
                f.write(mirna_id + "\t" + "\t".join(vals) + "\n")

        self.log(f"写入 {out_path}")

        meta = {
            "output_file": str(out_path),
            "n_mirnas": n_mirnas,
            "n_samples": n_samples,
            "samples": sample_order,
            "source_files": [str(f) for f in files],
        }
        with open(out_dir / "merge_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        self.update(pct=100, stage="完成")

    @staticmethod
    def _add_count_line(gene_data: dict, sample: str, cols: list[str]):
        """解析一行 miRNA counts 数据。"""
        mirna_id = cols[0].strip()
        count = cols[1].strip()
        if mirna_id and count:
            gene_data.setdefault(mirna_id, {})[sample] = count


if __name__ == "__main__":
    MergeCountsRunner.main()
