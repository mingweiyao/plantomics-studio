"""7 数据库功能注释 runner (diamond blastp + hmmscan + kofam_scan)。

对 TransDecoder 预测的蛋白序列进行多数据库功能注释:
  1. diamond blastp — NR 数据库
  2. diamond blastp — UniProt 数据库
  3. hmmscan — Pfam 数据库
  4. kofam_scan — KEGG 数据库
  5. diamond blastp — eggNOG 数据库(可选)

参数:
  pep_fasta:   str   - 蛋白序列(TransDecoder .pep)
  cds_fasta:   str   - CDS 序列(可选,用于后续分析)
  nr_db:       str   - NR DIAMOND 数据库路径
  uniprot_db:  str   - UniProt DIAMOND 数据库路径
  pfam_db:     str   - Pfam HMM 数据库路径
  kofam_db:    str   - KEGG kofam 数据库目录(含 profiles 和 ko_list)
  eggnog_db:   str   - eggNOG DIAMOND 数据库路径(可选)
  go_obo:      str   - go.obo 文件路径(可选,用于 GO 注释解析)
  evalue:      float - DIAMOND/HMMER e-value 阈值,默认 1e-5
  threads:     int   - 默认 8

产出(到 output_subdir):
  nr_blast.txt         - NR 比对结果(blast tabular)
  uniprot_blast.txt    - UniProt 比对结果
  pfam_domain.txt      - Pfam 结构域结果
  kofam_results.txt    - KEGG KO 注释结果
  eggnog_blast.txt     - eggNOG 比对结果(如有)
  merged_annotation.tsv - 合并的完整注释表
  annotation_summary.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class Annot7dbRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        pep = p.get("pep_fasta")
        nr_db = p.get("nr_db")
        uniprot_db = p.get("uniprot_db")
        pfam_db = p.get("pfam_db")
        kofam_db = p.get("kofam_db")
        evalue = float(p.get("evalue", 1e-5))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not pep or not Path(pep).exists():
            raise FileNotFoundError(f"蛋白序列文件不存在: {pep}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # ── 1. diamond blastp vs NR ──
        nr_out = out_dir / "nr_blast.txt"
        if nr_db and Path(nr_db).exists():
            self.update(pct=10, stage="DIAMOND NR 数据库注释", detail="blastp vs NR")
            self.log(f"  数据库: {nr_db}")
            self.run_command([
                "diamond", "blastp",
                "-d", str(nr_db),
                "-q", str(pep),
                "-o", str(nr_out),
                "--evalue", str(evalue),
                "-p", str(threads),
                "--outfmt", "6",
                "qseqid", "sseqid", "pident", "length", "mismatch",
                "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
            ], heartbeat_stage="DIAMOND NR", indeterminate=True)
        else:
            self.log("!! NR 数据库未提供或不存在,跳过 NR 注释")

        # ── 2. diamond blastp vs UniProt ──
        up_out = out_dir / "uniprot_blast.txt"
        if uniprot_db and Path(uniprot_db).exists():
            self.update(pct=35, stage="DIAMOND UniProt 数据库注释", detail="blastp vs UniProt")
            self.run_command([
                "diamond", "blastp",
                "-d", str(uniprot_db),
                "-q", str(pep),
                "-o", str(up_out),
                "--evalue", str(evalue),
                "-p", str(threads),
                "--outfmt", "6",
                "qseqid", "sseqid", "pident", "length", "mismatch",
                "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
            ], heartbeat_stage="DIAMOND UniProt", indeterminate=True)
        else:
            self.log("!! UniProt 数据库未提供或不存在,跳过 UniProt 注释")

        # ── 3. hmmscan vs Pfam ──
        pfam_out = out_dir / "pfam_domain.txt"
        if pfam_db and Path(pfam_db).exists():
            self.update(pct=55, stage="HMMSCAN Pfam 结构域注释", detail="hmmscan vs Pfam")
            self.run_command([
                "hmmscan", "--cpu", str(threads),
                "-E", str(evalue),
                "--domE", str(evalue),
                "--tblout", str(pfam_out),
                str(pfam_db), str(pep),
            ], heartbeat_stage="HMMSCAN Pfam", indeterminate=True)
        else:
            self.log("!! Pfam 数据库未提供或不存在,跳过 Pfam 注释")

        # ── 4. kofam_scan vs KEGG ──
        kofam_out = out_dir / "kofam_results.txt"
        if kofam_db and Path(kofam_db).exists():
            self.update(pct=75, stage="kofam_scan KEGG 注释", detail="KO 分配")
            profiles = Path(kofam_db) / "profiles"
            ko_list = Path(kofam_db) / "ko_list"
            if profiles.exists() and ko_list.exists():
                self.run_command([
                    "exec_annotation",
                    "-o", str(kofam_out),
                    "-E", str(evalue),
                    "-f", "mapper",
                    str(pep),
                    "-p", str(profiles),
                    "-k", str(ko_list),
                ], heartbeat_stage="kofam_scan KEGG", indeterminate=True)
            else:
                self.log("!! kofam 数据库缺少 profiles/ 或 ko_list,跳过 KEGG 注释")
        else:
            self.log("!! KEGG kofam 数据库未提供或不存在,跳过 KEGG 注释")

        # ── 5. eggNOG (可选) ──
        eggnog_out = out_dir / "eggnog_blast.txt"
        eggnog_db = p.get("eggnog_db")
        if eggnog_db and Path(eggnog_db).exists():
            self.update(pct=85, stage="DIAMOND eggNOG 注释", detail="blastp vs eggNOG")
            self.run_command([
                "diamond", "blastp",
                "-d", str(eggnog_db),
                "-q", str(pep),
                "-o", str(eggnog_out),
                "--evalue", str(evalue),
                "-p", str(threads),
                "--outfmt", "6",
                "qseqid", "sseqid", "pident", "length", "mismatch",
                "gapopen", "qstart", "qend", "sstart", "send",
                "evalue", "bitscore", "stitle",
            ], heartbeat_stage="DIAMOND eggNOG", indeterminate=True)

        # ── 6. 合并注释并生成统计 ──
        self.update(pct=92, stage="合并注释结果", detail="生成注释表")
        merged = out_dir / "merged_annotation.tsv"
        summary = {
            "pep_fasta": pep,
            "nr_db": nr_db,
            "uniprot_db": uniprot_db,
            "pfam_db": pfam_db,
            "kofam_db": kofam_db,
            "eggnog_db": eggnog_db,
            "evalue": evalue,
            "n_nr_hits": 0,
            "n_uniprot_hits": 0,
            "n_pfam_domains": 0,
            "n_kofam_ko": 0,
            "n_eggnog_hits": 0,
            "n_total_annotated": 0,
        }

        # 统计命中数
        if nr_out.exists():
            summary["n_nr_hits"] = sum(1 for _ in open(nr_out) if _.strip())
        if up_out.exists():
            summary["n_uniprot_hits"] = sum(1 for _ in open(up_out) if _.strip())
        if pfam_out.exists():
            summary["n_pfam_domains"] = sum(1 for _ in open(pfam_out) if _.strip())
        if kofam_out.exists():
            summary["n_kofam_ko"] = sum(1 for _ in open(kofam_out) if _.strip())
        if eggnog_out.exists():
            summary["n_eggnog_hits"] = sum(1 for _ in open(eggnog_out) if _.strip())

        # 建立合并列表:读取蛋白 ID 列表
        all_prots = set()
        for f_path in [nr_out, up_out, pfam_out, kofam_out, eggnog_out]:
            if f_path.exists():
                for line in open(f_path):
                    if line.strip():
                        all_prots.add(line.split("\t")[0])
        summary["n_total_annotated"] = len(all_prots)

        # 写入合并注释表(header)
        with open(merged, "w") as f:
            f.write("\t".join([
                "protein_id",
                "nr_hit", "nr_evalue", "nr_bitscore",
                "uniprot_hit", "uniprot_evalue", "uniprot_bitscore",
                "pfam_hit", "pfam_evalue",
                "kegg_ko", "kegg_evalue",
                "eggnog_hit", "eggnog_evalue",
            ]) + "\n")
            # 逐蛋白写入
            for prot in sorted(all_prots):
                row = [prot, "", "", "", "", "", "", "", "", "", "", "", ""]
                # NR
                if nr_out.exists():
                    for line in open(nr_out):
                        cols = line.strip().split("\t")
                        if cols[0] == prot:
                            row[1] = cols[12] if len(cols) > 12 else cols[1]
                            row[2] = cols[10]
                            row[3] = cols[11]
                            break
                # UniProt
                if up_out.exists():
                    for line in open(up_out):
                        cols = line.strip().split("\t")
                        if cols[0] == prot:
                            row[4] = cols[12] if len(cols) > 12 else cols[1]
                            row[5] = cols[10]
                            row[6] = cols[11]
                            break
                # Pfam
                if pfam_out.exists():
                    for line in open(pfam_out):
                        if line.startswith("#"):
                            continue
                        cols = line.strip().split()
                        if len(cols) >= 6 and cols[2] == prot:
                            row[7] = cols[0]
                            row[8] = cols[4]
                            break
                # KEGG
                if kofam_out.exists():
                    for line in open(kofam_out):
                        cols = line.strip().split("\t")
                        if len(cols) >= 3 and cols[0] == prot:
                            row[9] = cols[1]
                            row[10] = cols[2] if len(cols) > 2 else ""
                            break
                # eggNOG
                if eggnog_out.exists():
                    for line in open(eggnog_out):
                        cols = line.strip().split("\t")
                        if cols[0] == prot:
                            row[11] = cols[12] if len(cols) > 12 else cols[1]
                            row[12] = cols[10]
                            break
                f.write("\t".join(row) + "\n")

        (out_dir / "annotation_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 7 数据库注释完成: {summary['n_total_annotated']} 条蛋白被注释 ===")


if __name__ == "__main__":
    Annot7dbRunner.main()
