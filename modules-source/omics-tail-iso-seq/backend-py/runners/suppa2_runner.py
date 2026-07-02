"""SUPPA2 alternative splicing analysis runner for Tail Iso-seq.

Analyzes alternative splicing events from transcript quantification data.

Parameters:
  gtf: str                  - Reference annotation GTF
  tpm_files: dict or [str] - TPM files
  groups: dict              - Sample-group mapping
  comparison: list          - Pairwise comparisons
  event_type: str           - Event types (default: all)
  min_psi: float            - Minimum PSI change (default: 0.1)
  gene_fdr: float           - FDR threshold (default: 0.05)
  threads: int              - CPU threads (default: 4)

Outputs (to output_dir/):
  events/*.psi          - PSI values
  diff_splice/*.dpsi    - Differential splicing
  suppa2_summary.json   - Summary
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
            raise ValueError("tpm_files 不能为空")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Generate events
        self.update(pct=5, stage="SUPPA2 事件生成", indeterminate=True)
        events_dir = out_dir / "events"
        events_dir.mkdir(exist_ok=True)

        ioe_file = events_dir / "events.ioe"
        self.run_command([
            "suppa.py", "generateEvents",
            "-i", gtf,
            "-o", str(events_dir / "events"),
            "-f", "ioe",
            "-e", event_type if event_type else "SE,SS,MXE,A5,A3,AF,AL,RI",
        ], indeterminate=True, heartbeat_stage="generateEvents")

        # TPM merging and PSI calculation
        self.update(pct=30, stage="SUPPA2 PSI 计算", indeterminate=True)

        tpm_file_list = []
        if isinstance(tpm_files, dict):
            for cond_files in tpm_files.values():
                tpm_file_list.extend(cond_files)
        else:
            tpm_file_list = [tpm_files] if isinstance(tpm_files, str) else tpm_files

        tpm_tsv = events_dir / "tpm_merged.tsv"
        self._merge_tpm_files(tpm_file_list, tpm_tsv)

        if tpm_tsv.exists() and ioe_file.exists():
            self.run_command([
                "suppa.py", "psiPerEvent",
                "-i", str(ioe_file),
                "-e", str(tpm_tsv),
                "-o", str(events_dir / "events_psi"),
            ], indeterminate=True, heartbeat_stage="psiPerEvent")

        # Differential splicing
        self.update(pct=65, stage="SUPPA2 差异剪接", indeterminate=True)
        diff_dir = out_dir / "diff_splice"
        diff_dir.mkdir(exist_ok=True)

        for comp in comparisons:
            if not isinstance(comp, (list, tuple)) or len(comp) < 2:
                continue
            cond1, cond2 = comp[0], comp[1]
            cond1_samples = groups.get(cond1, [])
            cond2_samples = groups.get(cond2, [])

            if not cond1_samples and isinstance(tpm_files, dict):
                cond1_samples = tpm_files.get(cond1, [])
                cond2_samples = tpm_files.get(cond2, [])

            if cond1_samples and cond2_samples:
                comp_label = f"{cond1}_vs_{cond2}"
                comp_dir = diff_dir / comp_label
                comp_dir.mkdir(exist_ok=True)

                g1_file = comp_dir / "group1.txt"
                g2_file = comp_dir / "group2.txt"
                g1_file.write_text(
                    "\n".join(str(s) for s in cond1_samples) + "\n")
                g2_file.write_text(
                    "\n".join(str(s) for s in cond2_samples) + "\n")

                psi_file = events_dir / "events_psi.psi"
                if psi_file.exists():
                    self.run_command([
                        "suppa.py", "diffSplice",
                        "-m", "empirical",
                        "-i", str(ioe_file), "-p", str(psi_file),
                        "-l", str(g1_file), "-s", str(g2_file),
                        "-o", str(comp_dir / comp_label),
                        "--min-psi", str(min_psi),
                        "--gene-fdr", str(gene_fdr), "-gc",
                    ], indeterminate=True,
                       heartbeat_stage=f"diffSplice {comp_label}")

        summary = {
            "n_comparisons": len(comparisons),
            "event_types": event_type or "all",
        }
        (out_dir / "suppa2_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== SUPPA2 分析完成 → {out_dir} ===")

    @staticmethod
    def _merge_tpm_files(tpm_files, output_path):
        """Merge multiple TPM files into a single TSV."""
        import csv
        if not tpm_files:
            return
        tpm_data = {}
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
                tpm_col = len(header) - 1 if header else 4
                if header:
                    for i, h in enumerate(header):
                        if h.lower().strip() in ("tpm", "fpkm", "numreads"):
                            tpm_col = i
                            break
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
                vals = [str(tpm_data[tid].get(label, 0.0))
                        for label in file_labels]
                out.write(tid + "\t" + "\t".join(vals) + "\n")


if __name__ == "__main__":
    Suppa2Runner.main()
