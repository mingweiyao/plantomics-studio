"""poly(A) tail length statistical analysis runner for Tail Iso-seq.

Parameters:
  polya_tsv: str            - Combined poly(A) results TSV
  groups: dict              - Sample-group mapping
  comparisons: list         - Pairwise comparisons
  min_reads: int            - Min reads per transcript (default: 5)
  significance: float       - P-value threshold (default: 0.05)
  expression_file: str      - Optional expression file for correlation

Outputs (to output_dir/):
  polya_statistics.tsv         - Per-group statistics
  polya_expression_corr.tsv   - polyA-expression correlation
  polya_differential.tsv      - Differential polyA results
  polya_analysis_results.json - Full summary
"""
import json
from pathlib import Path
import csv

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

        # Load data
        self.update(pct=5, stage="加载 polyA 数据")

        polya_data = {}
        with open(polya_tsv, encoding="utf-8") as fh:
            reader = csv.reader(fh, delimiter="\t")
            header = next(reader, None)
            if not header:
                raise ValueError("空的 polyA 文件")

            cm = {c: i for i, c in enumerate(header)}
            rid_col = cm.get("read_id", 0)
            polya_col = cm.get("polya_length", 1)
            sample_col = cm.get("sample", None)
            trans_col = cm.get("transcript_id", None)

            for row in reader:
                if len(row) <= max(rid_col, polya_col):
                    continue
                rid = row[rid_col].strip()
                try:
                    length = int(row[polya_col].strip())
                except (ValueError, IndexError):
                    continue
                sample = row[sample_col].strip() if sample_col is not None and len(row) > sample_col else "default"
                transcript = row[trans_col].strip() if trans_col is not None and len(row) > trans_col else ""
                polya_data[rid] = {
                    "read_id": rid, "length": length,
                    "sample": sample, "transcript": transcript,
                }

        if not polya_data:
            raise ValueError("未解析到 polyA 数据")

        self.log(f"加载 {len(polya_data)} 条 polyA 记录")

        # Per-sample stats
        self.update(pct=20, stage="分组统计")
        sample_lengths = {}
        for info in polya_data.values():
            s = info["sample"]
            if s not in sample_lengths:
                sample_lengths[s] = []
            sample_lengths[s].append(info["length"])

        group_lengths = {}
        if groups:
            for gn, sl in groups.items():
                group_lengths[gn] = []
                for s in sl:
                    group_lengths[gn].extend(sample_lengths.get(s, []))

        # Write stats
        stats_tsv = out_dir / "polya_statistics.tsv"
        with open(stats_tsv, "w", encoding="utf-8") as out:
            out.write("entity\ttype\tn\tmean\tmedian\tmin\tmax\n")
            for s, ll in sorted(sample_lengths.items()):
                st = self._stats(ll)
                out.write(f"{s}\tsample\t{len(ll)}\t{st['mean']}\t"
                         f"{st['median']}\t{st['min']}\t{st['max']}\n")
            for g, ll in sorted(group_lengths.items()):
                st = self._stats(ll)
                out.write(f"{g}\tgroup\t{len(ll)}\t{st['mean']}\t"
                         f"{st['median']}\t{st['min']}\t{st['max']}\n")

        # Differential analysis
        self.update(pct=40, stage="差异 polyA 分析")
        diff_tsv = out_dir / "polya_differential.tsv"
        diff_results = {}

        if groups and comparisons:
            # Aggregate by transcript
            trans_group_lengths = {}
            for info in polya_data.values():
                tid = info.get("transcript", "")
                sample = info["sample"]
                if not tid:
                    continue
                for gn, samples in groups.items():
                    if sample in samples:
                        if tid not in trans_group_lengths:
                            trans_group_lengths[tid] = {}
                        if gn not in trans_group_lengths[tid]:
                            trans_group_lengths[tid][gn] = []
                        trans_group_lengths[tid][gn].append(info["length"])
                        break

            with open(diff_tsv, "w", encoding="utf-8") as out:
                out.write("transcript_id\tgroup1\tgroup2\t"
                         "mean1\tmean2\tmedian1\tmedian2\t"
                         "log2FC\tp_value\tsignificant\n")

                for g1, g2 in comparisons:
                    for tid, gd in trans_group_lengths.items():
                        len1 = gd.get(g1, [])
                        len2 = gd.get(g2, [])
                        if len(len1) < min_reads or len(len2) < min_reads:
                            continue

                        mean1 = sum(len1) / len(len1)
                        mean2 = sum(len2) / len(len2)
                        import statistics as _stats
                        median1 = _stats.median(len1) if len1 else 0
                        median2 = _stats.median(len2) if len2 else 0

                        log2fc = 0.0
                        if mean1 > 0 and mean2 > 0:
                            import math
                            log2fc = math.log2(mean1 / mean2)

                        # Perform Mann-Whitney U
                        from scipy import stats as scipy_stats
                        try:
                            u_stat, p_val = scipy_stats.mannwhitneyu(len1, len2, alternative="two-sided")
                        except Exception:
                            u_stat, p_val = 0, 1.0

                        sig = p_val < significance
                        out.write(f"{tid}\t{g1}\t{g2}\t{mean1:.2f}\t{mean2:.2f}\t"
                                 f"{median1:.2f}\t{median2:.2f}\t{log2fc:.4f}\t"
                                 f"{p_val:.6e}\t{'yes' if sig else 'no'}\n")
                        diff_results[f"{tid}|{g1}_vs_{g2}"] = {
                            "mean1": round(mean1, 2),
                            "mean2": round(mean2, 2),
                            "log2FC": round(log2fc, 4),
                            "p_value": round(p_val, 6),
                            "significant": sig,
                        }

        # Correlation
        self.update(pct=60, stage="表达相关")
        corr_results = {}
        if expression_file and Path(expression_file).exists():
            expr_data = {}
            with open(expression_file, encoding="utf-8") as fh:
                reader = csv.reader(fh, delimiter="\t")
                expr_header = next(reader, None)
                if expr_header:
                    expr_samples = expr_header[1:]
                    for row in reader:
                        tid = row[0].strip()
                        vals = [float(v) if v else 0.0 for v in row[1:]]
                        if sum(vals) > 0:
                            expr_data[tid] = dict(zip(expr_samples, vals))

            corr_tsv = out_dir / "polya_expression_corr.tsv"
            with open(corr_tsv, "w", encoding="utf-8") as out:
                out.write("transcript_id\tn_reads\tmean_polya\tmean_expr\tcorrelation\tp\n")
                from scipy import stats as scipy_stats

                # Aggregate polyA by transcript
                trans_polya = {}
                for info in polya_data.values():
                    tid = info.get("transcript", "")
                    if tid:
                        if tid not in trans_polya:
                            trans_polya[tid] = []
                        trans_polya[tid].append(info["length"])

                for tid, lengths in trans_polya.items():
                    if len(lengths) < min_reads or tid not in expr_data:
                        continue
                    avg_polya = sum(lengths) / len(lengths)
                    avg_expr = sum(expr_data[tid].values()) / len(expr_data[tid])

                    r, p_val = 0, 1.0
                    shared = set(sample_lengths.keys()) & set(expr_data[tid].keys())
                    if len(shared) >= 3:
                        polya_vals = []
                        expr_vals = []
                        for ss in shared:
                            sv = sample_lengths.get(ss, [])
                            if sv:
                                polya_vals.append(sum(sv) / len(sv))
                                expr_vals.append(expr_data[tid][ss])
                        if len(polya_vals) >= 3:
                            try:
                                r, p_val = scipy_stats.pearsonr(polya_vals, expr_vals)
                            except Exception:
                                pass

                    out.write(f"{tid}\t{len(lengths)}\t{avg_polya:.2f}\t"
                             f"{avg_expr:.4f}\t{r:.4f}\t{p_val:.6e}\n")
                    corr_results[tid] = {
                        "mean_polya": round(avg_polya, 2),
                        "mean_expr": round(avg_expr, 4),
                        "correlation": round(r, 4),
                        "p_value": round(p_val, 6),
                    }

        # Summary
        n_sig = sum(1 for r in diff_results.values() if r.get("significant"))
        results = {
            "n_reads": len(polya_data),
            "n_samples": len(sample_lengths),
            "n_comparisons": len(comparisons),
            "n_differential": n_sig,
            "significance": significance,
        }
        (out_dir / "polya_analysis_results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Poly(A) 分析完成: {n_sig} 差异, → {out_dir} ===")

    @staticmethod
    def _stats(lengths):
        if not lengths:
            return {"mean": 0, "median": 0, "min": 0, "max": 0}
        import statistics
        return {
            "mean": round(statistics.mean(lengths), 2),
            "median": round(statistics.median(lengths), 2),
            "min": min(lengths),
            "max": max(lengths),
        }


if __name__ == "__main__":
    PolyaAnalysisRunner.main()
