"""merge_counts runner — 把多个单样本 counts 合并成矩阵。

参数:
  counts_dir:    扫这个目录下所有 *.tsv(优先级低)
  counts_files:  显式指定要合并的文件列表(优先级高)
  output_name:   输出文件名,默认 "counts_merged.tsv"

单样本文件格式:gene_id\\t<sample_name>(2 列)
合并后格式:gene_id\\tsample1\\tsample2\\t... (outer join,缺失补 0)

输出:
  <output_subdir>/<output_name>
  <output_subdir>/merge_meta.json
"""
import json
from pathlib import Path

from runners.base import BaseRunner


# 排除这些文件名(避免循环合并 / 误读元数据)
EXCLUDED_BASENAMES = {
    "all_genes.tsv",
    "summary.tsv",
    "counts_merged.tsv",
    "gene_lengths.tsv",
}


class MergeCountsRunner(BaseRunner):
    
    def run(self):
        params = self.job.params or {}
        counts_dir = params.get("counts_dir")
        counts_files = params.get("counts_files") or []
        output_name = params.get("output_name") or "counts_merged.tsv"
        
        # 决定要合并哪些文件
        files: list[Path] = []
        if counts_files:
            files = [Path(f) for f in counts_files]
            self.log(f"用户指定 {len(files)} 个文件")
        elif counts_dir:
            d = Path(counts_dir)
            if not d.is_dir():
                raise FileNotFoundError(f"counts_dir 不是目录: {counts_dir}")
            all_tsvs = sorted(d.glob("*.tsv"))
            files = [
                p for p in all_tsvs
                if p.name not in EXCLUDED_BASENAMES
                and p.name != output_name
            ]
            self.log(
                f"从 {counts_dir} 扫到 {len(files)} 个 tsv "
                f"(排除元数据 + 已合并文件后)"
            )
        else:
            raise ValueError("必须提供 counts_dir 或 counts_files 之一")
        
        if not files:
            raise ValueError("没有要合并的文件")
        
        self.log("=== merge_counts 开始 ===")
        
        self.update(pct=20, stage="读各样本 counts")
        
        # gene_id -> {sample_name: count_str}
        gene_data: dict[str, dict[str, str]] = {}
        # 记录加入顺序
        sample_order: list[str] = []
        
        for i, f in enumerate(files):
            if not f.exists():
                self.log(f"跳过(不存在): {f}")
                continue
            
            self.update(
                pct=int(20 + 50 * i / len(files)),
                stage=f"读 {i+1}/{len(files)}: {f.name}",
            )
            
            with open(f, "r", encoding="utf-8", errors="replace") as fp:
                header_line = fp.readline().rstrip("\n").rstrip("\r")
                header = header_line.split("\t")
                if len(header) < 2:
                    self.log(f"跳过(列数不够): {f.name}")
                    continue
                
                # 第二列名 = 样本名
                sample = header[1]
                
                # 处理重复(后写覆盖前面的,但样本顺序保持原位)
                if sample in sample_order:
                    self.log(f"警告:样本 {sample} 重复,以最新为准")
                else:
                    sample_order.append(sample)
                
                self.log(f"  {f.name} → 样本 {sample}")
                
                for line in fp:
                    line = line.rstrip("\n").rstrip("\r")
                    if not line:
                        continue
                    cols = line.split("\t")
                    if len(cols) < 2:
                        continue
                    gene_id = cols[0]
                    count = cols[1]
                    
                    gene_data.setdefault(gene_id, {})[sample] = count
        
        if not sample_order:
            raise ValueError("没有读到任何有效样本")
        
        n_genes = len(gene_data)
        n_samples = len(sample_order)
        self.log(f"合并后:{n_genes} 基因 × {n_samples} 样本")
        
        self.update(pct=80, stage="写出合并矩阵")
        
        out_dir = self.output_dir()
        out_path = out_dir / output_name
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("gene_id\t" + "\t".join(sample_order) + "\n")
            for gene_id in sorted(gene_data.keys()):
                row = gene_data[gene_id]
                # 缺失填 0
                vals = [row.get(s, "0") for s in sample_order]
                f.write(gene_id + "\t" + "\t".join(vals) + "\n")
        
        self.log(f"写入 {out_path}")
        
        meta = {
            "output_file": str(out_path),
            "n_genes": n_genes,
            "n_samples": n_samples,
            "samples": sample_order,
            "source_files": [str(f) for f in files],
        }
        with open(out_dir / "merge_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        
        self.update(pct=100, stage="完成")


if __name__ == "__main__":
    MergeCountsRunner.main()
