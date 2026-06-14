"""读长检测 — 给定 fastq 文件/目录,采样估算读长,归到标准档。

转录组测序仪标准读长就这几档,实际值会有 ±2bp 的浮动(adapter trim 等),
所以我们检测后**对齐到最近的标准读长**。

被 star_index_runner / star_align_runner / pipeline_upstream_runner 共用。
"""
import gzip
import re
from collections import Counter, defaultdict
from pathlib import Path


# 标准读长档(覆盖 illumina/MGI 主流)
# 检测到的读长会归到这里最近的档
STANDARD_READ_LENGTHS = [36, 50, 75, 100, 125, 150, 250, 300]


def normalize_to_standard(detected_length: int) -> int:
    """把探测到的读长归到最近的标准档。"""
    if detected_length <= 0:
        return STANDARD_READ_LENGTHS[0]
    return min(STANDARD_READ_LENGTHS,
                key=lambda x: abs(x - detected_length))


def _sample_name_from_fastq(fastq_path: Path) -> str:
    """从 fastq 文件名推 sample 名(去掉 _1/_2/_R1/_R2 配对后缀和扩展名)。"""
    name = fastq_path.name
    name = re.sub(r"\.(fq|fastq)(\.gz)?$", "", name)
    name = re.sub(r"[._-](?:R?[12]|read[12])$", "", name, flags=re.IGNORECASE)
    return name


def detect_one_fastq(path: Path, n_reads: int = 1000) -> int | None:
    """采样一个 fastq,返回众数读长(原始值,**未归档**)。"""
    if not path.exists():
        return None
    try:
        opener = gzip.open if path.name.endswith(".gz") else open
        lengths = []
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= n_reads * 4:
                    break
                if i % 4 == 1:  # 序列行
                    lengths.append(len(line.rstrip()))
        if not lengths:
            return None
        return Counter(lengths).most_common(1)[0][0]
    except Exception:
        return None


def detect_dir(fastq_dir: Path, n_reads: int = 500) -> list[dict]:
    """递归扫目录,**按 sample 聚合**(R1/R2 合一),返回每个 sample 一条记录。
    
    每条记录:
      {sample, files, raw_read_length, read_length, sjdb_overhang}
    
    - raw_read_length: 实际探测到的最大值(R1/R2 取大)
    - read_length: 归档后的标准读长
    - sjdb_overhang: read_length - 1
    """
    if not fastq_dir.is_dir():
        return []
    
    candidates = []
    for ext in ("fq.gz", "fastq.gz", "fq", "fastq"):
        candidates.extend(fastq_dir.rglob(f"*.{ext}"))
    candidates = sorted(set(candidates))
    
    # 按 sample 名分组(_1/_2 同样本)
    by_sample: dict[str, list[Path]] = defaultdict(list)
    for fq in candidates:
        sample = _sample_name_from_fastq(fq)
        by_sample[sample].append(fq)
    
    results = []
    for sample, files in by_sample.items():
        # 每个文件探测一次,取最大值(R1 R2 长度可能差 1-2,以大的为准)
        per_file_lengths = []
        for fq in files:
            L = detect_one_fastq(fq, n_reads=n_reads)
            if L is not None:
                per_file_lengths.append(L)
        if not per_file_lengths:
            continue
        
        raw_max = max(per_file_lengths)
        std_length = normalize_to_standard(raw_max)
        
        results.append({
            "sample": sample,
            "files": [str(f) for f in files],
            "raw_read_length": raw_max,
            "read_length": std_length,
            "sjdb_overhang": std_length - 1,
        })
    return results


def unique_overhangs(records: list[dict]) -> list[int]:
    """从 detect_dir 结果中拿 unique 的 sjdb_overhang 列表(升序)。"""
    return sorted(set(r["sjdb_overhang"] for r in records))


def closest_overhang(read_length: int, available: list[int]) -> int | None:
    """给一个读长,从可用 overhang 列表里选最接近的(优先 ≥ 读长-1)。"""
    if not available:
        return None
    target = read_length - 1
    ge = [v for v in available if v >= target]
    if ge:
        return min(ge)
    return max(available)
