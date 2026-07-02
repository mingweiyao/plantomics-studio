"""gffcompare runner - novel transcript classification.

Compares query transcripts (from Flair, StringTie, etc.) against a
reference annotation to classify them (known, novel isoform, intergenic, etc.).

Parameters:
  query_gtf: str        - Query transcript GTF (e.g. from Flair collapse)
  reference_gtf: str    - Reference annotation GTF
  prefix: str           - Output prefix (default: "gffcmp")
  genome_fasta: str     - Optional genome FASTA for sequence-level comparison

Outputs (to output_dir/):
  <prefix>.merged.gtf           - Combined/merged annotation
  <prefix>.loci                  - Loci information
  <prefix>.stats                 - Classification statistics
  <prefix>.tracking              - Transcript tracking
  <prefix>.refmap                - Reference mapping
  <prefix>.tmap                  - Transcript map
  gffcompare_summary.json        - Machine-readable summary
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class GffcompareRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        query_gtf = p.get("query_gtf", "")
        reference_gtf = p.get("reference_gtf", "")
        prefix = p.get("prefix", "gffcmp")
        genome_fasta = p.get("genome_fasta", "")

        if not query_gtf or not Path(query_gtf).exists():
            raise FileNotFoundError(f"查询 GTF 不存在: {query_gtf}")
        if not reference_gtf or not Path(reference_gtf).exists():
            raise FileNotFoundError(f"参考 GTF 不存在: {reference_gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        self.update(pct=10, stage="gffcompare 分类", indeterminate=True)

        # Build gffcompare command
        prefix_path = out_dir / prefix
        cmd = ["gffcompare", "-r", reference_gtf,
               "-o", str(prefix_path)]

        # Add genome if provided (enables --sequence overlap checks)
        if genome_fasta and Path(genome_fasta).exists():
            cmd.extend(["-s", genome_fasta])

        cmd.append(str(query_gtf))

        self.run_command(cmd, indeterminate=True,
                         heartbeat_stage="gffcompare")

        # Parse results
        self.update(pct=60, stage="解析 gffcompare 结果")

        # gffcompare outputs: <prefix>.<query_gtf_basename>.tmap
        # Find the tmap file
        tmap_files = list(out_dir.glob(f"{prefix}*.tmap"))
        stats_file = list(out_dir.glob(f"{prefix}*.stats"))

        class_codes = Counter()
        novel_transcripts = []
        total_transcripts = 0

        if tmap_files:
            tmap = tmap_files[0]
            lines = tmap.read_text(encoding="utf-8", errors="ignore").splitlines()
            if len(lines) > 1:
                header = lines[0].split("\t")
                # Find relevant columns
                col_map = {}
                for col_name in ["class_code", "qry_id", "qry_gene_id",
                                 "num_exons", "chrm", "strand"]:
                    for idx, col in enumerate(header):
                        if col.strip() == col_name:
                            col_map[col_name] = idx
                            break

                for ln in lines[1:]:
                    cols = ln.split("\t")
                    if len(cols) < len(header):
                        continue
                    total_transcripts += 1
                    code_idx = col_map.get("class_code", 2)
                    if code_idx < len(cols):
                        code = cols[code_idx].strip()
                        class_codes[code] += 1
                        if code != "=":
                            novel_transcripts.append(ln)

        # Parse stats file
        stats_text = ""
        if stats_file:
            stats_text = stats_file[0].read_text(
                encoding="utf-8", errors="ignore")

        # Generate summary
        n_novel = len(novel_transcripts)
        code_descriptions = {
            "=": "完全匹配已知转录本",
            "c": "包含在参考转录本中",
            "j": "潜在的新异构体(与参考转录本共享至少一个剪切位点)",
            "e": "单外显子转录本(与参考有重叠)",
            "i": "完全内含子中的转录本",
            "o": "与参考外显子有重叠(反义链)",
            "p": "聚合酶链反应产物(与参考有重叠)",
            "r": "重复序列",
            "u": "基因间区转录本(未知)",
            "x": "反义链外显子重叠",
            "s": "内含子链(与参考内含子部分重叠)",
            ".": "追踪文件中的其他",
        }
        code_labels = {k: v for k, v in code_descriptions.items()
                       if k in class_codes}

        summary = {
            "total_query_transcripts": total_transcripts,
            "n_novel": n_novel,
            "class_code_counts": dict(class_codes),
            "class_code_descriptions": code_labels,
            "percent_novel": round(
                n_novel / max(total_transcripts, 1) * 100, 2),
            "output_prefix": str(prefix_path),
            "query_gtf": query_gtf,
            "reference_gtf": reference_gtf,
        }
        (out_dir / "gffcompare_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        # Write novel transcript list
        if novel_transcripts and tmap_files:
            header_line = tmap_files[0].read_text(
                encoding="utf-8", errors="ignore").splitlines()[0]
            (out_dir / "novel_transcripts.tsv").write_text(
                header_line + "\n" + "\n".join(novel_transcripts) + "\n",
                encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== gffcompare 完成: {n_novel}/{total_transcripts} "
                 f"条新转录本 → {out_dir} ===")


if __name__ == "__main__":
    GffcompareRunner.main()
