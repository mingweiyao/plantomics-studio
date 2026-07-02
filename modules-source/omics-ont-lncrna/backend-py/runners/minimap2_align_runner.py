"""minimap2 alignment runner for ONT full-length lncRNA data.

Aligns full-length transcripts to a reference genome using minimap2
with splice-aware parameters (-ax splice -uf -k14).

Parameters:
  fastq_files: [str]   - Input FASTQ files (required)
  genome_fasta: str     - Reference genome FASTA (required)
  threads: int          - CPU threads (default 8)

Outputs (to output_dir/):
  {name}.sorted.bam     - Sorted BAM alignment per sample
  {name}.sorted.bam.bai - BAM index
  {name}.flagstat       - Alignment statistics
  minimap2_align_summary.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class Minimap2AlignRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files", [])
        genome = p.get("genome_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq_files:
            raise ValueError("未提供 fastq_files")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        samples = []
        total = len(fastq_files)

        for i, fq in enumerate(fastq_files):
            fq_path = Path(fq)
            if not fq_path.exists():
                self.log(f"!! FASTQ 不存在: {fq}, 跳过")
                continue

            name = fq_path.stem.replace(".fastq", "").replace(".fq", "")
            self.update(pct=int(90 * i / max(total, 1)),
                        stage=f"minimap2 比对 ({i + 1}/{total})", detail=name)

            bam = out_dir / f"{name}.sorted.bam"
            cmd = [
                "minimap2", "-ax", "splice", "-uf", "-k14",
                genome, str(fq_path),
                "-t", str(threads),
            ]
            sort_cmd = ["samtools", "sort", "-o", str(bam), "-"]
            pipeline = f"({' '.join(cmd)}) | {' '.join(sort_cmd)}"
            self.log(f"$ {pipeline}")
            self.run_command(["bash", "-c", pipeline],
                             indeterminate=True,
                             heartbeat_stage=f"minimap2 比对 {name}")

            if not bam.exists():
                self.log(f"!! BAM 未生成: {bam}")
                continue

            # Index
            self.run_command(["samtools", "index", str(bam)],
                             indeterminate=True)

            # Flagstat
            flagstat_file = out_dir / f"{name}.flagstat"
            self.run_command(
                ["samtools", "flagstat", str(bam)],
                cwd=str(out_dir),
                indeterminate=True,
            )
            # Re-run to write file
            import subprocess
            result = subprocess.run(
                ["samtools", "flagstat", str(bam)],
                capture_output=True, text=True, check=False
            )
            flagstat_file.write_text(result.stdout + result.stderr)

            # Parse flagstat
            total_reads, mapped_reads = 0, 0
            for line in (result.stdout or "").splitlines():
                if "+ 0 in total" in line:
                    total_reads = int(line.split()[0])
                if "+ 0 mapped" in line:
                    mapped_reads = int(line.split()[0])
                if "mapped (" in line and total_reads == 0:
                    mapped_reads = int(line.split()[0])

            rate = (mapped_reads / max(total_reads, 1)) * 100
            samples.append({
                "name": name,
                "bam": str(bam),
                "total_reads": total_reads,
                "mapped_reads": mapped_reads,
                "mapping_rate": round(rate, 2),
            })
            self.log(f"  {name}: {total_reads} reads, {mapped_reads} mapped ({rate:.1f}%)")

        summary = {
            "n_samples": len(samples),
            "genome_fasta": genome,
            "aligner": "minimap2",
            "parameters": "-ax splice -uf -k14",
            "samples": samples,
        }
        (out_dir / "minimap2_align_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== minimap2 比对完成, {len(samples)} 个样本 ===")


if __name__ == "__main__":
    Minimap2AlignRunner.main()
