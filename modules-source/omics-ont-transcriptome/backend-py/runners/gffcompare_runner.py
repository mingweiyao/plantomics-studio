"""gffcompare novel transcript classification runner for ONT transcriptome data.

Compares query transcripts against a reference annotation to classify
transcripts by their relationship to known annotations. Identifies novel
isoforms, intergenic transcripts, antisense transcripts, and more.

Parameters:
  query_gtf:      str  - Query GTF file (transcripts to classify)
  reference_gtf:  str  - Reference annotation GTF
  prefix:         str  - Output prefix (default "gffcmp")
  extra_opts:     str  - Extra gffcompare options (optional)

Outputs (to output_dir/):
  <prefix>.tmap                    - Transcript map file with class codes
  <prefix>.loci                    - Loci file
  <prefix>.tracking               - Tracking file
  <prefix>.annot.gtf              - Annotated GTF
  novel_transcripts.tsv           - Non-reference-matching transcripts
  gffcompare_summary.json         - Class code counts and summary

Class code legend:
  =  exactly matches reference
  c  contained in reference
  j  novel isoform (shared splice junction)
  e  single-exon overlap
  i  intronic (within reference intron)
  o  generic exon overlap
  p  possible polymerase run-on
  r  repeat
  u  intergenic (novel)
  x  antisense
  s  intron retention (partial)
  m  full intron/exon overlap (different length)
"""
import csv
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


# Class code descriptions
_CLASS_CODE_DESC = {
    "=": "exactly matches reference",
    "c": "contained in reference",
    "j": "novel isoform (shared splice junction)",
    "e": "single-exon overlap with reference",
    "i": "intronic (within reference intron)",
    "o": "generic exon overlap",
    "p": "possible polymerase run-on",
    "r": "repeat",
    "u": "intergenic (novel transcript)",
    "x": "antisense",
    "s": "intron retention (partial)",
    "m": "full intron/exon overlap (different length)",
    ".": "unknown/unclassified",
}

# Novel class codes (not exact reference matches)
_NOVEL_CODES = {"u", "i", "x", "j", "c", "e", "o", "p", "r", "s", "m", "."}


class GffcompareRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        query_gtf = p.get("query_gtf", "")
        reference_gtf = p.get("reference_gtf", "")
        prefix = p.get("prefix", "gffcmp")
        extra_opts = p.get("extra_opts", "")

        if not query_gtf or not Path(query_gtf).exists():
            raise FileNotFoundError(f"查询 GTF 不存在: {query_gtf}")
        if not reference_gtf or not Path(reference_gtf).exists():
            raise FileNotFoundError(f"参考 GTF 不存在: {reference_gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        output_prefix = str(out_dir / prefix)

        self.log(f"gffcompare: query={query_gtf}, reference={reference_gtf}, "
                 f"prefix={prefix}")

        # ---- Step 1: Run gffcompare ----
        self.update(pct=10, stage="gffcompare", detail="运行 gffcompare",
                    indeterminate=True)

        cmd = ["gffcompare", "-R", "-C", "-K", "-M",
               "-r", reference_gtf,
               "-o", output_prefix,
               query_gtf]
        if extra_opts:
            cmd.extend(extra_opts.split())

        self.run_command(cmd, indeterminate=True,
                         heartbeat_stage="gffcompare")

        # Expected output files
        tmap_file = out_dir / f"{prefix}.tmap"
        loci_file = out_dir / f"{prefix}.loci"
        tracking_file = out_dir / f"{prefix}.tracking"
        annot_gtf = out_dir / f"{prefix}.annot.gtf"

        if not tmap_file.exists():
            raise RuntimeError(f"gffcompare 未生成 .tmap 文件: {tmap_file}")

        # ---- Step 2: Parse .tmap and classify ----
        self.update(pct=40, stage="解析 tmap", detail="分类转录本",
                    indeterminate=True)

        class_counts = Counter()
        novel_entries = []
        total_transcripts = 0

        with open(tmap_file, encoding="utf-8", errors="ignore") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            for row in reader:
                total_transcripts += 1
                class_code = row.get("class_code", ".").strip()
                if not class_code:
                    class_code = "."
                class_counts[class_code] += 1

                # Novel = anything that's not an exact match
                if class_code != "=":
                    novel_entries.append({
                        "ref_gene_id": row.get("ref_gene_id", ""),
                        "ref_id": row.get("ref_id", ""),
                        "class_code": class_code,
                        "class_desc": _CLASS_CODE_DESC.get(class_code, "unknown"),
                        "qry_gene_id": row.get("qry_gene_id", ""),
                        "qry_id": row.get("qry_id", ""),
                        "num_exons": row.get("num_exons", ""),
                        "coverage": row.get("coverage", ""),
                        "identity": row.get("identity", ""),
                    })

        self.log(f"gffcompare 统计: {total_transcripts} 条查询转录本, "
                 f"{len(novel_entries)} 条非精确匹配转录本")
        for code, count in sorted(class_counts.items()):
            desc = _CLASS_CODE_DESC.get(code, "unknown")
            self.log(f"  {code}: {count} ({desc})")

        # ---- Step 3: Write novel_transcripts.tsv ----
        self.update(pct=70, stage="写入结果",
                    detail=f"{len(novel_entries)} 条 novel 转录本")

        novel_file = out_dir / "novel_transcripts.tsv"
        if novel_entries:
            with open(novel_file, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "ref_gene_id", "ref_id", "class_code", "class_desc",
                        "qry_gene_id", "qry_id", "num_exons", "coverage",
                        "identity",
                    ],
                    delimiter="\t",
                )
                writer.writeheader()
                writer.writerows(novel_entries)
            self.log(f"  -> {novel_file} ({len(novel_entries)} 条 novel 转录本)")
        else:
            # Write an empty file with just a header
            novel_file.write_text(
                "ref_gene_id\tref_id\tclass_code\tclass_desc\t"
                "qry_gene_id\tqry_id\tnum_exons\tcoverage\tidentity\n",
                encoding="utf-8")
            self.log("  -> 没有发现 novel 转录本")

        # ---- Step 4: Write summary JSON ----
        summary = {
            "query_gtf": query_gtf,
            "reference_gtf": reference_gtf,
            "prefix": prefix,
            "total_query_transcripts": total_transcripts,
            "novel_transcripts": len(novel_entries),
            "class_code_counts": dict(class_counts),
            "class_code_descriptions": _CLASS_CODE_DESC,
            "output_files": {
                "tmap": str(tmap_file) if tmap_file.exists() else "",
                "loci": str(loci_file) if loci_file.exists() else "",
                "tracking": str(tracking_file) if tracking_file.exists() else "",
                "annot_gtf": str(annot_gtf) if annot_gtf.exists() else "",
                "novel_transcripts": str(novel_file)
                    if novel_file.exists() else "",
            },
        }
        (out_dir / "gffcompare_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== gffcompare 完成: {total_transcripts} 条转录本, "
                 f"{len(novel_entries)} 条 novel → {out_dir} ===")


if __name__ == "__main__":
    GffcompareRunner.main()
