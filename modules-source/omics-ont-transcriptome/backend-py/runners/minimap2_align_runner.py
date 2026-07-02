"""minimap2 alignment runner for ONT transcriptome data.

Aligns ONT transcriptome reads to a reference genome using minimap2 with
splice-aware parameters (-ax splice -uf -k14) for direct RNA/cDNA data.

Parameters:
  fastq:        str - Query FASTQ file path
  index:        str - Reference genome (.mmi index or FASTA)
  output_bam:   str - Optional output BAM path (default: <prefix>.sorted.bam)
  extra_opts:   str - Extra minimap2 options (optional)
  sort_memory:  str - samtools sort memory limit (default: "2G")
  threads:      int - CPU threads (default 8)

Outputs (to output_dir/):
  <prefix>.sorted.bam        - Sorted BAM alignment
  <prefix>.sorted.bam.bai    - BAM index
  <prefix>_align_stats.txt   - Alignment statistics from samtools flagstat
  minimap2_summary.json      - Alignment summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class Minimap2AlignRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq = p.get("fastq", "")
        index = p.get("index", "")
        output_bam = p.get("output_bam", "")
        extra_opts = p.get("extra_opts", "")
        sort_memory = p.get("sort_memory", "2G")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq or not Path(fastq).exists():
            raise FileNotFoundError(f"查询 FASTQ 不存在: {fastq}")
        if not index or not Path(index).exists():
            raise FileNotFoundError(f"参考基因组索引不存在: {index}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Derive output BAM path
        if output_bam:
            sorted_bam = Path(output_bam)
        else:
            prefix = Path(fastq).stem.split(".")[0]
            sorted_bam = out_dir / f"{prefix}.sorted.bam"

        # ---- Step 1: minimap2 alignment piped to samtools sort ----
        self.update(pct=5, stage="minimap2 比对 + samtools sort",
                    indeterminate=True)

        mm2_opts = ["-ax", "splice", "-uf", "-k14", "-t", str(threads)]
        if extra_opts:
            mm2_opts.extend(extra_opts.split())

        mm2_cmd = ["minimap2"] + mm2_opts + [index, fastq]

        # Pipe: minimap2 ... | samtools sort -@ threads -m sort_memory -o sorted.bam
        self.run_command(
            ["bash", "-c",
             f"{' '.join(mm2_cmd)} | "
             f"samtools sort -@ {threads} -m {sort_memory} "
             f"-o '{sorted_bam}' -"],
            indeterminate=True,
            heartbeat_stage="minimap2 + samtools sort",
        )

        if not sorted_bam.exists() or sorted_bam.stat().st_size == 0:
            raise RuntimeError("minimap2 没有产出 BAM 文件")

        # ---- Step 2: Index BAM ----
        self.update(pct=75, stage="索引 BAM")
        self.run_command([
            "samtools", "index", str(sorted_bam),
        ], indeterminate=True, heartbeat_stage="samtools index")

        # ---- Step 3: Alignment stats ----
        self.update(pct=85, stage="比对统计")
        stats_file = sorted_bam.with_name(
            sorted_bam.stem + "_align_stats.txt")
        self.run_command([
            "bash", "-c",
            f"samtools flagstat '{sorted_bam}' > '{stats_file}'",
        ], indeterminate=True, heartbeat_stage="samtools flagstat")

        # ---- Parse flagstat for summary ----
        n_total = 0
        n_primary = 0
        n_mapped = 0
        n_secondary = 0
        if stats_file.exists():
            for line in stats_file.read_text(
                    encoding="utf-8", errors="ignore").splitlines():
                if "in total" in line:
                    n_total = int(line.split()[0])
                elif "primary" in line and "mapped" in line:
                    n_primary = int(line.split()[0])
                elif "secondary" in line:
                    n_secondary = int(line.split()[0])
                elif "mapped" in line and "%" in line:
                    n_mapped = int(line.split()[0])

        summary = {
            "input_fastq": fastq,
            "reference_index": index,
            "sorted_bam": str(sorted_bam),
            "bam_index": str(sorted_bam.with_suffix(".bam.bai")),
            "stats_file": str(stats_file) if stats_file.exists() else "",
            "n_total_reads": n_total,
            "n_primary": n_primary,
            "n_secondary": n_secondary,
            "n_mapped": n_mapped,
        }
        (out_dir / "minimap2_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== minimap2 比对完成: {n_mapped}/{n_total} mapped "
                 f"→ {sorted_bam} ===")


if __name__ == "__main__":
    Minimap2AlignRunner.main()
