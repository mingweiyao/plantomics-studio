"""StringTie merge runner for ONT full-length lncRNA data.

Assembles transcripts per-sample, then merges into a unified transcript set.

Parameters:
  bam_files: [str]     - Aligned BAM files (required)
  gtf: str              - Reference annotation GTF (required)
  threads: int          - CPU threads (default 8)

Outputs (to output_dir/):
  per_sample/{name}.gtf  - Per-sample assemblies
  merged.gtf             - Merged transcript set
  stringtie_summary.json - Summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class StringtieRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bams = p.get("bam_files", [])
        gtf = p.get("gtf", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bams:
            raise ValueError("未提供 BAM 列表(bam_files)")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        per_sample = out_dir / "per_sample"
        per_sample.mkdir(exist_ok=True)

        # Per-sample assembly
        gtf_list = []
        n = len(bams)
        for i, bam in enumerate(bams):
            bam_path = Path(bam)
            if not bam_path.exists():
                self.log(f"!! BAM 不存在: {bam}, 跳过")
                continue
            name = bam_path.stem.replace(".sorted", "").replace(".bam", "")
            self.update(pct=int(50 * i / max(n, 1)),
                        stage=f"StringTie 组装 ({i + 1}/{n})", detail=name)
            sgtf = per_sample / f"{name}.gtf"
            cmd = ["stringtie", str(bam_path), "-p", str(threads),
                   "-G", gtf, "-o", str(sgtf), "-l", name]
            self.run_command(cmd, indeterminate=True,
                             heartbeat_stage=f"stringtie {name}")
            if sgtf.exists():
                gtf_list.append(str(sgtf))

        if not gtf_list:
            raise RuntimeError("没有任何样本组装成功")

        # Merge
        self.update(pct=60, stage="StringTie --merge 合并")
        gtf_list_file = out_dir / "gtf_list.txt"
        gtf_list_file.write_text("\n".join(gtf_list) + "\n", encoding="utf-8")
        merged = out_dir / "merged.gtf"
        self.run_command([
            "stringtie", "--merge", "-p", str(threads),
            "-G", gtf, "-o", str(merged), str(gtf_list_file),
        ], indeterminate=True, heartbeat_stage="stringtie merge")

        summary = {
            "n_samples": len(gtf_list),
            "merged_gtf": str(merged),
            "per_sample_gtfs": gtf_list,
        }
        (out_dir / "stringtie_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== StringTie 合并完成: {len(gtf_list)} 样本, merged.gtf ===")


if __name__ == "__main__":
    StringtieRunner.main()
