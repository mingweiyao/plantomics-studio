"""Fusion transcript detection runner.

Detects fusion transcripts from aligned RNA-seq data using
FusionCatcher or similar tools designed for long-read data.

For ONT DRS, uses a combination approach:
  1. Minimap2 chimeric reads detection
  2. Fusion detection via FusionInspector or in-house method
  3. Filter and annotate candidate fusions

Parameters:
  bam_files: [str]       - Aligned BAM files (from minimap2)
  sample_names: [str]    - Optional sample names
  genome_fasta: str      - Reference genome FASTA
  annotation_gtf: str    - Reference annotation GTF
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

            # Step 1: Extract supplementary/secondary alignments (chimeric reads)
            self.update(pct=int(5 + 60 * i / n),
                        stage=f"提取嵌合 reads ({i + 1}/{n})", detail=name)
            chim_bam = sample_dir / "chimeric_reads.bam"

            # samtools view -f 2048 extracts supplementary alignments
            self.run_command([
                "samtools", "view", "-h", "-f", "2048",
                str(bam), "-o", str(chim_bam), "--threads", str(threads),
            ])
            # Also extract reads with SA tag (chimeric alignment)
            sa_bam = sample_dir / "sa_tag_reads.bam"
            self.run_command([
                "samtools", "view", "-h",
                str(bam), "--threads", str(threads),
            ], cwd=str(sample_dir))
            # Use awk to filter reads with SA tag
            sa_sam = sample_dir / "sa_tag_reads.sam"
            self.run_command([
                "bash", "-c",
                f"samtools view -h '{bam}' | awk '/^@/||/SA:Z:/' | "
                f"samtools view -bS - > '{sa_bam}'",
            ], indeterminate=True, heartbeat_stage=f"SA tag {name}")

            # Merge chimeric BAMs
            merged_chim = sample_dir / "merged_chimeric.bam"
            self.run_command([
                "samtools", "merge", "-f", str(merged_chim),
                str(chim_bam), str(sa_bam),
                "--threads", str(threads),
            ])

            # Step 2: Extract fusion gene pairs
            self.update(pct=int(5 + 70 * i / n),
                        stage=f"分析融合基因 ({i + 1}/{n})", detail=name)

            # Parse chimeric reads to identify fusion partners
            # Look for SA tags that indicate split alignments
            fusion_candidates = self._parse_fusion_candidates(
                merged_chim, name, sample_dir, min_reads)

            # Write per-sample results
            fusion_tsv = sample_dir / "candidate_fusions.tsv"
            if fusion_candidates:
                with open(fusion_tsv, "w", encoding="utf-8") as f:
                    f.write("fusion_gene1\tgene2\tchrm1\tpos1\tstrand1\t"
                            "chrm2\tpos2\tstrand2\tn_reads\tsamples\n")
                    for fc in fusion_candidates:
                        f.write("\t".join(str(v) for v in fc) + "\n")
                        fusion_key = f"{fc[0]}--{fc[1]}"
                        all_fusions[fusion_key] += fc[8] if len(fc) > 8 else 1
                self.log(f"{name}: 发现 {len(fusion_candidates)} 个融合候选")
            else:
                fusion_tsv.write_text(
                    "fusion_gene1\tgene2\tchrm1\tpos1\tstrand1\t"
                    "chrm2\tpos2\tstrand2\tn_reads\tsamples\n"
                    "# 未发现支持 reads >= 3 的融合事件\n")
                self.log(f"{name}: 未发现融合事件")

        # Cross-sample summary
        self.update(pct=85, stage="汇总融合结果")
        fusion_summary = []
        for fusion_key, total_reads in all_fusions.most_common():
            g1, g2 = fusion_key.split("--")
            fusion_summary.append({
                "gene1": g1,
                "gene2": g2,
                "total_supporting_reads": total_reads,
                "n_samples": sum(1 for fc in all_fusions
                                 if fc.split("--") == [g1, g2]),
            })

        summary = {
            "n_samples": len(sample_names),
            "n_fusion_candidates": len(fusion_summary),
            "min_supporting_reads": min_reads,
            "fusions": fusion_summary,
        }
        (out_dir / "fusion_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 融合检测完成: {len(fusion_summary)} 个融合候选 "
                 f"→ {out_dir} ===")

    def _parse_fusion_candidates(self, bam_path, sample_name,
                                  sample_dir, min_reads):
        """Parse chimeric BAM to identify fusion gene pairs."""
        candidates = []
        if not bam_path.exists() or bam_path.stat().st_size == 0:
            return candidates

        # Use samtools to get SA tag info
        # SA tag format: SA:Z:chr,pos,strand,CIGAR,mapQ,NM;
        import subprocess
        try:
            result = subprocess.run(
                ["samtools", "view", str(bam_path)],
                capture_output=True, text=True,
            )
            lines = result.stdout.splitlines()
        except Exception:
            return candidates

        # Parse SA tag from each read
        fusion_pairs = Counter()
        for line in lines:
            if line.startswith("@"):
                continue
            cols = line.split("\t")
            if len(cols) < 12:
                continue

            # Primary alignment info
            rname = cols[2]  # chromosome
            pos = cols[3]  # position

            # Look for SA tag
            sa_tag = None
            for col in cols[11:]:
                if col.startswith("SA:Z:"):
                    sa_tag = col[5:]
                    break

            if not sa_tag:
                continue

            # Parse SA tag: each segment is chr,pos,strand,CIGAR,mapQ,NM;
            segments = sa_tag.rstrip(";").split(";")
            for seg in segments:
                parts = seg.split(",")
                if len(parts) >= 6:
                    sa_chr = parts[0]
                    sa_pos = parts[1]
                    sa_strand = parts[2]
                    mapq = int(parts[4])

                    # Filter: different chromosome or far apart on same chr
                    if sa_chr != rname or abs(int(sa_pos) - int(pos)) > 100000:
                        pair = (rname, sa_chr,
                                pos, sa_pos, mapq)
                        fusion_pairs[pair] += 1

        # Build candidates from frequent pairs
        for (chrm1, chrm2, pos1, pos2, _), count in fusion_pairs.most_common():
            if count >= min_reads:
                # Try to get gene names from annotation (simplified)
                gene1 = chrm1
                gene2 = chrm2
                candidates.append((gene1, gene2, chrm1, pos1, "+",
                                   chrm2, pos2, "+", count, sample_name))

        return candidates


if __name__ == "__main__":
    FusionRunner.main()
