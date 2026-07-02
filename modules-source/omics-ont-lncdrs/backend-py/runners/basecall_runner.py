"""Dorado basecalling runner for ONT Direct RNA Sequencing data.

Basecalls raw Oxford Nanopore signal data (pod5) to FASTQ using Dorado.

Parameters:
  pod5_dir: str       - Directory containing pod5 files (required)
  model: str          - Basecalling model (default: dna_r9.4.1_e8_sup@v3.3)
  kit: str            - Kit name (optional)
  estimate_poly_a: bool - Run with --estimate-poly-a flag (default False)
  device: str         - Device: cpu (default) or cuda:all
  threads: int        - CPU threads (default 8)

Outputs (to output_dir/):
  <output>.fastq              - Basecalled reads
  basecall_summary.json       - Basecalling summary
"""
import json
from pathlib import Path
import shutil

from runners.base import BaseRunner


class BasecallRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        pod5_dir = p.get("pod5_dir", "")
        model = p.get("model", "dna_r9.4.1_e8_sup@v3.3")
        kit = p.get("kit", "")
        estimate_poly_a = bool(p.get("estimate_poly_a", False))
        device = p.get("device", "cpu")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not pod5_dir or not Path(pod5_dir).is_dir():
            raise NotADirectoryError(f"pod5 目录不存在: {pod5_dir}")

        # Verify dorado is available
        if not shutil.which("dorado"):
            raise FileNotFoundError(
                "找不到 dorado。请确保 conda 环境中已安装 dorado。")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Count pod5 files
        pod5_files = sorted(Path(pod5_dir).glob("*.pod5"))
        if not pod5_files:
            raise FileNotFoundError(f"在 {pod5_dir} 中未找到 .pod5 文件")

        self.log(f"检测到 {len(pod5_files)} 个 pod5 文件, 使用 Dorado 进行碱基识别")

        # Output
        output_fastq = out_dir / "basecall_output.fastq"

        # Build dorado command
        self.update(pct=5, stage="Dorado 碱基识别", indeterminate=True)

        # Determine device flag
        device_flag = "--device"
        if device == "cpu":
            device_flag = ""  # dorado uses cpu by default

        cmd = ["dorado", "basecaller", model, pod5_dir, "--emit-fastq"]
        if device != "cpu" and device:
            cmd.extend(["--device", device])
        if kit:
            cmd.extend(["--kit-name", kit])
        if estimate_poly_a:
            cmd.append("--estimate-poly-a")

        # Run dorado, redirect stdout to fastq file
        self.run_command(
            ["bash", "-c", f"{' '.join(cmd)} > '{output_fastq}'"],
            indeterminate=True,
            heartbeat_stage="Dorado basecalling",
        )

        if not output_fastq.exists() or output_fastq.stat().st_size == 0:
            raise RuntimeError("Dorado 没有产出 FASTQ 文件")

        # Count reads
        read_count = 0
        try:
            with open(output_fastq, "rb") as fh:
                for line in fh:
                    if line.startswith(b"@"):
                        read_count += 1
        except Exception:
            pass

        summary = {
            "read_count": read_count,
            "model_used": model,
            "kit": kit,
            "device": device,
            "pod5_dir": pod5_dir,
            "n_pod5_files": len(pod5_files),
            "estimate_poly_a": estimate_poly_a,
            "output_fastq": str(output_fastq),
        }
        (out_dir / "basecall_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Dorado 碱基识别完成: {read_count} 条 reads → {output_fastq} ===")


if __name__ == "__main__":
    BasecallRunner.main()
