"""SRA 处理 runner - 下载 + 解压。

支持三种模式(组合):
  1. accessions: list[str]  - 用 prefetch 下载 + fasterq-dump 解压
  2. sra_files:  list[str]  - 已有 .sra 文件,只解压
  3. scan_dir:   str        - 扫目录里所有 .sra,只解压

每个 accession 独立,适合并行。

参数:
  accessions, sra_files, scan_dir 三选一(或组合)
  threads_per_sample: int  - 每个 fasterq-dump 的线程数,默认 4
  parallel:           int  - 同时跑几个,默认 2

输出:
  <output>/sra/<acc>.sra      (prefetch 产出)
  <output>/<acc>/<acc>_*.fastq.gz  (fasterq-dump + pigz 产出)
"""
import shutil
import tempfile
import uuid
from pathlib import Path

from runners.base import BaseRunner


class SraDownloadRunner(BaseRunner):

    def run(self):
        params = self.job.params or {}
        accessions = params.get("accessions", []) or []
        sra_files = params.get("sra_files", []) or []
        scan_dir = params.get("scan_dir") or ""

        threads_per = int(params.get(
            "threads_per_sample",
            params.get("threads", 4)
        ))
        parallel = int(params.get("parallel", 2))
        parallel, threads_per = self.effective_parallel_alloc(parallel, threads_per)

        out_dir = self.output_dir()

        # 收集要解压的 .sra(从 scan_dir + sra_files 来)
        sra_to_extract: list[Path] = []
        if scan_dir:
            d = Path(scan_dir)
            if d.is_dir():
                found = list(d.rglob("*.sra"))
                self.log(f"扫描 {scan_dir} 找到 {len(found)} 个 .sra")
                sra_to_extract.extend(found)
        for f in sra_files:
            p = Path(f)
            if p.exists():
                sra_to_extract.append(p)
            else:
                self.log(f"跳过不存在的 sra_file: {f}")
        sra_to_extract = list(set(sra_to_extract))  # 去重

        # 1. 先下载(并行)
        downloaded_sra: list[Path] = []
        if accessions:
            self.log(f"=== 阶段 1: 下载 {len(accessions)} 个 accession ===")

            def download_one(acc):
                sra = self._download_one(acc, out_dir)
                downloaded_sra.append(sra)

            self.run_in_parallel(
                func=download_one,
                items=accessions,
                workers=parallel,
                desc=f"prefetch 下载(并行 {parallel})",
            )
            sra_to_extract.extend(downloaded_sra)

        # 2. 解压(并行)
        if sra_to_extract:
            self.log(f"=== 阶段 2: 解压 {len(sra_to_extract)} 个 .sra ===")

            def extract_one(sra: Path):
                self._extract_one(sra, out_dir, threads_per)

            self.run_in_parallel(
                func=extract_one,
                items=sra_to_extract,
                workers=parallel,
                desc=f"fasterq-dump 解压(每样本 {threads_per} 线程,并行 {parallel})",
            )
        else:
            self.log("没有要解压的 .sra")

    def _download_one(self, acc: str, out_dir: Path) -> Path:
        """prefetch 下载到 <out_dir>/sra/<acc>.sra"""
        sra_dir = out_dir / "sra"
        sra_dir.mkdir(parents=True, exist_ok=True)

        self.run_command(
            ["prefetch", acc, "--output-directory", str(sra_dir),
             "--max-size", "100g"],
            timeout=7200,
        )
        # prefetch 写到 sra_dir/<acc>/<acc>.sra,提到 sra_dir/<acc>.sra
        nested = sra_dir / acc / f"{acc}.sra"
        flat = sra_dir / f"{acc}.sra"
        if nested.exists():
            nested.replace(flat)
            try:
                (sra_dir / acc).rmdir()
            except OSError:
                pass
        if flat.exists():
            return flat
        raise FileNotFoundError(f"prefetch 没产出 .sra: {acc}")

    def _extract_one(self, sra: Path, out_dir: Path, threads: int):
        """fasterq-dump + pigz。"""
        if not sra.exists():
            raise FileNotFoundError(f".sra 不存在: {sra}")

        acc = sra.stem
        sample_dir = out_dir / acc
        sample_dir.mkdir(parents=True, exist_ok=True)

        # ---- 步骤 1: fasterq-dump 解压 ----
        self.log(f"[{acc}] 1/2 fasterq-dump 解压")
        tmp_dir = Path(tempfile.gettempdir()) / f"fasterq_{acc}_{uuid.uuid4().hex[:8]}"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.run_command(
                ["fasterq-dump",
                 "--threads", str(threads),
                 "--split-files",
                 "--outdir", str(sample_dir),
                 "--temp", str(tmp_dir),
                 str(sra)],
                timeout=14400,
            )
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # ---- 步骤 2: pigz 压缩 ----
        fastqs = list(sample_dir.glob(f"{acc}*.fastq"))
        if fastqs:
            self.log(f"[{acc}] 2/2 pigz 压缩 {len(fastqs)} 个 fastq")
            for fastq in fastqs:
                self.run_command(
                    ["pigz", "-p", str(threads), str(fastq)],
                    timeout=3600,
                )
        else:
            self.log(f"[{acc}] !! fasterq-dump 没产出 fastq 文件")


if __name__ == "__main__":
    SraDownloadRunner.main()
