"""SUPPA2 alternative splicing analysis runner.

Generates alternative splicing events and computes PSI values.

Parameters:
  gtf: str               - Reference annotation GTF (required)
  expression_files: [dict] - Expression files: [{sample, tpm_file}] (optional)
  threads: int           - CPU threads (default 4)

Outputs (to output_dir/):
  events/                  - Generated splicing events
  psi/                     - PSI values (if expression provided)
  suppa2_summary.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class Suppa2Runner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        gtf = p.get("gtf", "")
        exp_files = p.get("expression_files", [])
        threads = int(p.get("threads", 4))

        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        events_dir = out_dir / "events"
        events_dir.mkdir(exist_ok=True)

        # Generate splicing events
        self.update(pct=15, stage="SUPPA2 生成事件", indeterminate=True)
        event_prefix = str(events_dir / "events")
        self.run_command([
            "suppa.py", "generateEvents",
            "-i", gtf,
            "-o", event_prefix,
            "-e", "SE", "SS", "MX", "RI", "FL",
        ], indeterminate=True, heartbeat_stage="suppa generateEvents")

        # Count events
        event_files = list(events_dir.glob("*"))
        event_types = {}
        total_events = 0
        for ef in event_files:
            if ef.suffix in (".ioe", ".gd"):
                continue
            if ef.is_file() and ef.stat().st_size > 0:
                name = ef.stem.replace("events_", "")
                count = sum(1 for _ in ef.read_text(
                    encoding="utf-8", errors="ignore").splitlines()
                    if _.strip()) - 1  # minus header
                event_types[name] = max(0, count)
                total_events += event_types[name]

        # Compute PSI if expression files provided
        psi_results = []
        if exp_files:
            self.update(pct=50, stage="SUPPA2 PSI 计算", indeterminate=True)
            psi_dir = out_dir / "psi"
            psi_dir.mkdir(exist_ok=True)

            for ef in exp_files:
                sample_name = ef.get("sample", "unknown")
                tpm_file = ef.get("tpm_file", "")
                if not tpm_file or not Path(tpm_file).exists():
                    self.log(f"!! {sample_name}: TPM 文件不存在 {tpm_file}, 跳过")
                    continue

                psi_out = str(psi_dir / f"{sample_name}")
                self.run_command([
                    "suppa.py", "psiPerEvent",
                    "-i", str(events_dir / "events_SE_SS_MX_RI_FL.ioe"),
                    "-e", tpm_file,
                    "-o", psi_out,
                ], indeterminate=True, heartbeat_stage=f"suppa psi {sample_name}")
                psi_results.append(sample_name)

        summary = {
            "n_events": total_events,
            "event_types": event_types,
            "n_psi_samples": len(psi_results),
            "psi_samples": psi_results,
        }
        (out_dir / "suppa2_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== SUPPA2: {total_events} event(s), {len(psi_results)} PSI sample(s) ===")


if __name__ == "__main__":
    Suppa2Runner.main()
