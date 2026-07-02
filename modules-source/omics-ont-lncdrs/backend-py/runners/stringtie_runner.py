"""StringTie assembly and merge runner for ONT DRS transcripts.

Runs StringTie on aligned BAM files to assemble transcripts, then merges
them with StringTie --merge.

Parameters:
  bam_files: list[str] - Aligned BAM files
  sample_names: list[str] - Optional sample names (default from BAM names)
  gtf: str             - Reference annotation GTF
  strand: int          - 0 unstranded / 1 fr / 2 rf, default 0
  threads: int         - CPU threads (default 8)

Outputs (to output_dir/):
  per_sample/<sample>.gtf   - Per-sample assemblies
  merged.gtf                - Merged transcript assembly
  stringtie_summary.json    - Summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class StringtieRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bams = p.get("bam_files") or p.get("bams") or []
        sample_names = p.get("sample_names", [])
        gtf = p.get("gtf")
        strand = int(p.get("strand", 0))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bams:
            raise ValueError("未提供 BAM 列表(bam_files)")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        per_sample = out_dir / "per_sample"
        per_sample.mkdir(exist_ok=True)

        if not sample_names or len(sample_names) != len(bams):
            sample_names = [Path(b).stem.split(".")[0] for b in bams]

        strand_flag = {1: ["--fr"], 2: ["--rf"]}.get(strand, [])

        # Per-sample assembly
        gtf_list = []
        n = len(bams)
        for i, (bam, name) in enumerate(zip(bams, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: BAM 不存在 {bam}")
                continue
            self.update(pct=int(5 + 55 * i / max(n, 1)),
                        stage=f"StringTie 组装 ({i + 1}/{n})", detail=name)
            sgtf = per_sample / f"{name}.gtf"
            cmd = ["stringtie", str(bam), "-p", str(threads), "-G", gtf,
                   "-o", str(sgtf), "-l", name] + strand_flag
            self.run_command(cmd)
            if sgtf.exists():
                gtf_list.append(str(sgtf))

        if not gtf_list:
            raise RuntimeError("没有任何样本组装成功")

        # Merge
        self.update(pct=70, stage="StringTie --merge 合并")
        gtf_list_file = out_dir / "gtf_list.txt"
        gtf_list_file.write_text("\n".join(gtf_list) + "\n", encoding="utf-8")
        merged = out_dir / "merged.gtf"
        cmd = ["stringtie", "--merge", "-p", str(threads), "-G", gtf,
               "-o", str(merged), str(gtf_list_file)]
        self.run_command(cmd)

        summary = {
            "n_samples": len(gtf_list),
            "merged_gtf": str(merged),
            "per_sample_gtfs": gtf_list,
        }
        (out_dir / "stringtie_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== StringTie 完成: {len(gtf_list)} 个样本 → {out_dir} ===")


if __name__ == "__main__":
    StringtieRunner.main()
