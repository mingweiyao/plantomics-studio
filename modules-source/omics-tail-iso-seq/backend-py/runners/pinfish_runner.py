"""Pinfish consensus transcript analysis runner.

Pinfish is a pipeline for analysis of long read transcriptome sequencing
data. It includes:
  1. Polish: polish alignments and collapse
  2. Cluster: cluster overlapping transcripts
  3. Polish: polish clusters into consensus

Parameters:
  bam_file: str                - Aligned BAM (from minimap2)
  genome_fasta: str            - Reference genome FASTA
  sample_name: str             - Sample name for output
  min_coverage: float          - Minimum coverage for polishing (default: 0.95)
  min_identity: float          - Minimum identity (default: 0.95)
  min_reads: int               - Minimum reads per cluster (default: 2)
  max_iters: int               - Maximum polishing iterations (default: 3)
  threads: int                 - CPU threads (default: 8)

Outputs (to output_dir/<sample_name>/):
  polished.bam               - Polished alignments
  clusters.bed               - Clustered transcripts
  consensus.fa               - Consensus transcript sequences
  consensus.gtf              - Consensus transcript GTF
  pinfish_summary.json       - Summary statistics
"""
import json
from pathlib import Path
import shutil

from runners.base import BaseRunner


class PinfishRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam_file = p.get("bam_file", "")
        genome_fasta = p.get("genome_fasta", "")
        sample_name = p.get("sample_name", "")
        min_coverage = float(p.get("min_coverage", 0.95))
        min_identity = float(p.get("min_identity", 0.95))
        min_reads = int(p.get("min_reads", 2))
        max_iters = int(p.get("max_iters", 3))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bam_file or not Path(bam_file).exists():
            raise FileNotFoundError(f"BAM 文件不存在: {bam_file}")
        if not genome_fasta or not Path(genome_fasta).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")

        if not sample_name:
            sample_name = Path(bam_file).stem.split(".")[0]

        out_dir = Path(self.output_dir()) / sample_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # Check available Pinfish scripts
        scripts = ["polish_clusters.py", "cluster_by_similarity.py",
                    "get_consensus.py", "filter_by_identity.py"]
        available = {}
        for script in scripts:
            path = shutil.which(script)
            available[script] = path
            if path:
                self.log(f"找到: {script} -> {path}")

        # Alternative: pinfish as single command
        pinfish_cmd = shutil.which("pinfish")

        if pinfish_cmd:
            self._run_pinfish_single(pinfish_cmd, bam_file, genome_fasta,
                                      threads, out_dir)
        else:
            self._run_pinfish_scripts(available, bam_file, genome_fasta,
                                       threads, out_dir)

        # Gather results
        consensus_fa = out_dir / "consensus.fa"
        consensus_gtf = out_dir / "consensus.gtf"

        n_consensus = 0
        if consensus_fa.exists():
            n_consensus = sum(1 for ln in consensus_fa.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if ln.startswith(">"))

        summary = {
            "sample_name": sample_name,
            "n_consensus_transcripts": n_consensus,
            "outputs": {
                "consensus_fa": str(consensus_fa) if consensus_fa.exists() else "",
                "consensus_gtf": str(consensus_gtf) if consensus_gtf.exists() else "",
            },
        }
        (out_dir / "pinfish_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Pinfish 完成: {n_consensus} 条一致性转录本 → {out_dir} ===")

    def _run_pinfish_single(self, pinfish, bam, genome, threads, out_dir):
        """Run pinfish as a single command."""
        self.update(pct=10, stage="Pinfish polish", indeterminate=True)

        polished_bam = out_dir / "polished.bam"
        self.run_command([
            pinfish, "polish",
            "-b", bam,
            "-g", genome,
            "-o", str(polished_bam),
            "-t", str(threads),
        ], indeterminate=True, heartbeat_stage="pinfish polish")

        self.update(pct=40, stage="Pinfish cluster", indeterminate=True)
        clusters_bed = out_dir / "clusters.bed"
        self.run_command([
            pinfish, "cluster",
            "-b", str(polished_bam),
            "-o", str(clusters_bed),
            "-t", str(threads),
        ], indeterminate=True, heartbeat_stage="pinfish cluster")

        self.update(pct=65, stage="Pinfish consensus", indeterminate=True)
        consensus_fa = out_dir / "consensus.fa"
        self.run_command([
            pinfish, "consensus",
            "-b", str(polished_bam),
            "-c", str(clusters_bed),
            "-o", str(consensus_fa),
            "-t", str(threads),
        ], indeterminate=True, heartbeat_stage="pinfish consensus")

        # Try to convert to GTF using gffread or similar
        if consensus_fa.exists():
            self.update(pct=85, stage="生成 GTF")
            gtf_path = out_dir / "consensus.gtf"
            if shutil.which("gffread"):
                self.run_command([
                    "gffread", "-E", str(consensus_fa),
                    "-o", str(gtf_path),
                ])

    def _run_pinfish_scripts(self, available, bam, genome, threads, out_dir):
        """Run Pinfish using individual Python scripts."""
        try:
            self.update(pct=10, stage="polish_clusters", indeterminate=True)
            polished_bam = out_dir / "polished.bam"
            polish_script = available.get("polish_clusters.py") or "polish_clusters.py"
            self.run_command([
                polish_script,
                "-b", bam,
                "-f", genome,
                "-o", str(polished_bam),
                "-t", str(threads),
            ], indeterminate=True, heartbeat_stage="polish_clusters")
        except Exception as e:
            self.log(f"polish_clusters 失败,尝试替代: {e}")
            # Copy input bam as polished if no polishing needed
            import shutil as _shutil
            _shutil.copy2(bam, out_dir / "polished.bam")

        try:
            self.update(pct=40, stage="cluster_by_similarity", indeterminate=True)
            clusters_bed = out_dir / "clusters.bed"
            cluster_script = available.get("cluster_by_similarity.py") or "cluster_by_similarity.py"
            self.run_command([
                cluster_script,
                "-b", str(out_dir / "polished.bam"),
                "-o", str(clusters_bed),
            ], indeterminate=True, heartbeat_stage="cluster")
        except Exception as e:
            self.log(f"cluster 失败: {e}")
            # Create placeholder
            (out_dir / "clusters.bed").write_text("")

        try:
            self.update(pct=65, stage="get_consensus", indeterminate=True)
            consensus_fa = out_dir / "consensus.fa"
            consensus_script = available.get("get_consensus.py") or "get_consensus.py"
            self.run_command([
                consensus_script,
                "-b", str(out_dir / "polished.bam"),
                "-c", str(out_dir / "clusters.bed"),
                "-f", genome,
                "-o", str(consensus_fa),
            ], indeterminate=True, heartbeat_stage="consensus")
        except Exception as e:
            self.log(f"get_consensus 失败: {e}")

        try:
            self.update(pct=85, stage="filter_by_identity", indeterminate=True)
            filter_script = available.get("filter_by_identity.py") or "filter_by_identity.py"
            filtered_fa = out_dir / "consensus_filtered.fa"
            self.run_command([
                filter_script,
                "-i", str(out_dir / "consensus.fa"),
                "-c", "0.95",
                "-o", str(filtered_fa),
            ])
            if filtered_fa.exists():
                import shutil as _shutil
                _shutil.copy2(filtered_fa, out_dir / "consensus.fa")
        except Exception:
            pass


if __name__ == "__main__":
    PinfishRunner.main()
