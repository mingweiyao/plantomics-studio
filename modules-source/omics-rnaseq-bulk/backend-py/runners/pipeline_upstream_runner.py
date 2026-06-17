"""一键运行上游(到标准化)。

任务流(顺序对应转录组标准流程):
  0. SRA 解压(raw/ 有 .sra 但没 fastq 时)
  1. FastQC(raw,过滤前)
  2. fastp 过滤(读 raw/,写 trimmed/)
  3. FastQC(trimmed,过滤后)
  4. 数据量统计(解析 fastp JSON → trimmed/stat.all.txt,报告 5.1.1)
  5. 检查/建 STAR Index(已存在则跳过)
  6. STAR Align(读 trimmed/,写 aligned/)
  7. 比对率统计(解析 STAR Log.final.out → aligned/align_stat.txt,报告 5.2.1)
  8. featureCounts(读 aligned/,写 counts/<sample>.tsv)+ 合并
  9. 标准化(TPM/FPKM/CPM → normalized/)
  之后按需:
  10. 新转录本(StringTie)
  11. 新转录本编码区预测(TransDecoder)
  12. 可变剪接(rMATS,需先分两组,无法在一键里自动跑)
  13. lncRNA 预测(CPC2 + PLEK)

每一步内部直接调对应 runner 的核心逻辑(不再走 HTTP/job 系统),
保持单 job 但日志清楚分阶段。

参数:
  workdir:        项目工作目录(必填)
  fasta:          参考 FASTA(STAR Index 用,如果索引已存在可省)
  gtf:            参考 GTF(STAR Index + featureCounts 必填)
  total_threads:  本任务总线程预算(可选;调度器据此 ÷ 并行度算每步线程)
  fastp:          {q, u, l, threads_per_sample, parallel} 等(可选)
  feature_counts: {paired, strand}(可选)
"""
import re
from pathlib import Path

from runners.base import BaseRunner

# 引入各 runner 类(直接实例化跑核心逻辑,不重新 fork 进程,省时间)
from runners.sra_download_runner import SraDownloadRunner
from runners.fastp_runner import FastpRunner
from runners.fastqc_runner import FastqcRunner
from runners.data_volume_stats_runner import DataVolumeStatsRunner
from runners.star_index_runner import StarIndexRunner
from runners.star_align_runner import StarAlignRunner
from runners.align_stats_runner import AlignStatsRunner
from runners.feature_counts_runner import FeatureCountsRunner
from runners.merge_counts_runner import MergeCountsRunner
from runners.normalize_runner import NormalizeRunner
from runners.stringtie_runner import StringtieRunner
from runners.transdecoder_runner import TransdecoderRunner
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
        qc_raw_dir = workdir / "01_qc" / "raw"
        qc_trimmed_dir = workdir / "01_qc" / "trimmed"
        trimmed_dir = workdir / "02_trimmed"
        index_dir = workdir / "03_star_index"
        aligned_dir = workdir / "04_aligned"
        counts_dir = workdir / "05_counts"
        normalized_dir = workdir / "06_normalized"
        for d in (raw_dir, qc_raw_dir, qc_trimmed_dir, trimmed_dir, index_dir,
                  aligned_dir, counts_dir, normalized_dir):
            d.mkdir(parents=True, exist_ok=True)

        # 用户选择要跑哪些步骤(不传 steps = 全跑,向后兼容)。
        # star_index 不是独立勾选项:跑 star_align 时会自动按需建索引。
        # alt_splicing(rMATS)要先分两组样本,无法在一键顺序里自动跑。
        _ALL_STEPS = [
            "sra", "fastqc_raw", "fastp", "fastqc_trimmed", "data_volume_stats",
            "star_align", "align_stats", "feature_counts", "normalize",
            "new_transcripts", "transdecoder", "alt_splicing", "lncrna",
        ]
        _req = params.get("steps")
        steps = set(s for s in _req if s in _ALL_STEPS) if _req else set(_ALL_STEPS)

        self.log("=== 一键上游 pipeline 开始 ===")
        self.log(f"workdir: {workdir}")
        self.log(f"本次启用步骤: {[s for s in _ALL_STEPS if s in steps]}")

        # ── SRA 解压(可选)──
        sra_files_in_dir = list(raw_dir.rglob("*.sra"))
        existing_fastq = list(raw_dir.rglob("*.fastq.gz")) + list(raw_dir.rglob("*.fq.gz"))
        if "sra" in steps and sra_files_in_dir and not existing_fastq:
            self._pipeline_step = "sra"
            self.update(pct=2, stage="SRA 解压")
            self.log(f"找到 {len(sra_files_in_dir)} 个 .sra,先解压")
            self._run_subrunner(
                SraDownloadRunner,
                params={"scan_dir": str(raw_dir),
                        "threads_per_sample": 4, "parallel": 2},
                output_subdir=str(raw_dir),
                pct_start=2, pct_end=8,
            )

        # ── FastQC raw(过滤前)──
        raw_fastqs = []
        for ext in ("fq.gz", "fastq.gz", "fq", "fastq"):
            raw_fastqs.extend(str(p) for p in raw_dir.rglob(f"*.{ext}"))
        if "fastqc_raw" in steps and raw_fastqs:
            self._pipeline_step = "fastqc_raw"
            self.update(pct=8, stage="FastQC(raw,过滤前)")
            self._run_subrunner(
                FastqcRunner,
                params={"fastq_files": raw_fastqs, "parallel": 4,
                        "summary_label": "raw"},
                output_subdir=str(qc_raw_dir),
                pct_start=8, pct_end=13,
            )

        # ── fastp ──
        if "fastp" in steps:
            self._pipeline_step = "fastp"
            self.update(pct=13, stage="fastp 质量过滤")
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
                pct_start=13, pct_end=24,
            )

        # ── FastQC trimmed(过滤后)──
        trimmed_fastqs = []
        for ext in ("fq.gz", "fastq.gz", "fq", "fastq"):
            trimmed_fastqs.extend(str(p) for p in trimmed_dir.rglob(f"*.{ext}"))
        if "fastqc_trimmed" in steps and trimmed_fastqs:
            self._pipeline_step = "fastqc_trimmed"
            self.update(pct=24, stage="FastQC(trimmed,过滤后)")
            self._run_subrunner(
                FastqcRunner,
                params={"fastq_files": trimmed_fastqs, "parallel": 4,
                        "summary_label": "trimmed"},
                output_subdir=str(qc_trimmed_dir),
                pct_start=24, pct_end=28,
            )

        # ── 数据量统计(解析 fastp JSON,报告 5.1.1)──
        if "data_volume_stats" in steps and list(trimmed_dir.rglob("*.fastp.json")):
            self._pipeline_step = "fastqc_trimmed"
            self.update(pct=28, stage="数据量统计")
            self._run_subrunner(
                DataVolumeStatsRunner,
                params={"trimmed_dir": str(trimmed_dir)},
                output_subdir=str(trimmed_dir),
                pct_start=28, pct_end=31,
            )
        elif "data_volume_stats" in steps:
            self.log("跳过 数据量统计:trimmed/ 没有 fastp JSON(没跑/没选 fastp)")

        # ── 检查/建 STAR Index(支持多读长)──
        from runners._readlength import detect_dir, unique_overhangs
        scan_for_lengths = trimmed_dir if any(trimmed_dir.iterdir()) else raw_dir
        records = detect_dir(scan_for_lengths)
        if records:
            needed_overhangs = unique_overhangs(records)
            self.log(f"检测到读长 → 需要 sjdbOverhang: {needed_overhangs}")
        else:
            needed_overhangs = [100]
            self.log("没扫到读长信息,用默认 overhang=100")

        # 检查每个需要的 overhang 索引是否已存在;已存在则不重建
        missing = []
        if (index_dir / "genomeParameters.txt").exists():
            self.log("检测到老版本单索引(根目录),跳过新建")
        else:
            for oh in needed_overhangs:
                if not (index_dir / str(oh) / "genomeParameters.txt").exists():
                    missing.append(oh)

        if "star_align" in steps and not (index_dir / "genomeParameters.txt").exists() and missing:
            self._pipeline_step = "star_index"
            self.update(pct=31, stage=f"STAR Index(建 {len(missing)} 个 overhang)")
            if not fasta or not Path(fasta).is_file():
                raise FileNotFoundError(
                    f"star_index 不存在但也没给 fasta,无法建索引: {fasta}")
            self._run_subrunner(
                StarIndexRunner,
                params={"fasta": fasta, "gtf": gtf, "sjdb_overhangs": missing},
                output_subdir=str(index_dir),
                pct_start=31, pct_end=44,
            )
        else:
            self.log("STAR Index 都已存在或本次不比对,跳过建索引")

        # ── STAR Align(自动按读长选索引;没跑 fastp 时直接用 raw/)──
        if "star_align" in steps:
            self._pipeline_step = "star_align"
            self.update(pct=44, stage="STAR 比对")
            align_samples = _detect_paired_samples(trimmed_dir)
            if not align_samples:
                align_samples = _detect_paired_samples(raw_dir)
                if align_samples:
                    self.log("trimmed/ 无样本,改用 raw/ 直接比对(未跑 fastp)")
            if not align_samples:
                raise RuntimeError("比对没有输入样本:trimmed/ 和 raw/ 都扫不到 fastq")
            self._run_subrunner(
                StarAlignRunner,
                params={"index_root": str(index_dir), "samples": align_samples,
                        "quant_mode": "GeneCounts"},
                output_subdir=str(aligned_dir),
                pct_start=44, pct_end=68,
            )

        # ── 比对率统计(解析 STAR Log.final.out,报告 5.2.1)──
        if "align_stats" in steps and list(aligned_dir.rglob("*.Log.final.out")):
            self._pipeline_step = "star_align"
            self.update(pct=68, stage="比对率统计")
            self._run_subrunner(
                AlignStatsRunner,
                params={"aligned_dir": str(aligned_dir)},
                output_subdir=str(aligned_dir),
                pct_start=68, pct_end=71,
            )
        elif "align_stats" in steps:
            self.log("跳过 比对率统计:aligned/ 没有 Log.final.out(没跑/没选比对)")

        # ── featureCounts + 合并 ──
        if "feature_counts" in steps:
            self._pipeline_step = "feature_counts"
            self.update(pct=71, stage="featureCounts 量化")
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
                },
                output_subdir=str(counts_dir),
                pct_start=71, pct_end=83,
            )
            self._pipeline_step = "feature_counts"
            self.update(pct=83, stage="合并 counts")
            self._run_subrunner(
                MergeCountsRunner,
                params={"counts_dir": str(counts_dir),
                        "output_name": "counts_merged.tsv"},
                output_subdir=str(counts_dir),
                pct_start=83, pct_end=86,
            )

        # ── 标准化(组学模块以标准化为终点,产出供分析模块消费)──
        if "normalize" in steps:
            self._pipeline_step = "normalize"
            self.update(pct=86, stage="标准化")
            self._run_subrunner(
                NormalizeRunner,
                params={"mode": "auto", "counts_dir": str(counts_dir),
                        "gtf": gtf, "methods": ["TPM", "FPKM", "CPM"]},
                output_subdir=str(normalized_dir),
                pct_start=86, pct_end=92,
            )

        # ── 标准化之后的可选分析 ──
        aligned_bams = sorted(
            str(b) for b in aligned_dir.rglob("*.Aligned.sortedByCoord.out.bam"))
        bam_names = [Path(b).name.split(".")[0] for b in aligned_bams]
        merged_gtf = workdir / "08_new_transcripts" / "merged.gtf"

        if "new_transcripts" in steps:
            if not aligned_bams:
                self.log("跳过 新转录本:aligned/ 没有 BAM")
            else:
                self._pipeline_step = "new_transcripts"
                self.update(pct=92, stage="新转录本(StringTie)")
                self._run_subrunner(
                    StringtieRunner,
                    params={"bam_files": aligned_bams, "gtf": gtf,
                            "sample_names": bam_names, "strand": 0},
                    output_subdir=str(workdir / "08_new_transcripts"),
                    pct_start=92, pct_end=95,
                )

        if "transdecoder" in steps:
            if not merged_gtf.is_file():
                self.log(f"跳过 TransDecoder:没找到 {merged_gtf}(需先跑新转录本)")
            elif not fasta or not Path(fasta).is_file():
                self.log("跳过 TransDecoder:项目没配基因组 FASTA")
            else:
                self._pipeline_step = "transdecoder"
                self.update(pct=95, stage="新转录本编码区(TransDecoder)")
                self._run_subrunner(
                    TransdecoderRunner,
                    params={"candidate_gtf": str(merged_gtf), "genome_fasta": fasta},
                    output_subdir=str(workdir / "08_new_transcripts" / "transdecoder"),
                    pct_start=95, pct_end=97,
                )

        if "alt_splicing" in steps:
            self.log("可变剪接(rMATS)需要先把样本分成两组,没法在一键顺序里自动跑;"
                     "请在流程图点开「可变剪接」单独配置后运行。")

        if "lncrna" in steps:
            if not merged_gtf.is_file():
                self.log(f"跳过 lncRNA:没找到候选转录本 {merged_gtf}(需先跑新转录本)")
            elif not fasta or not Path(fasta).is_file():
                self.log("跳过 lncRNA:项目没配基因组 FASTA")
            else:
                self._pipeline_step = "lncrna"
                self.update(pct=97, stage="lncRNA 预测(CPC2 + PLEK)")
                self._run_subrunner(
                    LncrnaRunner,
                    params={"candidate_gtf": str(merged_gtf), "genome_fasta": fasta},
                    output_subdir=str(workdir / "10_lncrna"),
                    pct_start=97, pct_end=100,
                )

        self.update(pct=100, stage="完成")
        self.log("=== 一键 pipeline 全部完成 ===")

    def _run_subrunner(self, runner_class, params: dict, output_subdir: str,
                       pct_start: float = 0, pct_end: float = 100):
        """临时把 self.job 的 params/output_subdir 改掉,调子 runner 的 run()。

        每个子 runner 写日志、更新进度,我们的 self.job 是同一个,所以全程
        日志和进度都连续。

        pct_start/pct_end:这个子步骤在整条 pipeline 里占的全局进度区间。
        子 runner 内部用 update(0..100) 写"本步骤局部进度",经由作用域被压缩进
        [pct_start, pct_end]。这样进度条只前进不倒退。
        """
        original_params = self.job.params
        original_output = self.job.output_subdir
        old_scope = self.push_scope(pct_start, max(0.0, pct_end - pct_start))

        try:
            self.job.params = params
            self.job.output_subdir = output_subdir

            sub = runner_class()
            sub.job = self.job  # 共享 job,日志/进度都到一起
            sub.data_dir = self.data_dir
            # 子 runner 继承同一进度作用域 + CPU 配额 + 当前流程节点
            sub._pct_base = self._pct_base
            sub._pct_span = self._pct_span
            sub._thread_quota = self._thread_quota
            sub._pipeline_step = self._pipeline_step
            sub.run()
        finally:
            self.job.params = original_params
            self.job.output_subdir = original_output
            self.pop_scope(old_scope)


if __name__ == "__main__":
    PipelineUpstreamRunner.main()
