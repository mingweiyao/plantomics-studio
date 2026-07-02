"""StringTie merge and redundancy removal runner for ONT transcriptome data.

Merges multiple per-sample GTF assemblies into a unified transcriptome,
using --conservative -L -R flags for long-read transcriptome data. Optionally
performs per-sample reference-guided assembly.

Parameters:
  input_gtfs:      list[str] - Input GTF files to merge
  gtf_list_file:   str      - OR a file listing GTF paths (one per line)
  annotation_gtf:  str      - Reference annotation GTF for -G guidance
  merge_only:      bool     - Only perform merge step (default False)
  threads:         int      - CPU threads (default 8)

Outputs (to output_dir/):
  merged.gtf                    - Merged transcriptome GTF
  gtf_list.txt                  - List of input GTFs used for merging
  <sample>_guided.gtf           - Per-sample guided assemblies (if not merge_only)
  stringtie_summary.json        - Pipeline summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class StringtieRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        input_gtfs = p.get("input_gtfs") or []
        gtf_list_file = p.get("gtf_list_file", "")
        annotation_gtf = p.get("annotation_gtf", "")
        merge_only = bool(p.get("merge_only", False))
        threads = self.effective_threads(int(p.get("threads", 8)))

        # ---- Collect input GTFs ----
        gtfs = []
        if input_gtfs:
            gtfs = list(input_gtfs)
        elif gtf_list_file:
            gtf_path = Path(gtf_list_file)
            if not gtf_path.exists():
                raise FileNotFoundError(f"GTF 列表文件不存在: {gtf_list_file}")
            gtfs = [
                line.strip() for line in
                gtf_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip()
            ]
        else:
            raise ValueError("必须提供 input_gtfs 或 gtf_list_file")

        # Validate input GTFs exist
        resolved_gtfs = []
        for g in gtfs:
            gp = Path(g)
            if not gp.exists():
                raise FileNotFoundError(f"输入 GTF 文件不存在: {g}")
            resolved_gtfs.append(str(gp.resolve()))

        if len(resolved_gtfs) < 1:
            raise ValueError("至少需要一个输入 GTF 文件")

        if not annotation_gtf or not Path(annotation_gtf).exists():
            raise FileNotFoundError(f"注释 GTF 不存在: {annotation_gtf}")

        self.log(f"StringTie 合并: {len(resolved_gtfs)} 个 GTF 文件, "
                 f"merge_only={merge_only}, threads={threads}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        merged_gtf = out_dir / "merged.gtf"

        # ---- Write gtf_list.txt ----
        list_file = out_dir / "gtf_list.txt"
        list_file.write_text("\n".join(resolved_gtfs) + "\n", encoding="utf-8")
        self.log(f"写入 GTF 列表: {list_file} ({len(resolved_gtfs)} 个文件)")

        # ---- Step 1: stringtie --merge ----
        self.update(pct=10, stage="stringtie --merge",
                    detail=f"合并 {len(resolved_gtfs)} 个 GTF",
                    indeterminate=True)

        merge_cmd = [
            "stringtie", "--merge",
            "--conservative",
            "-L",
            "-R",
            "-G", annotation_gtf,
            "-p", str(threads),
            "-o", str(merged_gtf),
            str(list_file),
        ]
        self.run_command(
            merge_cmd,
            indeterminate=True,
            heartbeat_stage="stringtie --merge",
        )

        if not merged_gtf.exists() or merged_gtf.stat().st_size == 0:
            raise RuntimeError("stringtie --merge 未生成 merged.gtf")

        # Count transcripts in merged GTF
        n_merged = 0
        merged_text = merged_gtf.read_text(encoding="utf-8", errors="ignore")
        for line in merged_text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) >= 3 and cols[2] == "transcript":
                n_merged += 1
        self.log(f"  -> {merged_gtf} ({n_merged} 条转录本)")

        # ---- Step 2: Per-sample guided assembly (if not merge_only) ----
        per_sample_results = {}
        if not merge_only:
            n_samples = len(resolved_gtfs)
            self.update(pct=60, stage="stringtie per-sample -G",
                        detail=f"处理 {n_samples} 个样本",
                        indeterminate=True)

            for i, gtf_path in enumerate(resolved_gtfs):
                sample_pct = 60 + int((i + 1) / n_samples * 35)
                self.update(pct=sample_pct,
                            stage=f"stringtie -G 样本 {i+1}/{n_samples}",
                            detail=gtf_path,
                            indeterminate=True)

                # Derive BAM path: replace .gtf with .bam in the original path
                gtf_p = Path(gtf_path)
                bam_candidates = [
                    gtf_p.with_suffix(".bam"),
                    gtf_p.parent / (gtf_p.stem + ".sorted.bam"),
                    gtf_p.parent / (gtf_p.stem + ".aligned.bam"),
                ]

                bam_file = None
                for candidate in bam_candidates:
                    if candidate.exists():
                        bam_file = str(candidate)
                        break

                if not bam_file:
                    self.log(f"  跳过样本 {gtf_path}: 未找到对应的 BAM 文件 "
                             f"(尝试: {', '.join(str(c) for c in bam_candidates)})")
                    continue

                guided_out = out_dir / f"{gtf_p.stem}_guided.gtf"
                guided_cmd = [
                    "stringtie",
                    "-G", annotation_gtf,
                    "-p", str(threads),
                    "-o", str(guided_out),
                    bam_file,
                ]
                self.run_command(
                    guided_cmd,
                    indeterminate=True,
                    heartbeat_stage=f"stringtie -G {gtf_p.stem}",
                )

                # Count transcripts in guided output
                n_guided = 0
                if guided_out.exists() and guided_out.stat().st_size > 0:
                    guided_text = guided_out.read_text(
                        encoding="utf-8", errors="ignore")
                    for line in guided_text.splitlines():
                        if line.startswith("#") or not line.strip():
                            continue
                        cols = line.split("\t")
                        if len(cols) >= 3 and cols[2] == "transcript":
                            n_guided += 1

                per_sample_results[gtf_p.stem] = {
                    "guided_gtf": str(guided_out) if guided_out.exists() else "",
                    "n_transcripts": n_guided,
                    "input_bam": bam_file,
                }
                self.log(f"  -> {guided_out} ({n_guided} 条转录本)")

        # ---- Summary ----
        summary = {
            "annotation_gtf": annotation_gtf,
            "merge_only": merge_only,
            "n_input_gtfs": len(resolved_gtfs),
            "input_gtfs": resolved_gtfs,
            "gtf_list_file": str(list_file),
            "merged_gtf": str(merged_gtf),
            "n_merged_transcripts": n_merged,
            "per_sample_guided": per_sample_results,
        }
        (out_dir / "stringtie_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== StringTie 完成: {n_merged} 条合并转录本 → {merged_gtf} ===")


if __name__ == "__main__":
    StringtieRunner.main()
