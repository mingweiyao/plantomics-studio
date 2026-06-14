"""Qualimap RNA-seq 文库质量评估 runner。

对 STAR 比对出的每个 BAM 跑 `qualimap rnaseq`,产出文库质量图:
  - 转录本覆盖均匀性(5'→3' 覆盖曲线)
  - reads 基因组来源分布(外显子/内含子/基因间)
  - 测序饱和度
对应商业报告里的"文库质量评估"(随机性分布 / reads 分布 / 测序饱和度)。

参数:
  bam_files:    list[str]   - STAR 输出的 BAM(来自上一步的 manifest)
  sample_names: list[str]   - 与 bam 一一对应(可选,默认从 BAM 名推)
  gtf:          str         - 参考注释 GTF(必填)
  paired:       bool|null   - 是否双端(默认自动从 BAM 检测)
  java_mem:     str         - qualimap JVM 内存,默认 "4G"

产出(到 output_subdir,每样本一个子目录):
  <sample>/qualimapReport.html
  <sample>/rnaseq_qc_results.txt
  <sample>/images_qualimapReport/*.png   (覆盖曲线、reads 来源、饱和度等)
  library_qc_summary.tsv                  (各样本关键指标汇总)
"""
import re
import subprocess
from pathlib import Path

from runners.base import BaseRunner


def detect_bam_paired(bam_path: Path):
    """读 BAM 首条 alignment 的 flag(0x1)判断单/双端;失败返回 None。"""
    try:
        r = subprocess.run(["samtools", "view", str(bam_path)],
                            capture_output=True, text=True, timeout=30,
                            stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            return None
        first = r.stdout.split("\n", 1)[0]
        if not first:
            return None
        fields = first.split("\t")
        return bool(int(fields[1]) & 0x1) if len(fields) >= 2 else None
    except Exception:
        return None


class QualimapRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        bams = params.get("bam_files") or params.get("bams") or []
        sample_names = params.get("sample_names", [])
        gtf = params.get("gtf")
        paired_param = params.get("paired")
        java_mem = params.get("java_mem", "4G")

        if not bams:
            raise ValueError("未提供 BAM 列表(bam_files)")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = self.output_dir()
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        if not sample_names or len(sample_names) != len(bams):
            sample_names = [Path(b).stem.split(".")[0] for b in bams]

        n = len(bams)
        summary_rows = []
        for i, (bam, name) in enumerate(zip(bams, sample_names)):
            bam_p = Path(bam)
            if not bam_p.exists():
                self.log(f"!! 跳过 {name}:BAM 不存在 {bam}")
                continue
            self.update(pct=int(5 + 90 * i / max(n, 1)),
                        stage=f"Qualimap ({i + 1}/{n})", detail=name)

            sample_out = Path(out_dir) / name
            sample_out.mkdir(parents=True, exist_ok=True)

            is_paired = paired_param if paired_param is not None else detect_bam_paired(bam_p)
            cmd = ["qualimap", "rnaseq",
                   "-bam", str(bam_p),
                   "-gtf", str(gtf),
                   "-outdir", str(sample_out),
                   f"--java-mem-size={java_mem}"]
            if is_paired:
                cmd.append("-pe")
            self.log(f"$ {' '.join(cmd)}")
            self.run_command(cmd)

            metrics = self._parse_metrics(sample_out / "rnaseq_qc_results.txt")
            metrics["sample"] = name
            summary_rows.append(metrics)

        # 汇总表
        if summary_rows:
            keys = ["sample", "reads_aligned", "exonic_pct", "intronic_pct",
                    "intergenic_pct", "5_3_bias"]
            lines = ["\t".join(keys)]
            for row in summary_rows:
                lines.append("\t".join(str(row.get(k, "")) for k in keys))
            (Path(out_dir) / "library_qc_summary.tsv").write_text(
                "\n".join(lines) + "\n", encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Qualimap 文库质控完成,{len(summary_rows)} 个样本 ===")

    @staticmethod
    def _parse_metrics(results_txt: Path) -> dict:
        """从 rnaseq_qc_results.txt 抽几个关键指标(尽力而为,解析不到就空)。"""
        m: dict = {}
        if not results_txt.exists():
            return m
        try:
            txt = results_txt.read_text(encoding="utf-8", errors="ignore")
            def grab(pat):
                g = re.search(pat, txt)
                return g.group(1).strip() if g else ""
            m["reads_aligned"] = grab(r"reads aligned\s*=\s*([\d,]+)")
            m["exonic_pct"] = grab(r"exonic\s*=\s*[\d,]+\s*\(([\d.]+%)\)")
            m["intronic_pct"] = grab(r"intronic\s*=\s*[\d,]+\s*\(([\d.]+%)\)")
            m["intergenic_pct"] = grab(r"intergenic\s*=\s*[\d,]+\s*\(([\d.]+%)\)")
            m["5_3_bias"] = grab(r"5'-3' bias\s*=\s*([\d.]+)")
        except Exception:
            pass
        return m


if __name__ == "__main__":
    QualimapRunner.main()
