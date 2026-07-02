"""poly(A) tail proportion/length detection runner using Dorado.

Detects poly(A) tails from ONT Direct RNA Sequencing data
using Dorado's --estimate-poly-a functionality.

Workflow:
  1. Basecall raw POD5 with Dorado --estimate-poly-a
  2. Parse poly(A) tags (pt:i: and ps:i: tags in BAM)
  3. Estimate poly(A) proportion and mean length

Parameters:
  pod5_dir: str          - Directory containing POD5 files (required)
  model: str             - Dorado basecalling model (default: dna_r9.4.1_e8_sup@v3.3)
  output_name: str       - Output files basename (default: "polya_output")
  threads: int           - Dorado threads (default 8)

Outputs (to output_dir/):
  polya_output.bam                 - Basecalled + poly(A) tagged BAM
  polya_lengths.tsv                - Per-read poly(A) lengths
  polya_detect_summary.json        - Detection summary
"""
import json
from pathlib import Path
import shutil
import statistics as _stats

from runners.base import BaseRunner


class PolyaDetectRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        pod5_dir = p.get("pod5_dir", "")
        model = p.get("model", "dna_r9.4.1_e8_sup@v3.3")
        output_name = p.get("output_name", "polya_output")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not pod5_dir or not Path(pod5_dir).is_dir():
            raise NotADirectoryError(f"pod5 目录不存在: {pod5_dir}")

        if not shutil.which("dorado"):
            raise FileNotFoundError("找不到 dorado")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        output_bam = out_dir / f"{output_name}.bam"
        lengths_tsv = out_dir / "polya_lengths.tsv"

        # ---- Step 1: Dorado basecalling with poly(A) estimation ----
        self.update(pct=5, stage="Dorado basecalling + polyA 估计",
                    indeterminate=True)

        cmd = [
            "dorado", "basecaller",
            model, pod5_dir,
            "--estimate-poly-a",
        ]

        if not shutil.which("nvidia-smi"):
            cmd.append("--device")
            cmd.append("cpu")

        self.run_command(
            ["bash", "-c", f"{' '.join(cmd)} > '{output_bam}'"],
            indeterminate=True,
            heartbeat_stage="Dorado basecall + polyA",
        )

        if not output_bam.exists() or output_bam.stat().st_size == 0:
            raise RuntimeError("Dorado 没有产出 BAM 文件")

        # ---- Step 2: Index BAM ----
        self.update(pct=50, stage="索引 BAM", indeterminate=True)
        self.run_command(
            ["samtools", "index", str(output_bam)],
            indeterminate=True, heartbeat_stage="samtools index")

        # ---- Step 3: Extract poly(A) tags from BAM ----
        self.update(pct=60, stage="提取 polyA 标签", indeterminate=True)

        # Dorado adds poly(A) tags as BAM tags:
        #   pt:i:NN  - poly(A) tail length (in nucleotides)
        #   ps:i:NN  - poly(A) signal tail length
        self.run_command([
            "bash", "-c",
            f"samtools view '{output_bam}' | "
            f"awk '{{ for(i=12;i<=NF;i++) "
            f"if($i ~ /^pt:i:/) print $1\"\\t\"substr($i,6); "
            f"else if($i ~ /^ps:i:/ && !match($0,/pt:i:/)) "
            f"print $1\"\\t\"substr($i,6) }}' > '{lengths_tsv}'",
        ], indeterminate=True, heartbeat_stage="extract polyA tags")

        # ---- Step 4: Compute poly(A) statistics ----
        self.update(pct=85, stage="polyA 统计")

        polya_lengths = []
        total_reads = 0

        if output_bam.exists():
            try:
                result = self.run_command(
                    ["samtools", "view", "-c", str(output_bam)],
                )
                # run_command raises CalledProcessError on non-zero,
                # so if we get here, it worked. But we can't easily capture output
                # from run_command since it streams to log. Let's count differently.
            except Exception:
                pass

        # Count reads from the lengths file
        n_with_polya = 0
        if lengths_tsv.exists():
            with open(lengths_tsv, encoding="utf-8") as fh:
                for line in fh:
                    cols = line.strip().split("\t")
                    if len(cols) >= 2 and cols[1].strip().isdigit():
                        polya_lengths.append(int(cols[1].strip()))
                        n_with_polya += 1

        self.log(f"提取到 {n_with_polya} 条带有 polyA 标签的 reads")

        n_reads = len(polya_lengths)
        if n_reads > 0:
            mean_len = _stats.mean(polya_lengths)
            median_len = _stats.median(polya_lengths)
            try:
                stdev_len = _stats.stdev(polya_lengths)
            except Exception:
                stdev_len = 0
        else:
            mean_len = median_len = stdev_len = 0

        # We don't have total_reads from the BAM without counting,
        # try to get it via samtools
        try:
            import subprocess
            result = subprocess.run(
                ["samtools", "view", "-c", str(output_bam)],
                capture_output=True, text=True, timeout=60)
            total_reads = int(result.stdout.strip())
        except Exception:
            total_reads = 0

        polya_proportion = round(
            n_reads / max(total_reads, 1) * 100, 2) if total_reads > 0 else 0

        summary = {
            "n_reads": total_reads,
            "n_with_polya": n_reads,
            "polya_proportion": polya_proportion,
            "mean_polya_length": round(mean_len, 2),
            "median_polya_length": round(median_len, 2),
            "stdev_polya_length": round(stdev_len, 2),
            "min_polya_length": min(polya_lengths) if polya_lengths else 0,
            "max_polya_length": max(polya_lengths) if polya_lengths else 0,
            "model": model,
            "pod5_dir": pod5_dir,
            "output_bam": str(output_bam),
        }
        (out_dir / "polya_detect_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== poly(A) 检测完成: {n_reads}/{total_reads} reads 带 polyA, "
                 f"比例 {polya_proportion}%, 平均长度 {mean_len:.1f} nt → {out_dir} ===")


if __name__ == "__main__":
    PolyaDetectRunner.main()
