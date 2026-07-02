"""minimap2 alignment runner for Direct RNA Sequencing data.

Aligns ONT DRS reads to a reference genome using minimap2 with
splice-aware parameters (-ax splice -uf) for direct RNA data.

Parameters:
  fastq_files: [str]   - Input FASTQ files (filtered or raw)
  sample_names: [str]  - Optional sample names
  genome_fasta: str    - Reference genome FASTA
  threads: int         - minimap2 threads (default 8)
  extra_flags: str     - Additional minimap2 flags (optional)

Outputs (per sample to output_dir/<sample>/):
  <sample>.sorted.bam        - Sorted BAM alignment
  <sample>.sorted.bam.bai    - BAM index
  <sample>_align_stats.txt   - Alignment statistics
  minimap2_align_summary.json - Summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class Minimap2AlignRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files", [])
        sample_names = p.get("sample_names", [])
        genome_fasta = p.get("genome_fasta", "")
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

        # DRS splice-aware alignment: -ax splice -uf (no -k14 needed for DRS)
        mm2_opts = ["-ax", "splice", "-uf"]
        mm2_opts.extend(["-t", str(threads)])
        if extra_flags:
            mm2_opts.extend(extra_flags.split())

        sample_results = []
        n = len(fastq_files)

        for i, (fq, name) in enumerate(zip(fastq_files, sample_names)):
            if not Path(fq).exists():
                self.log(f"!! 跳过 {name}: {fq} 不存在")
                continue

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            sam = sample_dir / f"{name}.sam"
            bam = sample_dir / f"{name}.bam"
            sorted_bam = sample_dir / f"{name}.sorted.bam"

            # Step 1: minimap2 alignment
            self.update(pct=int(5 + 75 * i / n),
                        stage=f"minimap2 比对 ({i + 1}/{n})", detail=name)
            self.run_command(
                ["bash", "-c",
                 f"minimap2 {' '.join(mm2_opts)} '{genome_fasta}' "
                 f"'{fq}' > '{sam}'"],
                indeterminate=True,
                heartbeat_stage=f"minimap2 {name}",
            )

            if not sam.exists() or sam.stat().st_size == 0:
                raise RuntimeError(f"{name}: minimap2 没产出 SAM 文件")

            # Step 2: SAM -> BAM
            self.update(pct=int(5 + 80 * i / n),
                        stage=f"SAM->BAM ({i + 1}/{n})", detail=name)
            self.run_command([
                "samtools", "view", "-bS", str(sam),
                "-o", str(bam), "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage=f"samtools view {name}")

            # Step 3: Sort BAM
            self.update(pct=int(5 + 85 * i / n),
                        stage=f"排序 BAM ({i + 1}/{n})", detail=name)
            self.run_command([
                "samtools", "sort", str(bam),
                "-o", str(sorted_bam), "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage=f"samtools sort {name}")

            # Step 4: Index BAM
            self.update(pct=int(5 + 90 * i / n),
                        stage=f"索引 BAM ({i + 1}/{n})", detail=name)
            self.run_command([
                "samtools", "index", str(sorted_bam),
            ], indeterminate=True, heartbeat_stage=f"samtools index {name}")

            # Step 5: flagstat
            self.update(pct=int(5 + 93 * i / n),
                        stage=f"比对统计 ({i + 1}/{n})", detail=name)
            stats_file = sample_dir / f"{name}_align_stats.txt"
            self.run_command([
                "bash", "-c",
                f"samtools flagstat '{sorted_bam}' > '{stats_file}'",
            ], indeterminate=True, heartbeat_stage=f"flagstat {name}")

            # Count mapped reads
            mapped = 0
            try:
                import subprocess
                result = subprocess.run(
                    ["samtools", "view", "-c", "-F", "4", str(sorted_bam)],
                    capture_output=True, text=True, timeout=30)
                mapped = int(result.stdout.strip())
            except Exception:
                pass

            sample_results.append({
                "sample": name,
                "input_fastq": fq,
                "sorted_bam": str(sorted_bam),
                "mapped_reads": mapped,
                "stats_file": str(stats_file),
            })

            # Clean up intermediates
            if sam.exists():
                sam.unlink()
            if bam.exists():
                bam.unlink()

        summary = {
            "n_samples": len(sample_results),
            "genome_fasta": genome_fasta,
            "samples": sample_results,
            "total_mapped": sum(s.get("mapped_reads", 0) for s in sample_results),
        }
        (out_dir / "minimap2_align_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== minimap2 比对完成 → {out_dir} ===")


if __name__ == "__main__":
    Minimap2AlignRunner.main()
