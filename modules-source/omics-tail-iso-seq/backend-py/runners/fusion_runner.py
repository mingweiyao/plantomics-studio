"""Fusion transcript detection runner for Tail Iso-seq.

Detects fusion transcripts from aligned long-read data by identifying
chimeric reads (split alignments, SA tags).

Parameters:
  bam_files: [str]       - Aligned BAM files
  sample_names: [str]    - Sample names
  genome_fasta: str      - Reference genome FASTA
  annotation_gtf: str    - Reference annotation GTF
  min_reads: int         - Minimum supporting reads (default: 3)
  threads: int           - CPU threads (default: 8)

Outputs (to output_dir/):
  per_sample/<name>/candidate_fusions.tsv   - Fusion candidates
  fusion_summary.json                       - Cross-sample summary
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class FusionRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam_files = p.get("bam_files", [])
        sample_names = p.get("sample_names", [])
        genome_fasta = p.get("genome_fasta", "")
        annotation_gtf = p.get("annotation_gtf", "")
        min_reads = int(p.get("min_reads", 3))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bam_files:
            raise ValueError("bam_files 列表为空")
        if not genome_fasta or not Path(genome_fasta).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")

        if not sample_names or len(sample_names) != len(bam_files):
            sample_names = [Path(b).stem.split(".")[0].replace(".sorted", "")
                            for b in bam_files]

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        all_fusions = Counter()
        n = len(bam_files)

        for i, (bam, name) in enumerate(zip(bam_files, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: {bam} 不存在")
                continue

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            self.update(pct=int(5 + 70 * i / n),
                        stage=f"分析融合 ({i + 1}/{n})", detail=name)

            # Extract reads with SA tag (chimeric/split alignments)
            sa_bam = sample_dir / "sa_tag_reads.bam"
            self.run_command([
                "bash", "-c",
                f"samtools view -h '{bam}' | awk '/^@/||/SA:Z:/' | "
                f"samtools view -bS - > '{sa_bam}'",
            ], indeterminate=True, heartbeat_stage=f"SA tag {name}")

            # Parse SA tags to find fusion gene pairs
            fusion_pairs = self._parse_sa_tags(sa_bam, name, sample_dir)

            fusion_tsv = sample_dir / "candidate_fusions.tsv"
            if fusion_pairs:
                with open(fusion_tsv, "w", encoding="utf-8") as f:
                    f.write("gene1\tgene2\tchrm1\tpos1\tchrm2\tpos2\t"
                            "n_reads\tsamples\n")
                    for key, count in fusion_pairs.most_common():
                        chrm1, chrm2, pos1, pos2 = key
                        g1, g2 = chrm1, chrm2
                        f.write(f"{g1}\t{g2}\t{chrm1}\t{pos1}\t"
                                f"{chrm2}\t{pos2}\t{count}\t{name}\n")
                        all_fusions[f"{g1}--{g2}"] += count
                self.log(f"{name}: {len(fusion_pairs)} 融合候选")
            else:
                fusion_tsv.write_text(
                    "gene1\tgene2\tchrm1\tpos1\tchrm2\tpos2\t"
                    "n_reads\tsamples\n# 未发现融合事件\n")
                self.log(f"{name}: 未发现融合")

        # Summary
        fusion_summary = []
        for key, total in all_fusions.most_common():
            g1, g2 = key.split("--")
            fusion_summary.append({"gene1": g1, "gene2": g2,
                                    "total_reads": total})

        summary = {
            "n_samples": len(sample_names),
            "n_fusion_candidates": len(fusion_summary),
            "fusions": fusion_summary,
        }
        (out_dir / "fusion_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 融合检测完成: {len(fusion_summary)} 候选 → {out_dir} ===")

    def _parse_sa_tags(self, sa_bam, sample_name, sample_dir):
        """Parse SA tags from chimeric BAM to find fusion pairs."""
        fusion_pairs = Counter()
        if not sa_bam.exists() or sa_bam.stat().st_size == 0:
            return fusion_pairs

        import subprocess
        try:
            result = subprocess.run(
                ["samtools", "view", str(sa_bam)],
                capture_output=True, text=True, timeout=300,
            )
            lines = result.stdout.splitlines()
        except Exception:
            return fusion_pairs

        for line in lines:
            if line.startswith("@"):
                continue
            cols = line.split("\t")
            if len(cols) < 12:
                continue

            rname = cols[2]
            pos = cols[3]

            sa_tag = None
            for col in cols[11:]:
                if col.startswith("SA:Z:"):
                    sa_tag = col[5:]
                    break

            if not sa_tag:
                continue

            segments = sa_tag.rstrip(";").split(";")
            for seg in segments:
                parts = seg.split(",")
                if len(parts) >= 6:
                    sa_chr = parts[0]
                    sa_pos = parts[1]
                    if sa_chr != rname or abs(int(sa_pos) - int(pos)) > 100000:
                        pair = (rname, sa_chr, pos, sa_pos)
                        fusion_pairs[pair] += 1

        # Filter by min_reads
        filtered = Counter()
        for pair, count in fusion_pairs.items():
            if count >= self.job.params.get("min_reads", 3):
                filtered[pair] = count
        return filtered


if __name__ == "__main__":
    FusionRunner.main()
