"""Transcription factor identification runner for lncRNA data.

Identifies potential transcription factors among lncRNA transcripts
by searching against plant TF databases.

Parameters:
  transcripts_fasta: str  - Transcript sequences FASTA (required)
  plant_tf_db: str        - PlantTFDB or HMM profiles path (optional)
  threads: int            - CPU threads (default 8)

Outputs (to output_dir/):
  tf_blast.out              - Diamond blastx results
  tf_candidates.tsv         - Identified TF candidates
  tf_summary.json
"""
import json
import shutil
from pathlib import Path

from runners.base import BaseRunner


class TfRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fasta = p.get("transcripts_fasta", "")
        tf_db = p.get("plant_tf_db", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fasta or not Path(fasta).exists():
            raise FileNotFoundError(f"转录本 FASTA 不存在: {fasta}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        tf_candidates = []

        # Method 1: Diamond blastx against PlantTFDB
        if tf_db and Path(tf_db).exists():
            if not shutil.which("diamond"):
                raise FileNotFoundError("找不到 diamond")

            self.update(pct=20, stage="Diamond blastx 搜索 TF", indeterminate=True)
            blast_out = out_dir / "tf_blast.out"
            self.run_command([
                "diamond", "blastx",
                "-d", tf_db,
                "-q", fasta,
                "-o", str(blast_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident",
                "evalue", "bitscore", "stitle",
                "--threads", str(threads),
            ], indeterminate=True, heartbeat_stage="diamond tf search")

            # Parse results
            if blast_out.exists():
                tf_families = {}
                with open(blast_out, encoding="utf-8", errors="ignore") as fh:
                    for line in fh:
                        cols = line.strip().split("\t")
                        if len(cols) < 6:
                            continue
                        qid = cols[0]
                        title = cols[5]
                        # Extract TF family from title
                        family = title.split("|")[1] if "|" in title else title
                        if qid not in tf_families:
                            tf_families[qid] = set()
                        tf_families[qid].add(family)

                for tid, families in tf_families.items():
                    tf_candidates.append({
                        "transcript_id": tid,
                        "tf_families": list(families),
                        "method": "diamond_blastx",
                    })

        # Method 2: HMMER against Pfam domains (if no TF DB)
        elif shutil.which("hmmsearch"):
            # Look for Pfam HMM profiles
            hmm_dirs = [
                Path("/opt/plantomics-studio/databases/Pfam"),
                Path("/data/databases/Pfam"),
                Path("/db/Pfam"),
            ]
            hmm_file = None
            for hd in hmm_dirs:
                candidates = list(hd.glob("*.hmm")) + list(hd.glob("Pfam*.hmm"))
                if candidates:
                    hmm_file = candidates[0]
                    break

            if hmm_file:
                self.update(pct=20, stage="HMMER 搜索 TF 结构域",
                            indeterminate=True)
                hmm_out = out_dir / "tf_hmmer.out"
                self.run_command([
                    "hmmsearch", "--cpu", str(threads),
                    "--tblout", str(hmm_out),
                    str(hmm_file), fasta,
                ], indeterminate=True, heartbeat_stage="hmmsearch tf")

                if hmm_out.exists():
                    with open(hmm_out, encoding="utf-8", errors="ignore") as fh:
                        for line in fh:
                            if line.startswith("#"):
                                continue
                            cols = line.strip().split()
                            if len(cols) >= 5:
                                qid = cols[2]
                                domain = cols[0]
                                tf_candidates.append({
                                    "transcript_id": qid,
                                    "tf_families": [domain],
                                    "method": "hmmsearch",
                                })

        if not tf_candidates:
            self.log("!! 未找到 TF 数据库或 HMM profile, 未检测到 TF")

        # Write results
        if tf_candidates:
            cand_tsv = out_dir / "tf_candidates.tsv"
            with open(cand_tsv, "w", encoding="utf-8") as wf:
                wf.write("transcript_id\ttf_families\tmethod\n")
                for c in tf_candidates:
                    wf.write(f"{c['transcript_id']}\t"
                             f"{','.join(c['tf_families'])}\t"
                             f"{c['method']}\n")

        summary = {
            "n_tf_candidates": len(tf_candidates),
            "tf_db_used": tf_db if tf_db else "Pfam_HMM",
            "transcripts_fasta": fasta,
        }
        (out_dir / "tf_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== TF 鉴定: {len(tf_candidates)} 个候选 TF ===")


if __name__ == "__main__":
    TfRunner.main()
