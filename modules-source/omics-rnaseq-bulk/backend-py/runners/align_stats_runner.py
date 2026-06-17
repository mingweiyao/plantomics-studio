"""比对率统计 runner(对应报告 5.2.1)。

解析 STAR 比对产出的每样本 Log.final.out,汇总成比对率统计表(align_stat.txt),
列与商业报告表 4 一致:
  Sample_Name / Total_Reads / Mapped_Reads(数 + 占比) /
  Uniq_Mapped_Reads(数 + 占比)
另附 Multi_Mapped_Reads(多位点比对)便于排查。
Total_Reads 取 STAR 的 "Number of input reads"(双端为读对数);
Mapped = 唯一比对 + 多位点比对。

参数:
  aligned_dir: str   - STAR 输出目录(扫 <sample>/<sample>.Log.final.out);
                       不传则用 output_path 自身。
产出(到 output_subdir):
  align_stat.txt
"""
import re
from pathlib import Path

from runners.base import BaseRunner


def _num(s) -> int:
    try:
        return int(str(s).strip())
    except (ValueError, AttributeError):
        return 0


class AlignStatsRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        scan_dir = Path(p.get("aligned_dir") or self.output_dir())
        if not scan_dir.is_dir():
            raise FileNotFoundError(f"找不到 STAR 输出目录: {scan_dir}")

        self.update(pct=10, stage="扫描 STAR Log.final.out")
        logs = sorted(scan_dir.rglob("*.Log.final.out"))
        if not logs:
            raise RuntimeError(
                f"{scan_dir} 下没有 *.Log.final.out — 比对统计依赖 STAR 日志,请先跑比对"
            )
        self.log(f"找到 {len(logs)} 个 STAR 日志")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        total = len(logs)
        for i, lf in enumerate(logs):
            sample = lf.name[:-len(".Log.final.out")]
            text = lf.read_text(encoding="utf-8", errors="ignore")

            def grab(label):
                m = re.search(re.escape(label) + r"\s*\|\s*(.+)", text)
                return m.group(1).strip() if m else ""

            n_input = _num(grab("Number of input reads"))
            n_uniq = _num(grab("Uniquely mapped reads number"))
            n_multi = _num(grab("Number of reads mapped to multiple loci"))
            n_mapped = n_uniq + n_multi

            def pct(n):
                return f"{(n / n_input * 100):.2f}%" if n_input else "NA"

            rows.append({
                "Sample_Name": sample,
                "Total_Reads": n_input,
                "Mapped_Reads": f"{n_mapped} ({pct(n_mapped)})",
                "Uniq_Mapped_Reads": f"{n_uniq} ({pct(n_uniq)})",
                "Multi_Mapped_Reads": f"{n_multi} ({pct(n_multi)})",
            })
            self.update(pct=int(10 + (i + 1) / total * 80),
                        stage=f"解析 {i + 1}/{total}")

        rows.sort(key=lambda r: r["Sample_Name"])
        cols = ["Sample_Name", "Total_Reads", "Mapped_Reads",
                "Uniq_Mapped_Reads", "Multi_Mapped_Reads"]
        out_file = out_dir / "align_stat.txt"
        with open(out_file, "w", encoding="utf-8") as wf:
            wf.write("\t".join(cols) + "\n")
            for r in rows:
                wf.write("\t".join(str(r[c]) for c in cols) + "\n")
        self.update(pct=100, stage="完成")
        self.log(f"=== 比对率统计完成,{len(rows)} 个样本 → {out_file} ===")


if __name__ == "__main__":
    AlignStatsRunner.main()
