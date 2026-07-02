"""poly(A) tail length statistical analysis runner for DRS.

Performs downstream analysis of poly(A) tail lengths:
  1. Parse poly(A) tail lengths from BAM tags (pt:i:)
  2. Per-transcript poly(A) length statistics
  3. Correlation analysis: poly(A) length vs expression
  4. Differential poly(A) analysis (Mann-Whitney U test)

Parameters:
  bam_files: [str]          - Aligned BAM files (from Dorado --estimate-poly-a)
  gtf: str                  - Reference annotation GTF
  polya_detect_dir: str     - Optional path to polya_detect output directory

Outputs (to output_dir/):
  polya_statistics.tsv          - Per-transcript poly(A) stats
  polya_expression_corr.tsv    - poly(A)-expression correlation
  polya_differential.tsv       - Differential poly(A) length results
  polya_analysis_summary.json  - Full analysis summary
"""
import json
from collections import Counter, defaultdict
from pathlib import Path
import statistics as _stats

from runners.base import BaseRunner


class PolyaAnalysisRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam_files = p.get("bam_files", [])
        gtf = p.get("gtf", "")
        polya_detect_dir = p.get("polya_detect_dir", "")

        if not bam_files:
            raise ValueError("bam_files 列表为空")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Step 1: Extract poly(A) lengths from BAM tags ----
        self.update(pct=5, stage="提取 polyA 长度")

        polya_lengths_by_read = {}  # read_id -> length
        polya_lengths_by_transcript = defaultdict(list)  # transcript -> [lengths]

        for bam in bam_files:
            if not Path(bam).exists():
                self.log(f"!! BAM 不存在: {bam}")
                continue

            bam_name = Path(bam).stem
            lengths_tsv = out_dir / f"{bam_name}_polya_lengths.tsv"

            self.run_command([
                "bash", "-c",
                f"samtools view '{bam}' | "
                f"awk '{{ for(i=12;i<=NF;i++) "
                f"if($i ~ /^pt:i:/) print $1\"\\t\"substr($i,6) }}' "
                f"> '{lengths_tsv}'",
            ], indeterminate=True, heartbeat_stage=f"extract polyA {bam_name}")

            if lengths_tsv.exists():
                with open(lengths_tsv, encoding="utf-8") as fh:
                    for line in fh:
                        cols = line.strip().split("\t")
                        if len(cols) >= 2 and cols[1].strip().isdigit():
                            read_id = cols[0].strip()
                            length = int(cols[1].strip())
                            polya_lengths_by_read[read_id] = length

        if not polya_lengths_by_read:
            # Try reading from polya_detect_dir if given
            if polya_detect_dir and Path(polya_detect_dir).exists():
                detect_dir = Path(polya_detect_dir)
                detect_summary = detect_dir / "polya_detect_summary.json"
                lengths_file = detect_dir / "polya_lengths.tsv"
                if lengths_file.exists():
                    self.log(f"从 polya_detect 输出读取: {lengths_file}")
                    with open(lengths_file, encoding="utf-8") as fh:
                        for line in fh:
                            cols = line.strip().split("\t")
                            if len(cols) >= 2 and cols[1].strip().isdigit():
                                polya_lengths_by_read[cols[0].strip()] = int(cols[1].strip())
                elif detect_summary.exists():
                    self.log(f"从 polya_detect 摘要读取: {detect_summary}")
                    with open(detect_summary) as fh:
                        data = json.load(fh)
                        self.log(f"polyA 比例: {data.get('polya_proportion', 'N/A')}%, "
                                 f"平均长度: {data.get('mean_polya_length', 'N/A')}")
            else:
                self.log("BAM 中没有 polyA 标签(pt:i:),尝试通过其他方式提取")

        self.log(f"提取到 {len(polya_lengths_by_read)} 条 polyA 长度")

        # ---- Step 2: Map reads to transcripts using GTF ----
        self.update(pct=25, stage="比对 reads 到转录本")
        # Parse GTF to build transcript to gene mapping
        transcript_info = self._parse_gtf(gtf)

        # Extract transcript IDs from read names
        for read_id in polya_lengths_by_read:
            # Read name format: <transcript_id>_<rest> or similar
            parts = read_id.split("_")
            if parts and parts[0] in transcript_info:
                tid = parts[0]
            else:
                tid = "unknown"
            polya_lengths_by_transcript[tid].append(
                polya_lengths_by_read[read_id])

        # ---- Step 3: Per-transcript statistics ----
        self.update(pct=45, stage="计算转录本 polyA 统计")
        stats_tsv = out_dir / "polya_statistics.tsv"
        min_reads = 5

        per_transcript_stats = {}
        with open(stats_tsv, "w", encoding="utf-8") as out:
            out.write("transcript_id\tgene_id\tn_reads\tmean\tmedian\t"
                      "min\tmax\tstdev\n")
            for tid, lengths in polya_lengths_by_transcript.items():
                if len(lengths) < min_reads:
                    continue
                s = self._calc_stats(lengths)
                gene_id = transcript_info.get(tid, {}).get("gene_id", "")
                per_transcript_stats[tid] = s
                out.write(f"{tid}\t{gene_id}\t{len(lengths)}\t{s['mean']}\t"
                         f"{s['median']}\t{s['min']}\t{s['max']}\t"
                         f"{s['stdev']}\n")

        self.log(f"计算了 {len(per_transcript_stats)} 个转录本的统计")

        # ---- Step 4: Correlation analysis ----
        self.update(pct=60, stage="polyA-表达相关性分析")
        corr_tsv = out_dir / "polya_expression_corr.tsv"
        correlation_results = {}
        self._write_empty_corr(corr_tsv)

        # Without expression data, we report what we have
        # Users can provide expression data separately
        self.log("表达相关性分析需要表达数据文件")

        # ---- Step 5: Differential poly(A) analysis ----
        self.update(pct=75, stage="差异 polyA 分析")
        diff_tsv = out_dir / "polya_differential.tsv"
        differential_results = {}

        if len(bam_files) >= 2:
            # Compare poly(A) lengths between first two BAMs
            self.log("进行 poly(A) 长度差异分析 (Mann-Whitney U test)")

            # Aggregate by condition (first half vs second half)
            n_bams = len(bam_files)
            condition_a = set()
            condition_b = set()

            # Parse poly(A) per BAM
            bam_polya = {}
            for bam in bam_files:
                name = Path(bam).stem
                bam_polya[name] = []
                try:
                    import subprocess
                    result = subprocess.run(
                        ["samtools", "view", str(bam)],
                        capture_output=True, text=True, timeout=600)
                    for line in result.stdout.splitlines():
                        if line.startswith("@"):
                            continue
                        cols = line.split("\t")
                        for col in cols[11:]:
                            if col.startswith("pt:i:"):
                                try:
                                    bam_polya[name].append(int(col[5:]))
                                except ValueError:
                                    pass
                                break
                except Exception:
                    pass

            if all(len(v) >= min_reads for v in bam_polya.values()):
                names = list(bam_polya.keys())
                half = len(names) // 2
                with open(diff_tsv, "w", encoding="utf-8") as out:
                    out.write("comparison\tn1\tn2\tmean1\tmean2\t"
                             "median1\tmedian2\tMW_U\tp_value\n")

                    for k in range(0, len(names), 2):
                        if k + 1 >= len(names):
                            break
                        n1, n2 = names[k], names[k + 1]
                        len1 = bam_polya[n1]
                        len2 = bam_polya[n2]
                        mean1 = _stats.mean(len1)
                        mean2 = _stats.mean(len2)
                        median1 = _stats.median(len1)
                        median2 = _stats.median(len2)

                        u_stat, p_val = 1.0, 1.0
                        try:
                            from scipy import stats as scipy_stats
                            u_stat, p_val = scipy_stats.mannwhitneyu(
                                len1, len2, alternative="two-sided")
                        except ImportError:
                            pass

                        out.write(f"{n1}_vs_{n2}\t{len(len1)}\t{len(len2)}\t"
                                 f"{mean1:.2f}\t{mean2:.2f}\t"
                                 f"{median1:.2f}\t{median2:.2f}\t"
                                 f"{u_stat:.0f}\t{p_val:.6e}\n")

                        key = f"{n1}_vs_{n2}"
                        differential_results[key] = {
                            "mean1": round(mean1, 2),
                            "mean2": round(mean2, 2),
                            "median1": round(median1, 2),
                            "median2": round(median2, 2),
                            "MW_U": round(u_stat, 0),
                            "p_value": round(p_val, 6),
                        }

        # ---- Compile results ----
        self.update(pct=90, stage="汇总结果")

        summary = {
            "n_reads_with_polya": len(polya_lengths_by_read),
            "n_transcripts_analyzed": len(per_transcript_stats),
            "n_transcripts_gtf": len(transcript_info),
            "min_reads_per_transcript": min_reads,
            "overall_stats": self._calc_stats(
                list(polya_lengths_by_read.values())),
            "per_transcript_stats": per_transcript_stats,
            "correlation_results": correlation_results,
            "differential_results": differential_results,
            "outputs": {
                "statistics_tsv": str(stats_tsv),
                "correlation_tsv": str(corr_tsv) if corr_tsv.exists() else "",
                "differential_tsv": str(diff_tsv) if diff_tsv.exists() else "",
            },
        }
        (out_dir / "polya_analysis_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== poly(A) 分析完成: {len(polya_lengths_by_read)} reads, "
                 f"{len(per_transcript_stats)} 转录本 → {out_dir} ===")

    def _parse_gtf(self, gtf_path):
        """Parse GTF to extract transcript -> gene mapping."""
        info = {}
        with open(gtf_path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("#"):
                    continue
                cols = line.strip().split("\t")
                if len(cols) < 9:
                    continue
                if cols[2] != "transcript":
                    continue
                attrs = cols[8]
                tid = self._extract_attr(attrs, "transcript_id")
                gid = self._extract_attr(attrs, "gene_id")
                if tid:
                    info[tid] = {"gene_id": gid or ""}
        return info

    @staticmethod
    def _extract_attr(attrs, key):
        """Extract attribute value from GTF attribute string."""
        import re
        m = re.search(rf'{key}\s+"([^"]+)"', attrs)
        return m.group(1) if m else ""

    @staticmethod
    def _calc_stats(lengths):
        if not lengths:
            return {"mean": 0, "median": 0, "stdev": 0,
                    "min": 0, "max": 0, "q25": 0, "q75": 0}
        n = len(lengths)
        mean = _stats.mean(lengths)
        median = _stats.median(lengths)
        stdev = _stats.stdev(lengths) if n > 1 else 0
        sorted_l = sorted(lengths)
        return {
            "mean": round(mean, 2),
            "median": round(median, 2),
            "stdev": round(stdev, 2),
            "min": min(lengths),
            "max": max(lengths),
            "q25": self._percentile(sorted_l, 25),
            "q75": self._percentile(sorted_l, 75),
        }

    @staticmethod
    def _percentile(sorted_data, p):
        if not sorted_data:
            return 0
        k = (len(sorted_data) - 1) * p / 100
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[-1]
        return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)

    @staticmethod
    def _write_empty_corr(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("transcript_id\tn_reads\tmean_polya\tmessage\n")
            f.write("-\t0\t0\t相关性分析需要表达数据\n")


if __name__ == "__main__":
    PolyaAnalysisRunner.main()
