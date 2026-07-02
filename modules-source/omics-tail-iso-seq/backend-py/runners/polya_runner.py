"""poly(A) tail length detection runner for Tail Iso-seq.

Detects poly(A) tail lengths using Dorado --estimate-poly-a.

Parameters:
  pod5_dir: str          - POD5 directory
  fast5_dir: str         - FAST5 directory (alternative)
  basecall_cfg: str      - Dorado model config
  threads: int           - Dorado threads (default: 4)
  batchsize: int         - Batch size (default: 256)

Outputs (to output_dir/):
  polya.bam               - Basecalled + polyA BAM
  polya_summary.tsv       - Per-read polyA lengths
  polya_lengths.txt       - Simple length list
  polya_statistics.json   - Distribution statistics
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class PolyaRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        pod5_dir = p.get("pod5_dir", "")
        fast5_dir = p.get("fast5_dir", "")
        basecall_cfg = p.get("basecall_cfg", "dna_r9.4.1_e8_sup@v3.3")
        threads = self.effective_threads(int(p.get("threads", 4)))
        batchsize = int(p.get("batchsize", 256))

        input_dir = ""
        if pod5_dir and Path(pod5_dir).exists():
            input_dir = pod5_dir
        elif fast5_dir and Path(fast5_dir).exists():
            input_dir = fast5_dir
        else:
            raise FileNotFoundError("需要 pod5_dir 或 fast5_dir")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        output_bam = out_dir / "polya.bam"

        # Dorado basecalling with polyA estimation
        self.update(pct=10, stage="Dorado basecall + polyA", indeterminate=True)
        self.run_command([
            "bash", "-c",
            f"dorado basecaller {basecall_cfg} '{input_dir}' "
            f"--emit-moves --batchsize {batchsize} --device cpu "
            f"--estimate-poly-a > '{output_bam}'",
        ], indeterminate=True, heartbeat_stage="Dorado")

        if not output_bam.exists() or output_bam.stat().st_size == 0:
            raise RuntimeError("Dorado 没有产出 BAM")

        # Index
        self.update(pct=50, stage="索引 BAM")
        self.run_command(["samtools", "index", str(output_bam)])

        # Extract polyA lengths
        self.update(pct=60, stage="提取 polyA 长度")
        lengths_txt = out_dir / "polya_lengths.txt"
        self.run_command([
            "bash", "-c",
            f"samtools view '{output_bam}' | "
            f"awk '{{ for(i=12;i<=NF;i++) "
            f"if($i ~ /^pa:i:/) print $1\"\\t\"substr($i,6) }}' "
            f"> '{lengths_txt}'",
        ], indeterminate=True, heartbeat_stage="extract polyA")

        # Statistics
        self.update(pct=85, stage="统计")
        polya_lengths = []
        if lengths_txt.exists():
            with open(lengths_txt, encoding="utf-8") as fh:
                for line in fh:
                    cols = line.strip().split("\t")
                    if len(cols) >= 2 and cols[1].strip().isdigit():
                        polya_lengths.append(int(cols[1].strip()))

        n = len(polya_lengths)
        if n > 0:
            mean = sum(polya_lengths) / n
            median = sorted(polya_lengths)[n // 2]
        else:
            mean = median = 0

        stats = {
            "n_reads_with_polya": n,
            "mean_length": round(mean, 2),
            "median_length": round(median, 2),
            "min": min(polya_lengths) if polya_lengths else 0,
            "max": max(polya_lengths) if polya_lengths else 0,
        }
        (out_dir / "polya_statistics.json").write_text(
            json.dumps(stats, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== poly(A) 检测完成: {n} reads → {out_dir} ===")


if __name__ == "__main__":
    PolyaRunner.main()
