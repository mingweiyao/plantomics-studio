"""STAR 基因组索引构建。

支持一次构建**多个 sjdbOverhang** 的索引(混合数据场景)。
每个 overhang 一个独立索引目录:
  <output_subdir>/<overhang>/  (例:star_index/99/, star_index/149/)

参数:
  fasta:           FASTA(.fa/.fa.gz)
  gtf:             GTF
  threads:         默认 8
  sjdb_overhangs:  list[int] 或 "auto"
                    "auto" → 从 sample_fastq_dir 自动扫读长,unique 后全建
  sample_fastq_dir: 给 "auto" 模式扫读长用(通常项目的 raw/)
  
  # 兼容老参数(仍然支持单值)
  sjdb_overhang:   int 或 "auto" — 等价于 sjdb_overhangs=[N]

产出:
  <output_subdir>/<overhang>/genomeParameters.txt + 其余索引文件
  <output_subdir>/index_meta.json — 记录建了哪几个 overhang
"""
import gzip
import json
import math
import shutil
import tempfile
import uuid
from pathlib import Path

from runners.base import BaseRunner
from runners._readlength import detect_dir, unique_overhangs


class StarIndexRunner(BaseRunner):
    
    def run(self):
        params = self.job.params or {}
        fasta = params.get("fasta")
        gtf = params.get("gtf")
        threads = self.effective_threads(int(params.get("threads", 8)))
        genome_sa_nbases = int(params.get("genome_sa_index_nbases", -1))
        # 强制重建(默认否):否则已存在且 sjdbOverhang 匹配的索引会被复用
        force_rebuild = bool(params.get("force_rebuild", False))
        
        # 处理 sjdb_overhangs (复数,新)和 sjdb_overhang (单数,旧)
        overhangs_param = params.get("sjdb_overhangs")
        single_param = params.get("sjdb_overhang")
        sample_fastq_dir = params.get("sample_fastq_dir") or ""
        
        if overhangs_param is not None:
            overhangs = self._resolve_overhangs(
                overhangs_param, sample_fastq_dir
            )
        elif single_param is not None:
            overhangs = self._resolve_overhangs(
                single_param, sample_fastq_dir
            )
        else:
            overhangs = self._resolve_overhangs("auto", sample_fastq_dir)
        
        if not overhangs:
            self.log("没有可用的 sjdb_overhangs,fallback 100")
            overhangs = [100]
        
        self.log(f"将构建 {len(overhangs)} 个索引,sjdbOverhang = {overhangs}")
        
        if not fasta or not Path(fasta).exists():
            raise FileNotFoundError(f"FASTA 不存在: {fasta}")
        if not gtf or not Path(gtf).exists():
            raise FileNotFoundError(f"GTF 不存在: {gtf}")
        
        out_root = self.output_dir()
        
        # 准备 fasta/gtf(若 gz 解一次,所有 overhang 共用)
        self.update(pct=3, stage="准备输入文件")
        # 解到工作目录的 _decompressed/(临时)
        decomp_dir = out_root / "_decompressed"
        decomp_dir.mkdir(parents=True, exist_ok=True)
        fasta_path = self._maybe_gunzip(Path(fasta), decomp_dir)
        gtf_path = self._maybe_gunzip(Path(gtf), decomp_dir)
        
        # 算 genome_sa_index_nbases(对 fasta 一次,所有 overhang 共用)
        if genome_sa_nbases < 0:
            genome_sa_nbases = self._estimate_sa_nbases(fasta_path)
        self.log(f"自动选 genome_sa_index_nbases={genome_sa_nbases}")
        
        # 循环建索引
        for i, overhang in enumerate(overhangs):
            if self.is_cancelled():
                self.log("收到取消,停止后续索引构建")
                break
            
            base_pct = 5 + int(90 * i / len(overhangs))
            self.update(
                pct=base_pct,
                stage=f"建索引 {i+1}/{len(overhangs)} (sjdbOverhang={overhang})",
            )
            self.log(f"=== 建索引 sjdbOverhang={overhang} ===")
            
            sub_dir = out_root / str(overhang)
            sub_dir.mkdir(parents=True, exist_ok=True)

            # 索引复用:已存在且 sjdbOverhang 匹配 → 跳过(除非强制重建)
            if not force_rebuild and self._is_index_complete(sub_dir, overhang):
                self.log(f"复用现有索引(sjdbOverhang={overhang} 已构建完整,跳过)")
                continue

            # cwd=sub_dir + 独立 outTmpDir(WSL/NTFS 友好)
            star_tmp = Path(tempfile.gettempdir()) / f"star_idx_{overhang}_{uuid.uuid4().hex[:8]}"
            if star_tmp.exists():
                shutil.rmtree(star_tmp, ignore_errors=True)
            try:
                self.run_command(
                    ["STAR",
                     "--runMode", "genomeGenerate",
                     "--genomeDir", str(sub_dir),
                     "--genomeFastaFiles", str(fasta_path),
                     "--sjdbGTFfile", str(gtf_path),
                     "--sjdbOverhang", str(overhang),
                     "--runThreadN", str(threads),
                     "--genomeSAindexNbases", str(genome_sa_nbases),
                     "--outTmpDir", str(star_tmp)],
                    timeout=14400,
                    cwd=str(sub_dir),
                )
            finally:
                if star_tmp.exists():
                    shutil.rmtree(star_tmp, ignore_errors=True)
        
        # 写 meta
        meta = {
            "overhangs_built": overhangs,
            "fasta": fasta,
            "gtf": gtf,
            "n_indexes": len(overhangs),
        }
        with open(out_root / "index_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        
        # 清理解压临时
        if decomp_dir.exists():
            shutil.rmtree(decomp_dir, ignore_errors=True)
        
        self.update(pct=100, stage="完成")

    @staticmethod
    def _is_index_complete(sub_dir: Path, overhang: int) -> bool:
        """判断 sub_dir 里是否已有一个**完整且 sjdbOverhang 匹配**的 STAR 索引。

        条件:
          - SAindex 存在(STAR 构建完成的标志文件)
          - genomeParameters.txt 存在且其中 sjdbOverhang == 指定值
        二者都满足才算可复用,避免半成品索引或 overhang 不符的索引被误用。
        """
        saindex = sub_dir / "SAindex"
        gp = sub_dir / "genomeParameters.txt"
        if not saindex.exists() or not gp.exists():
            return False
        try:
            for line in gp.read_text(errors="ignore").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "sjdbOverhang":
                    return int(parts[1]) == int(overhang)
        except Exception:
            return False
        return False

        """把 raw(可以是 'auto'/单 int/list)解析成 list[int]。"""
        # 列表
        if isinstance(raw, list):
            out = []
            for v in raw:
                try:
                    out.append(int(v))
                except (TypeError, ValueError):
                    pass
            return sorted(set(out))
        
        # auto:扫 fastq 拿读长
        if raw in (None, "", "auto", 0, "0"):
            if not sample_fastq_dir:
                self.log("auto 模式但没给 sample_fastq_dir,不知怎么扫读长")
                return []
            d = Path(sample_fastq_dir)
            if not d.is_dir():
                self.log(f"sample_fastq_dir 不存在: {d}")
                return []
            self.log(f"自动扫描 {d} 下 fastq 读长...")
            records = detect_dir(d)
            if not records:
                self.log("没扫到任何 fastq")
                return []
            for r in records:
                files_str = ", ".join(Path(f).name for f in r["files"])
                self.log(
                    f"  {r['sample']} ({files_str}): "
                    f"实际 {r['raw_read_length']}bp → "
                    f"归档 {r['read_length']}bp → overhang={r['sjdb_overhang']}"
                )
            ohs = unique_overhangs(records)
            self.log(f"unique overhangs: {ohs}")
            # 防御:极端值校正
            ohs = [self._sanitize_overhang(v) for v in ohs]
            return sorted(set(ohs))
        
        # 单 int
        try:
            return [int(raw)]
        except (TypeError, ValueError):
            self.log(f"sjdb_overhang 参数无效: {raw},用默认 100")
            return [100]
    
    def _sanitize_overhang(self, v: int) -> int:
        if v < 25:
            return 50
        if v > 300:
            return 150
        return v
    
    def _estimate_sa_nbases(self, fasta_path: Path) -> int:
        """genome_sa_index_nbases = min(14, log2(GenomeLength)/2 - 1)"""
        size = fasta_path.stat().st_size
        # 粗略估算(实际 fasta 多 10% 头),够用
        n = max(7, min(14, int(math.log2(size) / 2 - 1)))
        return n
    
    def _maybe_gunzip(self, src: Path, work_dir: Path) -> Path:
        if not src.name.endswith(".gz"):
            return src
        out = work_dir / src.stem  # foo.fa.gz -> foo.fa
        if out.exists():
            return out
        self.log(f"解压 {src.name} → {out.name}")
        with gzip.open(src, "rb") as fin, open(out, "wb") as fout:
            shutil.copyfileobj(fin, fout)
        return out


if __name__ == "__main__":
    StarIndexRunner.main()
