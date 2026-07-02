"""7 database functional annotation runner.

Annotates protein sequences against 7 major databases:
  1. diamond blastp -> NCBI Nr (non-redundant)
  2. diamond blastp -> UniProt (Swiss-Prot + TrEMBL)
  3. hmmscan -> Pfam (protein families)
  4. kofam_scan -> KEGG (orthologs and pathways)
  5. diamond blastp -> EggNOG (evolutionary genealogy)
  6. diamond blastp -> GO (Gene Ontology terms via interpro2go)
  7. SignalP -> Signal peptide prediction

Parameters:
  pep_fasta: str         - Input protein FASTA (from TransDecoder)
  threads: int           - CPU threads for diamond (default 8)
  db_dir: str            - Database directory (default: module data dir)
  nr_db: str             - Nr database path (default: <db_dir>/nr/nr.dmnd)
  uniprot_db: str        - UniProt database path (default: <db_dir>/uniprot/uniprot.dmnd)
  pfam_db: str           - Pfam HMM database (default: <db_dir>/Pfam/Pfam-A.hmm)
  kegg_db: str           - KEGG profile directory (default: <db_dir>/kofam_scan/profiles)
  ete3_db: str           - EggNOG database (default: <db_dir>/eggnog/eggnog.dmnd)
  evalue: float          - E-value threshold (default: 1e-5)
  max_targets: int       - Max target sequences per query (default: 20)

Outputs (to output_dir/):
  blast_nr.txt                   - Diamond Nr results (tabular)
  blast_uniprot.txt              - Diamond UniProt results
  pfam_domains.txt               - HMMER Pfam results
  kofam_scan_results.txt         - KEGG KO assignment
  eggnog_annotations.txt         - EggNOG annotations
  signalp_results.txt            - SignalP predictions
  combined_annotation.tsv        - Merged annotation table
  annotation_summary.json        - Summary statistics
"""
import json
import os
from pathlib import Path
import shutil

from runners.base import BaseRunner


def _find_db(candidate_paths, description: str) -> Path:
    """Search for a database file across candidate paths."""
    for p in candidate_paths:
        resolved = Path(p)
        if resolved.exists():
            return resolved
        # Also try with extensions
        for ext in [".dmnd", ".hmm", ".hmm.h3f", ".hmm.h3i", ".hmm.h3m", ".hmm.h3p"]:
            with_ext = resolved.parent / (resolved.name + ext)
            if with_ext.exists():
                return with_ext
    raise FileNotFoundError(
        f"找不到 {description} 数据库。检查以下路径:\n" +
        "\n".join(f"  - {p}" for p in candidate_paths))


class Annot7dbRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        pep_fasta = p.get("pep_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))
        db_dir = p.get("db_dir", "")
        evalue = float(p.get("evalue", 1e-5))
        max_targets = int(p.get("max_targets", 20))

        if not pep_fasta or not Path(pep_fasta).exists():
            raise FileNotFoundError(f"蛋白 FASTA 不存在: {pep_fasta}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Determine database directory
        if not db_dir:
            # Default: check common locations
            module_data = Path(os.environ.get("MODULE_DATA_DIR", ""))
            candidates = [
                module_data / "databases",
                module_data / "db",
                Path("/opt/plantomics-studio/databases"),
                Path("/data/databases"),
            ]
            for c in candidates:
                if c.exists():
                    db_dir = str(c)
                    break
        db_root = Path(db_dir) if db_dir else Path("/opt/plantomics-studio/databases")
        db_root = db_root.resolve()

        self.log(f"数据库目录: {db_root}")

        # Check input protein count
        n_pep = 0
        with open(pep_fasta, encoding="utf-8", errors="ignore") as fh:
            n_pep = sum(1 for ln in fh if ln.startswith(">"))
        self.log(f"输入蛋白: {n_pep} 条序列")

        # ---- Step 1: Diamond BLASTP vs NCBI Nr ----
        self.update(pct=5, stage="Diamond Nr", indeterminate=True)
        nr_out = out_dir / "blast_nr.txt"
        try:
            nr_db = _find_db([
                str(db_root / "nr" / "nr"),
                str(db_root / "nr.dmnd"),
            ], "Nr")
            self.run_command([
                "diamond", "blastp",
                "--db", str(nr_db).replace(".dmnd", ""),
                "--query", pep_fasta,
                "--out", str(nr_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
                "--evalue", str(evalue),
                "--max-target-seqs", str(max_targets),
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond Nr")
        except FileNotFoundError:
            self.log("Nr 数据库不可用,跳过")

        # ---- Step 2: Diamond BLASTP vs UniProt ----
        self.update(pct=20, stage="Diamond UniProt", indeterminate=True)
        uniprot_out = out_dir / "blast_uniprot.txt"
        try:
            up_db = _find_db([
                str(db_root / "uniprot" / "uniprot"),
                str(db_root / "uniprot.dmnd"),
            ], "UniProt")
            self.run_command([
                "diamond", "blastp",
                "--db", str(up_db).replace(".dmnd", ""),
                "--query", pep_fasta,
                "--out", str(uniprot_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
                "--evalue", str(evalue),
                "--max-target-seqs", str(max_targets),
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond UniProt")
        except FileNotFoundError:
            self.log("UniProt 数据库不可用,跳过")

        # ---- Step 3: HMMER hmmscan vs Pfam ----
        self.update(pct=35, stage="hmmscan Pfam", indeterminate=True)
        pfam_out = out_dir / "pfam_domains.txt"
        try:
            pfam_db = _find_db([
                str(db_root / "Pfam" / "Pfam-A.hmm"),
            ], "Pfam")
            self.run_command([
                "hmmscan", "--cpu", str(threads),
                "--domtblout", str(pfam_out),
                str(pfam_db),
                pep_fasta,
            ], indeterminate=True, heartbeat_stage="hmmscan Pfam")
        except FileNotFoundError:
            self.log("Pfam 数据库不可用,跳过")

        # ---- Step 4: kofam_scan vs KEGG ----
        self.update(pct=50, stage="kofam_scan KEGG", indeterminate=True)
        kegg_out = out_dir / "kofam_scan_results.txt"
        try:
            # Try kofam_scan: exec_annotation
            exec_cmd = shutil.which("exec_annotation") or shutil.which("kofam_scan")
            if exec_cmd:
                profile_dir = db_root / "kofam_scan" / "profiles"
                ko_list = db_root / "kofam_scan" / "ko_list"
                if profile_dir.exists() and ko_list.exists():
                    self.run_command([
                        "exec_annotation",
                        "-p", str(profile_dir),
                        "-k", str(ko_list),
                        "-o", str(kegg_out),
                        "-f", "mapper",
                        "-c", str(threads),
                        "-E", str(evalue),
                        pep_fasta,
                    ], indeterminate=True, heartbeat_stage="kofam_scan")
                else:
                    self.log("KEGG 配置文件不全,跳过")
            else:
                self.log("exec_annotation 不可用,跳过 KEGG")
        except FileNotFoundError:
            self.log("kofam_scan 不可用,跳过")

        # ---- Step 5: Diamond BLASTP vs EggNOG ----
        self.update(pct=65, stage="Diamond EggNOG", indeterminate=True)
        eggnog_out = out_dir / "eggnog_annotations.txt"
        try:
            egg_db = _find_db([
                str(db_root / "eggnog" / "eggnog"),
                str(db_root / "eggnog.dmnd"),
            ], "EggNOG")
            self.run_command([
                "diamond", "blastp",
                "--db", str(egg_db).replace(".dmnd", ""),
                "--query", pep_fasta,
                "--out", str(eggnog_out),
                "--outfmt", "6", "qseqid", "sseqid", "pident", "length",
                "mismatch", "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore",
                "--evalue", str(evalue),
                "--max-target-seqs", str(max_targets),
                "--threads", str(threads),
                "--sensitive",
            ], indeterminate=True, heartbeat_stage="Diamond EggNOG")
        except FileNotFoundError:
            self.log("EggNOG 数据库不可用,跳过")

        # ---- Step 6: SignalP ----
        self.update(pct=75, stage="SignalP 预测", indeterminate=True)
        signalp_out = out_dir / "signalp_results.txt"
        signalp_cmd = shutil.which("signalp") or shutil.which("signalp6")
        if signalp_cmd:
            self.run_command([
                signalp_cmd,
                "-fasta", pep_fasta,
                "-org", "euk",
                "-format", "txt",
                "-prefix", str(signalp_out).replace(".txt", ""),
                "-gff3",
            ], indeterminate=True, heartbeat_stage="SignalP")

        # ---- Step 7: Combine annotations ----
        self.update(pct=85, stage="整合注释结果")
        combined = out_dir / "combined_annotation.tsv"
        self._combine_annotations(
            nr_out if nr_out.exists() else None,
            uniprot_out if uniprot_out.exists() else None,
            pfam_out if pfam_out.exists() else None,
            kegg_out if kegg_out.exists() else None,
            eggnog_out if eggnog_out.exists() else None,
            combined,
        )

        # Generate summary
        n_annotated_nr = 0
        if nr_out.exists():
            n_annotated_nr = sum(1 for _ in open(nr_out))

        summary = {
            "query_sequences": n_pep,
            "databases_run": {
                "nr": nr_out.exists(),
                "uniprot": uniprot_out.exists(),
                "pfam": pfam_out.exists(),
                "kegg": kegg_out.exists(),
                "eggnog": eggnog_out.exists(),
                "signalp": signalp_out.exists() and signalp_out.stat().st_size > 0,
            },
            "n_annotated_nr": n_annotated_nr,
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
        (out_dir / "annotation_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 7 数据库注释完成 → {out_dir} ===")

    @staticmethod
    def _combine_annotations(nr_file, uniprot_file, pfam_file,
                              kegg_file, eggnog_file, output_path):
        """Merge annotation results into a single TSV."""
        annotated_ids = set()
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
                            qid = cols[0]
                            annotated_ids.add(qid)
                            out.write(f"{qid}\t{src}\t{db_name}\t"
                                      f"{cols[10]}\t{cols[12] if len(cols) > 12 else ''}\n")

            if pfam_file and pfam_file.exists():
                # Parse HMMER domtblout
                for line in open(pfam_file):
                    if line.startswith("#"):
                        continue
                    cols = line.strip().split()
                    if len(cols) >= 20:
                        qid = cols[3]
                        pfam_acc = cols[4]
                        pfam_name = cols[5]
                        evalue_col = cols[12]
                        annotated_ids.add(qid)
                        out.write(f"{qid}\thmmscan\tPfam\t{evalue_col}\t"
                                  f"{pfam_acc}|{pfam_name}\n")

            if kegg_file and kegg_file.exists():
                for line in open(kegg_file):
                    if line.startswith("#"):
                        continue
                    cols = line.strip().split("\t")
                    if len(cols) >= 2:
                        qid = cols[0]
                        ko = cols[1]
                        annotated_ids.add(qid)
                        out.write(f"{qid}\tkofam_scan\tKEGG\t-\t{ko}\n")


if __name__ == "__main__":
    Annot7dbRunner.main()
