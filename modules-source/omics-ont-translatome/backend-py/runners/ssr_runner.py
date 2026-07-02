"""SSR(微卫星)分析 runner。

对转录本序列或基因组序列进行简单重复序列(SSR)检测。

参数:
  fasta:         str   - 输入序列 FASTA
  min_repeats:   dict  - 各重复单元类型的最小重复次数
                         默认 {1:10, 2:6, 3:5, 4:5, 5:5, 6:5}
  output_prefix: str   - 输出文件名前缀
  method:        str   - "misa"(默认) 或 "ssrit"

产出(到 output_subdir):
  <prefix>_ssr_results.tsv     - SSR 检测结果
  <prefix>_ssr_statistics.txt  - MISA 统计(仅 misa)
  <prefix>_ssr_summary.json    - SSR 汇总统计
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class SsrRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        fasta = p.get("fasta")
        min_repeats = p.get("min_repeats", {1: 10, 2: 6, 3: 5, 4: 5, 5: 5, 6: 5})
        prefix = p.get("output_prefix", "ssr")
        method = p.get("method", "misa").lower()

        if not fasta or not Path(fasta).exists():
            raise FileNotFoundError(f"FASTA 不存在: {fasta}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        if method == "misa":
            self._run_misa(fasta, min_repeats, prefix, out_dir)
        elif method == "ssrit":
            self._run_ssrit(fasta, min_repeats, prefix, out_dir)
        else:
            raise ValueError(f"不支持的方法: {method}, 请选择 misa 或 ssrit")

    def _run_misa(self, fasta, min_repeats, prefix, out_dir):
        """MISA 微卫星检测。"""
        self.update(pct=10, stage="MISA SSR 检测", detail="准备配置文件")

        # 写 MISA 参数文件
        ini_path = out_dir / f"{prefix}_misa.ini"
        with open(ini_path, "w") as f:
            f.write("# MISA parameter file\n")
            # 定义各重复单元的最小次数
            defs = min_repeats if isinstance(min_repeats, dict) else {1: 10, 2: 6, 3: 5, 4: 5, 5: 5, 6: 5}
            for unit_len in [1, 2, 3, 4, 5, 6]:
                n = defs.get(unit_len, 10 if unit_len == 1 else 6 if unit_len == 2 else 5)
                f.write(f"unit_size_{unit_len}={n}\n")
            # 复合微卫星的最大间隔
            f.write("max_complex_gap=100\n")

        self.update(pct=30, stage="MISA SSR 检测", detail="运行 misa")

        # MISA 输出文件命名约定: <input_basename>.misa
        # misa <fasta> 会生成 <fasta>.misa 文件
        self.run_command([
            "misa", str(fasta), str(ini_path),
        ], heartbeat_stage="MISA", indeterminate=True)

        # MISA 生成 <input_basename>.misa 和 <input_basename>.statistics
        fasta_path = Path(fasta)
        misa_out = fasta_path.with_suffix(fasta_path.suffix + ".misa")
        stats_file = fasta_path.with_suffix(fasta_path.suffix + ".statistics")

        # 如果 MISA 在新版命名约定下输出到输入路径同目录,复制到输出目录
        result_file = out_dir / f"{prefix}_ssr_results.tsv"
        stats_out = out_dir / f"{prefix}_ssr_statistics.txt"

        # 解析结果
        ssr_records = []
        if misa_out.exists():
            import shutil
            shutil.copy2(misa_out, result_file)
            # 解析 SSR 记录
            with open(misa_out) as f:
                for line in f:
                    if line.startswith("#") or line.startswith("ID"):
                        continue
                    cols = line.strip().split("\t")
                    if len(cols) >= 6:
                        ssr_records.append({
                            "id": cols[0],
                            "motif": cols[1],
                            "type": self._classify_motif(cols[1]),
                            "start": int(cols[2]),
                            "end": int(cols[3]),
                            "repeats": int(cols[4]),
                            "length": int(cols[5]),
                        })
        if stats_file.exists():
            import shutil
            shutil.copy2(stats_file, stats_out)

        # 生成汇总
        summary = self._make_summary(ssr_records, prefix, result_file, stats_out)
        (out_dir / f"{prefix}_ssr_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== MISA SSR 分析完成: {summary['n_total_ssr']} 个 SSR 检出 ===")

    def _run_ssrit(self, fasta, min_repeats, prefix, out_dir):
        """SSRIT 微卫星检测。"""
        self.update(pct=30, stage="SSRIT SSR 检测", detail="运行 SSRIT")

        defs = min_repeats if isinstance(min_repeats, dict) else {1: 10, 2: 6, 3: 5, 4: 5, 5: 5, 6: 5}
        # SSRIT 命令行格式: ssrit <fasta> <min_mono> <min_di> <min_tri> <min_tetra> <min_penta> <min_hexa>
        cmd = [
            "ssrit",
            str(fasta),
            str(defs.get(1, 10)),
            str(defs.get(2, 6)),
            str(defs.get(3, 5)),
            str(defs.get(4, 5)),
            str(defs.get(5, 5)),
            str(defs.get(6, 5)),
        ]

        result_file = out_dir / f"{prefix}_ssr_results.tsv"
        with open(result_file, "w") as out_f:
            import subprocess
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for line in proc.stdout:
                out_f.write(line)
            proc.wait()

        # 解析 SSRIT 输出
        ssr_records = []
        if result_file.exists():
            with open(result_file) as f:
                for line in f:
                    if line.startswith("#") or line.strip() == "":
                        continue
                    cols = line.strip().split("\t")
                    if len(cols) >= 5:
                        ssr_records.append({
                            "id": cols[0],
                            "motif": cols[2],
                            "type": self._classify_motif(cols[2]),
                            "start": int(cols[3]) if cols[3].isdigit() else 0,
                            "end": int(cols[4]) if cols[4].isdigit() else 0,
                            "repeats": int(cols[5]) if len(cols) > 5 and cols[5].isdigit() else 0,
                            "length": 0,
                        })

        summary = self._make_summary(ssr_records, prefix, result_file, None)
        (out_dir / f"{prefix}_ssr_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== SSRIT SSR 分析完成: {summary['n_total_ssr']} 个 SSR 检出 ===")

    def _classify_motif(self, motif: str) -> str:
        """根据基序长度分类。"""
        motif = motif.upper().replace(" ", "")
        l = len(motif)
        if l == 1:
            return "mono"
        elif l == 2:
            return "di"
        elif l == 3:
            return "tri"
        elif l == 4:
            return "tetra"
        elif l == 5:
            return "penta"
        elif l >= 6:
            return "hexa"
        return "other"

    def _make_summary(self, records, prefix, result_file, stats_file):
        """生成汇总统计。"""
        type_counts = Counter(r["type"] for r in records)
        motif_counts = Counter(r["motif"].upper() for r in records)
        top_motifs = motif_counts.most_common(10)

        return {
            "method": "misa" if "misa" in str(result_file) else "ssrit",
            "n_total_ssr": len(records),
            "type_counts": dict(type_counts),
            "top_motifs": [{"motif": m, "count": c} for m, c in top_motifs],
            "output_prefix": prefix,
            "result_file": str(result_file),
            "statistics_file": str(stats_file) if stats_file and stats_file.exists() else None,
        }


if __name__ == "__main__":
    SsrRunner.main()
