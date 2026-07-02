"""rRNA removal runner for ONT reads.

Maps reads against an rRNA database and keeps unmapped (non-rRNA) reads.

Parameters:
  fastq_file: str - Input FASTQ file (required)
  rrna_db:    str - rRNA reference FASTA (required)
  threads:    int - Number of threads, default 8

Outputs (to output_subdir):
  rrna_mapped.sam   - SAM alignment against rRNA db
  non_rrna.fastq    - Reads that did not map to rRNA
  rrna_mapped.flagstat - Flagstat output
  rrna_remove_summary.json
"""
import json
import subprocess
from pathlib import Path

from runners.base import BaseRunner


class RrnaRemoveRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_file = p.get("fastq_file")
        rrna_db = p.get("rrna_db")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq_file or not Path(fastq_file).exists():
            raise FileNotFoundError(f"FASTQ 文件不存在: {fastq_file}")
        if not rrna_db or not Path(rrna_db).exists():
            raise FileNotFoundError(f"rRNA 数据库不存在: {rrna_db}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        sam_file = out_dir / "rrna_mapped.sam"

        # Step 1: minimap2 mapping against rRNA db
        self.update(pct=10, stage="minimap2 比对 rRNA", indeterminate=True)
        minimap2_cmd = [
            "minimap2", "-ax", "map-ont", str(rrna_db),
            str(fastq_file), "-t", str(threads),
            "--secondary=no",
        ]
        self.log(f"$ {' '.join(minimap2_cmd)} > {sam_file}")
        with open(sam_file, "w") as sf:
            proc = subprocess.Popen(minimap2_cmd, stdout=sf,
                                     stderr=subprocess.PIPE, text=True)
            _, stderr = proc.communicate()
            for line in stderr.splitlines():
                self.log(f"  {line}")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, minimap2_cmd)
        if not sam_file.exists():
            raise RuntimeError("minimap2 没有产出 SAM 文件")

        # Step 2: Convert SAM to BAM
        self.update(pct=40, stage="转换 SAM -> BAM")
        bam_file = out_dir / "rrna_mapped.bam"
        self.run_command([
            "samtools", "view", "-bS",
            "-o", str(bam_file), str(sam_file),
        ])

        # Step 3: Extract unmapped reads
        self.update(pct=50, stage="提取非 rRNA reads")
        unmapped_bam = out_dir / "unmapped.bam"
        non_rrna_fastq = out_dir / "non_rrna.fastq"

        self.run_command([
            "samtools", "view", "-f", "4", "-b",
            "-o", str(unmapped_bam), str(bam_file),
        ])

        # samtools fastq writes to -o or stdout
        self.run_command([
            "samtools", "fastq",
            "-o", str(non_rrna_fastq), str(unmapped_bam),
        ])

        # Step 4: flagstat
        self.update(pct=80, stage="flagstat 统计")
        flagstat_file = out_dir / "rRNA_mapped.flagstat"
        self.log(f"$ samtools flagstat {bam_file} > {flagstat_file}")
        with open(flagstat_file, "w") as ff:
            proc = subprocess.Popen(
                ["samtools", "flagstat", str(bam_file)],
                stdout=ff, stderr=subprocess.PIPE, text=True)
            _, stderr = proc.communicate()
            if stderr:
                for line in stderr.splitlines():
                    self.log(f"  {line}")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode,
                                                 ["samtools", "flagstat"])

        # Parse flagstat
        flagstat_stats = {}
        if flagstat_file.exists():
            for line in flagstat_file.read_text(encoding="utf-8").splitlines():
                if "+" in line:
                    parts = line.split("+", 1)
                    key = parts[1].strip() if len(parts) > 1 else ""
                    val = parts[0].strip()
                    flagstat_stats[key] = val

        n_non_rrna = 0
        if non_rrna_fastq.exists():
            n_non_rrna = sum(1 for _ in open(non_rrna_fastq, "r") if _.startswith("@"))

        summary = {
            "input": str(fastq_file),
            "rrna_db": str(rrna_db),
            "n_non_rrna_reads": n_non_rrna,
            "non_rrna_fastq": str(non_rrna_fastq),
            "flagstat": flagstat_stats,
        }
        (out_dir / "rrna_remove_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== rRNA 去除完成, 非 rRNA reads: {n_non_rrna} ===")


if __name__ == "__main__":
    RrnaRemoveRunner.main()
