"""rRNA removal runner for ONT Direct RNA Sequencing data.

Removes rRNA reads by aligning against an rRNA database with minimap2,
then extracting unmapped reads (which are non-rRNA).

Parameters:
  fastq_files: [str]   - Input FASTQ files
  sample_names: [str]  - Optional sample names
  rrna_db: str         - rRNA reference FASTA (required)
  threads: int         - minimap2 threads (default 4)

Outputs (to output_dir/<sample>/):
  <sample>.non_rrna.fastq.gz   - Reads after rRNA removal
  <sample>.rrna.fastq.gz       - rRNA reads (removed)
  <sample>_rrna_stats.txt      - Samtools flagstat of alignment
  rrna_remove_summary.json     - Summary statistics
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class RrnaRemoveRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_files = p.get("fastq_files", [])
        sample_names = p.get("sample_names", [])
        rrna_db = p.get("rrna_db", "")
        threads = self.effective_threads(int(p.get("threads", 4)))

        if not fastq_files:
            raise ValueError("fastq_files 列表为空")
        if not rrna_db or not Path(rrna_db).exists():
            raise FileNotFoundError(f"rRNA 数据库不存在: {rrna_db}")

        if not sample_names or len(sample_names) != len(fastq_files):
            sample_names = [Path(f).stem.split(".")[0] for f in fastq_files]

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        sample_results = []
        n = len(fastq_files)

        for i, (fq, name) in enumerate(zip(fastq_files, sample_names)):
            if not Path(fq).exists():
                self.log(f"!! 跳过 {name}: {fq} 不存在")
                continue

            self.update(pct=int(80 * i / n),
                        stage=f"rRNA 去除 ({i + 1}/{n})", detail=name)

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            # Step 1: minimap2 alignment to rRNA DB
            sam = sample_dir / f"{name}.rrna.sam"
            bam = sample_dir / f"{name}.rrna.bam"

            self.run_command([
                "minimap2", "-ax", "map-ont",
                "-t", str(threads),
                rrna_db, fq,
            ], cwd=str(sample_dir))

            # Actually run minimap2 with redirect to SAM
            self.run_command(
                ["bash", "-c",
                 f"minimap2 -ax map-ont -t {threads} '{rrna_db}' '{fq}' "
                 f"> '{sam}' 2>/dev/null"],
                indeterminate=True,
                heartbeat_stage=f"minimap2 rRNA {name}",
            )

            if not sam.exists() or sam.stat().st_size == 0:
                self.log(f"{name}: minimap2 没有产出 SAM,跳过")
                continue

            # Step 2: Convert to BAM, extract unmapped reads
            self.run_command([
                "samtools", "view", "-bS", str(sam),
                "-o", str(bam), "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage=f"samtools view {name}")

            # Extract unmapped reads (flag 4) -> non-rRNA
            non_rrna_bam = sample_dir / f"{name}.non_rrna.bam"
            self.run_command([
                "samtools", "view", "-b", "-f", "4",
                str(bam), "-o", str(non_rrna_bam),
                "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage=f"extract non-rRNA {name}")

            # Convert non-rRNA BAM to FASTQ
            non_rrna_fq = sample_dir / f"{name}.non_rrna.fastq.gz"
            self.run_command([
                "samtools", "fastq", str(non_rrna_bam),
                "--threads", str(threads),
            ], cwd=str(sample_dir))

            # samtools fastq writes to stdout, redirect
            self.run_command(
                ["bash", "-c",
                 f"samtools fastq '{non_rrna_bam}' --threads {threads} "
                 f"| gzip > '{non_rrna_fq}'"],
                indeterminate=True,
                heartbeat_stage=f"FASTQ conversion {name}",
            )

            # Extract mapped reads -> rRNA
            rrna_bam_out = sample_dir / f"{name}.rRNA.bam"
            self.run_command([
                "samtools", "view", "-b", "-F", "4",
                str(bam), "-o", str(rrna_bam_out),
                "--threads", str(threads),
            ])

            rrna_fq = sample_dir / f"{name}.rrna.fastq.gz"
            self.run_command(
                ["bash", "-c",
                 f"samtools fastq '{rrna_bam_out}' --threads {threads} "
                 f"| gzip > '{rrna_fq}'"],
                indeterminate=True,
            )

            # flagstat
            stats_file = sample_dir / f"{name}_rrna_stats.txt"
            self.run_command(
                ["bash", "-c",
                 f"samtools flagstat '{bam}' > '{stats_file}'"],
            )

            # Count reads
            non_rrna_count = 0
            try:
                import gzip
                with gzip.open(non_rrna_fq, "rt", errors="ignore") as fh:
                    for line in fh:
                        if line.startswith("@"):
                            non_rrna_count += 1
            except Exception:
                pass

            sample_results.append({
                "sample": name,
                "input_fastq": fq,
                "non_rrna_fastq": str(non_rrna_fq),
                "rrna_fastq": str(rrna_fq),
                "non_rrna_read_count": non_rrna_count,
            })

            # Clean up intermediate files
            for f in [sam, bam, non_rrna_bam, rrna_bam_out]:
                if f.exists():
                    f.unlink()

        summary = {
            "n_samples": len(sample_results),
            "rrna_db": rrna_db,
            "samples": sample_results,
            "total_non_rrna_reads": sum(
                s.get("non_rrna_read_count", 0) for s in sample_results),
        }
        (out_dir / "rrna_remove_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== rRNA 去除完成: {len(sample_results)} 个样本 → {out_dir} ===")


if __name__ == "__main__":
    RrnaRemoveRunner.main()
