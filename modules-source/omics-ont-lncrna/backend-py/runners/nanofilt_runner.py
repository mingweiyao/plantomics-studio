"""NanoFilt QC runner for ONT reads.

Filters and provides quality statistics for ONT long reads.

Parameters:
  fastq_file: str - Input FASTQ file (required)
  min_qual:   int - Minimum average quality score, default 7
  min_len:    int - Minimum read length, default 50

Outputs (to output_subdir):
  {name}_filtered.fastq - Filtered reads
  {name}_stats.txt     - NanoStat output
  nanofilt_summary.json
"""
import json
import subprocess
from pathlib import Path

from runners.base import BaseRunner


class NanofiltRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq_file = p.get("fastq_file")
        min_qual = int(p.get("min_qual", 7))
        min_len = int(p.get("min_len", 50))

        if not fastq_file or not Path(fastq_file).exists():
            raise FileNotFoundError(f"FASTQ 文件不存在: {fastq_file}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        name = Path(fastq_file).stem.split(".")[0]
        filtered = out_dir / f"{name}_filtered.fastq"

        # Step 1: NanoFilt filtering via stdin/stdout
        self.update(pct=10, stage="NanoFilt 过滤", indeterminate=True)
        nanofilt_cmd = ["NanoFilt", "-q", str(min_qual), "-l", str(min_len)]
        self.log(f"$ {nanofilt_cmd} < {fastq_file} > {filtered}")
        with open(fastq_file, "r", encoding="utf-8", errors="ignore") as fin:
            with open(filtered, "w", encoding="utf-8") as fout:
                proc = subprocess.Popen(
                    nanofilt_cmd, stdin=fin, stdout=fout,
                    stderr=subprocess.PIPE, text=True,
                )
                _, stderr = proc.communicate()
                if stderr:
                    for line in stderr.splitlines():
                        self.log(f"  {line}")
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, nanofilt_cmd)
        self.log(f"  NanoFilt 过滤完成: {filtered}")

        if not filtered.exists() or filtered.stat().st_size == 0:
            raise RuntimeError("NanoFilt 没产出结果")

        # Step 2: NanoStat
        self.update(pct=60, stage="NanoStat 统计")
        stats_out = out_dir / f"{name}_stats"
        nanostat_cmd = [
            "NanoStat", "--fastq", str(filtered),
            "-o", str(out_dir), "-n", str(stats_out),
        ]
        self.run_command(nanostat_cmd)
        self.log(f"  NanoStat 统计完成: {stats_out}.txt")

        # Parse NanoStat output
        stats = {"min_qual": min_qual, "min_len": min_len}
        stat_file = Path(str(stats_out) + ".txt")
        if stat_file.exists():
            for line in stat_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    stats[key.strip()] = val.strip()

        # Count reads in filtered file
        n_reads = 0
        with open(filtered, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("@"):
                    n_reads += 1
        stats["n_reads_filtered"] = n_reads

        summary = {
            "input": str(fastq_file),
            "output": str(filtered),
            "min_qual": min_qual,
            "min_len": min_len,
            "n_filtered_reads": n_reads,
            "nanostat": stats,
        }
        (out_dir / "nanofilt_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== NanoFilt 完成, 过滤后 reads: {n_reads} ===")


if __name__ == "__main__":
    NanofiltRunner.main()
