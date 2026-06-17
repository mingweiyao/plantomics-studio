"""新 lncRNA 预测 runner(CPC2 + PLEK,对应报告 5.5.1 / 6.1.9)。

对"新转录本"步骤(StringTie)产出的 merged.gtf,抽转录本序列后用两种编码潜能
工具各判一次,取两者一致认为"非编码"的转录本作为 lncRNA 候选:
  1. gffread -w transcripts.fa -g <genome> <merged.gtf>
  2. 长度过滤:序列长度 >= min_length(默认 200 nt,lncRNA 定义)
  3. CPC2:CPC2.py -i <fa> -o cpc2  -> 每条转录本 coding/noncoding 标签
  4. PLEK:PLEK -fasta <fa> -out plek -thread N -> 每条转录本 Coding/Non-coding
  5. 取 CPC2 与 PLEK 都判为非编码的交集 -> lncRNA

参数:
  candidate_gtf: str   - 候选转录本 GTF(用"新转录本"步骤的 merged.gtf)
  genome_fasta:  str   - 基因组 FASTA(抽序列用,必填)
  min_length:    int   - lncRNA 最短长度,默认 200
  threads:       int   - PLEK 线程数(CPC2 单线程)

产出(到 output_subdir):
  candidate_transcripts.fa   - 抽出并长度过滤后的转录本序列
  cpc2_result.txt            - CPC2 原始输出
  plek_result.txt            - PLEK 原始输出
  lncRNA_list.tsv            - 鉴定出的 lncRNA(transcript_id / length / cpc2 / plek)
  lncRNA.fa                  - lncRNA 序列子集
  lncrna_summary.json        - 候选数 + 各工具非编码数 + 交集数
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


class LncrnaRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        cand = p.get("candidate_gtf")
        genome = p.get("genome_fasta")
        min_len = int(p.get("min_length", 200))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(
                f"候选转录本 GTF 不存在(先跑新转录本步骤拿 merged.gtf): {cand}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) 抽转录本序列
        self.update(pct=10, stage="gffread 抽转录本序列")
        raw_fa = out_dir / "all_transcripts.fa"
        self.run_command(["gffread", "-w", str(raw_fa), "-g", genome, str(cand)])
        seqs_all = parse_fasta(raw_fa) if raw_fa.exists() else {}
        if not seqs_all:
            raise RuntimeError("gffread 没抽出转录本序列")

        # 2) 长度过滤
        seqs = {t: s for t, s in seqs_all.items() if len(s) >= min_len}
        fa = out_dir / "candidate_transcripts.fa"
        write_fasta(fa, seqs, list(seqs.keys()))
        self.log(f"长度 >= {min_len}nt 的候选转录本: {len(seqs)}/{len(seqs_all)}")
        if not seqs:
            raise RuntimeError("长度过滤后没有候选转录本")

        # 3) CPC2
        self.update(pct=35, stage="CPC2 编码潜能", indeterminate=True)
        self._ensure_cpc2_libsvm()
        cpc2_out = out_dir / "cpc2_result"   # CPC2 写 cpc2_result.txt
        self.run_command(["CPC2.py", "-i", str(fa), "-o", str(cpc2_out)],
                         indeterminate=True, heartbeat_stage="CPC2")
        cpc2_noncoding = self._parse_cpc2(Path(str(cpc2_out) + ".txt"))
        self.log(f"CPC2 判为非编码: {len(cpc2_noncoding)}")

        # 4) PLEK
        self.update(pct=65, stage="PLEK 编码潜能", indeterminate=True)
        plek_out = out_dir / "plek_result.txt"
        self.run_command(["PLEK", "-fasta", str(fa), "-out", str(plek_out),
                          "-thread", str(threads)],
                         indeterminate=True, heartbeat_stage="PLEK")
        plek_noncoding = self._parse_plek(plek_out)
        self.log(f"PLEK 判为非编码: {len(plek_noncoding)}")

        # 5) 交集
        self.update(pct=88, stage="取 CPC2 与 PLEK 非编码交集")
        lnc_ids = sorted(cpc2_noncoding & plek_noncoding)
        with open(out_dir / "lncRNA_list.tsv", "w", encoding="utf-8") as wf:
            wf.write("transcript_id\tlength\tcpc2\tplek\n")
            for tid in lnc_ids:
                wf.write(f"{tid}\t{len(seqs.get(tid, ''))}\tnoncoding\tnon-coding\n")
        write_fasta(out_dir / "lncRNA.fa", seqs, lnc_ids)

        (out_dir / "lncrna_summary.json").write_text(
            json.dumps({
                "n_candidates": len(seqs),
                "cpc2_noncoding": len(cpc2_noncoding),
                "plek_noncoding": len(plek_noncoding),
                "n_lncRNA": len(lnc_ids),
                "filters": {"min_length": min_len,
                            "rule": "CPC2 and PLEK both noncoding"},
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== lncRNA(CPC2 与 PLEK 交集): {len(lnc_ids)} 条 → {out_dir} ===")

    def _ensure_cpc2_libsvm(self):
        """修复 bioconda CPC2 找不到 svm-scale / svm-predict 的问题。

        CPC2.py 会在自己安装目录下的 libsvm 子目录里找 svm-scale / svm-predict,
        但 bioconda 的 cpc2 包常常没带可用的二进制(报 'No excutable svm-scale on CPC2 path!')。
        这里把 conda 装的 libsvm(env.yaml 已加 libsvm,提供 env/bin/svm-scale 等)软链到
        CPC2 期望的目录补齐它。env 里没 libsvm 时给出清晰可执行的提示。

        说明:CPC2 的目录布局随版本不同,这个修复是尽力而为,需在实际环境里验证。
        """
        cpc2 = shutil.which("CPC2.py")
        if not cpc2:
            raise FileNotFoundError(
                "找不到 CPC2.py。请重建模块 conda 环境(bash scripts/build-deb.sh,"
                "不要加 --skip-env、也不要复用旧的 env 包)以安装 cpc2。")
        svm = {n: shutil.which(n) for n in ("svm-scale", "svm-predict")}
        cpc2_real = Path(cpc2).resolve()           # 跟随软链到真实脚本位置
        pkg_root = cpc2_real.parent.parent          # 通常是 .../share/CPC2-beta/
        # 候选 libsvm 目录:常见布局 +(看起来像 CPC2 包目录时)有界搜索
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
                except OSError as e:
                    self.log(f"  链接 {dst} 失败: {e}")
        if linked:
            self.log("  已为 CPC2 补齐 libsvm: " + ", ".join(linked))
        ok = any((d / "svm-scale").exists() and (d / "svm-predict").exists()
                 for d in uniq)
        if not ok:
            raise FileNotFoundError(
                "CPC2 仍缺少 svm-scale / svm-predict(libsvm)。env.yaml 已添加 libsvm,"
                "请重建模块环境(bash scripts/build-deb.sh;不要 --skip-env、不要复用旧 env 包)"
                f"后再试。(CPC2.py 实际位置: {cpc2_real})")

    @staticmethod
    def _parse_cpc2(path: Path) -> set:
        """CPC2 输出表:含 label 列(coding/noncoding)。取 noncoding 的 ID。"""
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
                label_i = len(header) - 1  # 末列通常是 label
            for line in fh:
                cols = line.rstrip("\n").split("\t")
                if len(cols) <= label_i:
                    continue
                if cols[label_i].strip().lower() == "noncoding":
                    noncoding.add(cols[0].split()[0])
        return noncoding

    @staticmethod
    def _parse_plek(path: Path) -> set:
        """PLEK 输出每行:'Coding'/'Non-coding'<TAB>score<TAB>>id ...。取 Non-coding。"""
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
    LncrnaRunner.main()
