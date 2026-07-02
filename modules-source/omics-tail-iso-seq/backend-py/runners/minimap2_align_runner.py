"""minimap2 alignment runner for Tail Iso-seq data.

Aligns full-length transcript reads to reference genome.

Parameters:
  fastq_files: [str]   - Input FASTQ files (full-length from Pychopper)
  sample_names: [str]  - Optional sample names
  genome_fasta: str    - Reference genome FASTA
  splice: bool         - Splice-aware alignment (default: True)
  threads: int         - minimap2 threads (default: 8)
  extra_flags: str     - Additional minimap2 flags

Outputs (to output_dir/<sample>/):
  <sample>.sorted.bam       - Sorted BAM alignment
  <sample>.sorted.bam.bai   - BAM index
  <sample>_align_stats.txt  - Alignment statistics
"""
from pathlib import Path

from runners.base import BaseRunner


class Minimap2AlignRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files", [])
        sample_names = p.get("sample_names", [])
        genome_fasta = p.get("genome_fasta", "")
        splice = bool(p.get("splice", True))
        threads = self.effective_threads(int(p.get("threads", 8)))
        extra_flags = p.get("extra_flags", "")

        if not fastq_files:
            raise ValueError("fastq_files 列表为空")
        if not genome_fasta or not Path(genome_fasta).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")

        if not sample_names or len(sample_names) != len(fastq_files):
            sample_names = [Path(f).stem.split(".")[0] for f in fastq_files]

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        mm2_opts = []
        if splice:
            mm2_opts.extend(["-ax", "splice"])
        else:
            mm2_opts.extend(["-ax", "map-ont"])
        mm2_opts.extend(["-t", str(threads)])
        if extra_flags:
            mm2_opts.extend(extra_flags.split())

        n = len(fastq_files)
        for i, (fq, name) in enumerate(zip(fastq_files, sample_names)):
            if not Path(fq).exists():
                self.log(f"!! 跳过 {name}: {fq} 不存在")
                continue

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)
            sorted_bam = sample_dir / f"{name}.sorted.bam"

            self.update(pct=int(5 + 80 * i / n),
                        stage=f"minimap2 比对 ({i + 1}/{n})", detail=name)

            # Direct pipe: minimap2 -> samtools sort -> bam
            self.run_command([
                "bash", "-c",
                f"minimap2 {' '.join(mm2_opts)} {genome_fasta} '{fq}' "
                f"| samtools sort -o '{sorted_bam}' "
                f"--threads {threads} -"
            ], indeterminate=True, heartbeat_stage=f"minimap2 {name}")

            # Index
            self.run_command([
                "samtools", "index", str(sorted_bam),
            ])

            # Flagstat
            stats_file = sample_dir / f"{name}_align_stats.txt"
            self.run_command([
                "bash", "-c",
                f"samtools flagstat '{sorted_bam}' > '{stats_file}'",
            ])

        self.update(pct=100, stage="完成")
        self.log(f"=== minimap2 比对完成 → {out_dir} ===")


if __name__ == "__main__":
    Minimap2AlignRunner.main()
