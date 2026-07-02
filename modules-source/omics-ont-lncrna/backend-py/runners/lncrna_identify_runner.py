"""lncRNA identification runner for ONT full-length lncRNA data.

Identifies lncRNA candidates using CPC2 and PLEK coding potential tools,
with length filtering (200-20000bp) and ORF filtering.

Pipeline:
  1. gffread - extract transcript sequences from GTF
  2. Length filter: keep transcripts 200-20000bp
  3. CPC2: coding potential assessment
  4. PLEK: coding potential assessment
  5. ORF filter: remove transcripts with long ORFs (>=300nt)
  6. Intersection: CPC2 noncoding ∩ PLEK noncoding ∩ no_long_ORF

Parameters:
  candidate_gtf: str    - Transcript GTF (required)
  genome_fasta: str      - Genome FASTA (required)
  min_length: int       - Min transcript length, default 200
  max_length: int       - Max transcript length, default 20000
  threads: int          - CPU threads (default 8)

Outputs (to output_dir/):
  candidate_transcripts.fa     - Length-filtered transcripts
  cpc2_result.txt              - CPC2 output
  plek_result.txt              - PLEK output
  lncRNA_list.tsv              - Identified lncRNA transcripts
  lncRNA.fa                    - lncRNA sequences
  lncrna_identify_summary.json - Summary with Venn sets
"""
import json
import shutil
from pathlib import Path

from runners.base import BaseRunner


def parse_fasta(path: Path) -> dict:
    seqs, name, buf = {}, None, []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith(">"):
                if name is not None:
                    seqs[name] = "".join(buf)
                name = line[1:].strip().split()[0]
                buf = []
            else:
                buf.append(line.strip())
        if name is not None:
            seqs[name] = "".join(buf)
    return seqs


def write_fasta(path: Path, seqs: dict, ids):
    with open(path, "w", encoding="utf-8") as wf:
        for tid in ids:
            seq = seqs.get(tid, "")
            if not seq:
                continue
            wf.write(f">{tid}\n")
            for i in range(0, len(seq), 60):
                wf.write(seq[i:i + 60] + "\n")


class LncrnaIdentifyRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        cand = p.get("candidate_gtf", "")
        genome = p.get("genome_fasta", "")
        min_len = int(p.get("min_length", 200))
        max_len = int(p.get("max_length", 20000))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(f"候选转录本 GTF 不存在: {cand}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) Extract transcript sequences
        self.update(pct=5, stage="gffread 抽转录本序列")
        raw_fa = out_dir / "all_transcripts.fa"
        self.run_command(["gffread", "-w", str(raw_fa), "-g", genome, str(cand)])
        seqs_all = parse_fasta(raw_fa) if raw_fa.exists() else {}
        if not seqs_all:
            raise RuntimeError("gffread 没抽出转录本序列")
        self.log(f"抽到 {len(seqs_all)} 条转录本序列")

        # 2) Length filter 200-20000bp
        seqs = {t: s for t, s in seqs_all.items()
                if min_len <= len(s) <= max_len}
        fa = out_dir / "candidate_transcripts.fa"
        write_fasta(fa, seqs, list(seqs.keys()))
        self.log(f"长度过滤 {min_len}-{max_len}bp: {len(seqs)}/{len(seqs_all)}")
        if not seqs:
            raise RuntimeError("长度过滤后没有候选转录本")

        # 3) CPC2
        self.update(pct=20, stage="CPC2 编码潜能", indeterminate=True)
        self._ensure_cpc2_libsvm()
        cpc2_out = out_dir / "cpc2_result"
        self.run_command(["CPC2.py", "-i", str(fa), "-o", str(cpc2_out)],
                         indeterminate=True, heartbeat_stage="CPC2")
        cpc2_noncoding = self._parse_cpc2(Path(str(cpc2_out) + ".txt"))
        cpc2_coding = set(seqs.keys()) - cpc2_noncoding
        self.log(f"CPC2 非编码: {len(cpc2_noncoding)}, 编码: {len(cpc2_coding)}")

        # 4) PLEK
        self.update(pct=45, stage="PLEK 编码潜能", indeterminate=True)
        plek_out = out_dir / "plek_result.txt"
        self.run_command(["PLEK", "-fasta", str(fa), "-out", str(plek_out),
                          "-thread", str(threads)],
                         indeterminate=True, heartbeat_stage="PLEK")
        plek_noncoding = self._parse_plek(plek_out)
        plek_coding = set(seqs.keys()) - plek_noncoding
        self.log(f"PLEK 非编码: {len(plek_noncoding)}, 编码: {len(plek_coding)}")

        # 5) ORF filter: remove sequences with ORF >= 300nt (100aa)
        self.update(pct=65, stage="ORF 过滤", indeterminate=True)
        orf_fa = out_dir / "orfs.fa"
        self.run_command([
            "getorf", "-sequence", str(fa), "-outseq", str(orf_fa),
            "-find", "3", "-minsize", "300", "-noreverse",
        ], indeterminate=True, heartbeat_stage="getorf", check=False)

        seqs_with_long_orf = set()
        if orf_fa.exists() and orf_fa.stat().st_size > 0:
            orf_seqs = parse_fasta(orf_fa)
            for orf_name in orf_seqs:
                # getorf names: transcript_id_ORF_type_start_stop
                tid = orf_name.rsplit("_", 3)[0] if "_" in orf_name else orf_name
                seqs_with_long_orf.add(tid)
        no_orf = set(seqs.keys()) - seqs_with_long_orf
        self.log(f"有长 ORF(>=300nt): {len(seqs_with_long_orf)}, "
                 f"无长 ORF: {len(no_orf)}")

        # 6) Intersection
        self.update(pct=85, stage="取交集")
        lnc_ids = sorted(cpc2_noncoding & plek_noncoding & no_orf)

        # Write results
        with open(out_dir / "lncRNA_list.tsv", "w", encoding="utf-8") as wf:
            wf.write("transcript_id\tlength\tcpc2\tplek\tlong_orf\n")
            for tid in lnc_ids:
                wf.write(f"{tid}\t{len(seqs.get(tid, ''))}"
                         f"\tnoncoding\tnon-coding\tno\n")
        write_fasta(out_dir / "lncRNA.fa", seqs, lnc_ids)

        # Venn set info
        cpc2_only = sorted(cpc2_noncoding - plek_noncoding - seqs_with_long_orf)
        plek_only = sorted(plek_noncoding - cpc2_noncoding - seqs_with_long_orf)
        both_only = sorted(
            (cpc2_noncoding & plek_noncoding) - seqs_with_long_orf - set(lnc_ids))
        no_orf_only = sorted(no_orf - cpc2_noncoding - plek_noncoding)

        summary = {
            "n_candidates": len(seqs),
            "n_total_transcripts": len(seqs_all),
            "filters": {
                "min_length": min_len,
                "max_length": max_len,
                "min_orf_nt": 300,
            },
            "cpc2": {"noncoding": len(cpc2_noncoding), "coding": len(cpc2_coding)},
            "plek": {"noncoding": len(plek_noncoding), "coding": len(plek_coding)},
            "orf_filter": {
                "with_long_orf": len(seqs_with_long_orf),
                "no_long_orf": len(no_orf),
            },
            "venn": {
                "cpc2_only": len(cpc2_only),
                "plek_only": len(plek_only),
                "both": len(both_only),
                "no_orf_only": len(no_orf_only),
                "lncrna_identified": len(lnc_ids),
            },
            "n_lncRNA": len(lnc_ids),
        }
        (out_dir / "lncrna_identify_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== lncRNA(CPC2+PLEK+ORF 过滤): {len(lnc_ids)} 条 ===")

    def _ensure_cpc2_libsvm(self):
        """Fix CPC2 libsvm binaries. Copied from reference module."""
        cpc2 = shutil.which("CPC2.py")
        if not cpc2:
            raise FileNotFoundError("找不到 CPC2.py, 请重建 conda 环境")
        svm = {n: shutil.which(n) for n in ("svm-scale", "svm-predict")}
        cpc2_real = Path(cpc2).resolve()
        pkg_root = cpc2_real.parent.parent
        targets = [
            pkg_root / "libsvm" / "libsvm-3.18",
            cpc2_real.parent / "libsvm" / "libsvm-3.18",
        ]
        if "cpc2" in str(pkg_root).lower():
            targets += [d for d in pkg_root.rglob("libsvm*") if d.is_dir()]
        seen = set()
        uniq = []
        for d in targets:
            if str(d) not in seen:
                seen.add(str(d))
                uniq.append(d)
        linked = []
        for d in uniq:
            for name in ("svm-scale", "svm-predict"):
                dst = d / name
                if dst.exists() or not svm.get(name):
                    continue
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    dst.symlink_to(svm[name])
                    linked.append(str(dst))
                except OSError as e:
                    self.log(f"  链接 {dst} 失败: {e}")
        if linked:
            self.log("  已为 CPC2 补齐 libsvm: " + ", ".join(linked))
        ok = any((d / "svm-scale").exists() and (d / "svm-predict").exists()
                 for d in uniq)
        if not ok:
            raise FileNotFoundError(
                "CPC2 缺少 svm-scale/svm-predict (libsvm)")

    @staticmethod
    def _parse_cpc2(path: Path) -> set:
        noncoding = set()
        if not path.exists():
            raise RuntimeError(f"CPC2 没产出结果: {path}")
        with open(path, encoding="utf-8", errors="ignore") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            try:
                label_i = next(i for i, c in enumerate(header)
                               if c.strip().lower() in ("label",
                                                        "coding_probability_label"))
            except StopIteration:
                label_i = len(header) - 1
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) <= label_i:
                    continue
                if cols[label_i].strip().lower() == "noncoding":
                    noncoding.add(cols[0].split()[0])
        return noncoding

    @staticmethod
    def _parse_plek(path: Path) -> set:
        noncoding = set()
        if not path.exists():
            raise RuntimeError(f"PLEK 没产出结果: {path}")
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 3:
                    continue
                label = cols[0].strip().lower()
                if label.startswith("non-coding") or label == "noncoding":
                    seqid = cols[2].lstrip(">").strip().split()[0]
                    noncoding.add(seqid)
        return noncoding


if __name__ == "__main__":
    LncrnaIdentifyRunner.main()
