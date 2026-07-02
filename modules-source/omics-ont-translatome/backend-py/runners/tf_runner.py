"""转录因子鉴定 runner (PlantTFDB / AnimalTFDB)。

对蛋白序列进行 HMMER 搜索,通过与植物/动物转录因子
HMM 轮廓(profile)比对来鉴定转录因子及所属家族。

参数:
  pep_fasta:      str   - 蛋白序列 FASTA
  organism:       str   - "plant"(默认) 或 "animal"
  db_dir:         str   - TF 数据库目录(含 .hmm 文件)
  evalue:         float - HMMER e-value 阈值,默认 1e-5
  output_prefix:  str   - 输出文件名前缀
  threads:        int   - 默认 8

产出(到 output_subdir):
  <prefix>_tf_results.tsv       - TF 鉴定结果表
  <prefix>_tf_summary.json      - TF 家族分布统计
"""
import json
from collections import Counter
from pathlib import Path

from runners.base import BaseRunner


class TfRunner(BaseRunner):

    def run(self):
        p = self.job.params or {}
        pep = p.get("pep_fasta")
        organism = p.get("organism", "plant").lower()
        db_dir = p.get("db_dir")
        evalue = float(p.get("evalue", 1e-5))
        prefix = p.get("output_prefix", "tf")
        threads = self.effective_threads(int(p.get("threads", 8)))

        if not pep or not Path(pep).exists():
            raise FileNotFoundError(f"蛋白序列文件不存在: {pep}")
        if not db_dir or not Path(db_dir).exists():
            raise FileNotFoundError(f"TF 数据库目录不存在: {db_dir}")

        out_dir = Path(self.output_dir())
        out_dir.mkdir(parents=True, exist_ok=True)

        db_path = Path(db_dir)

        # 查找 HMM 文件:优先找组合 HMM,否则逐家族跑
        if organism == "plant":
            hmm_files = list(db_path.glob("PlantTFDB*.hmm")) or list(db_path.glob("*plant*.hmm"))
            if not hmm_files:
                hmm_files = list(db_path.glob("*.hmm"))
        else:
            hmm_files = list(db_path.glob("AnimalTFDB*.hmm")) or list(db_path.glob("*animal*.hmm"))
            if not hmm_files:
                hmm_files = list(db_path.glob("*.hmm"))

        if not hmm_files:
            raise FileNotFoundError(f"在 {db_dir} 中未找到 .hmm 文件")

        combined_hmm = hmm_files[0]  # 最匹配的文件
        tblout = out_dir / f"{prefix}_hmmscan.tbl"

        self.update(pct=20, stage="HMMER 搜索 TF 结构域",
                    detail=f"{organism.upper()} TF DB, e-value={evalue}")

        # 如果有多个 HMM 文件,合并为一个临时 HMM
        if len(hmm_files) > 1:
            merged_hmm = out_dir / "tf_merged.hmm"
            with open(merged_hmm, "w") as out_f:
                for hmm in hmm_files:
                    out_f.write(hmm.read_text() + "\n")
            combined_hmm = merged_hmm

        self.log(f"  使用 HMM 文件: {combined_hmm}")
        self.run_command([
            "hmmscan", "--cpu", str(threads),
            "-E", str(evalue),
            "--domE", str(evalue),
            "--tblout", str(tblout),
            str(combined_hmm), str(pep),
        ], heartbeat_stage="HMMSCAN TF", indeterminate=True)

        # 解析 tblout 结果
        self.update(pct=60, stage="解析 HMMER 结果", detail="分类 TF 家族")
        tf_results = []
        tf_families = Counter()

        if tblout.exists():
            with open(tblout) as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    cols = line.strip().split()
                    if len(cols) < 18:
                        continue
                    # tblout 格式:
                    # target_name accession query_name accession evalue score bias ...
                    target = cols[0]      # TF family name / HMM name
                    query = cols[2]       # protein ID
                    e_val = float(cols[4])
                    score = float(cols[5])
                    # 解析结构域范围(可选)
                    ali_from = int(cols[14])
                    ali_to = int(cols[15])
                    if e_val <= evalue:
                        tf_results.append({
                            "protein_id": query,
                            "tf_family": target,
                            "evalue": e_val,
                            "score": score,
                            "domain_start": ali_from,
                            "domain_end": ali_to,
                        })
                        tf_families[target] += 1

        # 去重:每条蛋白取最佳 hit(e-value 最小)
        best_per_protein = {}
        for r in tf_results:
            pid = r["protein_id"]
            if pid not in best_per_protein or r["evalue"] < best_per_protein[pid]["evalue"]:
                best_per_protein[pid] = r

        # 写入结果 TSV
        self.update(pct=80, stage="写入结果", detail="生成 TF 鉴定表")
        tsv = out_dir / f"{prefix}_tf_results.tsv"
        with open(tsv, "w") as f:
            f.write("protein_id\ttf_family\tevalue\tscore\tdomain_start\tdomain_end\n")
            for pid in sorted(best_per_protein.keys()):
                r = best_per_protein[pid]
                f.write("{protein_id}\t{tf_family}\t{evalue}\t{score}\t{domain_start}\t{domain_end}\n".format(**r))

        # 写入摘要
        summary = {
            "organism": organism,
            "db_dir": db_dir,
            "evalue": evalue,
            "n_tf_identified": len(best_per_protein),
            "n_families": len(tf_families),
            "family_counts": dict(tf_families.most_common()),
        }
        (out_dir / f"{prefix}_tf_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        self.update(pct=100, stage="完成")
        self.log(f"=== TF 鉴定完成: {len(best_per_protein)} 个转录因子, 涉及 {len(tf_families)} 个家族 ===")


if __name__ == "__main__":
    TfRunner.main()
