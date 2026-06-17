"""featureCounts runner — 把 BAM 量化成基因 count。

特性:
  - 自动检测每个 BAM 的单/双端(读 BAM header + 首条 read flag)
  - 单端和双端 BAM **分两次跑**(featureCounts 的 -p 是全局开关)
  - 最后合并产出每样本一个 .tsv
  - 线程数自动 cap 到 64(featureCounts 硬上限)

参数:
  bam_files: list[str]
  sample_names: list[str]   - 与 bams 一一对应(可选,默认从 BAM 名推)
  gtf: str
  strand: int               - 0 / 1 / 2,默认 0
  threads: int              - 默认 8,自动 cap 到 64
  feature_type: "exon"
  attribute: "gene_id"
  
  # 这两个参数被忽略 — 自动从 BAM 检测
  # paired:                 - 已废弃,自动检测

产出(到 counts/):
  <sample>.tsv             # gene_id + count
  all_genes.tsv            # 含 length(给 normalize 用)
  summary.tsv              # featureCounts 统计
"""
import struct
import subprocess
from pathlib import Path

from runners.base import BaseRunner


# featureCounts -T 上限
FC_THREADS_MAX = 64


def detect_bam_paired(bam_path: Path) -> bool | None:
    """读 BAM 第一条 alignment 的 flag,看 0x1 (paired) 位。
    
    用 samtools view 拿第一行(BAM 是二进制 + bgzip,不能直接读)。
    返回 True/False/None(None = 检测失败,默认当 paired)。
    """
    try:
        # samtools view 拿首条 alignment(不要头)
        result = subprocess.run(
            ["samtools", "view", str(bam_path)],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            return None
        first_line = result.stdout.split("\n", 1)[0]
        if not first_line:
            return None
        # SAM 格式:QNAME FLAG RNAME ...
        fields = first_line.split("\t")
        if len(fields) < 2:
            return None
        flag = int(fields[1])
        return bool(flag & 0x1)
    except Exception:
        return None


class FeatureCountsRunner(BaseRunner):
    
    def run(self):
        params = self.job.params or {}
        bams = params.get("bam_files") or params.get("bams") or []
        sample_names = params.get("sample_names", [])
        gtf = params.get("gtf")
        strand = int(params.get("strand", params.get("strandedness", 0)))
        threads = int(params.get("threads", 8))
        feature_type = params.get("feature_type", "exon")
        attribute = params.get("attribute", "gene_id")
        
        # 先取本任务的全局 CPU 配额(总预算 ÷ 并行度);再 cap 到 featureCounts -T 上限 64
        threads = self.effective_threads(threads)
        if threads > FC_THREADS_MAX:
            self.log(
                f"⚠️ featureCounts 的 -T 上限是 {FC_THREADS_MAX},"
                f"配额 {threads} 自动 cap 到 {FC_THREADS_MAX}"
            )
            threads = FC_THREADS_MAX
        
        if not bams:
            raise ValueError("未提供 BAM 列表")
        for b in bams:
            if not Path(b).exists():
                raise FileNotFoundError(f"BAM 不存在: {b}")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")
        
        # 默认样本名
        if not sample_names:
            sample_names = [Path(b).name.split(".")[0] for b in bams]
        if len(sample_names) != len(bams):
            raise ValueError("sample_names 数量与 bams 不一致")
        
        # 检测每个 BAM 的 paired 状态
        self.update(pct=10, stage="检测 BAM 单/双端")
        paired_status: list[bool] = []
        for bam, name in zip(bams, sample_names):
            p = detect_bam_paired(Path(bam))
            if p is None:
                self.log(f"  {name}: 检测失败,假定 paired")
                p = True
            paired_status.append(p)
            self.log(f"  {name}: {'paired-end' if p else 'single-end'}")
        
        # 分组:paired-end 的一组,single-end 的一组
        out_dir = self.output_dir()
        n_paired = sum(paired_status)
        n_single = len(paired_status) - n_paired
        self.log(f"分组结果:paired={n_paired},single={n_single}")
        
        # 用统一的 joint 输出文件,后面合并到 all_genes.tsv 和单样本
        partial_tsvs: list[Path] = []  # 每组的 joint 输出
        
        if n_paired > 0:
            self.update(pct=20, stage=f"featureCounts (paired,{n_paired} 个 BAM)")
            joint_p = out_dir / ".paired_joint.tsv"
            paired_bams = [b for b, p in zip(bams, paired_status) if p]
            paired_names = [n for n, p in zip(sample_names, paired_status) if p]
            self._run_fc(joint_p, paired_bams, gtf, strand, threads,
                          feature_type, attribute, paired=True)
            partial_tsvs.append(joint_p)
        
        if n_single > 0:
            self.update(
                pct=50 if n_paired > 0 else 20,
                stage=f"featureCounts (single,{n_single} 个 BAM)",
            )
            joint_s = out_dir / ".single_joint.tsv"
            single_bams = [b for b, p in zip(bams, paired_status) if not p]
            single_names = [n for n, p in zip(sample_names, paired_status) if not p]
            self._run_fc(joint_s, single_bams, gtf, strand, threads,
                          feature_type, attribute, paired=False)
            partial_tsvs.append(joint_s)
        
        # 合并所有 joint 输出到统一的单样本文件 + all_genes.tsv
        self.update(pct=85, stage="拆分 + 合并产出")
        self._merge_and_split(partial_tsvs, sample_names, paired_status, out_dir)
        
        self.update(pct=100, stage="完成")
    
    def _run_fc(self, joint_out: Path, bams: list, gtf: str, strand: int,
                  threads: int, feature_type: str, attribute: str,
                  paired: bool):
        """跑一次 featureCounts。"""
        cmd = [
            "featureCounts",
            "-T", str(threads),
            "-a", str(gtf),
            "-o", str(joint_out),
            "-t", feature_type,
            "-g", attribute,
            "-s", str(strand),
        ]
        if paired:
            cmd.append("-p")
            cmd.append("--countReadPairs")
        cmd += bams
        self.run_command(
            cmd, timeout=10800,
            indeterminate=True,
            heartbeat_stage=f"featureCounts ({'paired' if paired else 'single'}, {len(bams)} BAM)",
        )
    
    def _merge_and_split(self, partial_tsvs: list[Path],
                          sample_names: list[str],
                          paired_status: list[bool],
                          out_dir: Path):
        """把 paired joint + single joint 合并,每个样本一个 .tsv,
        + all_genes.tsv(含 Length 列给 normalize 用)
        + summary.tsv(合并 .summary)
        """
        # 收集每个文件的:gene 元数据 + sample → counts
        gene_meta = {}  # gene_id → [Geneid, Chr, Start, End, Strand, Length]
        sample_counts: dict[str, dict[str, str]] = {}  # sample → {gene → count}
        
        for tsv in partial_tsvs:
            with open(tsv, encoding="utf-8") as f:
                lines = f.readlines()
            if lines[0].startswith("#"):
                lines = lines[1:]
            header = lines[0].rstrip("\n").split("\t")
            # header: Geneid Chr Start End Strand Length sample1.bam sample2.bam ...
            samples_in_this_file = []
            for col in header[6:]:
                # 列名是 BAM 路径,从中找到对应 sample 名
                bam_name = Path(col).name
                # 匹配 sample_names
                matched = None
                for s in sample_names:
                    if bam_name.startswith(s + ".") or bam_name == s:
                        matched = s
                        break
                if matched is None:
                    # fallback 用 stem
                    matched = bam_name.split(".")[0]
                samples_in_this_file.append(matched)
            
            for line in lines[1:]:
                cols = line.rstrip("\n").split("\t")
                if len(cols) < 6:
                    continue
                gene_id = cols[0]
                if gene_id not in gene_meta:
                    gene_meta[gene_id] = cols[:6]
                for i, sample in enumerate(samples_in_this_file):
                    count = cols[6 + i] if 6 + i < len(cols) else "0"
                    sample_counts.setdefault(sample, {})[gene_id] = count
        
        # gene_id 顺序(按第一个 partial 的顺序)
        gene_order = list(gene_meta.keys())
        
        # all_genes.tsv:Geneid Chr Start End Strand Length sample1 sample2 ...
        all_path = out_dir / "all_genes.tsv"
        with open(all_path, "w", encoding="utf-8") as f:
            f.write("\t".join(["Geneid", "Chr", "Start", "End", "Strand",
                                  "Length"] + sample_names) + "\n")
            for g in gene_order:
                row = list(gene_meta[g])
                for s in sample_names:
                    row.append(sample_counts.get(s, {}).get(g, "0"))
                f.write("\t".join(row) + "\n")
        self.log(f"all_genes.tsv 写入(含 length 列)")
        
        # 每个样本一个 .tsv:gene_id + count
        for s in sample_names:
            sp = out_dir / f"{s}.tsv"
            with open(sp, "w", encoding="utf-8") as f:
                f.write(f"gene_id\t{s}\n")
                for g in gene_order:
                    f.write(f"{g}\t{sample_counts.get(s, {}).get(g, '0')}\n")
            self.log(f"{s}.tsv 写入")
        
        # summary 合并 — 直接拼 paired/single 的 .summary,每行注明来源
        summary_lines = []
        for tsv in partial_tsvs:
            sm = tsv.with_suffix(".tsv.summary")
            if sm.exists():
                summary_lines.append(f"# === from {sm.name} ===")
                with open(sm, encoding="utf-8") as f:
                    summary_lines.append(f.read().rstrip())
        if summary_lines:
            with open(out_dir / "summary.tsv", "w", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        
        # 清临时文件
        for tsv in partial_tsvs:
            for ext in ("", ".summary"):
                p = Path(str(tsv) + ext)
                if p.exists():
                    p.unlink()


if __name__ == "__main__":
    FeatureCountsRunner.main()
