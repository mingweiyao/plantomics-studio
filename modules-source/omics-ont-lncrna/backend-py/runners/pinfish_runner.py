"""Pinfish consensus transcript runner for ONT full-length lncRNA data.

Clusters aligned reads and polishes consensus sequences using Pinfish.

Parameters:
  bam_files: [str]     - Aligned BAM files (required)
  genome_fasta: str     - Reference genome FASTA (required)
  threads: int          - CPU threads (default 8)

Outputs (to output_dir/):
  clusters.gff          - Clustered transcript groups
  polished.gff          - Polished consensus transcripts
  pinfish_summary.json  - Summary statistics
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class PinfishRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam_files = p.get("bam_files", [])
        genome = p.get("genome_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bam_files:
            raise ValueError("未提供 bam_files")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Index BAMs if needed
        total = len(bam_files)
        valid_bams = []
        for i, bam in enumerate(bam_files):
            bam_path = Path(bam)
            if not bam_path.exists():
                self.log(f"!! BAM 不存在: {bam}, 跳过")
                continue
            self.update(pct=int(10 * i / max(total, 1)),
                        stage=f"索引 BAM ({i + 1}/{total})")
            if not (bam_path.with_suffix(".bam.bai")).exists() and \
               not (bam_path.parent / f"{bam_path.name}.bai").exists():
                self.run_command(["samtools", "index", str(bam_path)],
                                 indeterminate=True)
            valid_bams.append(str(bam_path))

        if not valid_bams:
            raise RuntimeError("没有有效的 BAM 文件")

        # Pinfish align_cluster
        self.update(pct=30, stage="Pinfish align_cluster", indeterminate=True)
        clusters = out_dir / "clusters.gff"
        self.run_command([
            "pinfish", "align_cluster",
            valid_bams[0], str(clusters),
            "-t", str(threads),
        ], indeterminate=True, heartbeat_stage="pinfish align_cluster")

        if not clusters.exists():
            raise RuntimeError("Pinfish align_cluster 失败")

        # Pinfish polish
        self.update(pct=65, stage="Pinfish polish", indeterminate=True)
        polished = out_dir / "polished.gff"
        self.run_command([
            "pinfish", "polish",
            str(clusters), genome, valid_bams[0], str(polished),
            "-t", str(threads),
        ], indeterminate=True, heartbeat_stage="pinfish polish")

        # Count results
        n_clusters = 0
        if clusters.exists():
            n_clusters = sum(1 for _ in clusters.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if not _.startswith("#"))

        n_polished = 0
        if polished.exists():
            n_polished = sum(1 for _ in polished.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if not _.startswith("#"))

        summary = {
            "n_bams": len(valid_bams),
            "n_clusters": n_clusters,
            "n_polished": n_polished,
            "clusters_gff": str(clusters),
            "polished_gff": str(polished),
        }
        (out_dir / "pinfish_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== Pinfish: {n_clusters} clusters, {n_polished} polished ===")


if __name__ == "__main__":
    PinfishRunner.main()
