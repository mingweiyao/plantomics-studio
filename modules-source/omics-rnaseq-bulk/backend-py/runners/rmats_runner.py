"""可变剪接分析 runner（rMATS-turbo）。

比较两组样本的可变剪接差异,鉴定 5 类事件(SE/A5SS/A3SS/MXE/RI)。
对应商业报告"可变剪接分析"。

参数:
  bam_files_g1: list[str]   - 第一组(对照)BAM
  bam_files_g2: list[str]   - 第二组(处理)BAM
  gtf:          str         - 参考注释 GTF(必填)
  read_length:  int         - 读长(必填,如 150)
  paired:       bool        - 双端 True / 单端 False,默认 True
  threads:      int         - 默认 8

产出(到 output_subdir):
  SE.MATS.JC.txt, A5SS.*, A3SS.*, MXE.*, RI.*   - 各类事件结果表
  as_events_summary.json                          - 各类型显著事件计数(FDR<0.05)
"""
import json
from pathlib import Path

from runners.base import BaseRunner

EVENT_TYPES = ["SE", "A5SS", "A3SS", "MXE", "RI"]


class RmatsRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        g1 = p.get("bam_files_g1") or []
        g2 = p.get("bam_files_g2") or []
        gtf = p.get("gtf")
        read_length = int(p.get("read_length", 150))
        paired = p.get("paired", True)
        threads = self.effective_threads(int(p.get("threads", 8)))

        if len(g1) < 1 or len(g2) < 1:
            raise ValueError("两组各至少需要 1 个 BAM(bam_files_g1 / bam_files_g2)")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp_dir = out_dir / "tmp"
        tmp_dir.mkdir(exist_ok=True)

        b1 = out_dir / "b1.txt"; b1.write_text(",".join(g1) + "\n", encoding="utf-8")
        b2 = out_dir / "b2.txt"; b2.write_text(",".join(g2) + "\n", encoding="utf-8")

        self.update(pct=10, stage="rMATS 运行中", detail="比对剪接事件", indeterminate=True)
        cmd = ["rmats.py", "--b1", str(b1), "--b2", str(b2), "--gtf", gtf,
               "-t", "paired" if paired else "single",
               "--readLength", str(read_length), "--variable-read-length",
               "--nthread", str(threads), "--od", str(out_dir), "--tmp", str(tmp_dir)]
        self.log("$ " + " ".join(cmd))
        self.run_command(cmd)

        # 统计各类型显著事件(FDR < 0.05)
        self.update(pct=90, stage="统计显著事件")
        counts = {}
        for et in EVENT_TYPES:
            f = out_dir / f"{et}.MATS.JC.txt"
            n_total, n_sig = 0, 0
            if f.exists():
                lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                if lines:
                    header = lines[0].split("\t")
                    fdr_i = header.index("FDR") if "FDR" in header else None
                    for ln in lines[1:]:
                        n_total += 1
                        if fdr_i is not None:
                            try:
                                if float(ln.split("\t")[fdr_i]) < 0.05:
                                    n_sig += 1
                            except (ValueError, IndexError):
                                pass
            counts[et] = {"total": n_total, "sig_fdr0.05": n_sig}

        (out_dir / "as_events_summary.json").write_text(
            json.dumps({"groups": {"g1": len(g1), "g2": len(g2)}, "events": counts},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log("=== 可变剪接事件统计: " +
                 ", ".join(f"{et}={counts[et]['sig_fdr0.05']}" for et in EVENT_TYPES) + " ===")


if __name__ == "__main__":
    RmatsRunner.main()
