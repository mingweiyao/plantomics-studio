"""SUPPA2 alternative splicing analysis runner for DRS.

Analyzes alternative splicing events from transcript quantification data
using SUPPA2.

Parameters:
  counts_file: str           - Transcript expression/TPM counts file (required)
  gtf: str                   - Reference annotation GTF (required)
  psi_file: str              - Optional pre-computed PSI file
  event_type: str            - Event types (default: SE,SS,MXE,A5,A3,AF,AL,RI)
  min_psi: float             - Minimum PSI change (default: 0.1)
  gene_fdr: float            - Gene-level FDR threshold (default: 0.05)

Outputs:
  events/                    - Event PSI files
  diff_splice/               - Differential splicing results
  suppa2_summary.json        - Analysis summary
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class Suppa2Runner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        counts_file = p.get("counts_file", "")
        gtf = p.get("gtf", "")
        psi_file = p.get("psi_file", "")
        event_type = p.get("event_type", "")
        min_psi = float(p.get("min_psi", 0.1))
        gene_fdr = float(p.get("gene_fdr", 0.05))

        if not counts_file or not Path(counts_file).exists():
            raise FileNotFoundError(f"counts 文件不存在: {counts_file}")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Generate events from GTF
        self.update(pct=5, stage="SUPPA2 生成事件", indeterminate=True)
        events_dir = out_dir / "events"
        events_dir.mkdir(exist_ok=True)

        self.run_command([
            "suppa.py", "generateEvents",
            "-i", gtf,
            "-o", str(events_dir / "events"),
            "-f", "ioe",
            "-e", event_type if event_type else "SE,SS,MXE,A5,A3,AF,AL,RI",
        ], indeterminate=True, heartbeat_stage="SUPPA2 generateEvents")

        # Calculate PSI
        self.update(pct=30, stage="SUPPA2 计算 PSI", indeterminate=True)
        ioe_file = events_dir / "events.ioe"

        # Use counts file as TPM expression
        self.run_command([
            "suppa.py", "psiPerEvent",
            "-i", str(ioe_file),
            "-e", str(counts_file),
            "-o", str(events_dir / "events_psi"),
        ], indeterminate=True, heartbeat_stage="SUPPA2 psiPerEvent")

        # Also isoform PSI
        self.run_command([
            "suppa.py", "psiPerIsoform",
            "-g", gtf,
            "-e", str(counts_file),
            "-o", str(events_dir / "isoform_psi"),
        ], indeterminate=True, heartbeat_stage="SUPPA2 psiPerIsoform")

        # Differential splicing if psi_file provided
        self.update(pct=65, stage="SUPPA2 差异剪接", indeterminate=True)
        diff_dir = out_dir / "diff_splice"
        diff_dir.mkdir(exist_ok=True)

        if psi_file and Path(psi_file).exists():
            self.run_command([
                "suppa.py", "diffSplice",
                "-m", "empirical",
                "-i", str(ioe_file),
                "-p", str(psi_file),
                "-o", str(diff_dir / "diff_result"),
                "--min-psi", str(min_psi),
                "--gene-fdr", str(gene_fdr),
                "-gc",
            ], indeterminate=True, heartbeat_stage="SUPPA2 diffSplice")

        # Summary
        n_psi = len(list(events_dir.glob("*.psi")))
        summary = {
            "gtf": gtf,
            "counts_file": counts_file,
            "n_event_files": n_psi,
            "event_types": event_type or "all",
        }
        (out_dir / "suppa2_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== SUPPA2 剪接分析完成 → {out_dir} ===")


if __name__ == "__main__":
    Suppa2Runner.main()
