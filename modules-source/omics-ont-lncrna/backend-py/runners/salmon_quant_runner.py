"""Salmon quantification runner for ONT lncRNA data.

Quantifies transcript expression using Salmon with selective alignment.

Parameters:
  sample_fastqs: [dict] - Sample FASTQ info: [{name, fastq}]
  transcriptome_fasta: str - Transcriptome FASTA (required)
  threads: int          - CPU threads (default 8)

Outputs (to output_dir/):
  salmon_index/           - Salmon index directory
  {sample}/quant.sf      - Per-sample quantification
  salmon_quant_summary.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class SalmonQuantRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        samples = p.get("sample_fastqs", [])
        transcriptome = p.get("transcriptome_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not samples:
            raise ValueError("未提供样本列表(sample_fastqs)")
        if not transcriptome or not Path(transcriptome).exists():
            raise FileNotFoundError(f"转录本 FASTA 不存在: {transcriptome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Build salmon index
        self.update(pct=5, stage="Salmon 索引构建", indeterminate=True)
        idx_dir = out_dir / "salmon_index"
        if not idx_dir.exists():
            self.run_command([
                "salmon", "index",
                "-t", transcriptome,
                "-i", str(idx_dir),
                "-p", str(threads),
            ], indeterminate=True, heartbeat_stage="salmon index")

        # Quantify each sample
        sample_results = []
        n = len(samples)
        for i, s in enumerate(samples):
            name = s.get("name", f"sample_{i}")
            fastq = s.get("fastq", "")
            if not fastq or not Path(fastq).exists():
                self.log(f"!! {name}: FASTQ 不存在 {fastq}, 跳过")
                continue

            self.update(pct=int(10 + 85 * (i + 1) / max(n, 1)),
                        stage=f"Salmon 定量 ({i + 1}/{n})", detail=name)

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            self.run_command([
                "salmon", "quant",
                "-i", str(idx_dir),
                "-l", "A",
                "-r", fastq,
                "-o", str(sample_dir),
                "-p", str(threads),
                "--validateMappings",
            ], indeterminate=True, heartbeat_stage=f"salmon quant {name}")

            quant_file = sample_dir / "quant.sf"
            if quant_file.exists():
                sample_results.append({
                    "name": name,
                    "quant_file": str(quant_file),
                })

        summary = {
            "n_samples": len(sample_results),
            "transcriptome": transcriptome,
            "samples": sample_results,
        }
        (out_dir / "salmon_quant_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== Salmon 定量完成: {len(sample_results)} 样本 ===")


if __name__ == "__main__":
    SalmonQuantRunner.main()
