"""7 database functional annotation runner for DRS.

Annotates protein sequences against 7 major databases:
  Nr, UniProt, Pfam, KEGG, EggNOG, GO, SignalP

Parameters:
  transcripts_fasta: str  - Input transcript/protein FASTA (required)
  threads: int            - CPU threads for diamond (default 8)
  db_dir: str             - Database directory (default: from env or standard paths)

Outputs (to output_dir/):
  blast_nr.txt              - Diamond Nr results
  blast_uniprot.txt         - Diamond UniProt results
  pfam_domains.txt          - HMMER Pfam results
  kofam_scan_results.txt    - KEGG KO assignment
  eggnog_annotations.txt    - EggNOG annotations
  signalp_results.txt       - SignalP predictions
  combined_annotation.tsv   - Merged annotation table
  annotation_summary.json   - Summary statistics
"""
import json
import os
from pathlib import Path
import shutil

from runners.base import BaseRunner


class Annot7dbRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        transcripts_fasta = p.get("transcripts_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))
        db_dir = p.get("db_dir", "")
        evalue = float(p.get("evalue", 1e-5))
        max_targets = int(p.get("max_targets", 20))

        if not transcripts_fasta or not Path(transcripts_fasta).exists():
            raise FileNotFoundError(
                f"输入 FASTA 不存在: {transcripts_fasta}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Determine database directory
        if not db_dir:
            module_data = Path(os.environ.get("MODULE_DATA_DIR", ""))
            candidates = [
                module_data / "databases",
                module_data / "db",
                Path("/opt/plantomics-studio/databases"),
            ]
            for c in candidates:
                if c.exists():
                    db_dir = str(c)
                    break
        db_root = Path(db_dir) if db_dir else Path("/opt/plantomics-studio/databases")
        db_root = db_root.resolve()
        self.log(f"数据库目录: {db_root}")

        # Count input sequences
        n_seq = 0
        with open(transcripts_fasta, encoding="utf-8", errors="ignore") as fh:
            n_seq = sum(1 for ln in fh if ln.startswith(">"))
        self.log(f"输入序列: {n_seq} 条")

        # Diamond blastp vs Nr
        self.update(pct=5, stage="Diamond Nr", indeterminate=True)
        nr_out = out_dir / "blast_nr.txt"
        nr_db = self._find_db_path(db_root, "nr", "Nr")
        if nr_db:
            self.run_command([
                "diamond", "blastp",
                "--db", str(nr_db).replace(".dmnd", ""),
                "--query", transcripts_fasta,
                "--out", str(nr_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
                "--evalue", str(evalue),
                "--max-target-seqs", str(max_targets),
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond Nr")

        # Diamond blastp vs UniProt
        self.update(pct=20, stage="Diamond UniProt", indeterminate=True)
        uniprot_out = out_dir / "blast_uniprot.txt"
        up_db = self._find_db_path(db_root, "uniprot", "UniProt")
        if up_db:
            self.run_command([
                "diamond", "blastp",
                "--db", str(up_db).replace(".dmnd", ""),
                "--query", transcripts_fasta,
                "--out", str(uniprot_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
                "--evalue", str(evalue),
                "--max-target-seqs", str(max_targets),
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond UniProt")

        # hmmscan vs Pfam
        self.update(pct=35, stage="hmmscan Pfam", indeterminate=True)
        pfam_out = out_dir / "pfam_domains.txt"
        pfam_db = self._find_db_path(db_root, "Pfam/Pfam-A.hmm", "Pfam")
        if pfam_db:
            self.run_command([
                "hmmscan", "--cpu", str(threads),
                "--domtblout", str(pfam_out),
                str(pfam_db), transcripts_fasta,
            ], indeterminate=True, heartbeat_stage="hmmscan Pfam")

        # kofam_scan vs KEGG
        self.update(pct=50, stage="kofam_scan KEGG", indeterminate=True)
        kegg_out = out_dir / "kofam_scan_results.txt"
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
                    transcripts_fasta,
                ], indeterminate=True, heartbeat_stage="kofam_scan")

        # Diamond vs EggNOG
        self.update(pct=65, stage="Diamond EggNOG", indeterminate=True)
        eggnog_out = out_dir / "eggnog_annotations.txt"
        egg_db = self._find_db_path(db_root, "eggnog", "EggNOG")
        if egg_db:
            self.run_command([
                "diamond", "blastp",
                "--db", str(egg_db).replace(".dmnd", ""),
                "--query", transcripts_fasta,
                "--out", str(eggnog_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore",
                "--evalue", str(evalue),
                "--max-target-seqs", str(max_targets),
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond EggNOG")

        # SignalP
        self.update(pct=75, stage="SignalP 预测", indeterminate=True)
        signalp_out = out_dir / "signalp_results.txt"
        signalp_cmd = shutil.which("signalp") or shutil.which("signalp6")
        if signalp_cmd:
            self.run_command([
                signalp_cmd, "-fasta", transcripts_fasta,
                "-org", "euk", "-format", "txt",
                "-prefix", str(signalp_out).replace(".txt", ""),
            ], indeterminate=True, heartbeat_stage="SignalP")

        # Combine annotations
        self.update(pct=85, stage="整合注释结果")
        combined = out_dir / "combined_annotation.tsv"
        self._combine_annotations(
            nr_out if nr_out.exists() else None,
            uniprot_out if uniprot_out.exists() else None,
            pfam_out if pfam_out.exists() else None,
            kegg_out if kegg_out.exists() else None,
            eggnog_out if eggnog_out.exists() else None,
            combined)

        summary = {
            "query_sequences": n_seq,
            "databases_run": {
                "nr": nr_db is not None,
                "uniprot": up_db is not None,
                "pfam": pfam_db is not None,
                "kegg": exec_cmd is not None,
                "eggnog": egg_db is not None,
                "signalp": signalp_cmd is not None,
            },
            "outputs": {
                "nr": str(nr_out) if nr_out.exists() else "",
                "uniprot": str(uniprot_out) if uniprot_out.exists() else "",
                "pfam": str(pfam_out) if pfam_out.exists() else "",
                "kegg": str(kegg_out) if kegg_out.exists() else "",
                "eggnog": str(eggnog_out) if eggnog_out.exists() else "",
                "signalp": str(signalp_out) if signalp_out.exists() else "",
                "combined": str(combined) if combined.exists() else "",
            },
        }
        (out_dir / "annot_7db_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 7 数据库注释完成 → {out_dir} ===")

    @staticmethod
    def _find_db_path(db_root, rel_path, description):
        """Search for a database file."""
        candidates = [
            db_root / f"{rel_path}",
            db_root / f"{rel_path}.dmnd",
            db_root / f"{rel_path}.hmm",
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    @staticmethod
    def _combine_annotations(nr_file, uniprot_file, pfam_file,
                              kegg_file, eggnog_file, output_path):
        """Merge annotation results into a single TSV."""
        with open(output_path, "w", encoding="utf-8") as out:
            out.write("query_id\tannotation_source\tdatabase\tevalue\tannotation\n")
            for src, fpath, db_name in [
                ("blast", nr_file, "Nr"),
                ("blast", uniprot_file, "UniProt"),
                ("eggnog", eggnog_file, "EggNOG"),
            ]:
                if fpath and fpath.exists():
                    for line in open(fpath):
                        cols = line.strip().split("\t")
                        if len(cols) >= 12:
                            out.write(f"{cols[0]}\t{src}\t{db_name}\t"
                                      f"{cols[10]}\t{cols[12] if len(cols) > 12 else ''}\n")

            if pfam_file and pfam_file.exists():
                for line in open(pfam_file):
                    if line.startswith("#"):
                        continue
                    cols = line.strip().split()
                    if len(cols) >= 20:
                        out.write(f"{cols[3]}\thmmscan\tPfam\t"
                                  f"{cols[12]}\t{cols[4]}|{cols[5]}\n")

            if kegg_file and kegg_file.exists():
                for line in open(kegg_file):
                    if line.startswith("#"):
                        continue
                    cols = line.strip().split("\t")
                    if len(cols) >= 2:
                        out.write(f"{cols[0]}\tkofam_scan\tKEGG\t-\t{cols[1]}\n")


if __name__ == "__main__":
    Annot7dbRunner.main()
