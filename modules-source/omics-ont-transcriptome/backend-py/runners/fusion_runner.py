"""融合转录本检测 runner。

基于 ONT 长读长测序检测融合转录本(fusion transcript)。
目前支持 ARTH(基于 minimap2 嵌合比对)和 PIZZA 两种方法。

参数:
  fastq:         str   - 输入 fastq 文件
  genome_fasta:  str   - 参考基因组 FASTA
  annotation_gtf: str  - 注释 GTF(可选)
  method:        str   - "artic"(默认,基于嵌合读长) 或 "pizza"
  output_prefix: str   - 输出文件名前缀
  extra_opts:    str   - 额外参数(追加到命令行末尾)
  threads:       int   - 默认 8

产出(到 output_subdir):
  <prefix>_fusion_candidates.tsv  - 融合候选列表
  <prefix>_fusion_summary.json    - 融合统计
"""
import json
import re
from pathlib import Path

from runners.base import BaseRunner


class FusionRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fastq = p.get("fastq")
        genome = p.get("genome_fasta")
        annotation = p.get("annotation_gtf")
        method = p.get("method", "artic").lower()
        prefix = p.get("output_prefix", "fusion")
        extra = p.get("extra_opts", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not fastq or not Path(fastq).exists():
            raise FileNotFoundError(f"fastq 不存在: {fastq}")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"参考基因组不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        if method == "artic":
            self._run_artic(fastq, genome, prefix, extra, threads, out_dir)
        elif method == "pizza":
            self._run_pizza(fastq, genome, annotation, prefix, extra, threads, out_dir)
        else:
            raise ValueError(f"不支持的方法: {method}, 请选择 artic 或 pizza")

    def _run_artic(self, fastq, genome, prefix, extra, threads, out_dir):
        """ARTH 方法:基于 minimap2 检测嵌合读长 + SA tag 解析。"""
        # Step 1: minimap2 比对(宽松参数以检出融合信号)
        self.update(pct=10, stage="minimap2 比对(含融合检测)", detail=f"样本 {Path(fastq).stem}")
        aln_sam = out_dir / f"{prefix}_aln.sam"
        aln_bam = out_dir / f"{prefix}_aln.bam"

        cmd = [
            "minimap2", "-ax", "splice", "-uf", "-C5",
            "-t", str(threads),
            str(genome), str(fastq),
        ]
        if extra:
            for part in extra.split():
                cmd.append(part)

        self.log(f"  minimap2 比对(融合宽松模式)...")
        # 用 PIPE 模式:对齐输出到 SAM,再用 samtools sort
        import subprocess
        sam_fh = open(aln_sam, "w")
        proc1 = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc2 = subprocess.Popen(
            ["samtools", "sort", "-@", str(max(1, threads // 2)), "-o", str(aln_bam)],
            stdin=proc1.stdout, stderr=subprocess.PIPE, text=True,
        )
        proc1.stdout.close()
        _, stderr2 = proc2.communicate()
        proc1.wait()
        sam_fh.close()

        if proc1.returncode != 0 or proc2.returncode != 0:
            self.log(f"  !! 比对出错 (rc1={proc1.returncode}, rc2={proc2.returncode})")
            # 可能有 stderr 信息
            if stderr2:
                self.log(f"  stderr: {stderr2[:200]}")

        # samtools index
        if aln_bam.exists():
            self.run_command(["samtools", "index", str(aln_bam)])

        # Step 2: 解析嵌合读长(检测 SA tag)
        self.update(pct=50, stage="解析融合候选", detail="检测嵌合读长")
        fusion_candidates = []
        seen = set()

        if aln_sam.exists():
            with open(aln_sam) as f:
                for line in f:
                    if line.startswith("@"):
                        continue
                    cols = line.strip().split("\t")
                    if len(cols) < 12:
                        continue
                    qname = cols[0]
                    flag = int(cols[1])
                    rname = cols[2]
                    pos = cols[3]
                    # 检测 SA tag (supplementary alignment)
                    sa_tag = None
                    for tag in cols[11:]:
                        if tag.startswith("SA:Z:"):
                            sa_tag = tag[5:]
                            break
                    if sa_tag:
                        # SA:Z:chr1,+,123456,+,100M,50,50;
                        parts = sa_tag.rstrip(";").split(";")
                        for part in parts:
                            sa_info = part.split(",")
                            if len(sa_info) >= 6:
                                chr2 = sa_info[0]
                                pos2 = sa_info[1]
                                if rname != chr2 and (rname, chr2) not in seen:
                                    seen.add((rname, chr2))
                                    fusion_candidates.append({
                                        "read_id": qname,
                                        "gene_chr1": rname,
                                        "pos1": pos,
                                        "gene_chr2": chr2,
                                        "pos2": pos2,
                                        "method": "SA_tag",
                                    })

        self.log(f"  检测到 {len(fusion_candidates)} 个融合候选")

        # Step 3: 写入结果
        self.update(pct=80, stage="写入结果", detail="生成融合候选 TSV")
        tsv = out_dir / f"{prefix}_fusion_candidates.tsv"
        if fusion_candidates:
            with open(tsv, "w") as f:
                f.write("read_id\tgene_chr1\tpos1\tgene_chr2\tpos2\tmethod\n")
                for cand in fusion_candidates:
                    f.write("{read_id}\t{gene_chr1}\t{pos1}\t{gene_chr2}\t{pos2}\t{method}\n".format(**cand))

        summary = {
            "method": "artic",
            "n_candidates": len(fusion_candidates),
            "output_tsv": str(tsv) if tsv.exists() else "",
        }
        (out_dir / f"{prefix}_fusion_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 融合检测(artic)完成: {len(fusion_candidates)} 个候选 ===")

    def _run_pizza(self, fastq, genome, annotation, prefix, extra, threads, out_dir):
        """PIZZA 融合检测流程。"""
        self.update(pct=10, stage="PIZZA 融合检测", detail="运行 PIZZA 流程")

        cmd = ["PIZZA", "run"]
        if annotation and Path(annotation).exists():
            cmd += ["-g", str(annotation)]
        cmd += [
            "-r", str(fastq),
            "-t", str(genome),
            "-o", str(out_dir),
            "-p", str(threads),
        ]
        if extra:
            for part in extra.split():
                cmd.append(part)

        self.run_command(cmd, heartbeat_stage="PIZZA", indeterminate=True)

        # 尝试收集结果
        candidates = []
        for pattern in ["*fusion*tsv", "*candidate*tsv", "*Fusion*tsv"]:
            for f_path in out_dir.glob(pattern):
                n = sum(1 for _ in open(f_path) if _.strip() and not _.startswith("#"))
                candidates.append(str(f_path))
                break

        summary = {
            "method": "pizza",
            "n_output_files": len(candidates),
            "output_dir": str(out_dir),
        }
        (out_dir / f"{prefix}_fusion_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== 融合检测(PIZZA)完成 ===")


if __name__ == "__main__":
    FusionRunner.main()
