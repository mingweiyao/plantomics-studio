"""新 lncRNA 预测 runner(不依赖 FEELnc)。

FEELnc 锁死在远古依赖(r-base 3.4 / 老 perl-bioperl / t_coffee),无法与现代 conda 环境
(python 3.11 + r-base 4.4)共存,会让整个 env 解不开。这里改用环境里已有的
gffcompare + gffread + 纯 Python ORF 编码潜能判断,实现等价的 lncRNA 识别:

  1. gffcompare 把候选转录本与参考注释比对 → class code
     (u=基因间/lincRNA, x=反义, i=内含子内 —— 这三类是 lncRNA 的标准结构分类)
  2. 长度过滤:外显子总长 ≥ min_length(默认 200 nt,lncRNA 定义)
  3. 编码潜能:gffread 抽转录本序列 → 算最长 ORF;最长 ORF < max_orf_aa(默认 100 aa)
     视为低编码潜能 → lncRNA 候选
  4. 按 class code 分类计数(intergenic / antisense / intronic)

参数:
  candidate_gtf: str   - 候选转录本 GTF(用"新转录本"步骤的 merged.gtf)
  gtf:           str   - 参考注释 GTF(已知 mRNA,必填)
  genome_fasta:  str   - 基因组 FASTA(必填,抽序列算 ORF 用)
  threads:       int   - 默认 8(本步骤基本单线程,保留参数)
  min_length:    int   - lncRNA 最短长度,默认 200
  max_orf_aa:    int   - 最长 ORF 上限(超过视为有编码潜能),默认 100

产出(到 output_subdir):
  lncRNA.gtf            - 鉴定出的 lncRNA(GTF)
  lncRNA_list.tsv       - 每条 lncRNA:transcript_id / 类型 / 长度 / 最长ORF(aa)
  lncrna_summary.json   - lncRNA 数量 + 各类型计数
"""
import json
import re
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner

# gffcompare class code → lncRNA 结构类型
CLASS_TO_TYPE = {"u": "intergenic", "x": "antisense", "i": "intronic"}
STOPS = {"TAA", "TAG", "TGA"}


def longest_orf_aa(seq: str) -> int:
    """3 个正向阅读框里最长 ORF(ATG→终止子),返回氨基酸数。"""
    seq = seq.upper().replace("U", "T")
    n = len(seq)
    best = 0
    for frame in range(3):
        i = frame
        while i <= n - 3:
            if seq[i:i + 3] == "ATG":
                j = i + 3
                while j <= n - 3:
                    if seq[j:j + 3] in STOPS:
                        aa = (j - i) // 3
                        if aa > best:
                            best = aa
                        break
                    j += 3
            i += 3
    return best


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


class LncrnaRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        cand = p.get("candidate_gtf")
        ref_gtf = p.get("gtf")
        genome = p.get("genome_fasta")
        min_len = int(p.get("min_length", 200))
        max_orf = int(p.get("max_orf_aa", 100))

        if not cand or not Path(cand).exists():
            raise FileNotFoundError(f"候选转录本 GTF 不存在(先跑新转录本步骤拿 merged.gtf): {cand}")
        if not ref_gtf or not Path(ref_gtf).exists():
            raise FileNotFoundError(f"参考 GTF 不存在: {ref_gtf}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1) gffcompare 拿 class code
        self.update(pct=15, stage="gffcompare 比对参考")
        self.run_command(["gffcompare", "-r", ref_gtf, "-o", str(out_dir / "gffcmp"), str(cand)])
        annotated = out_dir / "gffcmp.annotated.gtf"
        if not annotated.exists():
            raise RuntimeError("gffcompare 未产出 annotated.gtf")

        # 2) 解析:每条转录本的 class code、外显子总长、原始行
        self.update(pct=40, stage="解析候选 + 长度过滤")
        tx_class: dict = {}
        tx_len: dict = {}
        tx_lines: dict = {}
        with open(annotated, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if line.startswith("#") or "\t" not in line:
                    continue
                cols = line.split("\t")
                if len(cols) < 9:
                    continue
                tid_m = re.search(r'transcript_id "([^"]+)"', line)
                if not tid_m:
                    continue
                tid = tid_m.group(1)
                tx_lines.setdefault(tid, []).append(line)
                if cols[2] == "transcript":
                    cc = re.search(r'class_code "([^"]+)"', line)
                    tx_class[tid] = cc.group(1) if cc else "="
                elif cols[2] == "exon":
                    try:
                        tx_len[tid] = tx_len.get(tid, 0) + (int(cols[4]) - int(cols[3]) + 1)
                    except ValueError:
                        pass

        # 结构上是 lncRNA 候选(u/x/i)+ 长度达标
        struct_cand = [t for t, c in tx_class.items()
                       if c in CLASS_TO_TYPE and tx_len.get(t, 0) >= min_len]
        self.log(f"结构候选(u/x/i 且 ≥{min_len}nt): {len(struct_cand)}")

        # 3) gffread 抽序列 → ORF 编码潜能
        self.update(pct=60, stage="gffread 抽序列 + ORF 编码潜能", indeterminate=True)
        fa = out_dir / "candidate_transcripts.fa"
        self.run_command(["gffread", "-w", str(fa), "-g", genome, str(cand)])
        seqs = parse_fasta(fa) if fa.exists() else {}

        # 4) 过滤 + 分类
        self.update(pct=85, stage="编码潜能过滤 + 分类")
        lnc_rows = []
        class_counts = Counter()
        for tid in struct_cand:
            seq = seqs.get(tid)
            if not seq:
                continue
            orf = longest_orf_aa(seq)
            if orf < max_orf:           # 低编码潜能 → lncRNA
                ltype = CLASS_TO_TYPE[tx_class[tid]]
                lnc_rows.append((tid, ltype, len(seq), orf))
                class_counts[ltype] += 1

        lnc_ids = {r[0] for r in lnc_rows}
        with open(out_dir / "lncRNA.gtf", "w", encoding="utf-8") as wf:
            for tid in lnc_ids:
                wf.writelines(tx_lines.get(tid, []))
        with open(out_dir / "lncRNA_list.tsv", "w", encoding="utf-8") as wf:
            wf.write("transcript_id\ttype\tlength\tlongest_orf_aa\n")
            for tid, ltype, ln, orf in sorted(lnc_rows):
                wf.write(f"{tid}\t{ltype}\t{ln}\t{orf}\n")

        (out_dir / "lncrna_summary.json").write_text(
            json.dumps({"n_lncRNA": len(lnc_rows), "class_counts": dict(class_counts),
                        "filters": {"min_length": min_len, "max_orf_aa": max_orf}},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== 候选 lncRNA: {len(lnc_rows)} 条, 分类: {dict(class_counts)} ===")


if __name__ == "__main__":
    LncrnaRunner.main()
