"""一键运行上游(到 counts_merged.tsv 便停)。

任务流:
  0. 扫 raw/:
     - 有 .sra 但没 fastq → 先 SRA 解压
  1. fastp 过滤(读 raw/,写 trimmed/)
  2. 检查 star_index/(空就先 STAR Index — 但用户已有索引就跳过)
  3. STAR Align(读 trimmed/,写 aligned/)
  4. featureCounts(读 aligned/,写 counts/<sample>.tsv)
  5. 合并 counts → counts_merged.tsv

每一步内部直接调对应 runner 的核心逻辑(不再走 HTTP/job 系统),
保持单 job 但日志清楚分阶段。

参数:
  workdir:        项目工作目录(必填)
  fasta:          参考 FASTA(STAR Index 用,如果索引已存在可省)
  gtf:            参考 GTF(STAR Index + featureCounts 必填)
  
  fastp:          {q, u, l, threads_per_sample, parallel} 等(可选)
  star_align:     {threads}(可选)
  feature_counts: {paired, strand, threads}(可选)
"""
import re
import shutil
import sys
from pathlib import Path

from runners.base import BaseRunner

# 引入各 runner 类(直接实例化跑核心逻辑,不重新 fork 进程,省时间)
from runners.sra_download_runner import SraDownloadRunner
from runners.fastp_runner import FastpRunner
from runners.fastqc_runner import FastqcRunner
from runners.star_index_runner import StarIndexRunner
from runners.star_align_runner import StarAlignRunner
from runners.feature_counts_runner import FeatureCountsRunner
from runners.merge_counts_runner import MergeCountsRunner
from runners.normalize_runner import NormalizeRunner
from runners.qualimap_runner import QualimapRunner
from runners.stringtie_runner import StringtieRunner
from runners.lncrna_runner import LncrnaRunner


def _detect_paired_samples(fastq_dir: Path) -> list[dict]:
    """扫一个目录,识别 paired-end / single-end 样本。
    
    返回 [{name, r1, r2?}, ...]
    """
    if not fastq_dir.is_dir():
        return []
    
    fastq_files = []
    for ext in ("fq.gz", "fastq.gz", "fq", "fastq"):
        fastq_files.extend(fastq_dir.rglob(f"*.{ext}"))
    fastq_files = sorted(set(fastq_files))
    
    paired_pattern = re.compile(
        r"^(.+?)[._-](?:R?[12]|read[12])\.(?:f(?:ast)?q)(?:\.gz)?$",
        re.IGNORECASE,
    )
    
    pairs: dict[str, dict[str, str]] = {}
    singles: list[dict] = []
    
    for fq in fastq_files:
        m = paired_pattern.match(fq.name)
        if m:
            base = m.group(1)
            # 识别 _1/_2
            mate = "1" if re.search(r"[._-](?:R?1|read1)\.", fq.name, re.I) else "2"
            pairs.setdefault(base, {})[mate] = str(fq)
        else:
            name = re.sub(r"\.(fq|fastq)(\.gz)?$", "", fq.name)
            singles.append({"name": name, "r1": str(fq)})
    
    samples = []
    for base, mates in pairs.items():
        if "1" in mates:
            entry = {"name": base, "r1": mates["1"]}
            if "2" in mates:
                entry["r2"] = mates["2"]
            samples.append(entry)
    samples.extend(singles)
    return samples


class PipelineUpstreamRunner(BaseRunner):
    
    def run(self):
        params = self.job.params or {}
        workdir = Path(params.get("workdir", ""))
        fasta = params.get("fasta")
        gtf = params.get("gtf")
        
        if not workdir.is_dir():
            raise FileNotFoundError(f"workdir 无效: {workdir}")
        if not gtf or not Path(gtf).is_file():
            raise FileNotFoundError(f"gtf 无效: {gtf}")
        
        raw_dir = workdir / "00_raw"
        trimmed_dir = workdir / "02_trimmed"
        index_dir = workdir / "03_star_index"
        aligned_dir = workdir / "04_aligned"
        counts_dir = workdir / "05_counts"
        for d in (raw_dir, trimmed_dir, index_dir, aligned_dir, counts_dir):
            d.mkdir(parents=True, exist_ok=True)
        
        normalized_dir = workdir / "06_normalized"
        normalized_dir.mkdir(parents=True, exist_ok=True)

        # 用户选择要跑哪些步骤(不传 steps = 全跑,向后兼容)。
        # star_index 不是独立勾选项:跑 star_align 时会自动按需建索引。
        # 比对之后的可选分析(文库质控/新转录本/lncRNA)能从 aligned/ 自动取输入;
        # 可变剪接(alt_splicing)要先分两组样本,无法自动跑,需单独配置。
        _ALL_STEPS = ["sra", "fastqc", "fastp", "star_align", "feature_counts", "normalize",
                      "library_qc", "new_transcripts", "lncrna", "alt_splicing"]
        _req = params.get("steps")
        steps = set(s for s in _req if s in _ALL_STEPS) if _req else set(_ALL_STEPS)

        self.log("=== 一键上游 pipeline 开始 ===")
        self.log(f"workdir: {workdir}")
        self.log(f"本次启用步骤: {[s for s in _ALL_STEPS if s in steps]}")
        
        # ── 步骤 0:SRA 解压(可选)──
        sra_files_in_dir = list((raw_dir).rglob("*.sra"))
        existing_fastq = list((raw_dir).rglob("*.fastq.gz")) + list((raw_dir).rglob("*.fq.gz"))
        
        if "sra" in steps and sra_files_in_dir and not existing_fastq:
            self.update(pct=2, stage="0/7 SRA 解压")
            self.log(f"找到 {len(sra_files_in_dir)} 个 .sra,先解压")
            self._run_subrunner(
                SraDownloadRunner,
                params={
                    "scan_dir": str(raw_dir),
                    "threads_per_sample": 4,
                    "parallel": 2,
                },
                output_subdir=str(raw_dir),
                pct_start=2, pct_end=8,
            )
        
        # ── 步骤 1:FastQC raw(过滤前)──
        qc_raw_dir = workdir / "01_qc" / "raw"
        qc_raw_dir.mkdir(parents=True, exist_ok=True)
        raw_fastqs = []
        for ext in ("fq.gz", "fastq.gz", "fq", "fastq"):
            raw_fastqs.extend(str(p) for p in raw_dir.rglob(f"*.{ext}"))
        if "fastqc" in steps and raw_fastqs:
            self.update(pct=8, stage="1/7 FastQC(raw,过滤前)")
            self._run_subrunner(
                FastqcRunner,
                params={
                    "fastq_files": raw_fastqs,
                    "parallel": 4,
                    "summary_label": "raw",
                },
                output_subdir=str(qc_raw_dir),
                pct_start=8, pct_end=14,
            )
        
        # ── 步骤 2:fastp ──
        if "fastp" in steps:
            self.update(pct=15, stage="2/7 fastp 质量过滤")
            samples = _detect_paired_samples(raw_dir)
            if not samples:
                raise RuntimeError(f"raw/ 目录扫不到 fastq 样本: {raw_dir}")
            self.log(f"识别 {len(samples)} 个样本")

            fp_params = params.get("fastp", {})
            self._run_subrunner(
                FastpRunner,
                params={
                    "samples": samples,
                    "qualified_quality_phred": fp_params.get("q", 15),
                    "unqualified_percent_limit": fp_params.get("u", 40),
                    "length_required": fp_params.get("l", 30),
                    "threads_per_sample": fp_params.get("threads_per_sample", 4),
                    "parallel": fp_params.get("parallel", 2),
                },
                output_subdir=str(trimmed_dir),
                pct_start=15, pct_end=28,
            )
        
        # ── 步骤 3:FastQC trimmed(过滤后)──
        qc_trimmed_dir = workdir / "01_qc" / "trimmed"
        qc_trimmed_dir.mkdir(parents=True, exist_ok=True)
        trimmed_fastqs = []
        for ext in ("fq.gz", "fastq.gz", "fq", "fastq"):
            trimmed_fastqs.extend(str(p) for p in trimmed_dir.rglob(f"*.{ext}"))
        if "fastqc" in steps and trimmed_fastqs:
            self.update(pct=28, stage="3/7 FastQC(trimmed,过滤后)")
            self._run_subrunner(
                FastqcRunner,
                params={
                    "fastq_files": trimmed_fastqs,
                    "parallel": 4,
                    "summary_label": "trimmed",
                },
                output_subdir=str(qc_trimmed_dir),
                pct_start=28, pct_end=30,
            )
        
        # ── 步骤 4:检查/建 STAR Index(支持多读长)──
        # 扫 trimmed/(优先)或 raw/ 看有哪些读长
        from runners._readlength import detect_dir, unique_overhangs
        scan_for_lengths = trimmed_dir if any(trimmed_dir.iterdir()) else raw_dir
        records = detect_dir(scan_for_lengths)
        if records:
            needed_overhangs = unique_overhangs(records)
            self.log(f"检测到读长 → 需要 sjdbOverhang: {needed_overhangs}")
        else:
            needed_overhangs = [100]
            self.log("没扫到读长信息,用默认 overhang=100")
        
        # 检查每个需要的 overhang 索引是否已存在
        # 兼容老格式:如果 index_dir/genomeParameters.txt 直接存在(根目录单索引)→ 当作已有
        missing = []
        if (index_dir / "genomeParameters.txt").exists():
            self.log("检测到老版本单索引(根目录),跳过新建")
            existing_overhangs = "legacy_root"
        else:
            for oh in needed_overhangs:
                sub = index_dir / str(oh)
                if not (sub / "genomeParameters.txt").exists():
                    missing.append(oh)
        
        if "star_align" in steps and not (index_dir / "genomeParameters.txt").exists() and missing:
            self.update(pct=30,
                          stage=f"4/7 STAR Index(建 {len(missing)} 个 overhang)")
            if not fasta or not Path(fasta).is_file():
                raise FileNotFoundError(
                    f"star_index 不存在但也没给 fasta,无法建索引: {fasta}"
                )
            self._run_subrunner(
                StarIndexRunner,
                params={
                    "fasta": fasta,
                    "gtf": gtf,
                    "threads": 8,
                    "sjdb_overhangs": missing,
                },
                output_subdir=str(index_dir),
                pct_start=30, pct_end=44,
            )
        else:
            self.log("STAR Index 都已存在,跳过")
        
        # ── 步骤 5:STAR Align(自动按读长选索引;没跑 fastp 时直接用 raw/)──
        if "star_align" in steps:
            self.update(pct=45, stage="5/7 STAR 比对")
            align_samples = _detect_paired_samples(trimmed_dir)
            if not align_samples:
                # 没跑 fastp / trimmed 为空 → 退回用 raw/ 直接比对
                align_samples = _detect_paired_samples(raw_dir)
                if align_samples:
                    self.log("trimmed/ 无样本,改用 raw/ 直接比对(未跑 fastp)")
            if not align_samples:
                raise RuntimeError("比对没有输入样本:trimmed/ 和 raw/ 都扫不到 fastq")

            sa_params = params.get("star_align", {})
            self._run_subrunner(
                StarAlignRunner,
                params={
                    "index_root": str(index_dir),  # 让 align 自己找子索引
                    "samples": align_samples,
                    "threads": sa_params.get("threads", 8),
                    "quant_mode": "GeneCounts",
                },
                output_subdir=str(aligned_dir),
                pct_start=45, pct_end=74,
            )
        
        # ── 步骤 6:featureCounts + 合并 ──
        if "feature_counts" in steps:
            self.update(pct=75, stage="6/7 featureCounts 量化")
            bams = list(aligned_dir.rglob("*.Aligned.sortedByCoord.out.bam"))
            if not bams:
                raise RuntimeError("aligned/ 没有 BAM,STAR 是否成功?")

            fc_params = params.get("feature_counts", {})
            self._run_subrunner(
                FeatureCountsRunner,
                params={
                    "bam_files": [str(b) for b in bams],
                    "gtf": gtf,
                    "paired": fc_params.get("paired", True),
                    "strand": fc_params.get("strand", 0),
                    "threads": fc_params.get("threads", 8),
                },
                output_subdir=str(counts_dir),
                pct_start=75, pct_end=91,
            )

            # 合并 counts
            self.update(pct=85, stage="7/8 合并 counts")
            self._run_subrunner(
                MergeCountsRunner,
                params={
                    "counts_dir": str(counts_dir),
                    "output_name": "counts_merged.tsv",
                },
                output_subdir=str(counts_dir),
                pct_start=85, pct_end=90,
            )

        # ── 步骤 8:标准化(auto:合并矩阵 + 每样本,默认 TPM/FPKM/CPM)──
        # 组学模块以标准化为终点 —— 产出标准化定量矩阵供分析模块消费。
        # 输出写到 normalized/(和单步标准化、下游读取的目录一致),不是 counts/。
        if "normalize" in steps:
            self.update(pct=91, stage="8/8 标准化")
            self._run_subrunner(
                NormalizeRunner,
                params={
                    "mode": "auto",
                    "counts_dir": str(counts_dir),
                    "gtf": gtf,              # TPM/FPKM 需要基因长度
                    "methods": ["TPM", "FPKM", "CPM"],
                },
                output_subdir=str(normalized_dir),
                pct_start=91, pct_end=99,
            )

        # ── 比对之后的可选分析(各自写自己的子目录,从 aligned/ 自动取 BAM)──
        adv = [s for s in ("library_qc", "new_transcripts", "lncrna", "alt_splicing") if s in steps]
        if adv:
            aligned_bams = sorted(
                str(b) for b in aligned_dir.rglob("*.Aligned.sortedByCoord.out.bam")
            )
            bam_names = [Path(b).name.split(".")[0] for b in aligned_bams]

            if "library_qc" in steps:
                if not aligned_bams:
                    self.log("跳过 文库质控:aligned/ 没有 BAM(没选/没跑比对)")
                else:
                    self.update(pct=99, stage="文库质控(Qualimap)")
                    self._run_subrunner(
                        QualimapRunner,
                        params={"bam_files": aligned_bams, "gtf": gtf,
                                "sample_names": bam_names, "paired": True},
                        output_subdir=str(workdir / "07_library_qc"),
                        pct_start=99, pct_end=100,
                    )

            if "new_transcripts" in steps:
                if not aligned_bams:
                    self.log("跳过 新转录本:aligned/ 没有 BAM")
                else:
                    self.update(pct=99, stage="新转录本(StringTie)")
                    self._run_subrunner(
                        StringtieRunner,
                        params={"bam_files": aligned_bams, "gtf": gtf,
                                "sample_names": bam_names, "strand": 0},
                        output_subdir=str(workdir / "08_new_transcripts"),
                        pct_start=99, pct_end=100,
                    )

            if "lncrna" in steps:
                cand = workdir / "08_new_transcripts" / "merged.gtf"
                if not cand.is_file():
                    self.log(f"跳过 lncRNA:没找到候选转录本 {cand}(需同时勾选/先跑新转录本)")
                elif not fasta or not Path(fasta).is_file():
                    self.log("跳过 lncRNA:项目没配基因组 FASTA")
                else:
                    self.update(pct=99, stage="lncRNA 预测")
                    self._run_subrunner(
                        LncrnaRunner,
                        params={"candidate_gtf": str(cand), "gtf": gtf, "genome_fasta": fasta},
                        output_subdir=str(workdir / "10_lncrna"),
                        pct_start=99, pct_end=100,
                    )

            if "alt_splicing" in steps:
                self.log("可变剪接(rMATS)需要先把样本分成两组,没法在一键顺序里自动跑;"
                         "请在流程图点开「可变剪接」单独配置后运行。")

        self.update(pct=100, stage="完成")
        self.log("=== 一键 pipeline 全部完成(已跑到标准化)===")
        self.log(f"产出: {counts_dir}/counts_merged.tsv")
    
    def _run_subrunner(self, runner_class, params: dict, output_subdir: str,
                       pct_start: float = 0, pct_end: float = 100):
        """临时把 self.job 的 params/output_subdir 改掉,调子 runner 的 run()。
        
        每个子 runner 写日志、更新进度,我们的 self.job 是同一个,所以全程
        日志和进度都连续。

        pct_start/pct_end:这个子步骤在整条 pipeline 里占的全局进度区间。
        子 runner 内部用 update(0..100) 写"本步骤局部进度",经由作用域被压缩进
        [pct_start, pct_end]。这样进度条只前进不倒退(修复以前 STAR 把 45% 覆盖
        成 12% 再爬回去、featureCounts 又重置的跳变问题)。
        """
        # 备份当前 job state
        original_params = self.job.params
        original_output = self.job.output_subdir
        # 设进度作用域:span = pct_end - pct_start
        old_scope = self.push_scope(pct_start, max(0.0, pct_end - pct_start))
        
        try:
            self.job.params = params
            self.job.output_subdir = output_subdir
            
            sub = runner_class()
            sub.job = self.job  # 共享 job,日志/进度都到一起
            sub.data_dir = self.data_dir
            # 子 runner 继承同一进度作用域 + CPU 配额
            sub._pct_base = self._pct_base
            sub._pct_span = self._pct_span
            sub._thread_quota = self._thread_quota
            sub.run()
        finally:
            self.job.params = original_params
            self.job.output_subdir = original_output
            self.pop_scope(old_scope)


if __name__ == "__main__":
    PipelineUpstreamRunner.main()
