"""normalize runner - CPM / RPM 标准化(纯 Python 实现)。

miRNA 分析不需要基因长度,所以只支持:
  - CPM: Counts Per Million (library-size normalization)
  - RPM: Reads Per Million (同 CPM,miRNA 领域常用术语)

参数:
  counts_file:  输入的 counts 矩阵文件(TSV/CSV)
  methods:      ["CPM", "RPM"] 子集,默认 ["CPM", "RPM"]
  output_dir:   输出目录(可选,默认 output_path)

产出(到 output_subdir):
  cpm.tsv / rpm.tsv (按 methods)
  meta.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


def read_counts(path: Path) -> tuple[list[str], list[str], list[list[float]]]:
    """读 counts 文件,返回 (sample_names, gene_ids, matrix)。"""
    if str(path).endswith(".csv"):
        delim = ","
    else:
        delim = "\t"

    gene_ids = []
    matrix = []
    sample_names = []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split(delim)
        if len(header) < 2:
            raise ValueError(f"counts 文件至少 2 列: {path}")
        sample_names = header[1:]

        for line in f:
            if not line.strip():
                continue
            cols = line.rstrip("\n").split(delim)
            if len(cols) < 1 + len(sample_names):
                continue
            gene_ids.append(cols[0])
            row = []
            for v in cols[1:1 + len(sample_names)]:
                try:
                    row.append(float(v))
                except ValueError:
                    row.append(0.0)
            matrix.append(row)

    return sample_names, gene_ids, matrix


def write_tsv(path: Path, header: list[str],
              gene_ids: list[str], matrix: list[list[float]]):
    """写 TSV (第 1 列 miRNA_id,后续 sample)。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for gid, row in zip(gene_ids, matrix):
            vals = [f"{v:.6f}" if v != int(v) else str(int(v)) for v in row]
            f.write(gid + "\t" + "\t".join(vals) + "\n")


def compute_cpm(counts: list[list[float]]) -> list[list[float]]:
    """CPM = count / library_size * 1e6。"""
    n_genes = len(counts)
    n_samples = len(counts[0]) if counts else 0

    lib_sizes = [0.0] * n_samples
    for row in counts:
        for j, v in enumerate(row):
            lib_sizes[j] += v

    lib_sizes_safe = [s if s > 0 else 1.0 for s in lib_sizes]

    cpm = []
    for row in counts:
        cpm.append([
            row[j] / lib_sizes_safe[j] * 1e6
            for j in range(n_samples)
        ])
    return cpm


class NormalizeRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        counts_file = params.get("counts_file", "")
        methods_raw = params.get("methods") or ["CPM", "RPM"]
        methods = [m.upper() for m in methods_raw]

        if not counts_file:
            raise ValueError("需要 counts_file")
        counts_path = Path(counts_file)
        if not counts_path.exists():
            raise FileNotFoundError(f"counts_file 不存在: {counts_file}")

        self.log("=== normalize 开始 ===")
        self.log(f"methods = {', '.join(methods)}")
        self.log(f"counts_file = {counts_file}")

        out_dir = self.output_dir()

        self.update(pct=30, stage="读 counts 矩阵")
        sample_names, mirna_ids, counts = read_counts(counts_path)
        self.log(f"矩阵: {len(mirna_ids)} miRNA x {len(sample_names)} 样本")

        # CPM 和 RPM 是同一算法(CPM 是通用名,RPM 是 miRNA/small RNA 领域常用名)
        if "CPM" in methods or "RPM" in methods:
            self.update(pct=55, stage="计算 CPM/RPM")
            cpm = compute_cpm(counts)

            if "CPM" in methods:
                header = ["miRNA_id"] + sample_names
                cpm_path = out_dir / "cpm.tsv"
                write_tsv(cpm_path, header, mirna_ids, cpm)
                self.log(f"  CPM -> {cpm_path.name}")

            if "RPM" in methods:
                header = ["miRNA_id"] + sample_names
                rpm_path = out_dir / "rpm.tsv"
                write_tsv(rpm_path, header, mirna_ids, cpm)
                self.log(f"  RPM -> {rpm_path.name}")

        meta = {
            "source_file": counts_file,
            "methods": methods,
            "n_mirnas": len(mirna_ids),
            "n_samples": len(sample_names),
            "samples": sample_names,
        }
        with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        self.update(pct=100, stage="完成")


if __name__ == "__main__":
    NormalizeRunner.main()
