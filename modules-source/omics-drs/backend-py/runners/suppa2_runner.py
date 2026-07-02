"""SUPPA2 alternative splicing analysis runner.

Analyzes alternative splicing events from transcript quantification data:
  1. Generate isoform PSI (percent spliced-in) values
  2. Generate local events PSI
  3. Differential splicing analysis between conditions

Parameters:
  gtf: str                      - Reference annotation GTF
  tpm_files: dict or [str]     - TPM files per condition/batch
  groups: dict                 - Sample-group mapping (e.g., {"ctrl": [s1,s2], "trt": [s3,s4]})
  comparison: list             - List of pairwise comparisons (e.g., [["ctrl","trt"]])
  event_type: str              - Event types to analyze (comma-sep, default: all)
  min_psi: float               - Minimum PSI change (default: 0.1)
  gene_fdr: float              - Gene-level FDR threshold (default: 0.05)
  threads: int                 - CPU threads (default: 4)

Outputs (to output_dir/):
  events/isoforms.psi           - PSI values per isoform
  events/<event_type>.psi       - PSI per event type
  diff_splice/<comparison>_...  - Differential splicing results
  suppa2_summary.json           - Analysis summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class Suppa2Runner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        gtf = p.get("gtf", "")
        tpm_files = p.get("tpm_files", {})
        groups = p.get("groups", {})
        comparisons = p.get("comparison", [])
        event_type = p.get("event_type", "")
        min_psi = float(p.get("min_psi", 0.1))
        gene_fdr = float(p.get("gene_fdr", 0.05))
        threads = self.effective_threads(int(p.get("threads", 4)))

        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"注释 GTF 不存在: {gtf}")
        if not tpm_files:
            raise ValueError("tpm_files 不能为空(需提供样本 TPM 文件路径)")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Step 1: Generate isoform PSI ----
        self.update(pct=5, stage="SUPPA2 ioevents 生成", indeterminate=True)
        events_dir = out_dir / "events"
        events_dir.mkdir(exist_ok=True)

        # Generate events from GTF
        ioe_file = events_dir / "events.ioe"
        self.run_command([
            "suppa.py", "generateEvents",
            "-i", gtf,
            "-o", str(events_dir / "events"),
            "-f", "ioe",
            "-e", event_type if event_type else "SE,SS,MXE,A5,A3,AF,AL,RI",
        ], indeterminate=True, heartbeat_stage="SUPPA2 generateEvents")

        # ---- Step 2: Calculate PSI per event type ----
        self.update(pct=30, stage="SUPPA2 计算 PSI", indeterminate=True)

        # Prepare TPM file list - write a file with paths
        if isinstance(tpm_files, dict):
            # Dict: condition -> [file1, file2, ...]
            # Write a file-list for suppa
            psi_dir = events_dir / "psi"
            psi_dir.mkdir(exist_ok=True)

            # Write a sample list file
            sample_list_path = events_dir / "sample_list.txt"
            with open(sample_list_path, "w", encoding="utf-8") as f:
                for cond, files in tpm_files.items():
                    for fpath in files:
                        f.write(f"{cond}\t{fpath}\n")
                    if not files:
                        self.log(f"警告: 条件 {cond} 没有 TPM 文件")

            # Calculate PSI per condition
            tpm_file_list = []
            if isinstance(tpm_files, dict):
                for cond_files in tpm_files.values():
                    tpm_file_list.extend(cond_files)
            else:
                tpm_file_list = [tpm_files] if isinstance(tpm_files, str) else tpm_files

            # Create a merged TPM table if needed
            tpm_tsv = events_dir / "tpm_merged.tsv"
            self._merge_tpm_files(tpm_file_list, tpm_tsv)

            if tpm_tsv.exists():
                self.run_command([
                    "suppa.py", "psiPerEvent",
                    "-i", str(ioe_file),
                    "-e", str(tpm_tsv),
                    "-o", str(events_dir / "events_psi"),
                ], indeterminate=True, heartbeat_stage="SUPPA2 psiPerEvent")

            # Also calculate isoform-level PSI
            self.run_command([
                "suppa.py", "psiPerIsoform",
                "-g", gtf,
                "-e", str(tpm_tsv),
                "-o", str(events_dir / "isoform_psi"),
            ], indeterminate=True, heartbeat_stage="SUPPA2 psiPerIsoform")

        # ---- Step 3: Differential splicing ----
        self.update(pct=65, stage="SUPPA2 差异剪接", indeterminate=True)

        diff_dir = out_dir / "diff_splice"
        diff_dir.mkdir(exist_ok=True)

        for comp in comparisons:
            if not isinstance(comp, (list, tuple)) or len(comp) < 2:
                continue
            cond1, cond2 = comp[0], comp[1]

            cond1_samples = groups.get(cond1, []) if isinstance(groups, dict) else []
            cond2_samples = groups.get(cond2, []) if isinstance(groups, dict) else []

            if not cond1_samples or not cond2_samples:
                # Fallback: use all samples from tpm_files
                if isinstance(tpm_files, dict):
                    cond1_samples = tpm_files.get(cond1, [])
                    cond2_samples = tpm_files.get(cond2, [])

            if cond1_samples and cond2_samples:
                comp_label = f"{cond1}_vs_{cond2}"
                comp_dir = diff_dir / comp_label
                comp_dir.mkdir(exist_ok=True)

                # Write group files
                group1_file = comp_dir / "group1.txt"
                group2_file = comp_dir / "group2.txt"
                group1_file.write_text("\n".join(str(s) for s in cond1_samples) + "\n")
                group2_file.write_text("\n".join(str(s) for s in cond2_samples) + "\n")

                psi_file = events_dir / "events_psi.psi"
                if psi_file.exists():
                    self.run_command([
                        "suppa.py", "diffSplice",
                        "-m", "empirical",
                        "-i", str(ioe_file),
                        "-p", str(psi_file),
                        "-l", str(group1_file),
                        "-s", str(group2_file),
                        "-o", str(comp_dir / comp_label),
                        "--min-psi", str(min_psi),
                        "--gene-fdr", str(gene_fdr),
                        "-gc",
                    ], indeterminate=True,
                       heartbeat_stage=f"SUPPA2 diffSplice {comp_label}")

        # Generate summary
        dtus_file = diff_dir / f"{comparisons[0][0] if comparisons else 'NA'}_vs_{comparisons[0][1] if comparisons and len(comparisons[0]) > 1 else 'NA'}.dpsi"
        n_dtus = 0
        if dtus_file.exists():
            n_dtus = sum(1 for _ in open(dtus_file)) - 1

        summary = {
            "n_event_files": len(list(events_dir.glob("*.psi"))),
            "n_diff_splicing_tests": len(comparisons),
            "n_significant_dtus": max(0, n_dtus),
            "event_types": event_type or "all",
            "min_psi": min_psi,
            "gene_fdr": gene_fdr,
            "outputs": {
                "ioe": str(ioe_file) if ioe_file.exists() else "",
                "psi_dir": str(events_dir),
                "diff_dir": str(diff_dir),
            },
        }
        (out_dir / "suppa2_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== SUPPA2 剪接分析完成 → {out_dir} ===")

    @staticmethod
    def _merge_tpm_files(tpm_files, output_path):
        """Merge multiple TPM files into a single TSV matrix."""
        import csv

        if not tpm_files:
            return

        # Collect all transcript IDs and TPM values
        tpm_data = {}  # transcript -> {file_label: tpm}
        file_labels = []

        for fpath in tpm_files:
            pf = Path(fpath)
            if not pf.exists():
                continue
            label = pf.stem
            file_labels.append(label)
            with open(pf, encoding="utf-8") as fh:
                reader = csv.reader(fh, delimiter="\t")
                header = next(reader, None)
                # Find TPM column
                tpm_col = None
                if header:
                    for i, h in enumerate(header):
                        if h.lower().strip() in ("tpm", "tpm_value", "fpkm"):
                            tpm_col = i
                            break
                    if tpm_col is None:
                        tpm_col = len(header) - 1  # assume last

                for row in reader:
                    if len(row) > tpm_col:
                        tid = row[0].strip()
                        try:
                            tpm = float(row[tpm_col])
                        except ValueError:
                            continue
                        if tid not in tpm_data:
                            tpm_data[tid] = {}
                        tpm_data[tid][label] = tpm

        if not tpm_data or not file_labels:
            return

        with open(output_path, "w", encoding="utf-8") as out:
            out.write("transcript_id\t" + "\t".join(file_labels) + "\n")
            for tid in sorted(tpm_data.keys()):
                vals = [str(tpm_data[tid].get(label, 0.0)) for label in file_labels]
                out.write(tid + "\t" + "\t".join(vals) + "\n")


if __name__ == "__main__":
    Suppa2Runner.main()
