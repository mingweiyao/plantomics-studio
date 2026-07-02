"""gffcompare runner for Tail Iso-seq novel transcript classification.

Compares query transcripts (from Pinfish, StringTie, etc.) against a
reference annotation to classify them.

Parameters:
  query_gtf: str        - Query transcript GTF
  reference_gtf: str    - Reference annotation GTF
  prefix: str           - Output prefix (default: "gffcmp")
  genome_fasta: str     - Optional genome FASTA

Outputs (to output_dir/):
  <prefix>.merged.gtf           - Merged annotation
  <prefix>.tmap                 - Transcript map
  <prefix>.stats                - Classification statistics
  gffcompare_summary.json       - Machine-readable summary
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

        prefix_path = out_dir / prefix
        cmd = ["gffcompare", "-r", reference_gtf,
               "-o", str(prefix_path)]
        if genome_fasta and Path(genome_fasta).exists():
            cmd.extend(["-s", genome_fasta])
        cmd.append(str(query_gtf))

        self.run_command(cmd, indeterminate=True, heartbeat_stage="gffcompare")

        self.update(pct=60, stage="解析结果")

        tmap_files = list(out_dir.glob(f"{prefix}*.tmap"))
        stats_file = list(out_dir.glob(f"{prefix}*.stats"))

        class_codes = Counter()
        novel_transcripts = []
        total_transcripts = 0

        if tmap_files:
            tmap = tmap_files[0]
            lines = tmap.read_text(encoding="utf-8",
                                    errors="ignore").splitlines()
            if len(lines) > 1:
                header = lines[0].split("\t")
                col_map = {}
                for col_name in ["class_code", "qry_id", "qry_gene_id"]:
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

        n_novel = len(novel_transcripts)

        summary = {
            "total_query_transcripts": total_transcripts,
            "n_novel": n_novel,
            "class_code_counts": dict(class_codes),
            "percent_novel": round(
                n_novel / max(total_transcripts, 1) * 100, 2),
        }
        (out_dir / "gffcompare_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

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
