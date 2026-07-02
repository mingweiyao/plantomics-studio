"""FastQC runner - 多样本并行 QC,带汇总报告。

参数:
  fastq_files: list[str]   - 要 QC 的 fastq 文件列表
  parallel: int            - 同时跑几个 fastqc(默认 4)
  summary_label: str       - 汇总文件标签(默认 "summary"),用于区分 raw/trimmed

产出:
  <output_subdir>/<sample>/<sample>_fastqc.html / .zip
  <output_subdir>/fastqc_<label>_summary.tsv
"""
import io
import re
import zipfile
from pathlib import Path
from runners.base import BaseRunner


def _sample_name(fastq_path: str) -> str:
    name = Path(fastq_path).name
    name = re.sub(r"\.(fq|fastq)(\.gz)?$", "", name)
    return name


class FastqcRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        fastq_files = params.get("fastq_files", [])
        parallel = int(params.get("parallel", params.get("threads", 4)))
        parallel = self.effective_threads(parallel)
        summary_label = params.get("summary_label", "summary")

        if not fastq_files:
            raise ValueError("未提供 fastq_files")

        for f in fastq_files:
            if not Path(f).exists():
                raise FileNotFoundError(f"fastq 不存在: {f}")

        out_dir = self.output_dir()

        def process_one(fastq_path):
            sample = _sample_name(fastq_path)
            sample_dir = out_dir / sample
            sample_dir.mkdir(parents=True, exist_ok=True)
            self.run_command(
                ["fastqc",
                 "--outdir", str(sample_dir),
                 "--quiet",
                 fastq_path],
                timeout=3600,
            )

        self.run_in_parallel(
            func=process_one,
            items=fastq_files,
            workers=parallel,
            desc=f"FastQC 质控(并行 {parallel})",
        )

        # -- 汇总 --
        self.update(pct=92, stage="生成汇总报告")
        self._write_summary(out_dir, summary_label)
        self.update(pct=96, stage="MultiQC 合并报告")
        self._run_multiqc(out_dir, summary_label)
        self.update(pct=100, stage="完成")

    def _run_multiqc(self, out_dir: Path, label: str):
        import shutil as _sh
        import subprocess as _sp
        if not _sh.which("multiqc"):
            self.log("未安装 multiqc,跳过合并报告(基础汇总 TSV 已生成)")
            return
        report_name = f"multiqc_{label}"
        try:
            proc = _sp.run(
                ["multiqc", str(out_dir), "-o", str(out_dir),
                 "-n", report_name, "-f", "--no-ansi"],
                capture_output=True, text=True, timeout=1800,
            )
            if proc.returncode == 0:
                self.log(f"MultiQC 报告 -> {report_name}.html")
            else:
                self.log(f"MultiQC 非零退出({proc.returncode}),跳过;"
                         f"stderr: {proc.stderr[-300:] if proc.stderr else ''}")
        except Exception as e:
            self.log(f"MultiQC 运行失败,跳过: {e}")

    def _write_summary(self, out_dir: Path, label: str):
        zips = sorted(out_dir.rglob("*_fastqc.zip"))
        if not zips:
            self.log("没找到 fastqc zip,跳过汇总")
            return

        rows = []
        for z in zips:
            try:
                row = self._extract_metrics(z)
                if row:
                    rows.append(row)
            except Exception as e:
                self.log(f"  解析 {z.name} 失败: {e}")

        if not rows:
            self.log("没拿到任何样本指标,跳过汇总")
            return

        cols = [
            "filename",
            "total_sequences",
            "sequence_length",
            "%GC",
            "encoding",
            "Per_base_sequence_quality",
            "Per_tile_sequence_quality",
            "Per_sequence_quality_scores",
            "Per_base_sequence_content",
            "Per_sequence_GC_content",
            "Per_base_N_content",
            "Sequence_Length_Distribution",
            "Sequence_Duplication_Levels",
            "Overrepresented_sequences",
            "Adapter_Content",
        ]

        summary_path = out_dir / f"fastqc_{label}_summary.tsv"
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\t".join(cols) + "\n")
            for r in rows:
                f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")
        self.log(f"汇总报告 -> {summary_path.name} ({len(rows)} 个文件)")

    def _extract_metrics(self, zip_path: Path) -> dict:
        result = {"filename": zip_path.stem.replace("_fastqc", "")}

        with zipfile.ZipFile(zip_path) as zf:
            data_name = None
            summary_name = None
            for name in zf.namelist():
                if name.endswith("fastqc_data.txt"):
                    data_name = name
                elif name.endswith("summary.txt"):
                    summary_name = name

            if data_name:
                with zf.open(data_name) as f:
                    text = io.TextIOWrapper(f, encoding="utf-8").read()
                in_basic = False
                for line in text.splitlines():
                    if line.startswith(">>Basic Statistics"):
                        in_basic = True
                        continue
                    if in_basic and line.startswith(">>END_MODULE"):
                        break
                    if in_basic and "\t" in line and not line.startswith("#"):
                        k, v = line.split("\t", 1)
                        if k == "Total Sequences":
                            result["total_sequences"] = v
                        elif k == "Sequence length":
                            result["sequence_length"] = v
                        elif k == "%GC":
                            result["%GC"] = v
                        elif k == "Encoding":
                            result["encoding"] = v

            if summary_name:
                with zf.open(summary_name) as f:
                    text = io.TextIOWrapper(f, encoding="utf-8").read()
                for line in text.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 2:
                        status, module = parts[0], parts[1]
                        key = module.replace(" ", "_")
                        result[key] = status

        return result


if __name__ == "__main__":
    FastqcRunner.main()
