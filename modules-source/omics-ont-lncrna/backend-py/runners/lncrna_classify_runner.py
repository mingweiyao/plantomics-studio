"""lncRNA classification runner.

Classifies identified lncRNAs into biotypes: lincRNA (intergenic),
intronic, antisense, and sense/overlapping based on gffcompare results.

Parameters:
  lncrna_gtf: str        - lncRNA transcripts GTF (required)
  genome_fasta: str       - Reference genome FASTA (required)
  annotation_gtf: str     - Reference annotation GTF (required)

Outputs (to output_dir/):
  classification.tsv            - Per-transcript classification
  classification_summary.json   - Category counts
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class LncrnaClassifyRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        lncrna_gtf = p.get("lncrna_gtf", "")
        annotation_gtf = p.get("annotation_gtf", "")

        if not lncrna_gtf or not Path(lncrna_gtf).exists():
            raise FileNotFoundError(f"lncRNA GTF 不存在: {lncrna_gtf}")
        if not annotation_gtf or not Path(annotation_gtf).exists():
            raise FileNotFoundError(f"注释 GTF 不存在: {annotation_gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Run gffcompare to classify
        self.update(pct=20, stage="gffcompare 分类", indeterminate=True)
        prefix = out_dir / "classify"
        self.run_command([
            "gffcompare", "-r", annotation_gtf,
            "-o", str(prefix), str(lncrna_gtf),
        ], indeterminate=True, heartbeat_stage="gffcompare classify")

        # Parse .tmap for class codes
        tmap_files = list(out_dir.glob("classify*.tmap"))
        tmap = tmap_files[0] if tmap_files else None

        # Class code mapping
        CODE_MAP = {
            "u": "lincRNA",
            "i": "intronic",
            "x": "antisense",
            "s": "sense",
            "o": "sense",
            "j": "sense",
            "e": "sense",
            "p": "sense",
            "r": "sense",
            "c": "sense",
            "k": "sense",
            "m": "sense",
            "n": "sense",
            "=": "known",
        }

        class_counts = Counter()
        transcripts = []

        if tmap and tmap.exists():
            lines = tmap.read_text(
                encoding="utf-8", errors="ignore").splitlines()
            if lines:
                header = lines[0].split("\t")
                col_idx = {c: i for i, c in enumerate(header)}
                code_col = col_idx.get("class_code", 2)
                qry_id_col = col_idx.get("qry_gene_id", 3)
                ref_id_col = col_idx.get("ref_gene_id", 4)

                for line in lines[1:]:
                    fields = line.split("\t")
                    if len(fields) < len(header):
                        continue
                    code = fields[code_col] if code_col < len(fields) else "?"
                    biotype = CODE_MAP.get(code, "other")
                    class_counts[biotype] += 1
                    transcripts.append({
                        "transcript_id": fields[0] if fields else "",
                        "class_code": code,
                        "biotype": biotype,
                        "qry_gene_id": (fields[qry_id_col]
                                        if qry_id_col < len(fields) else ""),
                        "ref_gene_id": (fields[ref_id_col]
                                        if ref_id_col < len(fields) else ""),
                    })

        # Write classification TSV
        cls_tsv = out_dir / "classification.tsv"
        with open(cls_tsv, "w", encoding="utf-8") as wf:
            wf.write("transcript_id\tclass_code\tbiotype\tqry_gene_id\tref_gene_id\n")
            for t in transcripts:
                wf.write(f"{t['transcript_id']}\t{t['class_code']}\t"
                         f"{t['biotype']}\t{t['qry_gene_id']}\t"
                         f"{t['ref_gene_id']}\n")

        summary = {
            "n_total": len(transcripts),
            "classification": {
                "lincRNA": class_counts.get("lincRNA", 0),
                "intronic": class_counts.get("intronic", 0),
                "antisense": class_counts.get("antisense", 0),
                "sense": class_counts.get("sense", 0),
                "known": class_counts.get("known", 0),
                "other": class_counts.get("other", 0),
            },
        }
        (out_dir / "classification_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== lncRNA 分类: lincRNA={class_counts.get('lincRNA',0)}, "
                 f"intronic={class_counts.get('intronic',0)}, "
                 f"antisense={class_counts.get('antisense',0)}, "
                 f"sense={class_counts.get('sense',0)} ===")


if __name__ == "__main__":
    LncrnaClassifyRunner.main()
