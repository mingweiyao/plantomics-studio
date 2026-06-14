"""normalize runner — TPM / FPKM / CPM(纯 Python 实现)。

不依赖 R 生信包(GenomicFeatures 等),只用 Python 标准库 + 一些算术。
基因长度从 GTF 解析(union-of-exons 策略,跟 GenomicFeatures 同等)。

参数:
  mode:         "matrix"(默认)| "per_sample"
  counts_file:  matrix 模式 — 一个矩阵文件
  counts_files: per_sample 模式 — 多个单样本文件
  gtf:          路径(TPM/FPKM 必需,CPM 不需要)
  methods:      ["TPM", "FPKM", "CPM"] 子集,默认 ["TPM", "FPKM"]

产出(到 output_subdir,通常 = <workdir>/normalized/):
  matrix 模式:
    tpm.tsv / fpkm.tsv / cpm.tsv (按 methods)
  per_sample 模式:
    <sample>.tpm.tsv / <sample>.fpkm.tsv / <sample>.cpm.tsv
  gene_lengths.tsv (TPM/FPKM 时)
  meta.json
"""
import gzip
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from runners.base import BaseRunner


# ───────────────────────────────────────────────
# GTF 解析:union-of-exons 算每个基因的长度
# ───────────────────────────────────────────────

def parse_gtf_gene_lengths(gtf_path: Path,
                            log_func=lambda m: None) -> dict[str, int]:
    """从 GTF 用 union-of-exons 算每个基因的长度。
    
    跟 GenomicFeatures::exonsBy(by="gene") |> reduce() |> width() |> sum() 等价。
    
    返回 {gene_id: length_bp}
    """
    if not gtf_path.exists():
        raise FileNotFoundError(f"GTF 不存在: {gtf_path}")
    
    # 中间结构: gene_id -> [(chrom, start, end), ...] 所有 exon 区间
    gene_exons: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    
    opener = gzip.open if gtf_path.name.endswith(".gz") else open
    
    log_func(f"读 GTF: {gtf_path}")
    n_lines = 0
    n_exons = 0
    with opener(gtf_path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            n_lines += 1
            if not line or line[0] == "#":
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9:
                continue
            feature = cols[2]
            if feature != "exon":
                continue
            
            chrom = cols[0]
            try:
                start = int(cols[3])  # GTF 1-based inclusive
                end = int(cols[4])
            except ValueError:
                continue
            
            # 解析属性列:gene_id "ABC";
            attrs = cols[8]
            m = re.search(r'gene_id\s+"([^"]+)"', attrs)
            if not m:
                # 兼容 GFF3-ish: gene_id=ABC
                m = re.search(r'gene_id[=\s]+([^;\s]+)', attrs)
                if not m:
                    continue
            gene_id = m.group(1).strip().strip('"')
            
            gene_exons[gene_id].append((chrom, start, end))
            n_exons += 1
    
    log_func(f"  读了 {n_lines} 行,{n_exons} 个 exon,涉及 {len(gene_exons)} 个基因")
    
    # 对每个基因的区间做 union 求总长
    gene_lengths: dict[str, int] = {}
    for gene_id, intervals in gene_exons.items():
        # 按染色体分组(同一基因不同染色体很少见,但 GTF 里偶有)
        by_chrom: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for chrom, s, e in intervals:
            by_chrom[chrom].append((s, e))
        
        total = 0
        for chrom, ivs in by_chrom.items():
            # 排序后合并相邻/重叠
            ivs.sort()
            cur_s, cur_e = ivs[0]
            for s, e in ivs[1:]:
                if s <= cur_e + 1:  # 重叠或相邻(GTF 是 inclusive)
                    cur_e = max(cur_e, e)
                else:
                    total += (cur_e - cur_s + 1)
                    cur_s, cur_e = s, e
            total += (cur_e - cur_s + 1)
        gene_lengths[gene_id] = total
    
    log_func(f"  → {len(gene_lengths)} 个基因长度算完")
    return gene_lengths


# ───────────────────────────────────────────────
# Counts 文件 IO
# ───────────────────────────────────────────────

def read_counts(path: Path) -> tuple[list[str], list[str], list[list[float]]]:
    """读 counts 文件。
    
    返回 (sample_names, gene_ids, matrix)
    matrix[i][j] = 第 i 个 gene 在第 j 个 sample 的 count
    """
    if str(path).endswith(".csv"):
        delim = ","
    else:
        delim = "\t"
    
    gene_ids = []
    matrix = []
    sample_names = []
    
    opener = gzip.open if path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        header = f.readline().rstrip("\n").split(delim)
        if len(header) < 2:
            raise ValueError(f"counts 文件至少 2 列(gene_id + count): {path}")
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
    """写 TSV(第 1 列 gene_id,后续 sample)。"""
    with open(path, "w", encoding="utf-8") as f:
        f.write("\t".join(header) + "\n")
        for gid, row in zip(gene_ids, matrix):
            vals = [f"{v:.6f}" if v != int(v) else str(int(v)) for v in row]
            f.write(gid + "\t" + "\t".join(vals) + "\n")


# ───────────────────────────────────────────────
# 标准化算法
# ───────────────────────────────────────────────

def compute_cpm(counts: list[list[float]]) -> list[list[float]]:
    """CPM = count / library_size * 1e6。"""
    n_genes = len(counts)
    n_samples = len(counts[0]) if counts else 0
    
    # 每个样本的总 count
    lib_sizes = [0.0] * n_samples
    for row in counts:
        for j, v in enumerate(row):
            lib_sizes[j] += v
    
    # 防 0
    lib_sizes_safe = [s if s > 0 else 1.0 for s in lib_sizes]
    
    cpm = []
    for row in counts:
        cpm.append([
            row[j] / lib_sizes_safe[j] * 1e6
            for j in range(n_samples)
        ])
    return cpm


def compute_fpkm_tpm(counts: list[list[float]],
                     lengths_bp: list[float]
                     ) -> tuple[list[list[float]], list[list[float]]]:
    """同时算 FPKM 和 TPM(共用 rate 计算)。
    
    rate[i][j] = count[i][j] / length_kb[i]
    FPKM[i][j] = rate[i][j] / (lib_size_M[j])
    TPM[i][j]  = rate[i][j] / sum(rate[*][j]) * 1e6
    """
    n_genes = len(counts)
    n_samples = len(counts[0]) if counts else 0
    
    # rate
    rate = []
    for i, row in enumerate(counts):
        L_kb = lengths_bp[i] / 1000.0
        if L_kb <= 0:
            rate.append([0.0] * n_samples)
        else:
            rate.append([v / L_kb for v in row])
    
    # 每样本 lib_size(M) for FPKM
    lib_M = [0.0] * n_samples
    for row in counts:
        for j, v in enumerate(row):
            lib_M[j] += v
    lib_M = [s / 1e6 for s in lib_M]
    lib_M_safe = [s if s > 0 else 1.0 for s in lib_M]
    
    fpkm = [
        [rate[i][j] / lib_M_safe[j] for j in range(n_samples)]
        for i in range(n_genes)
    ]
    
    # TPM
    rate_sum = [0.0] * n_samples
    for row in rate:
        for j, v in enumerate(row):
            rate_sum[j] += v
    rate_sum_safe = [s if s > 0 else 1.0 for s in rate_sum]
    
    tpm = [
        [rate[i][j] / rate_sum_safe[j] * 1e6 for j in range(n_samples)]
        for i in range(n_genes)
    ]
    
    return fpkm, tpm


# ───────────────────────────────────────────────
# Runner
# ───────────────────────────────────────────────

class NormalizeRunner(BaseRunner):
    
    def run(self):
        params = self.job.params or {}
        mode = params.get("mode", "matrix")
        counts_file = params.get("counts_file")
        counts_files = params.get("counts_files") or []
        counts_dir = params.get("counts_dir")
        gtf = params.get("gtf")
        # auto 模式默认三种标准化
        methods_raw = params.get("methods") or (
            ["TPM", "FPKM", "CPM"] if mode == "auto" else ["TPM", "FPKM"]
        )
        methods = [m.upper() for m in methods_raw]
        
        self.log("=== normalize 开始 ===")
        self.log(f"mode = {mode}")
        self.log(f"methods = {', '.join(methods)}")
        
        needs_length = any(m in methods for m in ("TPM", "FPKM"))
        
        gene_lengths = None
        if needs_length:
            if not gtf or not Path(gtf).exists():
                raise FileNotFoundError(
                    f"TPM/FPKM 需要 GTF,但 GTF 不存在: {gtf}"
                )
            self.update(pct=15, stage="解析 GTF 算基因长度")
            gene_lengths = parse_gtf_gene_lengths(Path(gtf), log_func=self.log)
            
            # 写 gene_lengths.tsv
            gl_path = self.output_dir() / "gene_lengths.tsv"
            with open(gl_path, "w", encoding="utf-8") as f:
                f.write("gene_id\tlength\n")
                for gid, L in sorted(gene_lengths.items()):
                    f.write(f"{gid}\t{L}\n")
            self.log(f"基因长度表写入 {gl_path}")
        
        out_dir = self.output_dir()
        
        if mode == "matrix":
            self._run_matrix(counts_file, gene_lengths, methods, out_dir)
            meta = {
                "mode": "matrix",
                "source_file": counts_file,
                "methods": methods,
            }
        elif mode == "per_sample":
            samples = self._run_per_sample(counts_files, gene_lengths, methods,
                                            out_dir)
            meta = {
                "mode": "per_sample",
                "source_files": counts_files,
                "samples": samples,
                "methods": methods,
            }
        elif mode == "auto":
            # 自动扫 counts_dir:对合并矩阵 + 每个样本都做标准化。
            # 关键:只挑真正的样本定量文件,排除统计文件 / 派生文件,
            # 否则把 summary.tsv、all_genes.tsv、标准化结果等也当样本会出错。
            if not counts_dir or not Path(counts_dir).is_dir():
                raise FileNotFoundError(f"auto 模式需要 counts_dir: {counts_dir}")
            cdir = Path(counts_dir)
            matrix_file = cdir / "all_genes.tsv"
            sample_files = self._find_sample_count_files(cdir)
            self.log(f"auto: 合并矩阵 = {matrix_file.name if matrix_file.exists() else '(无)'}"
                     f";识别到 {len(sample_files)} 个样本定量文件")
            done = {"matrix": None, "per_sample": None}
            if matrix_file.exists():
                self._run_matrix(str(matrix_file), gene_lengths, methods, out_dir)
                done["matrix"] = str(matrix_file)
            if sample_files:
                done["per_sample"] = self._run_per_sample(
                    [str(p) for p in sample_files], gene_lengths, methods, out_dir)
            meta = {
                "mode": "auto",
                "counts_dir": str(cdir),
                "matrix_source": done["matrix"],
                "samples": done["per_sample"],
                "methods": methods,
            }
        else:
            raise ValueError(f"未知 mode: {mode}")
        
        if gtf:
            meta["gtf_used"] = gtf
        
        with open(out_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        
        self.update(pct=100, stage="完成")

    # featureCounts/normalize 在 counts/ 里会产生这些“非样本”文件,auto 扫描必须排除
    _NON_SAMPLE_NAMES = {
        "all_genes.tsv",       # 合并矩阵(单独处理)
        "summary.tsv",         # featureCounts 统计
        "gene_lengths.tsv",    # 基因长度表
        "counts_merged.tsv",   # 旧合并名
        "meta.json",
        "tpm.tsv", "fpkm.tsv", "cpm.tsv",  # 矩阵标准化输出
    }
    _NON_SAMPLE_SUFFIXES = (
        ".summary",                        # 每样本统计
        ".tpm.tsv", ".fpkm.tsv", ".cpm.tsv",  # 每样本标准化输出
    )

    @classmethod
    def _find_sample_count_files(cls, counts_dir: Path) -> list[Path]:
        """挑出真正的“每样本定量文件”,排除统计文件和派生文件。

        规则:counts_dir 下的 *.tsv,但排除:
          - 隐藏/中间文件(以 . 开头,如 .paired_joint.tsv)
          - 已知非样本名(all_genes/summary/gene_lengths/标准化矩阵输出等)
          - 已知非样本后缀(.summary / .tpm.tsv / .fpkm.tsv / .cpm.tsv)
        """
        out: list[Path] = []
        for p in sorted(counts_dir.glob("*.tsv")):
            name = p.name
            if name.startswith("."):
                continue
            if name in cls._NON_SAMPLE_NAMES:
                continue
            if any(name.endswith(suf) for suf in cls._NON_SAMPLE_SUFFIXES):
                continue
            out.append(p)
        return out

    
    def _run_matrix(self, counts_file, gene_lengths, methods, out_dir):
        if not counts_file or not Path(counts_file).exists():
            raise FileNotFoundError(f"counts_file 不存在: {counts_file}")
        
        self.update(pct=35, stage="读 counts 矩阵")
        sample_names, gene_ids, counts = read_counts(Path(counts_file))
        self.log(f"矩阵: {len(gene_ids)} 基因 × {len(sample_names)} 样本")
        
        # 对齐基因长度
        if gene_lengths is not None:
            lengths_bp = []
            n_missing = 0
            for gid in gene_ids:
                L = gene_lengths.get(gid)
                if L is None:
                    n_missing += 1
                    lengths_bp.append(0.0)  # 后面会被跳过(rate=0)
                else:
                    lengths_bp.append(float(L))
            if n_missing > 0:
                self.log(f"警告:{n_missing} 个基因在 GTF 中没找到")
        else:
            lengths_bp = None
        
        self.update(pct=55, stage="计算标准化")
        self._compute_and_write(counts, gene_ids, sample_names, lengths_bp,
                                 methods, out_dir, prefix="")
    
    def _run_per_sample(self, counts_files, gene_lengths, methods, out_dir):
        if not counts_files:
            raise ValueError("per_sample 模式需要 counts_files")
        
        samples_done = []
        total = len(counts_files)
        for i, f in enumerate(counts_files):
            p = Path(f)
            if not p.exists():
                self.log(f"跳过(不存在): {f}")
                continue
            
            self.update(
                pct=int(30 + 60 * i / total),
                stage=f"处理 {i+1}/{total}: {p.name}",
            )
            
            sample_names, gene_ids, counts = read_counts(p)
            if len(sample_names) != 1:
                self.log(
                    f"警告:{p.name} 有 {len(sample_names)} 个样本列,只用第 1 个"
                )
                # 截断
                counts = [[row[0]] for row in counts]
                sample_names = sample_names[:1]
            
            sample = sample_names[0]
            self.log(f"  样本: {sample}")
            
            if gene_lengths is not None:
                lengths_bp = [float(gene_lengths.get(g, 0)) for g in gene_ids]
            else:
                lengths_bp = None
            
            self._compute_and_write(counts, gene_ids, sample_names, lengths_bp,
                                     methods, out_dir, prefix=sample + ".")
            samples_done.append(sample)
        
        return samples_done
    
    def _compute_and_write(self, counts, gene_ids, sample_names, lengths_bp,
                            methods, out_dir, prefix: str):
        """算指定 methods 写到 <out_dir>/<prefix><method_lower>.tsv"""
        # 提前算 FPKM/TPM 共用部分
        fpkm = tpm = None
        if "FPKM" in methods or "TPM" in methods:
            if lengths_bp is None:
                raise RuntimeError("FPKM/TPM 需要基因长度")
            fpkm, tpm = compute_fpkm_tpm(counts, lengths_bp)
        
        if "CPM" in methods:
            cpm = compute_cpm(counts)
            path = out_dir / f"{prefix}cpm.tsv"
            write_tsv(path, ["gene_id"] + sample_names, gene_ids, cpm)
            self.log(f"  CPM → {path.name}")
        
        if "FPKM" in methods:
            path = out_dir / f"{prefix}fpkm.tsv"
            write_tsv(path, ["gene_id"] + sample_names, gene_ids, fpkm)
            self.log(f"  FPKM → {path.name}")
        
        if "TPM" in methods:
            path = out_dir / f"{prefix}tpm.tsv"
            write_tsv(path, ["gene_id"] + sample_names, gene_ids, tpm)
            self.log(f"  TPM → {path.name}")


if __name__ == "__main__":
    NormalizeRunner.main()
