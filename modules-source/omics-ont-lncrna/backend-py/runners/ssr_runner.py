"""SSR (Simple Sequence Repeat) analysis runner for lncRNA data.

Detects simple sequence repeats (microsatellites) in lncRNA transcripts.

Parameters:
  transcripts_fasta: str  - Transcript sequences FASTA (required)
  min_repeats: dict       - Min repeats per motif type:
    {mono: 10, di: 5, tri: 4, tetra: 4}

Outputs (to output_dir/):
  ssr_results.tsv          - Per-transcript SSR results
  ssr_summary.json
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from runners.base import BaseRunner


class SsrRunner(BaseRunner):

    # Motif patterns for SSR detection
    MONO_NUC = ["A", "T", "C", "G"]
    DI_NUC = [b1 + b2 for b1 in "ACGT" for b2 in "ACGT"]
    TRI_NUC = [b1 + b2 + b3 for b1 in "ACGT" for b2 in "ACGT" for b3 in "ACGT"]
    TETRA_NUC = [b1 + b2 + b3 + b4
                 for b1 in "ACGT" for b2 in "ACGT"
                 for b3 in "ACGT" for b4 in "ACGT"]

    def run(self):
        p = self.job.params or {}
        fasta = p.get("transcripts_fasta", "")
        min_rpts = p.get("min_repeats", {"mono": 10, "di": 5,
                                          "tri": 4, "tetra": 4})

        if not fasta or not Path(fasta).exists():
            raise FileNotFoundError(f"转录本 FASTA 不存在: {fasta}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Parse FASTA
        self.update(pct=5, stage="解析 FASTA 序列")
        seqs = {}
        name, buf = None, []
        with open(fasta, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith(">"):
                    if name is not None:
                        seqs[name] = "".join(buf)
                    name = line[1:].strip().split()[0]
                    buf = []
                else:
                    buf.append(line.strip().upper())
            if name is not None:
                seqs[name] = "".join(buf)

        if not seqs:
            raise RuntimeError("FASTA 文件中没有序列")

        # Detect SSRs
        self.update(pct=20, stage="SSR 检测")
        results = []
        motif_counts = Counter()
        seqs_with_ssr = set()

        total = len(seqs)
        for i, (tid, seq) in enumerate(seqs.items()):
            if self.is_cancelled():
                break
            self.update(pct=int(20 + 70 * i / max(total, 1)),
                        stage=f"SSR 检测 ({i + 1}/{total})")

            found_ssrs = []

            # Mono-nucleotide
            if int(min_rpts.get("mono", 10)) > 0:
                min_m = int(min_rpts["mono"])
                for nuc in self.MONO_NUC:
                    pattern = re.compile(f"({nuc}){{{min_m},}}")
                    for m in pattern.finditer(seq):
                        length = m.end() - m.start()
                        found_ssrs.append({
                            "type": "mono",
                            "motif": nuc,
                            "start": m.start() + 1,
                            "end": m.end(),
                            "length": length,
                            "repeats": length,
                        })

            # Di-nucleotide
            if int(min_rpts.get("di", 5)) > 0:
                min_d = int(min_rpts["di"])
                for nuc in self.DI_NUC:
                    pattern = re.compile(f"({nuc}){{{min_d},}}")
                    for m in pattern.finditer(seq):
                        repeat_len = m.end() - m.start()
                        found_ssrs.append({
                            "type": "di",
                            "motif": nuc,
                            "start": m.start() + 1,
                            "end": m.end(),
                            "length": repeat_len,
                            "repeats": repeat_len // 2,
                        })

            # Tri-nucleotide
            if int(min_rpts.get("tri", 4)) > 0:
                min_t = int(min_rpts["tri"])
                for nuc in self.TRI_NUC:
                    pattern = re.compile(f"({nuc}){{{min_t},}}")
                    for m in pattern.finditer(seq):
                        repeat_len = m.end() - m.start()
                        found_ssrs.append({
                            "type": "tri",
                            "motif": nuc,
                            "start": m.start() + 1,
                            "end": m.end(),
                            "length": repeat_len,
                            "repeats": repeat_len // 3,
                        })

            # Tetra-nucleotide
            if int(min_rpts.get("tetra", 4)) > 0:
                min_tt = int(min_rpts["tetra"])
                for nuc in self.TETRA_NUC:
                    pattern = re.compile(f"({nuc}){{{min_tt},}}")
                    for m in pattern.finditer(seq):
                        repeat_len = m.end() - m.start()
                        found_ssrs.append({
                            "type": "tetra",
                            "motif": nuc,
                            "start": m.start() + 1,
                            "end": m.end(),
                            "length": repeat_len,
                            "repeats": repeat_len // 4,
                        })

            if found_ssrs:
                seqs_with_ssr.add(tid)
                for ssr in found_ssrs:
                    motif_counts[ssr["type"]] += 1
                    results.append({
                        "transcript_id": tid,
                        "sequence_length": len(seq),
                        **ssr,
                    })

        # Write results
        if results:
            r_tsv = out_dir / "ssr_results.tsv"
            with open(r_tsv, "w", encoding="utf-8") as wf:
                wf.write("transcript_id\tsequence_length\ttype\tmotif\t"
                         "start\tend\tlength\trepeats\n")
                for r in results:
                    wf.write(f"{r['transcript_id']}\t{r['sequence_length']}\t"
                             f"{r['type']}\t{r['motif']}\t{r['start']}\t"
                             f"{r['end']}\t{r['length']}\t{r['repeats']}\n")

        summary = {
            "n_transcripts": len(seqs),
            "n_ssr_positive": len(seqs_with_ssr),
            "n_total_ssrs": len(results),
            "motif_counts": dict(motif_counts),
            "min_repeats": min_rpts,
        }
        (out_dir / "ssr_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== SSR: {len(seqs_with_ssr)}/{len(seqs)} 转录本含 SSR, "
                 f"共 {len(results)} 个 SSR ===")


if __name__ == "__main__":
    SsrRunner.main()
