"""lncRNA prediction runner for Tail Iso-seq (CNCI + CPC2 + PLEK).

Parameters:
  candidate_gtf: str    - Candidate transcript GTF
  genome_fasta: str     - Reference genome FASTA
  min_length: int       - Minimum transcript length (default: 200)
  consensus_mode: str   - 'strict' (all 3) or 'relaxed' (>=2, default)
  threads: int          - CPU threads (default: 8)

Outputs (to output_dir/):
  candidate_transcripts.fa    - Candidate sequences
  cpc2_result.txt             - CPC2 output
  cpc2_noncoding.txt          - CPC2 noncoding IDs
  plek_result.txt             - PLEK output
  plek_noncoding.txt          - PLEK noncoding IDs
  cnci_result/                - CNCI output
  cnci_noncoding.txt          - CNCI noncoding IDs
  lncRNA_list.tsv             - Consensus lncRNA list
  lncRNA.fa                   - lncRNA sequences
  lncrna_summary.json         - Summary
"""
import json
import shutil
from pathlib import Path

from runners.base import BaseRunner


def parse_fasta(path):
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


def write_fasta(path, seqs, ids):
    with open(path, "w", encoding="utf-8") as wf:
        for tid in ids:
            seq = seqs.get(tid, "")
            if not seq:
                continue
            wf.write(f">{tid}\n")
            for i in range(0, len(seq), 60):
                wf.write(seq[i:i + 60] + "\n")


class LncrnaPredictRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        cand = p.get("candidate_gtf")
        genome = p.get("genome_fasta")
        min_len = int(p.get("min_length", 200))
        consensus_mode = p.get("consensus_mode", "relaxed")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(f"候选 GTF 不存在: {cand}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) Extract sequences
        self.update(pct=5, stage="gffread 抽序列")
        raw_fa = out_dir / "all_transcripts.fa"
        self.run_command(["gffread", "-w", str(raw_fa), "-g", genome, str(cand)])
        seqs_all = parse_fasta(raw_fa) if raw_fa.exists() else {}
        if not seqs_all:
            raise RuntimeError("gffread 没抽出序列")

        # 2) Length filter
        seqs = {t: s for t, s in seqs_all.items() if len(s) >= min_len}
        fa = out_dir / "candidate_transcripts.fa"
        write_fasta(fa, seqs, list(seqs.keys()))
        self.log(f"长度 >= {min_len} nt: {len(seqs)}/{len(seqs_all)}")
        if not seqs:
            raise RuntimeError("无候选序列")

        # 3) CPC2
        self.update(pct=20, stage="CPC2", indeterminate=True)
        self._ensure_cpc2()
        cpc2_out = out_dir / "cpc2_result"
        self.run_command(["CPC2.py", "-i", str(fa), "-o", str(cpc2_out)],
                         indeterminate=True, heartbeat_stage="CPC2")
        cpc2_nc = self._parse_cpc2(Path(str(cpc2_out) + ".txt"))
        (out_dir / "cpc2_noncoding.txt").write_text(
            "\n".join(sorted(cpc2_nc)) + "\n", encoding="utf-8")

        # 4) PLEK
        self.update(pct=40, stage="PLEK", indeterminate=True)
        plek_out = out_dir / "plek_result.txt"
        self.run_command(["PLEK", "-fasta", str(fa), "-out", str(plek_out),
                          "-thread", str(threads)],
                         indeterminate=True, heartbeat_stage="PLEK")
        plek_nc = self._parse_plek(plek_out)
        (out_dir / "plek_noncoding.txt").write_text(
            "\n".join(sorted(plek_nc)) + "\n", encoding="utf-8")

        # 5) CNCI
        self.update(pct=60, stage="CNCI", indeterminate=True)
        cnci_out = out_dir / "cnci_result"
        cnci_out.mkdir(exist_ok=True)
        if shutil.which("CNCI.py"):
            self.run_command([
                "CNCI.py", "-i", str(fa), "-o", str(cnci_out),
                "-m", "ve", "-p", str(threads),
            ], indeterminate=True, heartbeat_stage="CNCI")
        cnci_nc = self._parse_cnci(cnci_out)
        (out_dir / "cnci_noncoding.txt").write_text(
            "\n".join(sorted(cnci_nc)) + "\n", encoding="utf-8")

        # 6) Venn consensus
        self.update(pct=80, stage="取交集")
        all_sets = {}
        if cnci_nc:
            all_sets["CNCI"] = cnci_nc
        if cpc2_nc:
            all_sets["CPC2"] = cpc2_nc
        if plek_nc:
            all_sets["PLEK"] = plek_nc

        if not all_sets:
            raise RuntimeError("所有工具都失败")

        if consensus_mode == "strict":
            lnc_ids = set.intersection(*all_sets.values()) if all_sets else set()
        else:
            from collections import Counter
            vote = Counter()
            for tid in seqs:
                n_votes = sum(1 for s in all_sets.values() if tid in s)
                vote[tid] = n_votes
            lnc_ids = {tid for tid, v in vote.items()
                       if v >= max(2, len(all_sets) - 1)}

        lnc_ids = sorted(lnc_ids)

        with open(out_dir / "lncRNA_list.tsv", "w", encoding="utf-8") as wf:
            wf.write("transcript_id\tlength")
            for nm in sorted(all_sets.keys()):
                wf.write(f"\t{nm}")
            wf.write("\n")
            for tid in lnc_ids:
                wf.write(f"{tid}\t{len(seqs.get(tid, ''))}")
                for nm in sorted(all_sets.keys()):
                    wf.write(f"\t{'noncoding' if tid in all_sets[nm] else 'coding'}")
                wf.write("\n")
        write_fasta(out_dir / "lncRNA.fa", seqs, lnc_ids)

        summary = {
            "n_candidates": len(seqs),
            "tools_used": sorted(all_sets.keys()),
            "tool_counts": {nm: len(v) for nm, v in all_sets.items()},
            "n_lncRNA_consensus": len(lnc_ids),
            "consensus_mode": consensus_mode,
        }
        (out_dir / "lncrna_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== lncRNA 预测: {len(lnc_ids)} 条 → {out_dir} ===")

    def _ensure_cpc2(self):
        cpc2 = shutil.which("CPC2.py")
        if not cpc2:
            raise FileNotFoundError("找不到 CPC2.py")
        svm = {n: shutil.which(n) for n in ("svm-scale", "svm-predict")}
        cpc2_real = Path(cpc2).resolve()
        pkg_root = cpc2_real.parent.parent
        targets = [
            pkg_root / "libsvm" / "libsvm-3.18",
            cpc2_real.parent / "libsvm" / "libsvm-3.18",
        ]
        if "cpc2" in str(pkg_root).lower():
            targets += [d for d in pkg_root.rglob("libsvm*") if d.is_dir()]
        for d in set(str(t) for t in targets):
            d_path = Path(d)
            for name in ("svm-scale", "svm-predict"):
                dst = d_path / name
                if dst.exists() or not svm.get(name):
                    continue
                try:
                    d_path.mkdir(parents=True, exist_ok=True)
                    dst.symlink_to(svm[name])
                except OSError:
                    pass

    @staticmethod
    def _parse_cpc2(path):
        nc = set()
        if not path.exists():
            return nc
        with open(path, encoding="utf-8", errors="ignore") as fh:
            header = fh.readline().rstrip("\n").split("\t")
            li = next((i for i, c in enumerate(header)
                       if c.strip().lower() in ("label", "coding_probability_label")),
                      len(header) - 1)
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) > li and cols[li].strip().lower() == "noncoding":
                    nc.add(cols[0].split()[0])
        return nc

    @staticmethod
    def _parse_plek(path):
        nc = set()
        if not path.exists():
            return nc
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) >= 3:
                    label = cols[0].strip().lower()
                    if label.startswith("non-coding") or label == "noncoding":
                        nc.add(cols[2].lstrip(">").strip().split()[0])
        return nc

    @staticmethod
    def _parse_cnci(cnci_dir):
        nc = set()
        if not cnci_dir.exists():
            return nc
        result_files = list(cnci_dir.glob("*.txt")) + \
                       list(cnci_dir.glob("*result*"))
        if not result_files:
            subdirs = [d for d in cnci_dir.iterdir() if d.is_dir()]
            for sd in subdirs:
                result_files = list(sd.glob("*.txt"))
                if result_files:
                    break
        for rf in result_files:
            with open(rf, encoding="utf-8", errors="ignore") as fh:
                header = fh.readline().rstrip("\n").split("\t")
                li = next((i for i, c in enumerate(header)
                           if "label" in c.lower()), len(header) - 1)
                for line in fh:
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) > li and cols[li].strip().lower() == "noncoding":
                        nc.add(cols[0].split()[0])
        return nc


if __name__ == "__main__":
    LncrnaPredictRunner.main()
