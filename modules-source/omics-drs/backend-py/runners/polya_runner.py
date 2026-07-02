"""poly(A) tail length detection runner using Dorado.

Detects poly(A) tail lengths from ONT Direct RNA Sequencing data
using Dorado's --estimate-poly-a functionality.

For DRS data, the workflow:
  1. Basecall raw POD5/FAST5 with Dorado (if needed)
  2. Estimate poly(A) tail lengths using Dorado --estimate-poly-a
  3. Output per-read poly(A) length estimates

Parameters:
  pod5_dir: str          - Directory containing POD5/FAST5 files
  fast5_dir: str         - Alternative: FAST5 directory
  basecall_cfg: str      - Dorado basecalling model config (default: "dna_r9.4.1_e8_sup@v3.3")
  model_path: str        - Full path to Dorado model (alternative to basecall_cfg)
  output_bam: str        - Output BAM filename (default: "polya.bam")
  estimate_poly_a: bool  - Run poly(A) estimation (default: True)
  min_qscore: float      - Minimum basecall Q-score for filtering (default: 7)
  threads: int           - Dorado threads (default: 4)
  batchsize: int         - Dorado batch size (default: 256)

Outputs (to output_dir/):
  polya.bam                  - Basecalled + poly(A) tagged alignments
  polya_summary.tsv          - Per-read poly(A) length summary
  polya_lengths.txt          - Simple list of read_id and polyA length
  polya_statistics.json      - Distribution statistics
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
        model_path = p.get("model_path", "")
        output_bam_name = p.get("output_bam", "polya.bam")
        estimate_poly_a = bool(p.get("estimate_poly_a", True))
        min_qscore = float(p.get("min_qscore", 7))
        threads = self.effective_threads(int(p.get("threads", 4)))
        batchsize = int(p.get("batchsize", 256))

        # Determine input directory
        input_dir = ""
        if pod5_dir and Path(pod5_dir).exists():
            input_dir = pod5_dir
        elif fast5_dir and Path(fast5_dir).exists():
            input_dir = fast5_dir
        else:
            raise FileNotFoundError(
                "需要 pod5_dir 或 fast5_dir 指向包含原始数据的目录")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        output_bam = out_dir / output_bam_name

        # Determine model
        if model_path and Path(model_path).exists():
            dorado_model = model_path
        else:
            dorado_model = basecall_cfg

        # ---- Step 1: Dorado basecalling with poly(A) estimation ----
        self.update(pct=10, stage="Dorado basecalling + polyA 估计",
                    indeterminate=True)

        dorado_cmd = [
            "dorado", "basecaller",
            dorado_model,
            input_dir,
            "--emit-moves",
            "--batchsize", str(batchsize),
            "--device", "cpu",
        ]

        if estimate_poly_a:
            dorado_cmd.append("--estimate-poly-a")

        # Pipe output to BAM
        self.run_command(
            dorado_cmd + [">", str(output_bam)],
            indeterminate=True,
            heartbeat_stage="Dorado basecall",
        )

        # Check if output was produced (dorado pipes to stdout, so check stderr logs)
        if not output_bam.exists() or output_bam.stat().st_size == 0:
            # Try alternative: run dorado and redirect separately
            self.log("Dorado 直接输出未产生 BAM,尝试重定向方式")
            self.run_command(
                ["bash", "-c", f"dorado basecaller {dorado_model} '{input_dir}' "
                 f"--emit-moves --batchsize {batchsize} --device cpu "
                 f"{'--estimate-poly-a' if estimate_poly_a else ''} "
                 f"> '{output_bam}'"],
                indeterminate=True, heartbeat_stage="Dorado basecall",
            )

        if not output_bam.exists() or output_bam.stat().st_size == 0:
            raise RuntimeError("Dorado 没有产出 BAM 文件")

        # ---- Step 2: Index BAM ----
        self.update(pct=50, stage="索引 BAM", indeterminate=True)
        self.run_command(["samtools", "index", str(output_bam)],
                         indeterminate=True, heartbeat_stage="samtools index")

        # ---- Step 3: Extract poly(A) tags from BAM ----
        self.update(pct=60, stage="提取 polyA 长度", indeterminate=True)
        summary_tsv = out_dir / "polya_summary.tsv"
        lengths_txt = out_dir / "polya_lengths.txt"

        # Dorado adds poly(A) tags as BAM tags:
        #   pa:i:NN  - poly(A) tail length
        #   ps:i:NN  - poly(A) signal tail length
        #   pt:i:NN  - poly(A) tail start

        # Use samtools view and awk to extract poly(A) tags
        perl_script = (
            'samtools view ' + str(output_bam) + " | "
            'head -n 500000 | '
            "perl -ne '"
            '  @c=split(/\\t/); '
            '  my ($pa,$ps,$pt); '
            '  foreach (@c[11..$#c]) { '
            '    $pa=$1 if /pa:i:(\\d+)/; '
            '    $ps=$1 if /ps:i:(\\d+)/; '
            '    $pt=$1 if /pt:i:(\\d+)/; '
            '  } '
            "  print qq($c[0]\t$pa\t$ps\t$pt\t$c[1]\n) if $pa; "
            "' > " + str(summary_tsv)
        )
        self.run_command([
            "bash", "-c", perl_script,
        ], indeterminate=True, heartbeat_stage="extract polyA tags")

        # Also produce simple length list
        if summary_tsv.exists():
            lengths = []
            with open(summary_tsv, encoding="utf-8") as fh:
                for line in fh:
                    cols = line.strip().split("\t")
                    if len(cols) >= 2 and cols[1]:
                        try:
                            lengths.append(int(cols[1]))
                        except ValueError:
                            pass

            with open(lengths_txt, "w", encoding="utf-8") as f:
                f.write("read_id\tpolya_length\n")
                with open(summary_tsv, encoding="utf-8") as fh:
                    for line in fh:
                        cols = line.strip().split("\t")
                        if len(cols) >= 2:
                            f.write(f"{cols[0]}\t{cols[1]}\n")

            self.log(f"提取到 {len(lengths)} 条 poly(A) 长度")
        else:
            # Try alternative extraction using samtools tag parser
            self.log("尝试替代方法提取 polyA 标签")
            self.run_command([
                "bash", "-c",
                f"samtools view '{output_bam}' | "
                f"awk '{{ for(i=12;i<=NF;i++) if($i ~ /^pa:i:/) "
                f"print $1\"\\t\"substr($i,6) }}' > '{lengths_txt}'",
            ], indeterminate=True, heartbeat_stage="extract polyA alt")

        # ---- Step 4: Statistics ----
        self.update(pct=85, stage="polyA 长度统计")

        polya_lengths = []
        if lengths_txt.exists():
            with open(lengths_txt, encoding="utf-8") as fh:
                header = fh.readline()  # skip header
                for line in fh:
                    cols = line.strip().split("\t")
                    if len(cols) >= 2 and cols[1].strip().isdigit():
                        polya_lengths.append(int(cols[1].strip()))

        n_reads = len(polya_lengths)
        if n_reads > 0:
            import statistics as _stats
            mean_len = _stats.mean(polya_lengths)
            median_len = _stats.median(polya_lengths)
            try:
                stdev_len = _stats.stdev(polya_lengths)
            except Exception:
                stdev_len = 0
        else:
            mean_len = median_len = stdev_len = 0

        stats = {
            "n_reads_with_polya": n_reads,
            "mean_length": round(mean_len, 2),
            "median_length": round(median_len, 2),
            "stdev": round(stdev_len, 2),
            "min_length": min(polya_lengths) if polya_lengths else 0,
            "max_length": max(polya_lengths) if polya_lengths else 0,
            "q25": self._percentile(polya_lengths, 25) if polya_lengths else 0,
            "q75": self._percentile(polya_lengths, 75) if polya_lengths else 0,
            "input_dir": input_dir,
        }
        (out_dir / "polya_statistics.json").write_text(
            json.dumps(stats, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== poly(A) 检测完成: {n_reads} 条 reads 带 polyA, "
                 f"均值 {mean_len:.1f} nt → {out_dir} ===")

    @staticmethod
    def _percentile(data, p):
        import statistics as _stats
        if not data:
            return 0
        sorted_data = sorted(data)
        k = (len(sorted_data) - 1) * p / 100
        f = int(k)
        c = f + 1
        if c >= len(sorted_data):
            return sorted_data[-1]
        return sorted_data[f] + (sorted_data[c] - sorted_data[f]) * (k - f)


if __name__ == "__main__":
    PolyaRunner.main()
