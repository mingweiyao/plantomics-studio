"""新基因/新转录本发现 runner（StringTie + gffcompare）。

对 STAR 比对的 BAM 做参考引导的转录本组装，合并后用 gffcompare 与参考注释比对，
鉴定新转录本/新基因。对应商业报告"新基因和新转录本分析"。

参数:
  bam_files:    list[str]   - STAR 输出 BAM
  sample_names: list[str]   - 可选，默认从 BAM 名推
  gtf:          str         - 参考注释 GTF(必填)
  strand:       int         - 0 无链特异 / 1 fr / 2 rf，默认 0
  threads:      int         - 默认 8

产出(到 output_subdir):
  per_sample/<sample>.gtf   - 每样本组装
  merged.gtf                - 合并后的转录本(含新转录本)
  gffcmp.*                  - gffcompare 结果(.tmap/.stats 等)
  novel_transcripts.tsv     - 被判为新的转录本(class code 非 '=')
  new_transcripts_summary.json
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class StringtieRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bams = p.get("bam_files") or p.get("bams") or []
        sample_names = p.get("sample_names", [])
        gtf = p.get("gtf")
        strand = int(p.get("strand", 0))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bams:
            raise ValueError("未提供 BAM 列表(bam_files)")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        per_sample = out_dir / "per_sample"
        per_sample.mkdir(exist_ok=True)

        if not sample_names or len(sample_names) != len(bams):
            sample_names = [Path(b).stem.split(".")[0] for b in bams]

        strand_flag = {1: ["--fr"], 2: ["--rf"]}.get(strand, [])

        # ── 每样本组装 ──
        gtf_list = []
        n = len(bams)
        for i, (bam, name) in enumerate(zip(bams, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: BAM 不存在 {bam}")
                continue
            self.update(pct=int(5 + 55 * i / max(n, 1)),
                        stage=f"StringTie 组装 ({i + 1}/{n})", detail=name)
            sgtf = per_sample / f"{name}.gtf"
            cmd = ["stringtie", str(bam), "-p", str(threads), "-G", gtf,
                   "-o", str(sgtf), "-l", name] + strand_flag
            self.log("$ " + " ".join(cmd))
            self.run_command(cmd)
            if sgtf.exists():
                gtf_list.append(str(sgtf))

        if not gtf_list:
            raise RuntimeError("没有任何样本组装成功")

        # ── 合并 ──
        self.update(pct=70, stage="StringTie --merge 合并转录本")
        gtf_list_file = out_dir / "gtf_list.txt"
        gtf_list_file.write_text("\n".join(gtf_list) + "\n", encoding="utf-8")
        merged = out_dir / "merged.gtf"
        cmd = ["stringtie", "--merge", "-p", str(threads), "-G", gtf,
               "-o", str(merged), str(gtf_list_file)]
        self.log("$ " + " ".join(cmd))
        self.run_command(cmd)

        # ── gffcompare 与参考比对，标记新转录本 ──
        self.update(pct=85, stage="gffcompare 鉴定新转录本")
        prefix = out_dir / "gffcmp"
        cmd = ["gffcompare", "-r", gtf, "-o", str(prefix), str(merged)]
        self.log("$ " + " ".join(cmd))
        self.run_command(cmd)

        # 解析 tmap：class code '=' 是已知，其余视为新（u=基因间, i=内含子, x=反义, j=新异构体...）
        tmap = Path(str(prefix) + ".merged.gtf.tmap")
        if not tmap.exists():
            cands = list(out_dir.glob("gffcmp*.tmap"))
            tmap = cands[0] if cands else None
        novel_rows, code_counts = [], Counter()
        novel_genes = set()
        if tmap and tmap.exists():
            lines = tmap.read_text(encoding="utf-8", errors="ignore").splitlines()
            header = lines[0].split("\t") if lines else []
            ci = {c: i for i, c in enumerate(header)}
            for ln in lines[1:]:
                f = ln.split("\t")
                if len(f) < len(header):
                    continue
                code = f[ci.get("class_code", 2)] if "class_code" in ci else f[2]
                code_counts[code] += 1
                if code != "=":
                    novel_rows.append(ln)
                    gid = f[ci.get("qry_gene_id", 3)] if "qry_gene_id" in ci else ""
                    if gid:
                        novel_genes.add(gid)
            if novel_rows:
                (out_dir / "novel_transcripts.tsv").write_text(
                    "\t".join(header) + "\n" + "\n".join(novel_rows) + "\n", encoding="utf-8")

        summary = {
            "n_samples": len(gtf_list),
            "n_novel_transcripts": len(novel_rows),
            "n_novel_genes": len(novel_genes),
            "class_code_counts": dict(code_counts),
            "merged_gtf": str(merged),
        }
        (out_dir / "new_transcripts_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== 新转录本: {len(novel_rows)} 个, 新基因: {len(novel_genes)} 个 ===")


if __name__ == "__main__":
    StringtieRunner.main()
