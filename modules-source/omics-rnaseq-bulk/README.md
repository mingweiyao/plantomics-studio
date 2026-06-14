# omics-rnaseq-bulk

PlantOmics Studio 的 Bulk RNA-seq 转录组分析模块。

## 这是什么

提供从 fastq/SRA 到差异表达的完整 bulk 转录组分析:
- 上游:STAR 比对 + featureCounts/Salmon 量化
- 下游:DESeq2 差异表达 + GO/KEGG 富集 + WGCNA

## 安装

需要先装主程序 `plantomics-studio`(>= 1.0.0)。然后:

```bash
sudo apt install ./plantomics-module-rnaseq-bulk_X.X.X_amd64.deb
```

或者在主程序的"模块"页选择"从本地 .deb 安装"。

装到 `/opt/plantomics-studio/modules/omics-rnaseq-bulk/`,大约 3-4 GB。

## 构建

```bash
bash scripts/build-deb.sh
```

第一次构建因为要下载所有 conda 包,需要 10-20 分钟。

`--skip-env` 跳过 env 创建(复用 `build/conda-env/`)。

## 模块协议

参见主程序文档 `docs/MODULE_DEVELOPMENT.md`。
