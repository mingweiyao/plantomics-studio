"""测序数据量统计 runner(对应报告 5.1.1)。

解析 fastp 过滤产出的每样本 JSON 报告,汇总成一张数据量统计表(stat.all.txt),
列与商业报告表 3 一致:
  Sample / Raw_reads / Raw_bases / Clean_reads / Clean_bases /
  Q20_rate / Q30_rate / GC_content
其中 Raw 取 fastp 的 before_filtering,Clean 取 after_filtering。

参数:
  trimmed_dir: str   - fastp 输出目录(扫 <sample>/<sample>.fastp.json);
                       不传则用 output_path 自身。
产出(到 output_subdir):
  stat.all.txt       - 制表符分隔的数据量统计表
"""
import json
from pathlib import Path

from runners.base import BaseRunner


def _fmt_pct(x) -> str:
    try:
        return f"{float(x) * 100:.2f}%"
    except (TypeError, ValueError):
        return "NA"


class DataVolumeStatsRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        scan_dir = Path(p.get("trimmed_dir") or self.output_dir())
        if not scan_dir.is_dir():
            raise FileNotFoundError(f"找不到 fastp 输出目录: {scan_dir}")

        self.update(pct=10, stage="扫描 fastp JSON 报告")
        json_files = sorted(scan_dir.rglob("*.fastp.json"))
        if not json_files:
            raise RuntimeError(
                f"{scan_dir} 下没有 *.fastp.json — 数据量统计依赖 fastp 报告,请先跑过滤"
            )
        self.log(f"找到 {len(json_files)} 个 fastp 报告")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = []
        total = len(json_files)
        for i, jf in enumerate(json_files):
            sample = jf.name[:-len(".fastp.json")]
            try:
                d = json.loads(jf.read_text(encoding="utf-8", errors="ignore"))
            except Exception as e:
                self.log(f"!! 解析失败 {jf}: {e}")
                continue
            summary = d.get("summary", {})
            before = summary.get("before_filtering", {})
            after = summary.get("after_filtering", {})
            rows.append({
                "Sample": sample,
                "Raw_reads": before.get("total_reads", ""),
                "Raw_bases": before.get("total_bases", ""),
                "Clean_reads": after.get("total_reads", ""),
                "Clean_bases": after.get("total_bases", ""),
                "Q20_rate": _fmt_pct(after.get("q20_rate")),
                "Q30_rate": _fmt_pct(after.get("q30_rate")),
                "GC_content": _fmt_pct(after.get("gc_content")),
            })
            self.update(pct=int(10 + (i + 1) / total * 80),
                        stage=f"解析 {i + 1}/{total}")

        rows.sort(key=lambda r: r["Sample"])
        cols = ["Sample", "Raw_reads", "Raw_bases", "Clean_reads",
                "Clean_bases", "Q20_rate", "Q30_rate", "GC_content"]
        out_file = out_dir / "stat.all.txt"
        with open(out_file, "w", encoding="utf-8") as wf:
            wf.write("\t".join(cols) + "\n")
            for r in rows:
                wf.write("\t".join(str(r[c]) for c in cols) + "\n")
        self.update(pct=100, stage="完成")
        self.log(f"=== 数据量统计完成,{len(rows)} 个样本 → {out_file} ===")


if __name__ == "__main__":
    DataVolumeStatsRunner.main()
