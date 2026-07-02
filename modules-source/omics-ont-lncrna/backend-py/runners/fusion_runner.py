"""Fusion detection runner for ONT lncRNA data.

Detects gene fusions by analyzing chimeric reads (supplementary alignments)
from aligned BAM files.

Parameters:
  bam_files: [str]     - Aligned BAM files (required)
  genome_fasta: str     - Reference genome FASTA (required)
  threads: int          - CPU threads (default 8)

Outputs (to output_dir/):
  fusion_candidates.tsv      - Detected fusion candidates
  fusion_summary.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


class FusionRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam_files = p.get("bam_files", [])
        genome = p.get("genome_fasta", "")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not bam_files:
            raise ValueError("未提供 bam_files")
        if not genome or not Path(genome).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        fusion_candidates = []

        for bam in bam_files:
            bam_path = Path(bam)
            if not bam_path.exists():
                self.log(f"!! BAM 不存在: {bam}, 跳过")
                continue

            name = bam_path.stem.replace(".sorted", "").replace(".bam", "")
            self.update(pct=int(10), stage=f"分析融合: {name}", indeterminate=True)

            # Extract supplementary alignments (flag 2048) and chimeric reads
            chimeric_tsv = out_dir / f"{name}_chimeric.tsv"
            self.run_command([
                "samtools", "view", "-f", "2048", str(bam_path),
            ], cwd=str(out_dir), indeterminate=True,
               heartbeat_stage=f"extract supp {name}")

            # Also check for discordant pairs / split reads
            # Parse SA tags for fusion candidates
            sa_tsv = out_dir / f"{name}_sa_tags.tsv"
            awk_script = (
                f"samtools view '{bam_path}' | "
                f"awk '{{ for(i=12;i<=NF;i++) if($i ~ /^SA:Z:/) "
                f"print $1\"\\t\"substr($i,6) }}' "
                f"> '{sa_tsv}'"
            )
            self.run_command(["bash", "-c", awk_script],
                             indeterminate=True,
                             heartbeat_stage=f"parse SA {name}")

            # Parse SA tags for fusion candidates
            if sa_tsv.exists() and sa_tsv.stat().st_size > 0:
                lines = sa_tsv.read_text(
                    encoding="utf-8", errors="ignore").splitlines()
                for line in lines:
                    if not line.strip():
                        continue
                    parts = line.split("\t")
                    read_id = parts[0]
                    sa_tags = parts[1].split(";") if len(parts) > 1 else []
                    for tag in sa_tags:
                        if not tag.strip():
                            continue
                        fields = tag.split(",")
                        if len(fields) >= 4:
                            chr2 = fields[0]
                            pos2 = fields[1]
                            strand2 = fields[2]
                            fusion_candidates.append({
                                "read_id": read_id,
                                "chr2": chr2,
                                "pos2": pos2,
                                "strand2": strand2,
                                "bam": name,
                            })

            self.log(f"  {name}: {len(fusion_candidates)} 个融合候选")

        # Write candidates
        if fusion_candidates:
            cand_tsv = out_dir / "fusion_candidates.tsv"
            with open(cand_tsv, "w", encoding="utf-8") as wf:
                wf.write("read_id\tchr2\tpos2\tstrand2\tbam\n")
                for c in fusion_candidates:
                    wf.write(f"{c['read_id']}\t{c['chr2']}\t"
                             f"{c['pos2']}\t{c['strand2']}\t{c['bam']}\n")

        summary = {
            "n_fusion_candidates": len(fusion_candidates),
            "n_bams": len(bam_files),
            "fusion_file": str(cand_tsv) if fusion_candidates else None,
        }
        (out_dir / "fusion_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update(pct=100, stage="完成")
        self.log(f"=== 融合检测: {len(fusion_candidates)} 个候选 ===")


if __name__ == "__main__":
    FusionRunner.main()
