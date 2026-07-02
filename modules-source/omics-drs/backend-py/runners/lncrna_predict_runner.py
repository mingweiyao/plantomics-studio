"""lncRNA prediction runner using CNCI + CPC2 + PLEK with consensus Venn.

Predicts long non-coding RNAs from DRS transcript assemblies by applying
three independent coding potential tools and taking their consensus:

  1. gffread -w transcripts.fa -g <genome> <candidate_gtf>
  2. Length filtering: >= min_length
  3. CNCI: CNCI.py -i <fa> -o cnci_output -m ve -p <threads>
  4. CPC2: CPC2.py -i <fa> -o cpc2_output
  5. PLEK: PLEK -fasta <fa> -out plek -thread <threads>
  6. Venn intersection: transcripts noncoding in ALL three tools -> lncRNA
  7. Consensus filtering: >= 2 tools or all 3

Parameters:
  candidate_gtf: str    - Candidate transcript GTF (from Flair collapse)
  genome_fasta: str     - Reference genome FASTA
  min_length: int       - Minimum transcript length (default 200)
  consensus_mode: str   - 'strict' (all 3) or 'relaxed' (>=2, default)
  threads: int          - CPU threads for PLEK and CNCI (default 8)

Outputs (to output_dir/):
  candidate_transcripts.fa    - Length-filtered transcript sequences
  cnci_result/                - CNCI output directory
  cnci_noncoding.txt          - CNCI noncoding IDs
  cpc2_result.txt             - CPC2 output
  cpc2_noncoding.txt          - CPC2 noncoding IDs
  plek_result.txt             - PLEK output
  plek_noncoding.txt          - PLEK noncoding IDs
  lncRNA_list.tsv             - Consensus lncRNA list (all 3)
  lncRNA.fa                   - lncRNA sequences
  lncrna_summary.json         - Summary statistics
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


class LncrnaPredictRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        cand = p.get("candidate_gtf")
        genome = p.get("genome_fasta")
        min_len = int(p.get("min_length", 200))
        consensus_mode = p.get("consensus_mode", "relaxed")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(
                f"候选转录本 GTF 不存在: {cand}")
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

        # 2) Length filtering
        seqs = {t: s for t, s in seqs_all.items() if len(s) >= min_len}
        fa = out_dir / "candidate_transcripts.fa"
        write_fasta(fa, seqs, list(seqs.keys()))
        self.log(f"长度 >= {min_len} nt 的候选: {len(seqs)}/{len(seqs_all)}")
        if not seqs:
            raise RuntimeError("长度过滤后没有候选转录本")

        # 3) CPC2
        self.update(pct=20, stage="CPC2 编码潜能", indeterminate=True)
        self._ensure_cpc2_libsvm()
        cpc2_out = out_dir / "cpc2_result"
        self.run_command(["CPC2.py", "-i", str(fa), "-o", str(cpc2_out)],
                         indeterminate=True, heartbeat_stage="CPC2")
        cpc2_noncoding = self._parse_cpc2(Path(str(cpc2_out) + ".txt"))
        (out_dir / "cpc2_noncoding.txt").write_text(
            "\n".join(sorted(cpc2_noncoding)) + "\n", encoding="utf-8")
        self.log(f"CPC2 非编码: {len(cpc2_noncoding)}")

        # 4) PLEK
        self.update(pct=40, stage="PLEK 编码潜能", indeterminate=True)
        plek_out = out_dir / "plek_result.txt"
        self.run_command(["PLEK", "-fasta", str(fa), "-out", str(plek_out),
                          "-thread", str(threads)],
                         indeterminate=True, heartbeat_stage="PLEK")
        plek_noncoding = self._parse_plek(plek_out)
        (out_dir / "plek_noncoding.txt").write_text(
            "\n".join(sorted(plek_noncoding)) + "\n", encoding="utf-8")
        self.log(f"PLEK 非编码: {len(plek_noncoding)}")

        # 5) CNCI
        self.update(pct=60, stage="CNCI 编码潜能", indeterminate=True)
        cnci_out = out_dir / "cnci_result"
        cnci_out.mkdir(exist_ok=True)
        cnci_cmd = shutil.which("CNCI.py")
        if cnci_cmd:
            self.run_command([
                "CNCI.py", "-i", str(fa),
                "-o", str(cnci_out),
                "-m", "ve",
                "-p", str(threads),
            ], indeterminate=True, heartbeat_stage="CNCI")
        else:
            self.log("CNCI.py 不可用,跳过 CNCI 分析")
        cnci_noncoding = self._parse_cnci(cnci_out)
        (out_dir / "cnci_noncoding.txt").write_text(
            "\n".join(sorted(cnci_noncoding)) + "\n", encoding="utf-8")
        self.log(f"CNCI 非编码: {len(cnci_noncoding)}")

        # 6) Venn consensus
        self.update(pct=80, stage="取交集(CNCI ∩ CPC2 ∩ PLEK)")

        all_sets = {}
        if cnci_noncoding:
            all_sets["CNCI"] = cnci_noncoding
        if cpc2_noncoding:
            all_sets["CPC2"] = cpc2_noncoding
        if plek_noncoding:
            all_sets["PLEK"] = plek_noncoding

        if not all_sets:
            raise RuntimeError("所有工具都失败,无法进行 lncRNA 预测")

        # Calculate intersections
        if consensus_mode == "strict":
            # All tools must agree
            lnc_ids = set.intersection(*all_sets.values()) if all_sets else set()
        else:
            # relaxed: at least 2 tools agree
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
            for name in sorted(all_sets.keys()):
                wf.write(f"\t{name}")
            wf.write("\n")
            for tid in lnc_ids:
                wf.write(f"{tid}\t{len(seqs.get(tid, ''))}")
                for name in sorted(all_sets.keys()):
                    wf.write(f"\t{'noncoding' if tid in all_sets[name] else 'coding'}")
                wf.write("\n")
        write_fasta(out_dir / "lncRNA.fa", seqs, lnc_ids)

        # Tool-wise statistics
        tool_stats = {}
        for name, ids in all_sets.items():
            tool_stats[name] = {
                "noncoding": len(ids),
                "coding": len(seqs) - len(ids),
            }

        summary = {
            "n_candidates": len(seqs),
            "consensus_mode": consensus_mode,
            "tools_used": sorted(all_sets.keys()),
            "tool_stats": tool_stats,
            "venn_counts": {
                name: len(ids) for name, ids in all_sets.items()
            },
            "n_lncRNA_consensus": len(lnc_ids),
            "percent_lncRNA": round(
                len(lnc_ids) / max(len(seqs), 1) * 100, 2),
            "filters": {
                "min_length": min_len,
                "rule": f"strict (all {len(all_sets)})" if consensus_mode == "strict"
                        else f"relaxed (>= {max(2, len(all_sets) - 1)} of {len(all_sets)})",
            },
        }
        (out_dir / "lncrna_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== lncRNA 预测(CNCI+CPC2+PLEK {consensus_mode}交集): "
                 f"{len(lnc_ids)} 条 → {out_dir} ===")

    def _ensure_cpc2_libsvm(self):
        """Ensure CPC2 can find svm-scale/svm-predict."""
        cpc2 = shutil.which("CPC2.py")
        if not cpc2:
            raise FileNotFoundError(
                "找不到 CPC2.py。请重建 conda 环境。")
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
                "CPC2 缺少 svm-scale/svm-predict。env.yaml 已添加 libsvm,"
                "请重建环境后再试。")

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

    @staticmethod
    def _parse_cnci(cnci_dir: Path) -> set:
        """Parse CNCI output: looks for result files."""
        noncoding = set()
        if not cnci_dir.exists():
            return noncoding

        # CNCI produces index.html and result files with .txt
        result_files = list(cnci_dir.glob("*.txt")) + \
                       list(cnci_dir.glob("*result*"))
        if not result_files:
            # Try parent: CNCI may output to a differently-named dir
            parent_results = list(cnci_dir.parent.glob("*result*"))
            if parent_results:
                result_files = parent_results

        if not result_files:
            # CNCI may create a subdirectory with format CNCI_result_<timestamp>
            subdirs = [d for d in cnci_dir.iterdir() if d.is_dir()]
            for sd in subdirs:
                result_files = list(sd.glob("*.txt"))
                if result_files:
                    break

        for result_file in result_files:
            with open(result_file, encoding="utf-8", errors="ignore") as fh:
                header = fh.readline().rstrip("\n").split("\t")
                # Find label column (usually "coding_label" or "label")
                label_i = next(
                    (i for i, c in enumerate(header)
                     if "label" in c.lower() or "coding" in c.lower()),
                    len(header) - 1,
                )
                id_col = 0  # First column is usually sequence ID
                for line in fh:
                    cols = line.rstrip("\n").split("\t")
                    if len(cols) <= label_i:
                        continue
                    label = cols[label_i].strip().lower()
                    if label == "noncoding":
                        noncoding.add(cols[id_col].split()[0])

        return noncoding


if __name__ == "__main__":
    LncrnaPredictRunner.main()
