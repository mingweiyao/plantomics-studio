"""Dorado/Guppy basecalling runner for ONT transcriptome data.

Basecalls raw Oxford Nanopore signal data (pod5/fast5) to FASTQ using
Dorado (pod5) or Guppy (fast5) basecallers.

Parameters:
  input_dir:     str - Directory containing pod5 or fast5 files
  model:         str - Basecalling model (default: dna_r10.4.1_e8.2_400bps_hac@v4.2.0)
  kit:           str - Library preparation kit (default: SQK-LSK114)
  sample_name:   str - Sample name for output labelling
  output_format: str - Output format: fastq (default) / cram / bam
  skip_qscore:   bool - Skip basecall quality scoring (default False)
  trim:          bool - Auto-trim adapters (default True)
  device:        str - Basecalling device: cuda:0 (default) / cpu
  batchsize:     int - Reads per batch (default 256)
  threads:       int - CPU threads for basecalling (default 8)

Outputs (to output_dir/):
  <sample_name>.fastq.gz       - Basecalled reads (or .bam / .cram)
  <sample_name>.fastq.gz.bai   - samtools index of output
  basecall_summary.json        - Basecalling summary
"""
import json
import shutil
from pathlib import Path

from runners.base import BaseRunner


class BasecallRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        input_dir = p.get("input_dir", "")
        model = p.get("model", "dna_r10.4.1_e8.2_400bps_hac@v4.2.0")
        kit = p.get("kit", "SQK-LSK114")
        sample_name = p.get("sample_name", "sample")
        output_format = p.get("output_format", "fastq").lower()
        skip_qscore = bool(p.get("skip_qscore", False))
        trim = bool(p.get("trim", True))
        device = p.get("device", "cuda:0")
        batchsize = int(p.get("batchsize", 256))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not input_dir or not Path(input_dir).is_dir():
            raise NotADirectoryError(f"输入目录不存在或不是目录: {input_dir}")

        if output_format not in ("fastq", "bam", "cram"):
            raise ValueError(f"不支持的输出格式: {output_format}, 需要 fastq/bam/cram")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Detect file type: pod5 or fast5
        pod5_files = sorted(Path(input_dir).glob("*.pod5"))
        fast5_files = sorted(Path(input_dir).glob("*.fast5"))

        if pod5_files:
            basecaller = "dorado"
        elif fast5_files:
            basecaller = "guppy_basecaller"
        else:
            raise FileNotFoundError(
                f"在 {input_dir} 中未找到 .pod5 或 .fast5 文件")

        self.log(f"检测到 {len(pod5_files or fast5_files)} 个信号文件, "
                 f"使用 {basecaller} 进行碱基识别")

        tools_ok = shutil.which(basecaller)
        if not tools_ok:
            raise FileNotFoundError(
                f"找不到 {basecaller}。请确保模块 conda 环境中已安装 "
                f"{'dorado' if basecaller == 'dorado' else 'guppy'}。")

        # Output filename based on format
        ext = {"fastq": ".fastq.gz", "bam": ".bam", "cram": ".cram"}[output_format]
        output_file = out_dir / f"{sample_name}{ext}"

        # ---- Basecalling ----
        self.update(pct=5, stage=f"正在碱基识别 ({basecaller})", detail=sample_name)

        if basecaller == "dorado":
            cmd = [
                "dorado", "basecaller",
                model,
                input_dir,
                "--kit-name", kit,
                "--device", device,
                "--batchsize", str(batchsize),
            ]
            if skip_qscore:
                cmd.append("--skip-skip")
            if not trim:
                cmd.append("--no-trim")
            # dorado outputs to stdout; redirect to file
            self.run_command(
                ["bash", "-c",
                 f"{' '.join(cmd)} > '{output_file}' 2> >(grep -v '^\\[' >&2)"],
                indeterminate=True,
                heartbeat_stage=f"dorado basecaller {sample_name}",
            )
        else:  # guppy_basecaller
            cmd = [
                "guppy_basecaller",
                "-i", input_dir,
                "-s", str(out_dir / "guppy_out"),
                "-c", f"dna_r10.4.1_e8.2_400bps_hac.cfg",
                "--kit", kit,
                "--device", device,
                "--num_callers", str(threads),
                "--gpu_runners_per_device", str(max(1, batchsize // 64)),
                "--chunks_per_runner", str(min(256, batchsize)),
            ]
            if skip_qscore:
                cmd.append("--disable_qscore")
            if trim:
                cmd.append("--trim_adapters")
            self.run_command(
                cmd,
                indeterminate=True,
                heartbeat_stage=f"guppy basecaller {sample_name}",
            )
            # Guppy outputs in chunks; concatenate FASTQ
            guppy_fastq_dir = out_dir / "guppy_out" / "pass"
            if guppy_fastq_dir.exists():
                self.update(pct=60, stage="合并 Guppy FASTQ 文件")
                self.run_command(
                    ["bash", "-c",
                     f"cat '{guppy_fastq_dir}'/*.fastq.gz > '{output_file}'"],
                    indeterminate=True,
                )

        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError(f"{basecaller} 没有产出碱基识别结果文件")

        # ---- Convert BAM/CRAM to FASTQ if requested ----
        if output_format == "fastq" and output_file.suffix in (".bam", ".cram"):
            self.update(pct=75, stage="转换 BAM→FASTQ")
            fastq_out = out_dir / f"{sample_name}.fastq.gz"
            self.run_command([
                "samtools", "fastq", str(output_file),
                "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage="samtools fastq")
            output_file = fastq_out

        # ---- Index with samtools ----
        self.update(pct=85, stage="索引碱基识别结果")
        if output_format == "fastq":
            # Index FASTQ with samtools fqidx
            self.run_command([
                "samtools", "fqidx", str(output_file),
                "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage="samtools fqidx")
        else:
            # Index BAM/CRAM
            self.run_command([
                "samtools", "index", str(output_file),
                "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage="samtools index")

        # ---- Summary ----
        n_reads = 0
        try:
            with open(output_file, "rb") as fh:
                for line in fh:
                    if line.startswith(b"@"):
                        n_reads += 1
        except Exception:
            pass

        summary = {
            "sample_name": sample_name,
            "basecaller": basecaller,
            "model": model,
            "kit": kit,
            "device": device,
            "output_format": output_format,
            "n_signal_files": len(pod5_files or fast5_files),
            "n_reads": n_reads,
            "output_file": str(output_file),
        }
        (out_dir / "basecall_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 碱基识别完成: {sample_name}, {n_reads} 条 reads, "
                 f"→ {output_file} ===")


if __name__ == "__main__":
    BasecallRunner.main()
