"""7-database functional annotation runner for lncRNA transcripts.

Runs diamond blastx against available protein/nucleotide databases to
functionally annotate lncRNA transcripts.

Parameters:
  transcripts_fasta: str  - Transcript sequences FASTA (required)
  threads: int            - CPU threads (default 8)

Outputs (to output_dir/):
  diamond_{db}.out          - Diamond blastx results per DB
  annot_7db_summary.json    - Annotation counts per database
"""
import json
from pathlib import Path
import shutil

from runners.base import BaseRunner


class Annot7dbRunner(BaseRunner):

    # Standard database paths
    DB_NAMES = ["NR", "SwissProt", "Pfam", "GO", "KEGG", "COG", "InterPro"]

    def run(self):
        p = self.job.params or {}
        fasta = p.get("transcripts_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fasta or not Path(fasta).exists():
            raise FileNotFoundError(f"转录本 FASTA 不存在: {fasta}")

        if not shutil.which("diamond"):
            raise FileNotFoundError("找不到 diamond, 请重建 conda 环境")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Check for databases in standard locations
        db_dirs = [
            Path("/opt/plantomics-studio/databases"),
            Path("/data/databases"),
            Path("/db"),
        ]
        db_paths = {}
        for db_name in self.DB_NAMES:
            for db_dir in db_dirs:
                candidates = [
                    db_dir / f"{db_name}" / f"{db_name}.dmnd",
                    db_dir / f"{db_name}.dmnd",
                    db_dir / f"{db_name}" / f"{db_name}",
                    db_dir / f"{db_name.lower()}" / f"{db_name.lower()}.dmnd",
                ]
                for c in candidates:
                    if c.exists():
                        db_paths[db_name] = str(c)
                        break
                if db_name in db_paths:
                    break

        if not db_paths:
            self.log("!! 未找到任何注释数据库, 仅输出占位信息")
            summary = {
                "n_transcripts": 0,
                "databases_found": [],
                "results": {},
                "note": "未找到注释数据库, 请配置数据库路径",
            }
            (out_dir / "annot_7db_summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log("=== 注释数据库未找到, 跳过 ===")
            self.update(pct=100, stage="完成(无数据库)")
            return

        results = {}
        db_list = list(db_paths.keys())
        n_dbs = len(db_list)

        for i, (db_name, db_path) in enumerate(db_paths.items()):
            self.update(pct=int(90 * (i + 1) / max(n_dbs, 1)),
                        stage=f"Diamond blastx: {db_name} ({i + 1}/{n_dbs})",
                        indeterminate=True)

            out_file = out_dir / f"diamond_{db_name}.out"
            self.run_command([
                "diamond", "blastx",
                "-d", db_path,
                "-q", fasta,
                "-o", str(out_file),
                "--threads", str(threads),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "evalue", "bitscore", "stitle",
            ], indeterminate=True, heartbeat_stage=f"diamond {db_name}")

            n_hits = 0
            if out_file.exists():
                n_hits = sum(1 for _ in out_file.read_text(
                    encoding="utf-8", errors="ignore").splitlines()
                    if _.strip())
            results[db_name] = {
                "db_path": db_path,
                "n_hits": n_hits,
                "output_file": str(out_file),
            }
            self.log(f"  {db_name}: {n_hits} hits")

        summary = {
            "n_transcripts": 0,
            "databases_found": db_list,
            "results": results,
        }
        (out_dir / "annot_7db_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== 7 DB 注释完成: {len(results)} 个数据库 ===")


if __name__ == "__main__":
    Annot7dbRunner.main()
