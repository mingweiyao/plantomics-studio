"""NanoFilt filtering + NanoStat reporting runner for ONT transcriptome data.

Filters and trims ONT reads using NanoFilt, then generates quality
statistics with NanoStat.

Parameters:
  fastq:          str - Input FASTQ file path
  q:              int - Minimum average quality score (default 7)
  min_length:     int - Minimum read length (default 50)
  max_length:     int - Maximum read length (default 0 = no limit)
  headcrop:       int - Trim N bases from read start (default 0)
  tailcrop:       int - Trim N bases from read end (default 0)
  output_prefix:  str - Prefix for output files

Outputs (to output_dir/):
  <output_prefix>.fastq.gz       - Filtered reads
  <output_prefix>_nanostat.txt   - NanoStat summary
  nanofilt_summary.json          - Filtering summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class NanofiltRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq = p.get("fastq", "")
        min_qual = int(p.get("q", 7))
        min_len = int(p.get("min_length", 50))
        max_len = int(p.get("max_length", 0))
        headcrop = int(p.get("headcrop", 0))
        tailcrop = int(p.get("tailcrop", 0))
        output_prefix = p.get("output_prefix", "filtered")

        if not fastq or not Path(fastq).exists():
            raise FileNotFoundError(f"输入 FASTQ 不存在: {fastq}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        filtered_fq = out_dir / f"{output_prefix}.fastq.gz"
        nanostat_out = out_dir / f"{output_prefix}_nanostat.txt"

        # ---- Step 1: NanoFilt filtering via pipe ----
        self.update(pct=5, stage="NanoFilt 过滤", detail=output_prefix)

        cmd = ["NanoFilt"]
        if min_qual > 0:
            cmd.extend(["-q", str(min_qual)])
        if min_len > 0:
            cmd.extend(["-l", str(min_len)])
        if max_len > 0:
            cmd.extend(["--maxlength", str(max_len)])
        if headcrop > 0:
            cmd.extend(["--headcrop", str(headcrop)])
        if tailcrop > 0:
            cmd.extend(["--tailcrop", str(tailcrop)])

        # Pipe: cat fastq | NanoFilt | gzip > output
        self.run_command(
            ["bash", "-c",
             f"cat '{fastq}' | {' '.join(cmd)} | gzip > '{filtered_fq}'"],
            indeterminate=True,
            heartbeat_stage="NanoFilt 过滤",
        )

        if not filtered_fq.exists() or filtered_fq.stat().st_size == 0:
            self.log("!! NanoFilt 过滤后输出为空,跳过统计")
            summary = {
                "input_fastq": fastq,
                "filtered_fastq": str(filtered_fq),
                "read_count": 0,
                "filter_params": {
                    "min_qual": min_qual, "min_length": min_len,
                    "max_length": max_len, "headcrop": headcrop,
                    "tailcrop": tailcrop,
                },
            }
            (out_dir / "nanofilt_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self.update(pct=100, stage="完成")
            self.log("=== NanoFilt 过滤后无 reads 保留 ===")
            return

        # ---- Step 2: NanoStat on filtered output ----
        self.update(pct=60, stage="NanoStat 统计", detail=output_prefix)
        self.run_command([
            "NanoStat", "--fastq", str(filtered_fq),
            "--outdir", str(out_dir),
            "--name", f"{output_prefix}_nanostat.txt",
        ], indeterminate=True, heartbeat_stage="NanoStat")

        # ---- Count reads ----
        read_count = 0
        try:
            import gzip
            with gzip.open(filtered_fq, "rt", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("@"):
                        read_count += 1
        except Exception:
            pass

        summary = {
            "input_fastq": fastq,
            "filtered_fastq": str(filtered_fq),
            "read_count": read_count,
            "filter_params": {
                "min_qual": min_qual,
                "min_length": min_len,
                "max_length": max_len,
                "headcrop": headcrop,
                "tailcrop": tailcrop,
            },
            "nanostat": str(nanostat_out) if nanostat_out.exists() else "",
        }
        (out_dir / "nanofilt_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== NanoFilt 完成: 过滤后 {read_count} 条 reads → {filtered_fq} ===")


if __name__ == "__main__":
    NanofiltRunner.main()
