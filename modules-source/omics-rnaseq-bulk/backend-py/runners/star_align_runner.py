"""STAR 比对 runner。

参数:
  index_dir: str               - STAR 索引目录(由 star_index runner 产生,或用户提供)
  samples: list[dict]          - 样本列表
    [
      {"name": "S1", "r1": "/path/...", "r2": "/path/..."},  # paired
      {"name": "S2", "r1": "/path/..."},                      # single
    ]
  threads: int                 - 默认 8
  out_sam_type: str            - "BAM SortedByCoordinate"(默认)
  quant_mode: str              - "GeneCounts"(默认)— 比对时同时出 ReadsPerGene
  二参数: 用 STAR 推荐用法

产出:
  <output_subdir>/<name>/<name>.Aligned.sortedByCoord.out.bam
  <output_subdir>/<name>/<name>.ReadsPerGene.out.tab
  <output_subdir>/<name>/<name>.Log.final.out  (STAR 报告)
  <output_subdir>/manifest.json

注意:WSL 环境下输出可能在 /mnt/d 等 NTFS,STAR 要建 FIFO 不支持,所以用
--outTmpDir 指到 /tmp/...(Linux 文件系统),最终输出还是写指定的 outFileNamePrefix。
"""
import json
import shutil
import tempfile
import uuid
from pathlib import Path

from runners.base import BaseRunner


class StarAlignRunner(BaseRunner):
    
    def run(self):
        params = self.job.params or {}
        # 新参数:index_root 是 star_index/ 这一层(下面有 99/, 149/ 多个子索引)
        index_root = params.get("index_root")
        # 老参数:index_dir 直接是某一个索引目录(向后兼容)
        index_dir = params.get("index_dir")
        samples = params.get("samples", [])
        threads = self.effective_threads(int(params.get("threads", 8)))
        quant_mode = params.get("quant_mode", "GeneCounts")
        
        if not samples:
            raise ValueError("未提供 samples")
        
        # 决定可用索引列表 [{overhang, path}, ...]
        # 兼容三种:
        #   1. index_root 指向 star_index/,下面有 99/ 149/(新)
        #   2. index_root 指向 star_index/,下面**没有**子目录,直接是单索引(老版本)
        #   3. index_dir 直接给(老兼容)
        available_indexes = self._discover_indexes(index_root, index_dir)
        if not available_indexes:
            raise FileNotFoundError(
                f"找不到 STAR 索引(index_root={index_root}, index_dir={index_dir})"
            )
        self.log(f"可用索引: {[(o, str(p)) for o, p in available_indexes]}")
        
        out_dir = self.output_dir()
        manifest = {"samples": []}
        
        total = len(samples)
        for i, s in enumerate(samples):
            if self.is_cancelled():
                break
            name = s.get("name") or f"sample_{i + 1}"
            r1 = s.get("r1")
            r2 = s.get("r2")
            
            if not r1 or not Path(r1).exists():
                self.log(f"!! {name}: r1 ({r1}) 不存在,跳过")
                continue
            
            self.update(
                pct=int(i / total * 100),
                stage=f"比对 {i + 1}/{total}: {name}",
            )
            self.log(f"=== {name} STAR 比对 ===")
            
            # 给这个样本选索引 — 探测 r1 读长,取最接近的
            chosen_index = self._pick_index_for_sample(r1, r2, available_indexes)
            self.log(f"  使用索引: {chosen_index}")
            
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
                # BAM 排序桶数(默认 50)。线程多时 50 个桶 × N 线程
                # 会打开几千个文件,撑爆 ulimit -n。降到 20 减少文件句柄。
                "--outBAMsortingBinsN", "20",
            ]
            
            # WSL/NTFS 环境:STAR 要建 FIFO,NTFS 不支持。
            # 把 tmp 指到 Linux fs(/tmp)。STAR 要求这个目录"不存在"才会建,
            # 我们用 uuid 随机名避免冲突。
            star_tmp = Path(tempfile.gettempdir()) / f"star_{uuid.uuid4().hex[:12]}"
            # STAR 自己会创建 star_tmp,我们不能预先创建它,但可以确保它不存在
            if star_tmp.exists():
                shutil.rmtree(star_tmp, ignore_errors=True)
            cmd += ["--outTmpDir", str(star_tmp)]
            
            try:
                # STAR 比对单样本是一次长调用,中途无法估算百分比 →
                # indeterminate,前端显示流动动画 + 心跳证明活着(不会卡死)
                self.run_command(
                    cmd, timeout=14400, cwd=str(sample_dir),
                    indeterminate=True,
                    heartbeat_stage=f"STAR 比对 {i + 1}/{total}: {name}",
                )
            finally:
                # 清理 tmp(STAR 失败时可能留垃圾)
                if star_tmp.exists():
                    shutil.rmtree(star_tmp, ignore_errors=True)
            
            bam = sample_dir / f"{name}.Aligned.sortedByCoord.out.bam"
            counts = sample_dir / f"{name}.ReadsPerGene.out.tab"
            log_final = sample_dir / f"{name}.Log.final.out"
            
            manifest["samples"].append({
                "name": name,
                "bam": str(bam.relative_to(out_dir)) if bam.exists() else None,
                "counts_tab": str(counts.relative_to(out_dir)) if counts.exists() else None,
                "log_final": str(log_final.relative_to(out_dir)) if log_final.exists() else None,
                "index_used": str(chosen_index),
            })
            self.log(f"=== {name} 完成 ===")
        
        with open(out_dir / "manifest.json", "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        
        self.update(pct=100, stage="完成")
    
    def _discover_indexes(self, index_root, index_dir):
        """返回 [(overhang_or_None, Path)],按可用顺序。"""
        from runners._readlength import detect_one_fastq  # 复用
        
        # 优先 index_root(新格式)
        if index_root and Path(index_root).is_dir():
            root = Path(index_root)
            # 1. 看根目录是不是直接索引(老版本兼容)
            if (root / "genomeParameters.txt").exists():
                # 试图从 genomeParameters.txt 读 sjdbOverhang
                overhang = self._read_overhang_from_index(root)
                self.log(f"index_root 是单索引(老版本),overhang={overhang}")
                return [(overhang, root)]
            # 2. 看子目录(新版本)
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
        
        # 老兼容:index_dir 直接给
        if index_dir and Path(index_dir).is_dir():
            if (Path(index_dir) / "genomeParameters.txt").exists():
                overhang = self._read_overhang_from_index(Path(index_dir))
                return [(overhang, Path(index_dir))]
        
        return []
    
    def _read_overhang_from_index(self, index_path: Path) -> int | None:
        """从 genomeParameters.txt 读 sjdbOverhang。"""
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
        """根据样本读长选最接近的索引。
        
        available = [(overhang, path), ...]
        R1/R2 都探测一下,取最大值,归到标准档,再选索引。
        """
        from runners._readlength import (
            detect_one_fastq, closest_overhang, normalize_to_standard
        )
        
        # 只有一个就直接用
        if len(available) == 1:
            return available[0][1]
        
        # 探测 R1(+ R2 如有)的最大读长
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
        self.log(
            f"  样本读长 探测={raw_max}bp → 归档={std_L}bp"
            + (f" (R1+R2: {per_file})" if len(per_file) > 1 else "")
        )
        
        overhangs = [oh for oh, _ in available if oh is not None]
        if not overhangs:
            return available[0][1]
        chosen_oh = closest_overhang(std_L, overhangs)
        for oh, p in available:
            if oh == chosen_oh:
                self.log(f"  → 选 sjdbOverhang={chosen_oh} ({p})")
                return p
        return available[0][1]


if __name__ == "__main__":
    StarAlignRunner.main()
