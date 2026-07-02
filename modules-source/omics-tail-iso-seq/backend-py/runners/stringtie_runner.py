"""StringTie redundancy removal and assembly runner.

Removes redundant transcripts from Pinfish consensus and/or assembles
transcripts from BAM alignments with reference guidance.

Parameters:
  bam_files: [str]       - Aligned BAM files (from minimap2)
  sample_names: [str]    - Optional sample names
  gtf: str               - Reference annotation GTF
  strand: int            - 0 unstranded / 1 fr / 2 rf (default: 0)
  merge_only: bool       - Only merge existing GTF files (default: False)
  input_gtf_files: [str] - Input GTF files for merging (optional)
  threads: int           - CPU threads (default: 8)

Outputs (to output_dir/):
  per_sample/<sample>.gtf   - Per sample assemblies
  merged.gtf                - Merged transcriptome
  stringtie_summary.json    - Summary statistics
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class StringtieRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bams = p.get("bam_files") or []
        sample_names = p.get("sample_names", [])
        gtf = p.get("gtf", "")
        strand = int(p.get("strand", 0))
        merge_only = bool(p.get("merge_only", False))
        input_gtf_files = p.get("input_gtf_files", [])
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"参考 GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        if merge_only and input_gtf_files:
            # Only merge existing GTFs
            return self._merge_only(input_gtf_files, gtf, threads, out_dir)

        if not bams:
            raise ValueError("bam_files 列表为空")

        if not sample_names or len(sample_names) != len(bams):
            sample_names = [Path(b).stem.split(".")[0].replace(".sorted", "")
                            for b in bams]

        per_sample = out_dir / "per_sample"
        per_sample.mkdir(exist_ok=True)

        strand_flag = {1: ["--fr"], 2: ["--rf"]}.get(strand, [])

        # Per-sample assembly
        gtf_list = []
        n = len(bams)
        for i, (bam, name) in enumerate(zip(bams, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: BAM 不存在 {bam}")
                continue

            self.update(pct=int(5 + 55 * i / n),
                        stage=f"StringTie 组装 ({i + 1}/{n})", detail=name)
            sgtf = per_sample / f"{name}.gtf"
            cmd = ["stringtie", str(bam), "-p", str(threads),
                   "-G", gtf, "-o", str(sgtf), "-l", name] + strand_flag
            self.run_command(cmd)
            if sgtf.exists():
                gtf_list.append(str(sgtf))

        if not gtf_list:
            raise RuntimeError("没有样本组装成功")

        # Merge
        self.update(pct=70, stage="StringTie --merge 合并")
        gtf_list_file = out_dir / "gtf_list.txt"
        gtf_list_file.write_text("\n".join(gtf_list) + "\n", encoding="utf-8")
        merged = out_dir / "merged.gtf"
        cmd = ["stringtie", "--merge", "-p", str(threads),
               "-G", gtf, "-o", str(merged), str(gtf_list_file)]
        self.run_command(cmd)

        n_transcripts = 0
        if merged.exists():
            n_transcripts = sum(1 for ln in merged.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if ln.startswith("\t"))

        summary = {
            "n_samples": len(gtf_list),
            "n_transcripts_in_merged": n_transcripts,
            "merged_gtf": str(merged),
        }
        (out_dir / "stringtie_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== StringTie 完成: {len(gtf_list)} 样本, "
                 f"合并后 {n_transcripts} 转录本 → {out_dir} ===")

    def _merge_only(self, input_gtfs, reference_gtf, threads, out_dir):
        """Merge existing GTF files only."""
        self.update(pct=30, stage="StringTie --merge 合并已有的 GTF")
        gtf_list_file = out_dir / "gtf_list.txt"
        gtf_list_file.write_text("\n".join(input_gtfs) + "\n", encoding="utf-8")
        merged = out_dir / "merged.gtf"
        cmd = ["stringtie", "--merge", "-p", str(threads),
               "-G", reference_gtf, "-o", str(merged),
               str(gtf_list_file)]
        self.run_command(cmd)

        n_transcripts = 0
        if merged.exists():
            n_transcripts = sum(1 for ln in merged.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if ln.startswith("\t"))

        summary = {"n_input_gtfs": len(input_gtfs),
                    "n_merged_transcripts": n_transcripts}
        (out_dir / "stringtie_summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== StringTie 合并完成: {n_transcripts} 转录本 → {out_dir} ===")


if __name__ == "__main__":
    StringtieRunner.main()
