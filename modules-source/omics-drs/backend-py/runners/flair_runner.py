"""Flair (Full-Length Alternative Isoform analysis of RNA) runner.

Runs the FLAIR pipeline for DRS data to identify full-length transcripts:
  1. flair align    - Align reads to genome
  2. flair correct  - Correct splice sites using annotation
  3. flair collapse - Collapse redundant isoforms

Parameters:
  reads_fa: str         - Input reads in FASTA format (can be concatenated)
  genome_fasta: str     - Reference genome FASTA
  annotation_gtf: str   - Reference annotation GTF
  sample_name: str      - Sample name for output labelling
  read_type: str        - Read type for flair align (default: "ont" for DRS)
  threads: int          - CPU threads (default 8)
  extra_align: str      - Extra flair align flags
  extra_correct: str    - Extra flair correct flags
  extra_collapse: str   - Extra flair collapse flags

Outputs (to output_dir/<sample_name>/):
  flair.aligned.bed              - Aligned reads BED
  flair.aligned.psl              - Aligned reads PSL
  flair_all_corrected.fa         - Corrected transcripts
  flair_all_corrected.psl        - Corrected alignment PSL
  flair.collapse.isoforms.fa     - Collapsed isoform sequences
  flair.collapse.isoforms.psl    - Collapsed isoform PSL
  flair.collapse.isoforms.bed    - Collapsed isoforms in BED
  flair.collapse.isoforms.gtf    - Collapsed isoforms in GTF
  counts_matrix.tsv              - Isoform count matrix
  flair_summary.json             - Pipeline summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class FlairRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        reads_fa = p.get("reads_fa", "")
        genome_fasta = p.get("genome_fasta", "")
        annotation_gtf = p.get("annotation_gtf", "")
        sample_name = p.get("sample_name", "sample")
        read_type = p.get("read_type", "ont")
        threads = self.effective_threads(int(p.get("threads", 8)))
        extra_align = p.get("extra_align", "")
        extra_correct = p.get("extra_correct", "")
        extra_collapse = p.get("extra_collapse", "")

        if not reads_fa or not Path(reads_fa).exists():
            raise FileNotFoundError(f"reads FASTA 不存在: {reads_fa}")
        if not genome_fasta or not Path(genome_fasta).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")
        if not annotation_gtf or not Path(annotation_gtf).exists():
            raise FileNotFoundError(f"注释 GTF 不存在: {annotation_gtf}")

        out_dir = Path(self.output_dir()) / sample_name
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Step 1: flair align ----
        self.update(pct=5, stage="flair align", indeterminate=True)
        align_cmd = [
            "flair", "align",
            "-g", genome_fasta,
            "-r", reads_fa,
            "-t", str(threads),
        ]
        if extra_align:
            align_cmd.extend(extra_align.split())
        self.run_command(align_cmd, cwd=str(out_dir),
                         indeterminate=True, heartbeat_stage="flair align")

        # flair align outputs to current directory with fixed names
        # Look for the aligned output
        aligned_bed = None
        for p in out_dir.glob("*.bed"):
            if "align" in p.name:
                aligned_bed = p
                break
        if not aligned_bed:
            aligned_bed = out_dir / "flair.aligned.bed"

        # If flair align produced output in a different location, symlink or log
        if not aligned_bed.exists():
            # flair may output to cwd; check the dir where we ran the command
            cwd_bed = Path.cwd() / "flair.aligned.bed"
            if cwd_bed.exists():
                self.run_command(["cp", str(cwd_bed), str(aligned_bed)])

        if not aligned_bed.exists():
            # Search more broadly
            found_beds = list(Path.cwd().glob("*.aligned.bed"))
            if found_beds:
                self.run_command(["cp", str(found_beds[0]), str(aligned_bed)])
            else:
                raise RuntimeError("flair align 没有产出 .aligned.bed 文件")

        self.log(f"flair align 完成: {aligned_bed}")

        # ---- Step 2: flair correct ----
        self.update(pct=35, stage="flair correct", indeterminate=True)
        correct_cmd = [
            "flair", "correct",
            "-g", genome_fasta,
            "-q", reads_fa,
            "-t", str(threads),
            "-c", annotation_gtf,
        ]
        if extra_correct:
            correct_cmd.extend(extra_correct.split())
        self.run_command(correct_cmd, cwd=str(out_dir),
                         indeterminate=True, heartbeat_stage="flair correct")

        # Find corrected output
        corrected_fa = out_dir / "flair_all_corrected.fa"
        if not corrected_fa.exists():
            cwd_corrected = Path.cwd() / "flair_all_corrected.fa"
            if cwd_corrected.exists():
                self.run_command(["cp", str(cwd_corrected), str(corrected_fa)])

        if not corrected_fa.exists():
            found_fa = list(Path.cwd().glob("*corrected.fa"))
            if found_fa:
                self.run_command(["cp", str(found_fa[0]), str(corrected_fa)])

        self.log(f"flair correct 完成: {corrected_fa}")

        # ---- Step 3: flair collapse ----
        self.update(pct=65, stage="flair collapse", indeterminate=True)
        collapse_cmd = [
            "flair", "collapse",
            "-g", genome_fasta,
            "-r", reads_fa,
            "-t", str(threads),
            "-q", reads_fa,
        ]
        if extra_collapse:
            collapse_cmd.extend(extra_collapse.split())
        self.run_command(collapse_cmd, cwd=str(out_dir),
                         indeterminate=True, heartbeat_stage="flair collapse")

        # Gather outputs
        iso_gtf = out_dir / "flair.collapse.isoforms.gtf"
        iso_fa = out_dir / "flair.collapse.isoforms.fa"
        iso_bed = out_dir / "flair.collapse.isoforms.bed"

        # Check if outputs exist (flair may name them differently)
        if not iso_gtf.exists():
            candidates = list(out_dir.glob("*collaps*.gtf")) + \
                         list(Path.cwd().glob("*collaps*.gtf"))
            for c in candidates:
                self.run_command(["cp", str(c), str(iso_gtf)])
                break
        if not iso_fa.exists():
            candidates = list(out_dir.glob("*collaps*.fa")) + \
                         list(Path.cwd().glob("*collaps*.fa"))
            for c in candidates:
                self.run_command(["cp", str(c), str(iso_fa)])
                break

        # Count isoforms
        n_isoforms = 0
        if iso_fa.exists():
            n_isoforms = sum(1 for ln in iso_fa.read_text(
                encoding="utf-8", errors="ignore").splitlines()
                if ln.startswith(">"))

        # Find count matrix
        count_matrix = out_dir / "counts_matrix.tsv"
        if not count_matrix.exists():
            candidates = list(out_dir.glob("*count*.tsv")) + \
                         list(Path.cwd().glob("*count*.tsv"))
            if candidates:
                self.run_command(["cp", str(candidates[0]), str(count_matrix)])

        # Count rows in matrix
        n_expressed = 0
        if count_matrix.exists():
            with open(count_matrix, encoding="utf-8") as fh:
                n_expressed = sum(1 for _ in fh) - 1  # minus header

        summary = {
            "sample_name": sample_name,
            "n_isoforms": n_isoforms,
            "n_expressed": max(0, n_expressed),
            "output_dir": str(out_dir),
            "outputs": {
                "gtf": str(iso_gtf) if iso_gtf.exists() else "",
                "fa": str(iso_fa) if iso_fa.exists() else "",
                "bed": str(iso_bed) if iso_bed.exists() else "",
                "counts": str(count_matrix) if count_matrix.exists() else "",
            },
        }
        (out_dir / "flair_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Flair 完成: {n_isoforms} 个异构体, "
                 f"{summary['n_expressed']} 个表达 → {out_dir} ===")


if __name__ == "__main__":
    FlairRunner.main()
