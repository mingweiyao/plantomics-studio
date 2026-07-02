"""Bowtie alignment runner for miRNA analysis.

工作流程(每样本):
  1. bowtie-build 索引(如果没有)
  2. bowtie -v 0 -S 比对到基因组
  3. samtools sort -> BAM + index
  4. miRDeep2 mapper.pl -> collapsed.fa + .arf

参数:
  genome_fasta: str           - 基因组 FASTA 路径
  samples: list[str|dict]     - fastq 文件路径列表,或 [{name, fastq}]
  index_dir:  str             - bowtie 索引目录(可选)
  threads:    int             - 每样本线程数(默认 4)
  parallel:   int             - 并行样本数(默认 1)
  bowtie_mismatches: int      - bowtie -v 错配数(默认 0)
  skip_bam: bool              - 跳过 BAM 生成(只做 mapper.pl)

产出:
  <output>/<sample>/
    <sample>.sam / .bam / .bam.bai
    <sample>_collapsed.fa
    <sample>_vs_genome.arf
"""
import os
from pathlib import Path

from runners.base import BaseRunner


class BowtieAlignRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        genome_fasta = params.get("genome_fasta", "")
        samples_raw = params.get("samples", [])
        index_dir_str = params.get("index_dir", "")
        threads = int(params.get("threads", 4))
        parallel = int(params.get("parallel", 1))
        mismatches = int(params.get("bowtie_mismatches", 0))
        skip_bam = bool(params.get("skip_bam", False))

        if not genome_fasta:
            raise ValueError("需要 genome_fasta")
        genome_path = Path(genome_fasta)
        if not genome_path.exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")

        parallel, threads = self.effective_parallel_alloc(parallel, threads)

        out_dir = self.output_dir()

        # 规范化为 list[dict]
        samples: list[dict] = []
        for s in samples_raw:
            if isinstance(s, str):
                name = Path(s).stem
                samples.append({"name": name, "fastq": s})
            elif isinstance(s, dict):
                samples.append(s)
            else:
                self.log(f"跳过无法识别的样本: {s}")

        if not samples:
            raise ValueError("没有有效的样本")

        # 1. 构建 bowtie 索引
        index_dir = Path(index_dir_str) if index_dir_str else (out_dir / "index")
        index_dir.mkdir(parents=True, exist_ok=True)

        # 索引前缀 = FASTA 文件名(无扩展名)
        index_prefix = genome_path.stem
        # bowtie 索引文件: <prefix>.1.ebwt
        idx_file = index_dir / f"{index_prefix}.1.ebwt"
        idx_file_alt = index_dir / f"{index_prefix}.1.ebwtl"  # 大索引

        if not (idx_file.exists() or idx_file_alt.exists()):
            self.log(f"=== 阶段 1: bowtie-build 索引 ===")
            self.log(f"  输入: {genome_fasta}")
            self.log(f"  输出: {index_dir}/{index_prefix}.*.ebwt")
            self.update(pct=5, stage="bowtie-build 索引")
            self.run_command(
                ["bowtie-build", "--threads", str(threads),
                 str(genome_path), str(index_dir / index_prefix)],
                timeout=7200,
            )
            self.log(f"  bowtie 索引构建完成")
        else:
            self.log(f"  bowtie 索引已存在,跳过构建")

        idx_path = str(index_dir / index_prefix)

        # 确保 samtools 可用
        have_samtools = self._check_tool("samtools")

        # 2. 处理每个样本
        total = len(samples)
        self.log(f"=== 阶段 2: 比对 {total} 个样本 ===")

        for i, sample in enumerate(samples):
            if self._cancelled:
                raise InterruptedError("任务取消")

            name = sample.get("name", f"sample_{i}")
            fastq = sample.get("fastq", "")

            if not fastq or not Path(fastq).exists():
                self.log(f"  跳过 {name}: fastq 不存在 ({fastq})")
                continue

            pct_base = 20 + int(70 * i / total)
            self.update(pct=pct_base, stage=f"比对 {i+1}/{total}: {name}")

            sample_dir = out_dir / name
            sample_dir.mkdir(parents=True, exist_ok=True)

            # 2a. bowtie 比对
            sam_file = sample_dir / f"{name}.sam"
            self.log(f"  [{name}] bowtie 比对")
            self.run_command(
                ["bowtie", "-q",
                 "-v", str(mismatches),
                 "--threads", str(threads),
                 "-S",
                 idx_path,
                 str(fastq),
                 str(sam_file)],
                timeout=14400,
            )

            # 2b. samtools sort -> BAM (除非跳过)
            if have_samtools and not skip_bam:
                bam_file = sample_dir / f"{name}.bam"
                self.log(f"  [{name}] samtools sort -> BAM")
                self.run_command(
                    ["samtools", "sort",
                     "-@", str(max(1, threads - 1)),
                     "-o", str(bam_file),
                     str(sam_file)],
                    timeout=7200,
                )
                self.log(f"  [{name}] samtools index")
                self.run_command(
                    ["samtools", "index", str(bam_file)],
                    timeout=3600,
                )
                # 删除中间 SAM 以节省空间
                try:
                    sam_file.unlink()
                except OSError:
                    pass
            else:
                self.log(f"  [{name}] 跳过 BAM 生成")

            # 2c. miRDeep2 mapper.pl -> collapsed.fa + .arf
            collapsed_fa = sample_dir / f"{name}_collapsed.fa"
            arf_file = sample_dir / f"{name}_vs_genome.arf"

            self.log(f"  [{name}] mapper.pl: collapsed.fa + .arf")
            self.run_command(
                ["mapper.pl",
                 str(fastq),
                 "-c",       # collapsed output
                 "-j",       # just compute (no full miRDeep2)
                 "-k", idx_path,
                 "-s", str(collapsed_fa),
                 "-t", str(arf_file),
                 "-p", "/usr/bin/bowtie",
                 "-v",       # verbose
                 "-n",       # allow Ns
                 "-h",       # include hard clipped reads
                 ],
                timeout=14400,
            )

            self.log(f"  [{name}] 完成: {collapsed_fa.name}, {arf_file.name}")

        self.update(pct=95, stage="完成")
        self.log("所有样本比对完成")

    def _check_tool(self, name: str) -> bool:
        """检查工具是否可用。"""
        import shutil
        return shutil.which(name) is not None


if __name__ == "__main__":
    BowtieAlignRunner.main()
