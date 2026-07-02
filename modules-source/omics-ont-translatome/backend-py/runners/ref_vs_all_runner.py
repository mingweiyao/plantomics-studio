"""Ref vs All 双流程对比分析 runner。

同时跑两套流程并对比结果:
  - Ref-only:  仅使用参考注释(从参考 GTF 中提取的已知转录本)
  - Full-pipeline: 完整的 ONT 分析流程(Pinfish -> StringTie -> gffcompare -> TransDecoder -> ...)

对比内容包括:
  1. 转录本数量对比
  2. 基因数量对比
  3. Venn 图数据(共同 vs 特有转录本/基因)
  4. 功能注释富集对比

参数:
  ref_gtf:        str   - 参考注释 GTF
  ref_pep:        str   - Ref-only 流程的蛋白序列(从参考 GTF 翻译)
  full_gtf:       str   - 全流程的组装 GTF
  full_pep:       str   - 全流程的蛋白序列(TransDecoder 输出)
  full_annot:     str   - 全流程的注释结果 TSV
  genome_fasta:   str   - 参考基因组
  output_prefix:  str   - 输出前缀
  threads:        int   - 默认 8

产出(到 output_subdir):
  ref_vs_all_comparison.tsv  - 对比统计表
  ref_vs_all_summary.json    - 对比汇总
  data/                      - Venn/pyramid/富集对比用数据文件
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class RefVsAllRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        ref_gtf = p.get("ref_gtf")
        ref_pep = p.get("ref_pep")
        full_gtf = p.get("full_gtf")
        full_pep = p.get("full_pep")
        full_annot = p.get("full_annot")
        genome_fasta = p.get("genome_fasta")
        prefix = p.get("output_prefix", "ref_vs_all")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not ref_gtf or not Path(ref_gtf).exists():
            raise FileNotFoundError(f"参考注释 GTF 不存在: {ref_gtf}")
        if not full_gtf or not Path(full_gtf).exists():
            raise FileNotFoundError(f"全流程 GTF 不存在: {full_gtf}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        data_dir = out_dir / "data"
        data_dir.mkdir(exist_ok=True)

        # ── 1. 统计参考注释的转录本/基因数 ──
        self.update(pct=10, stage="统计参考注释", detail="解析 ref GTF")
        ref_tx_count, ref_gene_count = self._count_gtf(ref_gtf)
        self.log(f"  参考注释: {ref_tx_count} 条转录本, {ref_gene_count} 个基因")

        # ── 2. 统计全流程的转录本/基因数 ──
        self.update(pct=25, stage="统计全流程结果", detail="解析 full GTF")
        full_tx_count, full_gene_count = self._count_gtf(full_gtf)
        self.log(f"  全流程: {full_tx_count} 条转录本, {full_gene_count} 个基因")

        # ── 3. 提取转录本/基因 ID 列表 ──
        self.update(pct=40, stage="提取 ID 列表", detail="用于 Venn 分析")
        ref_tx_ids = self._get_tx_ids(ref_gtf)
        ref_gene_ids = self._get_gene_ids(ref_gtf)
        full_tx_ids = self._get_tx_ids(full_gtf)
        full_gene_ids = self._get_gene_ids(full_gtf)

        # Venn 数据:共同 vs 特有
        common_tx = ref_tx_ids & full_tx_ids
        ref_only_tx = ref_tx_ids - full_tx_ids
        full_only_tx = full_tx_ids - ref_tx_ids

        common_genes = ref_gene_ids & full_gene_ids
        ref_only_genes = ref_gene_ids - full_gene_ids
        full_only_genes = full_gene_ids - ref_gene_ids

        # ── 4. 写入 Venn 数据 ──
        self.update(pct=55, stage="生成 Venn 数据", detail="写入对比数据文件")
        venn_tx = data_dir / "venn_transcripts.txt"
        with open(venn_tx, "w") as f:
            f.write("category\tid\n")
            for tid in ref_only_tx:
                f.write(f"ref_only\t{tid}\n")
            for tid in full_only_tx:
                f.write(f"full_only\t{tid}\n")
            for tid in common_tx:
                f.write(f"common\t{tid}\n")

        venn_genes = data_dir / "venn_genes.txt"
        with open(venn_genes, "w") as f:
            f.write("category\tid\n")
            for gid in ref_only_genes:
                f.write(f"ref_only\t{gid}\n")
            for gid in full_only_genes:
                f.write(f"full_only\t{gid}\n")
            for gid in common_genes:
                f.write(f"common\t{gid}\n")

        # ── 5. 蛋白序列对比(如有) ──
        pep_comparison = {}
        if ref_pep and full_pep and Path(ref_pep).exists() and Path(full_pep).exists():
            self.update(pct=70, stage="蛋白序列对比", detail="比较 ORF 预测")
            ref_orfs = self._count_pep(ref_pep)
            full_orfs = self._count_pep(full_pep)
            pep_comparison = {
                "ref_orfs": ref_orfs,
                "full_orfs": full_orfs,
                "difference": full_orfs - ref_orfs,
                "ref_pep": ref_pep,
                "full_pep": full_pep,
            }
            self.log(f"  Ref ORFs: {ref_orfs}, Full ORFs: {full_orfs}")

        # ── 6. 功能注释对比(如有) ──
        annot_comparison = {}
        if full_annot and Path(full_annot).exists():
            self.update(pct=80, stage="功能注释对比", detail="解析全流程注释")
            n_annotated = 0
            with open(full_annot) as f:
                header = f.readline()
                for line in f:
                    cols = line.strip().split("\t")
                    if len(cols) > 1 and cols[1]:
                        n_annotated += 1
            annot_comparison = {
                "full_annotation_file": full_annot,
                "n_annotated": n_annotated,
                "n_total": full_tx_count,
                "annotation_rate": round(n_annotated / max(full_tx_count, 1) * 100, 2),
            }
            self.log(f"  全流程注释率: {annot_comparison['annotation_rate']}%")

        # ── 7. 写入对比 TSV ──
        self.update(pct=90, stage="写入结果", detail="生成对比报告")
        comparison_file = out_dir / f"{prefix}_comparison.tsv"
        with open(comparison_file, "w") as f:
            f.write("metric\tref_only\tfull_pipeline\tdifference\n")
            f.write(f"transcripts\t{ref_tx_count}\t{full_tx_count}\t{full_tx_count - ref_tx_count}\n")
            f.write(f"genes\t{ref_gene_count}\t{full_gene_count}\t{full_gene_count - ref_gene_count}\n")
            f.write(f"unique_transcripts\t{len(ref_only_tx)}\t{len(full_only_tx)}\t{len(full_only_tx) - len(ref_only_tx)}\n")
            f.write(f"shared_transcripts\t{len(common_tx)}\t{len(common_tx)}\t0\n")
            if pep_comparison:
                f.write(f"predicted_orfs\t{pep_comparison['ref_orfs']}\t{pep_comparison['full_orfs']}\t{pep_comparison['difference']}\n")

        # ── 8. 写入汇总 JSON ──
        summary = {
            "ref_gtf": ref_gtf,
            "full_gtf": full_gtf,
            "ref_transcripts": ref_tx_count,
            "full_transcripts": full_tx_count,
            "ref_genes": ref_gene_count,
            "full_genes": full_gene_count,
            "transcript_difference": full_tx_count - ref_tx_count,
            "gene_difference": full_gene_count - ref_gene_count,
            "venn": {
                "common_transcripts": len(common_tx),
                "ref_only_transcripts": len(ref_only_tx),
                "full_only_transcripts": len(full_only_tx),
                "common_genes": len(common_genes),
                "ref_only_genes": len(ref_only_genes),
                "full_only_genes": len(full_only_genes),
            },
            "pep_comparison": pep_comparison,
            "annot_comparison": annot_comparison,
            "data_dir": str(data_dir),
            "comparison_file": str(comparison_file),
        }
        (out_dir / f"{prefix}_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Ref vs All 对比完成: 参考 {ref_tx_count}, 全流程 {full_tx_count}, "
                 f"共同 {len(common_tx)} 条转录本 ===")

    def _count_gtf(self, gtf_path: str) -> tuple:
        """统计 GTF 中转录本数和基因数。"""
        tx_ids = set()
        gene_ids = set()
        with open(gtf_path) as f:
            for line in f:
                if line.startswith("#") or line.strip() == "":
                    continue
                cols = line.strip().split("\t")
                if len(cols) < 9:
                    continue
                if cols[2] == "transcript":
                    attrs = cols[8]
                    # Extract transcript_id
                    for part in attrs.replace('"', '').split(";"):
                        part = part.strip()
                        if part.startswith("transcript_id"):
                            tx_ids.add(part.split()[1])
                        elif part.startswith("gene_id"):
                            gene_ids.add(part.split()[1])
                elif cols[2] == "gene":
                    attrs = cols[8]
                    for part in attrs.replace('"', '').split(";"):
                        part = part.strip()
                        if part.startswith("gene_id"):
                            gene_ids.add(part.split()[1])
        return len(tx_ids), max(len(gene_ids), 1)

    def _get_tx_ids(self, gtf_path: str) -> set:
        """提取 GTF 中所有转录本 ID。"""
        ids = set()
        with open(gtf_path) as f:
            for line in f:
                if line.startswith("#") or line.strip() == "":
                    continue
                cols = line.strip().split("\t")
                if len(cols) < 9 or cols[2] != "transcript":
                    continue
                attrs = cols[8]
                for part in attrs.replace('"', '').split(";"):
                    part = part.strip()
                    if part.startswith("transcript_id"):
                        ids.add(part.split()[1])
        return ids

    def _get_gene_ids(self, gtf_path: str) -> set:
        """提取 GTF 中所有基因 ID。"""
        ids = set()
        with open(gtf_path) as f:
            for line in f:
                if line.startswith("#") or line.strip() == "":
                    continue
                cols = line.strip().split("\t")
                if len(cols) < 9:
                    continue
                if cols[2] not in ("transcript", "gene"):
                    continue
                attrs = cols[8]
                for part in attrs.replace('"', '').split(";"):
                    part = part.strip()
                    if part.startswith("gene_id"):
                        ids.add(part.split()[1])
        return ids

    def _count_pep(self, pep_path: str) -> int:
        """统计蛋白 FASTA 中的序列数。"""
        count = 0
        with open(pep_path) as f:
            for line in f:
                if line.startswith(">"):
                    count += 1
        return count


if __name__ == "__main__":
    RefVsAllRunner.main()
