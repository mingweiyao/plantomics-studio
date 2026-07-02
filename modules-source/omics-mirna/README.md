# omics-mirna - miRNA 测序分析模块

基于 miRDeep2 的标准 miRNA 分析流程模块,用于 PlantOmics Studio。

## 功能

| 步骤 | 说明 |
|------|------|
| SRA 下载 | 使用 prefetch + fasterq-dump 下载和解压 SRA 数据 |
| FastQC | 测序数据质量评估 |
| fastp | 接头去除与质量过滤 |
| Bowtie 比对 | bowtie 比对 + miRDeep2 mapper.pl (collapsed.fa + .arf) |
| miRDeep2 预测 | miRNA 鉴定与 novel miRNA 发现 |
| miRNA 定量 | quantifier.pl 表达定量 |
| 合并矩阵 | 合并多样本 counts 为表达矩阵 |
| CPM/RPM 标准化 | 文库大小标准化 |
| 差异表达 | DESeq2 差异表达分析 |
| 靶基因预测 | miRanda 靶基因预测 |
| GO/KEGG 富集 | 靶基因功能富集分析 |
| 聚类分析 | miRNA 表达模式聚类(层次聚类 + 热图) |
| 共表达网络 | miRNA-mRNA 共表达网络分析 |

## 依赖

- Python 3.11 (FastAPI, uvicorn)
- R 4.4 (plumber, DESeq2, ggplot2)
- SRA Toolkit (prefetch, fasterq-dump)
- fastp, FastQC, MultiQC
- bowtie, samtools
- miRDeep2, miRanda

## 构建

```bash
bash scripts/build-deb.sh
```
