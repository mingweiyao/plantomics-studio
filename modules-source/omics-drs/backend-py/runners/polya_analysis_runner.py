"""poly(A) tail length statistical analysis runner.

Performs downstream analysis of poly(A) tail lengths:
  1. Basic statistics per sample/group
  2. Expression correlation analysis
  3. Differential poly(A) length analysis (Mann-Whitney U test)
  4. Visualization data preparation

Parameters:
  polya_tsv: str            - Combined poly(A) results TSV (from polya_runner)
  groups: dict              - Sample-group mapping {group_name: [sample_names]}
  comparisons: list         - Pairwise comparisons [["group1","group2"]]
  min_reads: int           - Minimum reads per transcript for analysis (default 5)
  significance: float      - P-value threshold (default 0.05)
  expression_file: str     - Optional TPM/expression file for correlation

Outputs (to output_dir/):
  polya_statistics.tsv         - Per-group poly(A) statistics
  polya_expression_corr.tsv   - poly(A)-expression correlation
  polya_differential.tsv      - Differential poly(A) length results
  polya_analysis_results.json - Full analysis summary
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class PolyaAnalysisRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        polya_tsv = p.get("polya_tsv", "")
        groups = p.get("groups", {})
        comparisons = p.get("comparisons", [])
        min_reads = int(p.get("min_reads", 5))
        significance = float(p.get("significance", 0.05))
        expression_file = p.get("expression_file", "")

        if not polya_tsv or not Path(polya_tsv).exists():
            raise FileNotFoundError(f"poly(A) TSV 不存在: {polya_tsv}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Step 1: Load and parse poly(A) data ----
        self.update(pct=5, stage="加载 polyA 数据")

        import csv
        polya_data = {}  # {read_id: {read_id, sample, polya_len, transcript?}}
        with open(polya_tsv, encoding="utf-8") as fh:
            reader = csv.reader(fh, delimiter="\t")
            header = next(reader, None)
            if not header:
                raise ValueError("空的 polyA TSV 文件")

            col_map = {c: i for i, c in enumerate(header)}

            read_id_col = col_map.get("read_id", 0)
            polya_col = col_map.get("polya_length", 1)
            sample_col = col_map.get("sample", None)
            transcript_col = col_map.get("transcript_id", None)

            for row in reader:
                if len(row) <= max(read_id_col, polya_col):
                    continue
                read_id = row[read_id_col].strip()
                try:
                    length = int(row[polya_col].strip())
                except (ValueError, IndexError):
                    continue
                sample = row[sample_col].strip() if sample_col is not None and len(row) > sample_col else "default"
                transcript = row[transcript_col].strip() if transcript_col is not None and len(row) > transcript_col else ""

                polya_data[read_id] = {
                    "read_id": read_id,
                    "length": length,
                    "sample": sample,
                    "transcript": transcript,
                }

        if not polya_data:
            raise ValueError("未解析到任何 poly(A) 数据")

        self.log(f"加载了 {len(polya_data)} 条 poly(A) 数据")

        # ---- Step 2: Per-group statistics ----
        self.update(pct=20, stage="分组统计")

        # Organize by sample
        sample_lengths = {}
        for read_id, info in polya_data.items():
            sample = info["sample"]
            if sample not in sample_lengths:
                sample_lengths[sample] = []
            sample_lengths[sample].append(info["length"])

        # Organize by group
        group_lengths = {}
        if groups:
            for group_name, sample_list in groups.items():
                group_lengths[group_name] = []
                for s in sample_list:
                    group_lengths[group_name].extend(
                        sample_lengths.get(s, []))

        # Write per-group statistics
        stats_tsv = out_dir / "polya_statistics.tsv"
        with open(stats_tsv, "w", encoding="utf-8") as out:
            out.write("entity\ttype\tn_reads\tmean\tmedian\tstdev\tmin\tmax\tq25\tq75\n")

            # Per sample
            for sample, lengths in sorted(sample_lengths.items()):
                s = self._calc_stats(lengths)
                out.write(f"{sample}\tsample\t{len(lengths)}\t{s['mean']}\t"
                         f"{s['median']}\t{s['stdev']}\t{s['min']}\t"
                         f"{s['max']}\t{s['q25']}\t{s['q75']}\n")

            # Per group
            for group, lengths in sorted(group_lengths.items()):
                s = self._calc_stats(lengths)
                out.write(f"{group}\tgroup\t{len(lengths)}\t{s['mean']}\t"
                         f"{s['median']}\t{s['stdev']}\t{s['min']}\t"
                         f"{s['max']}\t{s['q25']}\t{s['q75']}\n")

        # ---- Step 3: Expression correlation ----
        self.update(pct=40, stage="表达相关分析")
        corr_tsv = out_dir / "polya_expression_corr.tsv"

        correlation_results = {}
        if expression_file and Path(expression_file).exists():
            # Load expression data
            expr_data = {}  # {transcript: {sample: tpm}}
            with open(expression_file, encoding="utf-8") as fh:
                reader = csv.reader(fh, delimiter="\t")
                expr_header = next(reader, None)
                if expr_header:
                    samples_in_expr = expr_header[1:]
                    for row in reader:
                        tid = row[0].strip()
                        values = []
                        for v in row[1:]:
                            try:
                                values.append(float(v))
                            except ValueError:
                                values.append(0.0)
                        if sum(values) > 0:
                            expr_data[tid] = dict(zip(samples_in_expr, values))

            with open(corr_tsv, "w", encoding="utf-8") as out:
                out.write("transcript_id\tn_reads\tmean_polya\t"
                         "mean_expression\tcorrelation\tp_value\n")

                # Aggregate poly(A) by transcript
                transcript_polya = {}
                for info in polya_data.values():
                    tid = info.get("transcript", "")
                    if not tid:
                        continue
                    if tid not in transcript_polya:
                        transcript_polya[tid] = []
                    transcript_polya[tid].append(info["length"])

                from scipy import stats as scipy_stats
                has_scipy = True

                for tid, lengths in transcript_polya.items():
                    if len(lengths) < min_reads:
                        continue
                    if tid not in expr_data:
                        continue

                    avg_polya = sum(lengths) / len(lengths)
                    avg_expr = sum(expr_data[tid].values()) / len(expr_data[tid])

                    # Simple pearson correlation on per-sample basis
                    shared_samples = set(sample_lengths.keys()) & set(expr_data[tid].keys())
                    if len(shared_samples) >= 3:
                        polya_vals = []
                        expr_vals = []
                        for ss in shared_samples:
                            ss_lengths = sample_lengths.get(ss, [])
                            if ss_lengths:
                                polya_vals.append(sum(ss_lengths) / len(ss_lengths))
                                expr_vals.append(expr_data[tid][ss])

                        if len(polya_vals) >= 3 and has_scipy:
                            try:
                                r, p_val = scipy_stats.pearsonr(polya_vals, expr_vals)
                            except Exception:
                                r, p_val = 0, 1.0
                        else:
                            r, p_val = 0, 1.0

                        out.write(f"{tid}\t{len(lengths)}\t{avg_polya:.2f}\t"
                                 f"{avg_expr:.4f}\t{r:.4f}\t{p_val:.6f}\n")
                        correlation_results[tid] = {
                            "n_reads": len(lengths),
                            "mean_polya": round(avg_polya, 2),
                            "mean_expression": round(avg_expr, 4),
                            "correlation": round(r, 4),
                            "p_value": round(p_val, 6),
                        }
        else:
            self.log("未提供表达数据文件,跳过相关性分析")

        # ---- Step 4: Differential poly(A) analysis (Mann-Whitney U) ----
        self.update(pct=60, stage="差异 polyA 分析")
        diff_tsv = out_dir / "polya_differential.tsv"

        differential_results = {}
        if groups and comparisons:
            from scipy import stats as scipy_stats
            has_scipy = True

            with open(diff_tsv, "w", encoding="utf-8") as out:
                out.write("transcript_id\tgroup1\tgroup2\t"
                         "mean1\tmean2\tmedian1\tmedian2\t"
                         "log2FC\tMW_U_statistic\tp_value\tsignificant\n")

                # Aggregate poly(A) by transcript and group
                trans_group_lengths = {}
                for info in polya_data.values():
                    tid = info.get("transcript", "")
                    sample = info["sample"]
                    if not tid:
                        continue
                    # Determine group
                    for gname, samples in groups.items():
                        if sample in samples:
                            if tid not in trans_group_lengths:
                                trans_group_lengths[tid] = {}
                            if gname not in trans_group_lengths[tid]:
                                trans_group_lengths[tid][gname] = []
                            trans_group_lengths[tid][gname].append(info["length"])
                            break

                for g1, g2 in comparisons:
                    if g1 not in group_lengths or g2 not in group_lengths:
                        self.log(f"跳过比较 {g1} vs {g2}: 组数据缺失")
                        continue

                    for tid, group_data in trans_group_lengths.items():
                        len1 = group_data.get(g1, [])
                        len2 = group_data.get(g2, [])
                        if len(len1) < min_reads or len(len2) < min_reads:
                            continue

                        mean1 = sum(len1) / len(len1)
                        mean2 = sum(len2) / len(len2)

                        import statistics as _stats
                        try:
                            median1 = _stats.median(len1)
                            median2 = _stats.median(len2)
                        except Exception:
                            median1, median2 = mean1, mean2

                        # Log2 fold change
                        log2fc = 0.0
                        if mean1 > 0 and mean2 > 0:
                            import math
                            log2fc = math.log2(mean1 / mean2)

                        # Mann-Whitney U test
                        u_stat, p_val = 0, 1.0
                        if has_scipy:
                            try:
                                u_stat, p_val = scipy_stats.mannwhitneyu(
                                    len1, len2, alternative="two-sided")
                            except Exception:
                                pass

                        significant = p_val < significance

                        out.write(f"{tid}\t{g1}\t{g2}\t"
                                 f"{mean1:.2f}\t{mean2:.2f}\t"
                                 f"{median1:.2f}\t{median2:.2f}\t"
                                 f"{log2fc:.4f}\t{u_stat:.0f}\t"
                                 f"{p_val:.6e}\t{'yes' if significant else 'no'}\n")

                        key = f"{tid}|{g1}_vs_{g2}"
                        differential_results[key] = {
                            "transcript": tid,
                            "group1": g1,
                            "group2": g2,
                            "mean1": round(mean1, 2),
                            "mean2": round(mean2, 2),
                            "log2FC": round(log2fc, 4),
                            "MW_U": round(u_stat, 0),
                            "p_value": round(p_val, 6),
                            "significant": significant,
                        }
        else:
            self.log("未提供分组/比较信息,跳过差异分析")

        # ---- Compile results ----
        self.update(pct=85, stage="汇总结果")

        n_sig = sum(1 for r in differential_results.values()
                    if r.get("significant"))
        n_corr = sum(1 for r in correlation_results.values()
                     if abs(r.get("correlation", 0)) > 0.5)

        results = {
            "n_total_reads": len(polya_data),
            "n_samples_with_data": len(sample_lengths),
            "n_groups": len(group_lengths),
            "n_comparisons": len(comparisons),
            "n_differential_transcripts": n_sig,
            "n_significant_correlations": n_corr,
            "min_reads_per_transcript": min_reads,
            "significance_threshold": significance,
            "per_group_stats": {
                g: self._calc_stats(l)
                for g, l in group_lengths.items()
            },
            "differential_results": differential_results,
            "correlation_results": correlation_results,
            "outputs": {
                "statistics_tsv": str(stats_tsv),
                "differential_tsv": str(diff_tsv) if diff_tsv.exists() else "",
                "correlation_tsv": str(corr_tsv) if corr_tsv.exists() else "",
            },
        }
        (out_dir / "polya_analysis_results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== poly(A) 分析完成: {len(polya_data)} reads, "
                 f"{n_sig} 差异转录本 → {out_dir} ===")

    @staticmethod
    def _calc_stats(lengths):
        import statistics as _stats
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
            "q25": PolyaAnalysisRunner._percentile(sorted_l, 25),
            "q75": PolyaAnalysisRunner._percentile(sorted_l, 75),
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


if __name__ == "__main__":
    PolyaAnalysisRunner.main()
