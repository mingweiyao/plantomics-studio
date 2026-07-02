"""Fusion transcript detection runner for ONT DRS.

Detects fusion transcripts from aligned DRS RNA-seq data by analyzing
chimeric reads (supplementary alignments and SA tags).

Parameters:
  bam_files: [str]       - Aligned BAM files (from minimap2)
  sample_names: [str]    - Optional sample names
  genome_fasta: str      - Reference genome FASTA (required)
  min_reads: int         - Minimum supporting reads (default 3)
  threads: int           - CPU threads (default 8)

Outputs (to output_dir/):
  per_sample/<name>/         - Per-sample fusion results
    chimeric_reads.bam       - Chimeric/split reads
    candidate_fusions.tsv    - Candidate fusion list
  fusion_summary.json        - Cross-sample fusion summary
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
        sample_results = []
        n = len(bam_files)

        for i, (bam, name) in enumerate(zip(bam_files, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: {bam} 不存在")
                continue

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            # Extract supplementary alignments (chimeric reads)
            self.update(pct=int(5 + 70 * i / n),
                        stage=f"提取嵌合 reads ({i + 1}/{n})", detail=name)
            chim_bam = sample_dir / "chimeric_reads.bam"
            self.run_command([
                "samtools", "view", "-h", "-f", "2048",
                str(bam), "-o", str(chim_bam), "--threads", str(threads),
            ])
            # Also extract reads with SA tag
            sa_bam = sample_dir / "sa_tag_reads.bam"
            self.run_command([
                "bash", "-c",
                f"samtools view -h '{bam}' | "
                f"awk '/^@/||/SA:Z:/' | "
                f"samtools view -bS - > '{sa_bam}'",
            ], indeterminate=True, heartbeat_stage=f"SA tag {name}")

            # Merge chimeric BAMs
            merged_chim = sample_dir / "merged_chimeric.bam"
            self.run_command([
                "samtools", "merge", "-f", str(merged_chim),
                str(chim_bam), str(sa_bam), "--threads", str(threads),
            ])

            # Parse chimeric reads for fusion candidates
            fusion_candidates = self._parse_fusion_candidates(
                merged_chim, name, min_reads)

            # Write per-sample results
            fusion_tsv = sample_dir / "candidate_fusions.tsv"
            if fusion_candidates:
                with open(fusion_tsv, "w", encoding="utf-8") as f:
                    f.write("gene1\tgene2\tchrm1\tpos1\tchrm2\tpos2"
                            "\tn_reads\n")
                    for fc in fusion_candidates:
                        f.write("\t".join(str(v) for v in fc[:7]) + "\n")
                        fusion_key = f"{fc[0]}--{fc[1]}"
                        all_fusions[fusion_key] += fc[6] if len(fc) > 6 else 1

            sample_results.append({
                "sample": name,
                "n_candidates": len(fusion_candidates),
            })

            # Clean up
            for f in [chim_bam, sa_bam, merged_chim]:
                if f.exists():
                    f.unlink()

        # Cross-sample summary
        self.update(pct=85, stage="汇总融合结果")
        fusion_summary = []
        for fusion_key, total_reads in all_fusions.most_common():
            g1, g2 = fusion_key.split("--")
            fusion_summary.append({
                "gene1": g1,
                "gene2": g2,
                "total_supporting_reads": total_reads,
            })

        summary = {
            "n_samples": len(sample_results),
            "n_fusion_candidates": len(fusion_summary),
            "min_supporting_reads": min_reads,
            "fusions": fusion_summary,
        }
        (out_dir / "fusion_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 融合检测完成: {len(fusion_summary)} 个融合候选 → {out_dir} ===")

    def _parse_fusion_candidates(self, bam_path, sample_name, min_reads):
        """Parse chimeric BAM to identify fusion gene pairs."""
        candidates = []
        if not bam_path.exists() or bam_path.stat().st_size == 0:
            return candidates

        import subprocess
        try:
            result = subprocess.run(
                ["samtools", "view", str(bam_path)],
                capture_output=True, text=True, timeout=300)
            lines = result.stdout.splitlines()
        except Exception:
            return candidates

        fusion_pairs = Counter()
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

        for (chrm1, chrm2, pos1, pos2), count in fusion_pairs.most_common():
            if count >= min_reads:
                candidates.append((chrm1, chrm2, chrm1, pos1, chrm2, pos2, count))

        return candidates


if __name__ == "__main__":
    FusionRunner.main()
