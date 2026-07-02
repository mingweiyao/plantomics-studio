"""SUPPA2 alternative splicing analysis runner.

SUPPA2 generates alternative splicing events from a GTF annotation,
calculates Percent Spliced In (PSI) values from transcript expression,
and optionally performs differential splicing analysis.

Parameters:
  gtf: str               - Annotation GTF file
  tpm_file: str          - TPM expression matrix (genes/transcripts x samples)
  condition_file: str    - Condition group file for differential analysis (optional)
  output_prefix: str     - Prefix for output files
  as_types: list[str]    - Splice types to consider (default: all 7 types)
                           SE/SS/MXE/A5/A3/AF/AL
  psi_threshold: float   - PSI threshold for differential splicing (default 0.1)
  pval_threshold: float  - P-value threshold for differential splicing (default 0.05)
  threads: int           - CPU threads (default 8)

Outputs (to output_dir/):
  <prefix>_events.ioe             - Inclusion/exclusion events
  <prefix>_psi.tsv                - PSI values per event per sample
  <prefix>_diffsplice.dpsi        - Delta PSI values (if condition_file given)
  <prefix>_diffsplice.psivec      - PSI vectors (if condition_file given)
  <prefix>_summary.json           - Summary statistics
"""
import json
import os
from pathlib import Path

from runners.base import BaseRunner


# All 7 SUPPA2 alternative splicing event types
_ALL_AS_TYPES = ["SE", "SS", "MXE", "A5", "A3", "AF", "AL"]


class Suppa2Runner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        gtf = p.get("gtf", "")
        tpm_file = p.get("tpm_file", "")
        condition_file = p.get("condition_file", "")
        output_prefix = p.get("output_prefix", "suppa2")
        as_types = p.get("as_types", _ALL_AS_TYPES)
        psi_threshold = float(p.get("psi_threshold", 0.1))
        pval_threshold = float(p.get("pval_threshold", 0.05))
        threads = self.effective_threads(int(p.get("threads", 8)))

        # Validate inputs
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 注释文件不存在: {gtf}")
        if not tpm_file or not Path(tpm_file).exists():
            raise FileNotFoundError(f"TPM 表达矩阵文件不存在: {tpm_file}")

        # Validate AS types
        valid_types = set(_ALL_AS_TYPES)
        requested_types = []
        for t in as_types:
            t_upper = t.upper().strip()
            if t_upper not in valid_types:
                raise ValueError(
                    f"无效的 AS 事件类型 '{t}'。有效类型: {', '.join(_ALL_AS_TYPES)}"
                )
            requested_types.append(t_upper)
        if not requested_types:
            requested_types = list(_ALL_AS_TYPES)

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        self.log(f"SUPPA2 参数: gtf={gtf}, tpm={tpm_file}, types={requested_types}, "
                 f"psi_thr={psi_threshold}, pval_thr={pval_threshold}")

        # Build output paths
        events_ioe = out_dir / f"{output_prefix}_events.ioe"
        psi_file = out_dir / f"{output_prefix}_psi.tsv"

        # ---- Step 1: generateEvents ----
        self.update(pct=10, stage="SUPPA2 generateEvents", detail="生成 AS 事件")
        event_types_str = ",".join(requested_types)
        self.run_command([
            "suppa.py", "generateEvents",
            "-i", gtf,
            "-o", str(out_dir / output_prefix),
            "-f", "ioe",
            "-e", event_types_str,
        ], indeterminate=True, heartbeat_stage="SUPPA2 generateEvents")

        if not events_ioe.exists():
            raise RuntimeError(
                f"SUPPA2 generateEvents 未生成事件文件: {events_ioe}")

        # Count events per type
        events_per_type = self._count_events_by_type(events_ioe, requested_types)
        total_events = sum(events_per_type.values())
        self.log(f"SUPPA2 事件统计: {total_events} 个事件")
        for t, count in sorted(events_per_type.items()):
            self.log(f"  {t}: {count}")

        # ---- Step 2: psiPerEvent ----
        self.update(pct=40, stage="SUPPA2 psiPerEvent", detail="计算 PSI 值")
        self.run_command([
            "suppa.py", "psiPerEvent",
            "-i", str(events_ioe),
            "-e", tpm_file,
            "-o", str(psi_file).replace(".tsv", ""),
        ], indeterminate=True, heartbeat_stage="SUPPA2 psiPerEvent")

        if not psi_file.exists():
            raise RuntimeError(f"SUPPA2 psiPerEvent 未生成 PSI 文件: {psi_file}")

        # ---- Step 3: diffSplice (optional) ----
        diffsplice_dpsi = None
        diffsplice_psivec = None
        if condition_file:
            if not Path(condition_file).exists():
                raise FileNotFoundError(
                    f"条件组文件不存在: {condition_file}")
            self.update(pct=65, stage="SUPPA2 diffSplice",
                        detail="差异剪切分析")
            diffsplice_prefix = out_dir / f"{output_prefix}_diffsplice"
            # Build diffSplice command
            cmd = [
                "suppa.py", "diffSplice",
                "-m", "empirical",
                "-i", str(events_ioe),
                "-s", str(psi_file),
                "-c", condition_file,
                "-o", str(diffsplice_prefix),
                "--save-psivec",
                "--save-dpsi",
            ]
            self.run_command(
                cmd, indeterminate=True,
                heartbeat_stage="SUPPA2 diffSplice")

            # Check output files
            dpsi_candidates = [
                diffsplice_prefix.parent / f"{diffsplice_prefix.name}.dpsi",
                out_dir / f"{output_prefix}_diffsplice.dpsi",
            ]
            for candidate in dpsi_candidates:
                if candidate.exists():
                    diffsplice_dpsi = candidate
                    break

            psivec_candidates = [
                diffsplice_prefix.parent / f"{diffsplice_prefix.name}.psivec",
                out_dir / f"{output_prefix}_diffsplice.psivec",
            ]
            for candidate in psivec_candidates:
                if candidate.exists():
                    diffsplice_psivec = candidate
                    break

            if diffsplice_dpsi:
                self.log(f"差异剪切结果: {diffsplice_dpsi}")
            else:
                self.log("差异剪切结果文件未找到,可能无显著事件")
        else:
            self.log("未提供 condition_file,跳过差异剪切分析")

        # ---- Collect results ----
        self.update(pct=85, stage="收集结果")
        summary = self._generate_summary(
            output_prefix, total_events, events_per_type,
            events_ioe, psi_file, diffsplice_dpsi, diffsplice_psivec,
            requested_types, condition_file, psi_threshold, pval_threshold,
        )

        (out_dir / f"{output_prefix}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== SUPPA2 分析完成 → {out_dir} ===")

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _count_events_by_type(ioe_path: Path, as_types: list[str]) -> dict:
        """Count SUPPA2 events per type from the IOE file."""
        counts = {t: 0 for t in as_types}
        try:
            with open(ioe_path, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("#") or line.startswith("seqname"):
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) >= 3:
                        event_id = parts[2]
                        for t in as_types:
                            if event_id.startswith(t):
                                counts[t] = counts.get(t, 0) + 1
                                break
        except Exception:
            pass
        return counts

    def _generate_summary(self, prefix, total_events, events_per_type,
                          events_ioe, psi_file, diffsplice_dpsi,
                          diffsplice_psivec, as_types, condition_file,
                          psi_threshold, pval_threshold) -> dict:
        """Build summary dictionary with all results."""
        n_diff = 0
        if diffsplice_dpsi and diffsplice_dpsi.exists():
            try:
                with open(diffsplice_dpsi, encoding="utf-8",
                          errors="ignore") as fh:
                    for line in fh:
                        if line.startswith("#") or not line.strip():
                            continue
                        n_diff += 1
            except Exception:
                pass

        return {
            "output_prefix": prefix,
            "as_types_analyzed": as_types,
            "total_events": total_events,
            "events_per_type": events_per_type,
            "psi_threshold": psi_threshold,
            "pval_threshold": pval_threshold,
            "differential_analysis": bool(condition_file),
            "n_significant_events": n_diff,
            "output_files": {
                "events_ioe": str(events_ioe) if events_ioe.exists() else "",
                "psi": str(psi_file) if psi_file.exists() else "",
                "dpsi": str(diffsplice_dpsi) if diffsplice_dpsi
                        and diffsplice_dpsi.exists() else "",
                "psivec": str(diffsplice_psivec) if diffsplice_psivec
                          and diffsplice_psivec.exists() else "",
            },
        }


if __name__ == "__main__":
    Suppa2Runner.main()
