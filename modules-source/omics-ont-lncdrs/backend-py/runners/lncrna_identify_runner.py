"""lncRNA identification runner for ONT DRS (CPC2 + PLEK + ORF filter).

Predicts lncRNAs by applying CPC2 and PLEK coding potential tools
and taking their noncoding intersection.

  1. gffread -w transcripts.fa -g <genome> <candidate_gtf>
  2. Length filtering: min_length..max_length
  3. CPC2: CPC2.py -i <fa> -o cpc2
  4. PLEK: PLEK -fasta <fa> -out plek -thread N
  5. ORF filter: getorf -minsize 300 -> remove transcripts with ORF >= 100 aa
  6. Intersection: CPC2 noncoding AND PLEK noncoding -> lncRNA
  7. Venn diagram: Venn summary of coding/noncoding calls

Parameters:
  candidate_gtf: str    - Candidate transcript GTF
  genome_fasta: str     - Reference genome FASTA
  min_length: int       - Minimum transcript length (default 200)
  max_length: int       - Maximum transcript length (default 20000)
  threads: int          - CPU threads for PLEK (default 8)

Outputs:
  candidate_transcripts.fa   - Length-filtered transcript sequences
  cpc2_result.txt            - CPC2 output
  plek_result.txt            - PLEK output
  orf_filtered.fa            - Transcripts after ORF filter (getorf -minsize 300)
  lncRNA_list.tsv            - lncRNA list (consensus)
  lncRNA.fa                  - lncRNA sequences
  lncrna_summary.json        - Summary statistics
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
        cand = p.get("candidate_gtf")
        genome = p.get("genome_fasta")
        min_len = int(p.get("min_length", 200))
        max_len = int(p.get("max_length", 20000))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(
                f"候选转录本 GTF 不存在: {cand}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) Extract transcript sequences
        self.update(pct=10, stage="gffread 抽转录本序列")
        raw_fa = out_dir / "all_transcripts.fa"
        self.run_command(["gffread", "-w", str(raw_fa), "-g", genome, str(cand)])
        seqs_all = parse_fasta(raw_fa) if raw_fa.exists() else {}
        if not seqs_all:
            raise RuntimeError("gffread 没抽出转录本序列")

        # 2) Length filtering
        seqs = {t: s for t, s in seqs_all.items()
                if min_len <= len(s) <= max_len}
        fa = out_dir / "candidate_transcripts.fa"
        write_fasta(fa, seqs, list(seqs.keys()))
        self.log(f"长度 {min_len}~{max_len}nt 的候选: {len(seqs)}/{len(seqs_all)}")
        if not seqs:
            raise RuntimeError("长度过滤后没有候选转录本")

        # 3) CPC2
        self.update(pct=30, stage="CPC2 编码潜能", indeterminate=True)
        self._ensure_cpc2_libsvm()
        cpc2_out = out_dir / "cpc2_result"
        self.run_command(["CPC2.py", "-i", str(fa), "-o", str(cpc2_out)],
                         indeterminate=True, heartbeat_stage="CPC2")
        cpc2_noncoding = self._parse_cpc2(Path(str(cpc2_out) + ".txt"))
        self.log(f"CPC2 非编码: {len(cpc2_noncoding)}")

        # 4) PLEK
        self.update(pct=55, stage="PLEK 编码潜能", indeterminate=True)
        plek_out = out_dir / "plek_result.txt"
        self.run_command(["PLEK", "-fasta", str(fa), "-out", str(plek_out),
                          "-thread", str(threads)],
                         indeterminate=True, heartbeat_stage="PLEK")
        plek_noncoding = self._parse_plek(plek_out)
        self.log(f"PLEK 非编码: {len(plek_noncoding)}")

        # 5) ORF filter: getorf -minsize 300 (100 aa)
        self.update(pct=72, stage="getorf ORF 过滤", indeterminate=True)
        orf_fa = out_dir / "orf_filtered.fa"
        # getorf outputs all ORFs; we keep IDs that had NO ORF >= 300 nt
        getorf_out = out_dir / "_getorf_output.fa"
        self.run_command([
            "getorf", "-sequence", str(fa), "-outseq", str(getorf_out),
            "-minsize", "300", "-find", "0", "-noreverse",
        ], indeterminate=True, heartbeat_stage="getorf")
        # Parse getorf output -> which transcripts have ORF >= 300
        ids_with_orf = set()
        if getorf_out.exists():
            with open(getorf_out, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if line.startswith(">"):
                        # getorf header: >seq_id_orf_number [...]
                        tid = line[1:].strip().split("_orf")[0].strip()
                        ids_with_orf.add(tid)
        # Transcripts WITHOUT any ORF >= 300 nt pass the filter
        orf_filtered_ids = sorted(set(seqs.keys()) - ids_with_orf)
        self.log(f"getorf 过滤: {len(ids_with_orf)} 条有 ORF>=300nt, "
                 f"{len(orf_filtered_ids)} 条通过")
        if not orf_filtered_ids:
            raise RuntimeError("ORF 过滤后没有候选转录本")
        # Write ORF-filtered FASTA
        write_fasta(orf_fa, seqs, orf_filtered_ids)

        # 6) Intersection (only from ORF-passing transcripts)
        self.update(pct=88, stage="取 CPC2 与 PLEK 非编码交集")
        cpc2_noncoding_or = cpc2_noncoding & set(orf_filtered_ids)
        plek_noncoding_or = plek_noncoding & set(orf_filtered_ids)
        lnc_ids = sorted(cpc2_noncoding_or & plek_noncoding_or)

        with open(out_dir / "lncRNA_list.tsv", "w", encoding="utf-8") as wf:
            wf.write("transcript_id\tlength\tcpc2\tplek\n")
            for tid in lnc_ids:
                wf.write(f"{tid}\t{len(seqs.get(tid, ''))}\tnoncoding\tnon-coding\n")
        write_fasta(out_dir / "lncRNA.fa", seqs, lnc_ids)

        summary = {
            "n_candidates": len(seqs),
            "cpc2_noncoding": len(cpc2_noncoding),
            "plek_noncoding": len(plek_noncoding),
            "n_with_orf_300": len(ids_with_orf),
            "n_after_orf_filter": len(orf_filtered_ids),
            "n_lncRNA": len(lnc_ids),
            "filters": {
                "min_length": min_len,
                "max_length": max_len,
                "orf_minsize": 300,
                "venn": {
                    "cpc2_only": len(cpc2_noncoding_or - plek_noncoding_or),
                    "plek_only": len(plek_noncoding_or - cpc2_noncoding_or),
                    "both_noncoding": len(lnc_ids),
                    "total_or_filtered": len(orf_filtered_ids),
                },
                "rule": "ORF filter (getorf) -> CPC2 and PLEK both noncoding",
            },
        }
        (out_dir / "lncrna_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== lncRNA(CPC2 + PLEK 交集): {len(lnc_ids)} 条 → {out_dir} ===")

    def _ensure_cpc2_libsvm(self):
        """Ensure CPC2 can find svm-scale/svm-predict."""
        cpc2 = shutil.which("CPC2.py")
        if not cpc2:
            raise FileNotFoundError("找不到 CPC2.py。请重建 conda 环境。")
        svm = {n: shutil.which(n) for n in ("svm-scale", "svm-predict")}
        cpc2_real = Path(cpc2).resolve()
        pkg_root = cpc2_real.parent.parent
        targets = [
            pkg_root / "libsvm" / "libsvm-3.18",
            cpc2_real.parent / "libsvm" / "libsvm-3.18",
        ]
        if "cpc2" in str(pkg_root).lower():
            targets += [d for d in pkg_root.rglob("libsvm*") if d.is_dir()]
        seen, uniq = set(), []
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
                except OSError:
                    pass
        if linked:
            self.log("  已为 CPC2 补齐 libsvm: " + ", ".join(linked))
        ok = any((d / "svm-scale").exists() and (d / "svm-predict").exists()
                 for d in uniq)
        if not ok:
            raise FileNotFoundError(
                "CPC2 缺少 svm-scale/svm-predict。请重建环境。")

    @staticmethod
    def _parse_cpc2(path: Path) -> set:
        noncoding = set()
        if not path.exists():
            raise RuntimeError(f"CPC2 没产出结果: {path}")
        with open(path, encoding="utf-8", errors="ignore") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            label_i = next(
                (i for i, c in enumerate(header)
                 if c.strip().lower() in ("label", "coding_probability_label")),
                len(header) - 1,
            )
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
