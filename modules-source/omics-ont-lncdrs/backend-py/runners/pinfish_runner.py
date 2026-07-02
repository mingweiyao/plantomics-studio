"""Pinfish consensus transcript runner for ONT Direct RNA Sequencing.

Uses Pinfish to generate consensus transcript sequences from
aligned DRS reads.

Pipeline:
  1. pinfish polya - remove poly(A) tails from BAM
  2. pinfish cluster - cluster reads into gene/transcript groups
  3. pinfish polish - generate consensus sequences

Parameters:
  bam_files: [str]     - Aligned BAM files (from minimap2)
  sample_names: [str]  - Optional sample names
  genome_fasta: str    - Reference genome FASTA
  threads: int         - CPU threads (default 8)

Outputs (to output_dir/<sample>/):
  <sample>/polya_removed.bam        - BAM with polyA tails removed
  <sample>/clusters.tsv             - Read clustering results
  <sample>/consensus.fa             - Consensus transcript sequences
  <sample>/pinfish_summary.json     - Per-sample summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class PinfishRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam_files = p.get("bam_files", [])
        sample_names = p.get("sample_names", [])
        genome_fasta = p.get("genome_fasta", "")
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

        sample_results = []
        n = len(bam_files)

        for i, (bam, name) in enumerate(zip(bam_files, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: {bam} 不存在")
                continue

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            # Step 1: poly(A) tail removal
            self.update(pct=int(5 + 70 * i / n),
                        stage=f"Pinfish polyA 去除 ({i + 1}/{n})", detail=name)
            polya_bam = sample_dir / "polya_removed.bam"
            self.run_command([
                "pinfish", "polya", str(bam),
                "-o", str(polya_bam),
                "-t", str(threads),
            ], indeterminate=True, heartbeat_stage=f"pinfish polya {name}")

            if not polya_bam.exists():
                self.log(f"{name}: pinfish polya 未产出,尝试直接使用输入 BAM")
                polya_bam = Path(bam)

            # Step 2: Clustering
            self.update(pct=int(5 + 80 * i / n),
                        stage=f"Pinfish 聚类 ({i + 1}/{n})", detail=name)
            clusters_tsv = sample_dir / "clusters.tsv"
            self.run_command([
                "pinfish", "cluster", str(polya_bam),
                genome_fasta,
                "-o", str(clusters_tsv),
                "-t", str(threads),
            ], indeterminate=True, heartbeat_stage=f"pinfish cluster {name}")

            if not clusters_tsv.exists():
                self.log(f"{name}: pinfish cluster 未产出,跳过")
                continue

            # Step 3: Polish / generate consensus
            self.update(pct=int(5 + 90 * i / n),
                        stage=f"Pinfish 一致性序列 ({i + 1}/{n})", detail=name)
            consensus_fa = sample_dir / "consensus.fa"
            self.run_command([
                "pinfish", "polish", str(polya_bam),
                str(clusters_tsv),
                genome_fasta,
                "-o", str(consensus_fa),
                "-t", str(threads),
            ], indeterminate=True, heartbeat_stage=f"pinfish polish {name}")

            # Count consensus transcripts
            n_transcripts = 0
            if consensus_fa.exists():
                with open(consensus_fa, encoding="utf-8", errors="ignore") as fh:
                    n_transcripts = sum(1 for ln in fh if ln.startswith(">"))

            sample_results.append({
                "sample": name,
                "input_bam": bam,
                "clusters_tsv": str(clusters_tsv) if clusters_tsv.exists() else "",
                "consensus_fasta": str(consensus_fa) if consensus_fa.exists() else "",
                "n_consensus_transcripts": n_transcripts,
            })

        summary = {
            "n_samples": len(sample_results),
            "genome_fasta": genome_fasta,
            "n_total_consensus_transcripts": sum(
                s.get("n_consensus_transcripts", 0) for s in sample_results),
            "samples": sample_results,
        }
        (out_dir / "pinfish_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Pinfish 完成: {len(sample_results)} 个样本 → {out_dir} ===")


if __name__ == "__main__":
    PinfishRunner.main()
