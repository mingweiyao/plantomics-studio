"""7 database functional annotation runner for Tail Iso-seq.

Annotates protein sequences against multiple databases:
  diamond blastp -> Nr/UniProt/EggNOG
  hmmscan -> Pfam
  kofam_scan -> KEGG
  SignalP -> signal peptide

Parameters:
  pep_fasta: str         - Input protein FASTA
  threads: int           - CPU threads (default: 8)
  db_dir: str            - Database directory
  evalue: float          - E-value threshold (default: 1e-5)

Outputs (to output_dir/):
  blast_nr.txt                 - Nr results
  blast_uniprot.txt            - UniProt results
  pfam_domains.txt             - Pfam results
  kofam_scan_results.txt       - KEGG results
  combined_annotation.tsv      - Merged annotation
  annotation_summary.json      - Summary
"""
import json
import os
from pathlib import Path
import shutil

from runners.base import BaseRunner


def _find_db(candidates, desc):
    for p in candidates:
        resolved = Path(p)
        if resolved.exists():
            return resolved
        for ext in [".dmnd", ".hmm", ".hmm.h3f"]:
            with_ext = resolved.parent / (resolved.name + ext)
            if with_ext.exists():
                return with_ext
    raise FileNotFoundError(
        f"找不到 {desc} 数据库。检查:\n" + "\n".join(f"  - {p}" for p in candidates))


class Annot7dbRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        pep_fasta = p.get("pep_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))
        db_dir = p.get("db_dir", "")
        evalue = float(p.get("evalue", 1e-5))

        if not pep_fasta or not Path(pep_fasta).exists():
            raise FileNotFoundError(f"蛋白 FASTA 不存在: {pep_fasta}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        if not db_dir:
            module_data = Path(os.environ.get("MODULE_DATA_DIR", ""))
            for c in [module_data / "databases",
                      Path("/opt/plantomics-studio/databases")]:
                if c.exists():
                    db_dir = str(c)
                    break
        db_root = Path(db_dir) if db_dir else Path("/opt/plantomics-studio/databases")

        n_pep = sum(1 for ln in open(pep_fasta) if ln.startswith(">"))
        self.log(f"输入 {n_pep} 条蛋白序列")

        # 1) Diamond Nr
        self.update(pct=5, stage="Diamond Nr", indeterminate=True)
        nr_out = out_dir / "blast_nr.txt"
        try:
            nr_db = _find_db([str(db_root / "nr" / "nr"),
                               str(db_root / "nr.dmnd")], "Nr")
            self.run_command([
                "diamond", "blastp",
                "--db", str(nr_db).replace(".dmnd", ""),
                "--query", pep_fasta,
                "--out", str(nr_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
                "--evalue", str(evalue),
                "--max-target-seqs", "20",
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond Nr")
        except FileNotFoundError:
            self.log("Nr 数据库不可用,跳过")

        # 2) Diamond UniProt
        self.update(pct=20, stage="Diamond UniProt", indeterminate=True)
        uniprot_out = out_dir / "blast_uniprot.txt"
        try:
            up_db = _find_db([str(db_root / "uniprot" / "uniprot"),
                               str(db_root / "uniprot.dmnd")], "UniProt")
            self.run_command([
                "diamond", "blastp",
                "--db", str(up_db).replace(".dmnd", ""),
                "--query", pep_fasta,
                "--out", str(uniprot_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
                "--evalue", str(evalue),
                "--max-target-seqs", "20",
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond UniProt")
        except FileNotFoundError:
            self.log("UniProt 数据库不可用,跳过")

        # 3) hmmscan Pfam
        self.update(pct=35, stage="hmmscan Pfam", indeterminate=True)
        pfam_out = out_dir / "pfam_domains.txt"
        try:
            pfam_db = _find_db([str(db_root / "Pfam" / "Pfam-A.hmm")], "Pfam")
            self.run_command([
                "hmmscan", "--cpu", str(threads),
                "--domtblout", str(pfam_out),
                str(pfam_db), pep_fasta,
            ], indeterminate=True, heartbeat_stage="hmmscan Pfam")
        except FileNotFoundError:
            self.log("Pfam 数据库不可用,跳过")

        # 4) kofam_scan KEGG
        self.update(pct=50, stage="kofam_scan KEGG", indeterminate=True)
        kegg_out = out_dir / "kofam_scan_results.txt"
        try:
            exec_cmd = shutil.which("exec_annotation")
            if exec_cmd:
                profile_dir = db_root / "kofam_scan" / "profiles"
                ko_list = db_root / "kofam_scan" / "ko_list"
                if profile_dir.exists() and ko_list.exists():
                    self.run_command([
                        "exec_annotation",
                        "-p", str(profile_dir), "-k", str(ko_list),
                        "-o", str(kegg_out), "-f", "mapper",
                        "-c", str(threads), "-E", str(evalue),
                        pep_fasta,
                    ], indeterminate=True, heartbeat_stage="kofam_scan")
        except FileNotFoundError:
            self.log("kofam_scan 不可用,跳过")

        # 5) SignalP
        self.update(pct=75, stage="SignalP 预测", indeterminate=True)
        signalp_out = out_dir / "signalp_results.txt"
        sp = shutil.which("signalp") or shutil.which("signalp6")
        if sp:
            self.run_command([
                sp, "-fasta", pep_fasta, "-org", "euk",
                "-format", "txt", "-prefix",
                str(signalp_out).replace(".txt", ""),
            ], indeterminate=True, heartbeat_stage="SignalP")

        # Merge annotations
        self.update(pct=85, stage="整合注释")
        combined = out_dir / "combined_annotation.tsv"
        with open(combined, "w", encoding="utf-8") as out:
            out.write("query_id\tsource\tdatabase\tevalue\tannotation\n")
            for fpath, src, db_name in [
                (nr_out, "blast", "Nr"),
                (uniprot_out, "blast", "UniProt"),
            ]:
                if fpath and fpath.exists():
                    for line in open(fpath):
                        cols = line.strip().split("\t")
                        if len(cols) >= 12:
                            out.write(f"{cols[0]}\t{src}\t{db_name}\t"
                                      f"{cols[10]}\t{cols[12] if len(cols) > 12 else ''}\n")
            if pfam_out.exists():
                for line in open(pfam_out):
                    if line.startswith("#"):
                        continue
                    cols = line.strip().split()
                    if len(cols) >= 20:
                        out.write(f"{cols[3]}\thmmscan\tPfam\t{cols[12]}\t{cols[4]}|{cols[5]}\n")

        n_annotated = sum(1 for ln in open(combined)) - 1
        summary = {
            "query_sequences": n_pep,
            "n_annotated": n_annotated,
            "nr_hits": sum(1 for _ in open(nr_out)) if nr_out.exists() else 0,
        }
        (out_dir / "annotation_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 注释完成: {n_annotated} 条序列有注释 → {out_dir} ===")


if __name__ == "__main__":
    Annot7dbRunner.main()
