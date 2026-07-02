"""gffcompare novel transcript identification runner.

Compares assembled transcripts against a reference annotation to identify
novel transcripts using gffcompare with flags -R -C -K -M.

Parameters:
  query_gtf: str       - Assembled transcripts GTF (e.g. merged.gtf) (required)
  reference_gtf: str    - Reference annotation GTF (required)
  prefix: str           - Output prefix (default: "gffcmp")

Outputs (to output_dir/):
  gffcmp.*              - gffcompare output files
  novel_transcripts.tsv - Transcripts with class code != '='
  gffcompare_summary.json
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class GffcompareRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        query = p.get("query_gtf", "")
        ref = p.get("reference_gtf", "")
        prefix = p.get("prefix", "gffcmp")

        if not query or not Path(query).exists():
            raise FileNotFoundError(f"查询 GTF 不存在: {query}")
        if not ref or not Path(ref).exists():
            raise FileNotFoundError(f"参考 GTF 不存在: {ref}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        self.update(pct=20, stage="gffcompare", indeterminate=True)
        pref_path = out_dir / prefix
        self.run_command([
            "gffcompare", "-R", "-C", "-K", "-M",
            "-r", ref, "-o", str(pref_path), query,
        ], indeterminate=True, heartbeat_stage="gffcompare")

        # Find the .tmap file
        tmap_files = list(out_dir.glob(f"{prefix}*.tmap"))
        tmap = tmap_files[0] if tmap_files else None

        novel_rows = []
        code_counts = Counter()
        known_count = 0

        if tmap and tmap.exists():
            lines = tmap.read_text(encoding="utf-8", errors="ignore").splitlines()
            if lines:
                header = lines[0].split("\t")
                col_idx = {c: i for i, c in enumerate(header)}
                code_col = col_idx.get("class_code", 2)
                qry_id_col = col_idx.get("qry_gene_id", 3)

                for line in lines[1:]:
                    fields = line.split("\t")
                    if len(fields) < len(header):
                        continue
                    code = fields[code_col] if code_col < len(fields) else "?"
                    code_counts[code] += 1
                    if code == "=":
                        known_count += 1
                    else:
                        novel_rows.append(line)

            if novel_rows:
                novel_tsv = out_dir / "novel_transcripts.tsv"
                novel_tsv.write_text(
                    "\t".join(lines[0].split("\t")) + "\n" +
                    "\n".join(novel_rows) + "\n",
                    encoding="utf-8")

        summary = {
            "n_known": known_count,
            "n_novel": len(novel_rows),
            "class_code_counts": dict(code_counts),
            "tmap_file": str(tmap) if tmap else None,
        }
        (out_dir / "gffcompare_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== gffcompare: {known_count} known, {len(novel_rows)} novel ===")


if __name__ == "__main__":
    GffcompareRunner.main()
