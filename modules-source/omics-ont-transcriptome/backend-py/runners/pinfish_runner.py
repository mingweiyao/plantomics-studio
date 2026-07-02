"""Pinfish consensus transcript calling runner for ONT transcriptome data.

Runs the pinfish pipeline to call consensus transcripts from long-read
alignment data. The pipeline consists of 4 steps connected via shell pipes:

  1. spliced_bam2gff  - Convert spliced BAM alignments to GFF
  2. cluster_gff      - Cluster overlapping transcripts
  3. collapse_partials - Collapse partial transcripts using reference
  4. polish_clusters  - Polish consensus sequences

Parameters:
  bam:              str  - Sorted alignment BAM file
  genome_fasta:     str  - Reference genome FASTA file
  annotation_gtf:   str  - Optional reference annotation GTF (for collapse_partials)
  min_coverage:     float - Minimum coverage for polishing (default 0.1)
  min_cluster_size: int  - Minimum reads per cluster (default 5)
  threads:          int  - CPU threads (default 8)

Outputs (to output_dir/):
  transcripts.gff   - GFF from spliced alignments
  clusters.gff      - Clustered transcripts
  collapsed.gff     - Collapsed partial transcripts
  polished.gff      - Polished consensus transcripts
  pinfish_summary.json - Pipeline summary
"""
import json
import subprocess
import shutil
from pathlib import Path

from runners.base import BaseRunner


class PinfishRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam = p.get("bam", "")
        genome_fasta = p.get("genome_fasta", "")
        annotation_gtf = p.get("annotation_gtf", "")
        min_coverage = float(p.get("min_coverage", 0.1))
        min_cluster_size = int(p.get("min_cluster_size", 5))
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bam or not Path(bam).exists():
            raise FileNotFoundError(f"BAM 文件不存在: {bam}")
        if not genome_fasta or not Path(genome_fasta).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")
        if annotation_gtf and not Path(annotation_gtf).exists():
            raise FileNotFoundError(f"注释 GTF 不存在: {annotation_gtf}")

        # Tool precheck
        missing = [t for t in (
            "spliced_bam2gff", "cluster_gff",
            "collapse_partials", "polish_clusters",
        ) if not shutil.which(t)]
        if missing:
            raise FileNotFoundError(
                f"找不到 pinfish 工具: {', '.join(missing)}。"
                "请确保模块 conda 环境中已安装 pinfish。")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # ---- Step 1: spliced_bam2gff ----
        self.update(pct=5, stage="spliced_bam2gff", indeterminate=True)
        self.log("$ spliced_bam2gff " + str(bam) + " > transcripts.gff")

        gff_out = out_dir / "transcripts.gff"
        try:
            proc1 = subprocess.Popen(
                ["spliced_bam2gff", str(bam)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            with open(gff_out, "w") as f:
                for line in proc1.stdout:
                    f.write(line)
            proc1.wait()
            stderr_text = proc1.stderr.read()
            if proc1.returncode != 0:
                raise subprocess.CalledProcessError(
                    proc1.returncode, ["spliced_bam2gff", str(bam)])
            if stderr_text.strip():
                for ln in stderr_text.splitlines():
                    self.log(f"  {ln}")
        except FileNotFoundError:
            raise FileNotFoundError("找不到 spliced_bam2gff,请检查 pinfish 安装")

        if not gff_out.exists() or gff_out.stat().st_size == 0:
            raise RuntimeError("spliced_bam2gff 没有产出转录本 GFF")

        n_transcripts = sum(1 for ln in gff_out.read_text(
            encoding="utf-8", errors="ignore").splitlines()
            if ln.startswith("#") is False and ln.strip())

        self.log(f"  -> {gff_out} ({n_transcripts} 条转录本)")

        # ---- Step 2: cluster_gff ----
        self.update(pct=30, stage="cluster_gff", indeterminate=True)
        self.log(f"$ cluster_gff {gff_out} {min_cluster_size} > clusters.gff")

        clusters_out = out_dir / "clusters.gff"
        try:
            proc2 = subprocess.Popen(
                ["cluster_gff", str(gff_out), str(min_cluster_size)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            with open(clusters_out, "w") as f:
                for line in proc2.stdout:
                    f.write(line)
            proc2.wait()
            stderr_text = proc2.stderr.read()
            if proc2.returncode != 0:
                raise subprocess.CalledProcessError(
                    proc2.returncode,
                    ["cluster_gff", str(gff_out), str(min_cluster_size)])
            if stderr_text.strip():
                for ln in stderr_text.splitlines():
                    self.log(f"  {ln}")
        except FileNotFoundError:
            raise FileNotFoundError("找不到 cluster_gff,请检查 pinfish 安装")

        if not clusters_out.exists() or clusters_out.stat().st_size == 0:
            raise RuntimeError("cluster_gff 没有产出聚类 GFF")

        n_clusters = sum(1 for ln in clusters_out.read_text(
            encoding="utf-8", errors="ignore").splitlines()
            if ln.startswith("#") is False and ln.strip())

        self.log(f"  -> {clusters_out} ({n_clusters} 个聚类)")

        # ---- Step 3: collapse_partials ----
        self.update(pct=55, stage="collapse_partials", indeterminate=True)

        collapsed_out = out_dir / "collapsed.gff"
        collapse_cmd = ["collapse_partials", str(clusters_out), genome_fasta]
        if annotation_gtf:
            collapse_cmd.append(annotation_gtf)
        self.log("$ " + " ".join(str(c) for c in collapse_cmd) + " > collapsed.gff")

        try:
            proc3 = subprocess.Popen(
                collapse_cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            with open(collapsed_out, "w") as f:
                for line in proc3.stdout:
                    f.write(line)
            proc3.wait()
            stderr_text = proc3.stderr.read()
            if proc3.returncode != 0:
                raise subprocess.CalledProcessError(proc3.returncode, collapse_cmd)
            if stderr_text.strip():
                for ln in stderr_text.splitlines():
                    self.log(f"  {ln}")
        except FileNotFoundError:
            raise FileNotFoundError("找不到 collapse_partials,请检查 pinfish 安装")

        if not collapsed_out.exists() or collapsed_out.stat().st_size == 0:
            self.log("!! collapse_partials 输出为空,可能没有可折叠的部分转录本")
            collapsed_out.write_text("##gff-version 3\n", encoding="utf-8")

        n_collapsed = sum(1 for ln in collapsed_out.read_text(
            encoding="utf-8", errors="ignore").splitlines()
            if ln.startswith("#") is False and ln.strip())

        self.log(f"  -> {collapsed_out} ({n_collapsed} 条折叠后转录本)")

        # ---- Step 4: polish_clusters ----
        self.update(pct=80, stage="polish_clusters", indeterminate=True)

        polished_out = out_dir / "polished.gff"
        polish_cmd = [
            "polish_clusters",
            str(collapsed_out), genome_fasta, str(bam),
            "--coverage", str(min_coverage),
            "--threads", str(threads),
        ]
        self.log("$ " + " ".join(str(c) for c in polish_cmd) + " > polished.gff")

        try:
            proc4 = subprocess.Popen(
                polish_cmd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            with open(polished_out, "w") as f:
                for line in proc4.stdout:
                    f.write(line)
            proc4.wait()
            stderr_text = proc4.stderr.read()
            if proc4.returncode != 0:
                raise subprocess.CalledProcessError(proc4.returncode, polish_cmd)
            if stderr_text.strip():
                for ln in stderr_text.splitlines():
                    self.log(f"  {ln}")
        except FileNotFoundError:
            raise FileNotFoundError("找不到 polish_clusters,请检查 pinfish 安装")

        if not polished_out.exists() or polished_out.stat().st_size == 0:
            self.log("!! polish_clusters 输出为空")
            polished_out.write_text("##gff-version 3\n", encoding="utf-8")

        n_polished = sum(1 for ln in polished_out.read_text(
            encoding="utf-8", errors="ignore").splitlines()
            if ln.startswith("#") is False and ln.strip())

        self.log(f"  -> {polished_out} ({n_polished} 条 polishe 转录本)")

        # ---- Summary ----
        summary = {
            "input_bam": bam,
            "genome_fasta": genome_fasta,
            "annotation_gtf": annotation_gtf if annotation_gtf else "",
            "min_cluster_size": min_cluster_size,
            "min_coverage": min_coverage,
            "transcripts_gff": str(gff_out),
            "clusters_gff": str(clusters_out),
            "collapsed_gff": str(collapsed_out),
            "polished_gff": str(polished_out),
            "n_transcripts": n_transcripts,
            "n_clusters": n_clusters,
            "n_collapsed": n_collapsed,
            "n_polished": n_polished,
        }
        (out_dir / "pinfish_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== Pinfish 完成: {n_transcripts} 转录本 -> "
                 f"{n_clusters} 聚类 -> {n_collapsed} 折叠 -> "
                 f"{n_polished} polishe → {out_dir} ===")


if __name__ == "__main__":
    PinfishRunner.main()
