"""Salmon 转录本定量 runner。

对 ONT 全长转录本进行基于比对(Salmon)的定量分析。

参数:
  fastq:      str   - 输入 fastq 文件
  index:      str   - Salmon 索引目录
  lib_type:   str   - 文库类型,默认 A(自动检测)
  output_dir: str   - 输出子目录名
  extra_opts: str   - 额外参数(追加到命令行)
  threads:    int   - 默认 8

产出(到 output_subdir):
  <output_dir>/quant.sf           - 定量结果(TPM/EstCount)
  <output_dir>/quant.sf           - 定量结果文件
  <output_dir>/aux_info/          - 辅助信息
  salmon_quant_summary.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class SalmonQuantRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq = p.get("fastq")
        index = p.get("index")
        lib_type = p.get("lib_type", "A")
        out_name = p.get("output_dir", "salmon_quant")
        extra = p.get("extra_opts", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq or not Path(fastq).exists():
            raise FileNotFoundError(f"fastq 不存在: {fastq}")
        if not index or not Path(index).exists():
            raise FileNotFoundError(f"Salmon 索引不存在: {index}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        quant_dir = out_dir / out_name

        self.update(pct=10, stage="Salmon 定量", detail=f"文库类型={lib_type}")

        cmd = [
            "salmon", "quant",
            "-i", str(index),
            "-l", lib_type,
            "-r", str(fastq),
            "-o", str(quant_dir),
            "-p", str(threads),
            "--validateMappings",
        ]
        if extra:
            cmd.extend(extra.split())

        self.run_command(cmd, heartbeat_stage="Salmon quant", indeterminate=True)

        # 解析结果
        sf = quant_dir / "quant.sf"
        summary = {"output": str(quant_dir), "total_transcripts": 0, "total_numreads": 0}
        if sf.exists():
            n = 0
            total_reads = 0.0
            with open(sf) as f:
                header = next(f, "")
                for line in f:
                    cols = line.strip().split("\t")
                    if len(cols) >= 4:
                        n += 1
                        total_reads += float(cols[3])
            summary["total_transcripts"] = n
            summary["total_numreads"] = total_reads
            summary["quant_sf"] = str(sf)
            self.log(f"  Salmon 定量完成: {n} 条转录本, {total_reads:.0f} 条 read 比对")
        else:
            self.log("  !! quant.sf 未生成,量化可能失败")

        (out_dir / "salmon_quant_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")


if __name__ == "__main__":
    SalmonQuantRunner.main()
