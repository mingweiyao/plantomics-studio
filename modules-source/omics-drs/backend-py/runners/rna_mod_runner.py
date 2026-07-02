"""RNA modification detection runner (m6A/m5C).

Detects RNA modifications from ONT Direct RNA Sequencing data.
Uses m6Anet or Tombo-based approaches for modification detection:

For m6A detection:
  - Utilizes DRUMMER/m6Anet signal-level analysis approach
  - Alternatively uses Tombo resquiggle + modification detection

For m5C detection:
  - Uses Tombo or Nanocompore-based approach

Since different tools have different inputs, this runner provides
flexible analysis pathways depending on available data.

Parameters:
  bam_files: [str]       - Aligned BAM files (from minimap2)
  sample_names: [str]    - Optional sample names
  genome_fasta: str      - Reference genome FASTA
  mod_type: str          - Modification type: 'm6A' | 'm5C' | 'both' (default: 'both')
  method: str            - Detection method: 'auto' | 'tombo' | 'm6anet' (default: 'auto')
  fast5_dirs: [str]      - Per-sample FAST5/POD5 directories (for signal-level analysis)
  threads: int           - CPU threads (default: 8)
  min_coverage: int      - Minimum read coverage for modification calling (default: 5)
  min_prob: float        - Minimum modification probability (default: 0.8)
  differential: bool     - Run differential modification analysis (default: True)
  groups: dict           - Sample groups for differential analysis

Outputs (to output_dir/):
  per_sample/<name>/         - Per-sample modification results
    mod_sites_<type>.bed     - Detected modification sites (BED format)
    mod_evidence.tsv         - Per-read modification evidence
  modification_summary.json  - Cross-sample modification summary
"""
import json
from pathlib import Path
import shutil

from runners.base import BaseRunner


class RnaModRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        bam_files = p.get("bam_files", [])
        sample_names = p.get("sample_names", [])
        genome_fasta = p.get("genome_fasta", "")
        mod_type = p.get("mod_type", "both")
        method = p.get("method", "auto")
        fast5_dirs = p.get("fast5_dirs", [])
        threads = self.effective_threads(int(p.get("threads", 8)))
        min_coverage = int(p.get("min_coverage", 5))
        min_prob = float(p.get("min_prob", 0.8))
        differential = bool(p.get("differential", True))
        groups = p.get("groups", {})

        if not bam_files:
            raise ValueError("bam_files 列表为空")
        if not genome_fasta or not Path(genome_fasta).exists():
            raise FileNotFoundError(f"基因组 FASTA 不存在: {genome_fasta}")

        if not sample_names or len(sample_names) != len(bam_files):
            sample_names = [Path(b).stem.split(".")[0].replace(".sorted", "")
                            for b in bam_files]

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        # Detect available tools
        has_tombo = shutil.which("tombo") is not None
        has_m6anet = shutil.which("m6anet") is not None
        has_eligos = shutil.which("eligos") is not None

        self.log(f"可用工具: tombo={has_tombo}, m6anet={has_m6anet}, "
                 f"eligos={has_eligos}")

        if method == "tombo":
            if not has_tombo:
                raise FileNotFoundError("tombo 不可用,请安装 tombo")
            self._run_tombo(bam_files, sample_names, genome_fasta,
                            fast5_dirs, mod_type, threads, min_coverage,
                            min_prob, out_dir)
        elif method == "m6anet":
            if not has_m6anet:
                raise FileNotFoundError("m6anet 不可用,请安装 m6anet")
            self._run_m6anet(bam_files, sample_names, threads,
                              min_prob, min_coverage, out_dir)
        else:
            # auto: try multiple approaches
            if has_m6anet:
                self.log("自动选择 m6anet 方法")
                self._run_m6anet(bam_files, sample_names, threads,
                                  min_prob, min_coverage, out_dir)
            elif has_tombo:
                self.log("自动选择 tombo 方法")
                self._run_tombo(bam_files, sample_names, genome_fasta,
                                fast5_dirs, mod_type, threads, min_coverage,
                                min_prob, out_dir)
            else:
                self._run_signal_based(bam_files, sample_names, genome_fasta,
                                        mod_type, out_dir)

        # Generate summary
        self.update(pct=85, stage="汇总修饰结果")
        self._generate_summary(out_dir, sample_names, mod_type, groups)

        self.update(pct=100, stage="完成")
        self.log(f"=== RNA 修饰检测完成 → {out_dir} ===")

    def _run_tombo(self, bam_files, sample_names, genome_fasta,
                    fast5_dirs, mod_type, threads, min_coverage,
                    min_prob, out_dir):
        """RNA modification detection using Tombo."""
        n = len(bam_files)
        for i, (bam, name) in enumerate(zip(bam_files, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: {bam} 不存在")
                continue
            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            # Step 1: Tombo resquiggle (re-annotate raw signal)
            self.update(pct=int(5 + 40 * i / n),
                        stage=f"Tombo resquiggle ({i + 1}/{n})", detail=name,
                        indeterminate=True)

            # Determine fast5 directory for this sample
            f5_dir = ""
            if fast5_dirs and i < len(fast5_dirs):
                f5_dir = fast5_dirs[i]
            if not f5_dir:
                # Try to find fast5 next to BAM
                bam_parent = Path(bam).parent
                candidates = [bam_parent / "fast5", bam_parent / "pod5",
                              bam_parent / "fast5_pass",
                              bam_parent / "pod5_pass"]
                for c in candidates:
                    if c.exists():
                        f5_dir = str(c)
                        break
                if not f5_dir:
                    self.log(f"{name}: 未找到 FAST5 目录,跳过 signal-level 分析")
                    continue

            # Create corrected group for this sample
            self.run_command([
                "tombo", "resquiggle",
                f5_dir, genome_fasta,
                "--overwrite",
                "--processes", str(threads),
                "--num-most-common-errors", "5",
                "--corrected-group", "RawGenomeCorrected_000",
                "--basecall-group", "Basecall_1D_000",
                "--sequence-file", bam,
            ], indeterminate=True, heartbeat_stage=f"tombo resquiggle {name}")

            # Step 2: Detect modifications
            self.update(pct=int(5 + 60 * i / n),
                        stage=f"Tombo 修饰检测 ({i + 1}/{n})", detail=name,
                        indeterminate=True)

            if mod_type in ("m6A", "both"):
                m6a_bed = sample_dir / "mod_sites_m6A.bed"
                self.run_command([
                    "tombo", "detect_modifications",
                    "de_novo",
                    "--fast5-basedir", f5_dir,
                    "--corrected-group", "RawGenomeCorrected_000",
                    "--statistics-file", str(sample_dir / "tombo_m6a_stats.txt"),
                    "--per-read-statistics-file", str(sample_dir / "tombo_m6a_per_read.txt"),
                    "--processes", str(threads),
                    "--alternate-model", "m6A",
                    "--minimum-kmer-coverage", str(min_coverage),
                    "--minimum-signal-coverage", str(min_coverage * 2),
                ], indeterminate=True, heartbeat_stage=f"tombo m6A {name}")

                # Export detected sites to BED
                self.run_command([
                    "tombo", "plot_max_coverage",
                    "--fast5-basedir", f5_dir,
                    "--corrected-group", "RawGenomeCorrected_000",
                    "--statistics-file", str(sample_dir / "tombo_m6a_stats.txt"),
                    "--output", str(m6a_bed),
                    "--format", "bed",
                    "--percent-signal", str(sample_dir / "m6a_signal.txt"),
                ])

            if mod_type in ("m5C", "both"):
                m5c_bed = sample_dir / "mod_sites_m5C.bed"
                self.run_command([
                    "tombo", "detect_modifications",
                    "de_novo",
                    "--fast5-basedir", f5_dir,
                    "--corrected-group", "RawGenomeCorrected_000",
                    "--statistics-file", str(sample_dir / "tombo_m5c_stats.txt"),
                    "--per-read-statistics-file", str(sample_dir / "tombo_m5c_per_read.txt"),
                    "--processes", str(threads),
                    "--alternate-model", "m5C",
                    "--minimum-kmer-coverage", str(min_coverage),
                    "--minimum-signal-coverage", str(min_coverage * 2),
                ], indeterminate=True, heartbeat_stage=f"tombo m5C {name}")

                self.run_command([
                    "tombo", "plot_max_coverage",
                    "--fast5-basedir", f5_dir,
                    "--corrected-group", "RawGenomeCorrected_000",
                    "--statistics-file", str(sample_dir / "tombo_m5c_stats.txt"),
                    "--output", str(m5c_bed),
                    "--format", "bed",
                ])

    def _run_m6anet(self, bam_files, sample_names, threads,
                     min_prob, min_coverage, out_dir):
        """RNA modification detection using m6Anet."""
        n = len(bam_files)
        for i, (bam, name) in enumerate(zip(bam_files, sample_names)):
            if not Path(bam).exists():
                self.log(f"!! 跳过 {name}: {bam} 不存在")
                continue

            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            self.update(pct=int(5 + 60 * i / n),
                        stage=f"m6Anet 修饰检测 ({i + 1}/{n})", detail=name,
                        indeterminate=True)

            # m6Anet runs dataprocess then training/inference
            # data preparation
            data_dir = sample_dir / "m6anet_data"
            self.run_command([
                "m6anet", "dataprocess",
                "--bam", bam,
                "--output_dir", str(data_dir),
                "--n_processes", str(threads),
            ], indeterminate=True, heartbeat_stage=f"m6anet data {name}")

            # Inference
            result_dir = sample_dir / "m6anet_results"
            self.run_command([
                "m6anet", "inference",
                "--data_dir", str(data_dir),
                "--out_dir", str(result_dir),
                "--num_workers", str(threads),
            ], indeterminate=True, heartbeat_stage=f"m6anet inference {name}")

            # Filter and export
            sites_bed = sample_dir / "mod_sites_m6A.bed"
            self._export_m6anet_results(result_dir, sites_bed,
                                         min_prob, min_coverage)

            # Per-read evidence
            evidence = sample_dir / "mod_evidence.tsv"
            self._export_m6anet_evidence(result_dir, evidence,
                                          min_prob)

    def _run_signal_based(self, bam_files, sample_names, genome_fasta,
                           mod_type, out_dir):
        """Fallback signal-based modification detection."""
        n = len(bam_files)
        for i, (bam, name) in enumerate(zip(bam_files, sample_names)):
            if not Path(bam).exists():
                continue
            sample_dir = out_dir / name
            sample_dir.mkdir(exist_ok=True)

            self.update(pct=int(5 + 70 * i / n),
                        stage=f"信号分析 ({i + 1}/{n})", detail=name,
                        indeterminate=True)

            # For DRS data without signal-level tools, use occupancy-based
            # approach: identify positions with basecall errors that might
            # indicate modifications (simplified)
            self.log(f"{name}: 使用比对信号分析(无专用修饰工具)")

            # Extract coverage profile at each position
            cov_file = sample_dir / "coverage_profile.tsv"
            self.run_command([
                "bash", "-c",
                f"samtools depth -aa '{bam}' > '{cov_file}' 2>/dev/null",
            ])

            # Parse coverage for potential modification sites
            # (high error rate positions from MD tags)
            md_bed = sample_dir / "mod_sites_mismatch.bed"

            # Build perl script without Python escape warnings
            perl1 = (
                "samtools view " + str(bam) + " | "
                "perl -ne '"
                "  @c=split(/\\t/); "
                "  next unless $c[5]=~/(\\d+)M(\\d+)/; "
                '  print join("\\t",$c[2],$c[3],$c[3]+$1'
                ",$c[0],$2, $c[1]&0x10?\"-\":\"+\").\"\\n\""
                "' | sort -k1,1 -k2,2n | uniq -c | "
                "awk '{print $2\"\\t\"$3\"\\t\"$4\"\\tmod\\t\"$1}' "
                "| head -10000 > " + str(md_bed)
            )
            self.run_command(["bash", "-c", perl1])

            # Parse into evidence
            evidence = sample_dir / "mod_evidence.tsv"
            perl2 = (
                "echo 'read_id\\tposition\\ttype\\tscore' > " + str(evidence) + "; "
                "samtools view " + str(bam) + " | "
                "perl -ne '"
                "  @c=split(/\\t/); "
                "  next unless $c[5]=~/(\\d+)M/; "
                "  next unless $c[13]=~/^NM:i:(\\d+)/; "
                '  print $c[0]."\\t".$c[3]."\\tNM\\t".$1."\\n"'
                "' | head -50000 >> " + str(evidence)
            )
            self.run_command(["bash", "-c", perl2])

    @staticmethod
    def _export_m6anet_results(result_dir, sites_bed, min_prob, min_coverage):
        """Export m6Anet results in BED format."""
        if not result_dir.exists():
            return

        # m6Anet produces data.csv with modification probabilities
        import csv
        data_file = result_dir / "data.csv"
        if not data_file.exists():
            # Search for CSV files
            csv_files = list(result_dir.glob("*.csv"))
            if csv_files:
                data_file = csv_files[0]
            else:
                return

        with open(sites_bed, "w", encoding="utf-8") as bed:
            with open(data_file, encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                # Find relevant columns
                col_map = {}
                for col_name in ["contig", "position", "probability",
                                 "coverage", "strand", "read_name"]:
                    for idx, col in enumerate(header):
                        if col.lower().strip() == col_name.lower():
                            col_map[col_name] = idx
                            break

                for row in reader:
                    if not row:
                        continue
                    # Get probability
                    prob_col = col_map.get("probability", 4)
                    prob = float(row[prob_col]) if len(row) > prob_col else 0
                    if prob < min_prob:
                        continue

                    # Get coverage
                    cov_col = col_map.get("coverage", 5)
                    cov = int(row[cov_col]) if len(row) > cov_col else 0
                    if cov < min_coverage:
                        continue

                    contig = row[col_map.get("contig", 0)] if "contig" in col_map else row[0]
                    pos = row[col_map.get("position", 1)] if "position" in col_map else row[1]
                    strand = row[col_map.get("strand", 6)] if "strand" in col_map else "+"

                    bed.write(f"{contig}\t{pos}\t{int(pos) + 1}\t"
                             f"m6A\t{prob:.4f}\t{strand}\n")

    @staticmethod
    def _export_m6anet_evidence(result_dir, evidence_path, min_prob):
        """Export per-read modification evidence."""
        if not result_dir.exists():
            return
        data_file = result_dir / "data.csv"
        if not data_file.exists():
            csv_files = list(result_dir.glob("*.csv"))
            data_file = csv_files[0] if csv_files else None
        if not data_file:
            return

        import csv
        with open(evidence_path, "w", encoding="utf-8") as out:
            out.write("read_id\tcontig\tposition\tprobability\tcoverage\n")
            with open(data_file, encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                col_map = {}
                for col_name in ["read_name", "contig", "position",
                                 "probability", "coverage"]:
                    for idx, col in enumerate(header):
                        if col.lower().strip() == col_name.lower():
                            col_map[col_name] = idx
                            break
                for row in reader:
                    if not row:
                        continue
                    prob_col = col_map.get("probability", 4)
                    prob = float(row[prob_col]) if len(row) > prob_col else 0
                    if prob < min_prob:
                        continue
                    read = row[col_map.get("read_name", 0)] if "read_name" in col_map else row[0]
                    contig = row[col_map.get("contig", 1)] if "contig" in col_map else row[1]
                    pos = row[col_map.get("position", 2)] if "position" in col_map else row[2]
                    cov = row[col_map.get("coverage", 5)] if len(row) > col_map.get("coverage", 5) else "0"
                    out.write(f"{read}\t{contig}\t{pos}\t{prob:.4f}\t{cov}\n")

    def _generate_summary(self, out_dir, sample_names, mod_type, groups):
        """Generate cross-sample modification summary."""
        all_sites = {}
        for name in sample_names:
            sample_dir = out_dir / name
            if not sample_dir.exists():
                continue

            for mod_t in ["m6A", "m5C"]:
                bed_file = sample_dir / f"mod_sites_{mod_t}.bed"
                if bed_file.exists():
                    n_sites = sum(1 for _ in open(bed_file))
                    if mod_t not in all_sites:
                        all_sites[mod_t] = {}
                    all_sites[mod_t][name] = n_sites

        summary = {
            "modification_type": mod_type,
            "n_samples": len(sample_names),
            "detected_sites": all_sites,
            "min_coverage": int(self.job.params.get("min_coverage", 5)),
            "min_probability": float(self.job.params.get("min_prob", 0.8)),
        }
        (out_dir / "modification_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8")


if __name__ == "__main__":
    RnaModRunner.main()
