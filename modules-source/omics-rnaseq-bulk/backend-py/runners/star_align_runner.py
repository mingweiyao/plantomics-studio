"""STAR 比对 runner，支持多样本并行。

参数:
  index_root: str              - STAR 索引根目录
  index_dir: str               - STAR 索引目录(老兼容)
  samples: list[dict]          - 样本列表
  threads: int                 - 每样本线程数,默认 8
  parallel: int                - 同时比对数,默认 2
  quant_mode: str              - "GeneCounts"(默认)

产出:
  <output_subdir>/<name>/<name>.Aligned.sortedByCoord.out.bam
  <output_subdir>/<name>/<name>.ReadsPerGene.out.tab
  <output_subdir>/<name>/<name>.Log.final.out
  <output_subdir>/manifest.json
"""
import json
import shutil
import tempfile
import uuid
import threading
from pathlib import Path

from runners.base import BaseRunner


class StarAlignRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        index_root = params.get("index_root")
        index_dir = params.get("index_dir")
        samples = params.get("samples", [])
        threads = self.effective_threads(int(params.get("threads", 8)))
        parallel = int(params.get("parallel", 2))
        quant_mode = params.get("quant_mode", "GeneCounts")

        if not samples:
            raise ValueError("未提供 samples")

        available_indexes = self._discover_indexes(index_root, index_dir)
        if not available_indexes:
            raise FileNotFoundError(
                f"找不到 STAR 索引(index_root={index_root}, index_dir={index_dir})"
            )
        self.log(f"可用索引: {[(o, str(p)) for o, p in available_indexes]}")

        # 并行数 × 单样本线程数 clamp 到全局 CPU 配额
        parallel, threads = self.effective_parallel_alloc(parallel, threads)

        out_dir = self.output_dir()
        manifest = {"samples": []}
        lock = threading.Lock()

        def process_one(s):
            name = s.get("name") or "?"
            r1 = s.get("r1")
            r2 = s.get("r2")

            if not r1 or not Path(r1).exists():
                self.log(f"!! {name}: r1 ({r1}) 不存在,跳过")
                return

            chosen_index = self._pick_index_for_sample(r1, r2, available_indexes)

            sample_dir = out_dir / name
            sample_dir.mkdir(parents=True, exist_ok=True)

            read_files = [r1]
            if r2 and Path(r2).exists():
                read_files.append(r2)

            cmd = [
                "STAR",
                "--runThreadN", str(threads),
                "--genomeDir", str(chosen_index),
                "--readFilesIn"] + read_files + [
                "--readFilesCommand", "zcat" if r1.endswith(".gz") else "cat",
                "--outFileNamePrefix", str(sample_dir) + "/" + name + ".",
                "--outSAMtype", "BAM", "SortedByCoordinate",
                "--quantMode", quant_mode,
                "--outSAMunmapped", "Within",
                "--outFilterMultimapNmax", "20",
                "--outBAMsortingBinsN", "20",
            ]

            star_tmp = Path(tempfile.gettempdir()) / f"star_{uuid.uuid4().hex[:12]}"
            if star_tmp.exists():
                shutil.rmtree(star_tmp, ignore_errors=True)
            cmd += ["--outTmpDir", str(star_tmp)]

            try:
                self.run_command(
                    cmd, timeout=14400, cwd=str(sample_dir),
                    indeterminate=True,
                    heartbeat_stage=f"STAR: {name}",
                )
            finally:
                if star_tmp.exists():
                    shutil.rmtree(star_tmp, ignore_errors=True)

            bam = sample_dir / f"{name}.Aligned.sortedByCoord.out.bam"
            counts = sample_dir / f"{name}.ReadsPerGene.out.tab"
            log_final = sample_dir / f"{name}.Log.final.out"

            with lock:
                manifest["samples"].append({
                    "name": name,
                    "bam": str(bam.relative_to(out_dir)) if bam.exists() else None,
                    "counts_tab": str(counts.relative_to(out_dir)) if counts.exists() else None,
                    "log_final": str(log_final.relative_to(out_dir)) if log_final.exists() else None,
                    "index_used": str(chosen_index),
                })
            self.log(f"=== {name} 完成 ===")

        self.run_in_parallel(
            func=process_one,
            items=samples,
            workers=parallel,
            desc=f"STAR(并行 {parallel} 样本,每样本 {threads} 线程)",
        )

        with open(out_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)

        self.update(pct=100, stage="完成")

    def _discover_indexes(self, index_root, index_dir):
        """返回 [(overhang_or_None, Path)],按可用顺序。"""
        from runners._readlength import detect_one_fastq

        if index_root and Path(index_root).is_dir():
            root = Path(index_root)
            if (root / "genomeParameters.txt").exists():
                overhang = self._read_overhang_from_index(root)
                self.log(f"index_root 是单索引(老版本),overhang={overhang}")
                return [(overhang, root)]
            sub_indexes = []
            for sub in sorted(root.iterdir()):
                if sub.is_dir() and (sub / "genomeParameters.txt").exists():
                    try:
                        overhang = int(sub.name)
                    except ValueError:
                        overhang = self._read_overhang_from_index(sub)
                    sub_indexes.append((overhang, sub))
            if sub_indexes:
                return sub_indexes

        if index_dir and Path(index_dir).is_dir():
            if (Path(index_dir) / "genomeParameters.txt").exists():
                overhang = self._read_overhang_from_index(Path(index_dir))
                return [(overhang, Path(index_dir))]

        return []

    def _read_overhang_from_index(self, index_path: Path) -> int | None:
        gp = index_path / "genomeParameters.txt"
        if not gp.exists():
            return None
        try:
            for line in gp.read_text().splitlines():
                if line.startswith("sjdbOverhang"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
        except Exception:
            pass
        return None

    def _pick_index_for_sample(self, r1_path: str, r2_path: str | None,
                                  available: list) -> Path:
        from runners._readlength import (
            detect_one_fastq, closest_overhang, normalize_to_standard
        )

        if len(available) == 1:
            return available[0][1]

        per_file = []
        for p in (r1_path, r2_path):
            if p and Path(p).exists():
                L = detect_one_fastq(Path(p), n_reads=200)
                if L is not None:
                    per_file.append(L)

        if not per_file:
            self.log(f"  读长探测失败,用第一个索引")
            return available[0][1]

        raw_max = max(per_file)
        std_L = normalize_to_standard(raw_max)
        overhangs = [oh for oh, _ in available if oh is not None]
        if not overhangs:
            return available[0][1]
        chosen_oh = closest_overhang(std_L, overhangs)
        for oh, p in available:
            if oh == chosen_oh:
                self.log(f"  → 选 sjdbOverhang={chosen_oh} ({p})")
                return p
        return available[0][1]
