# PlantOmics Studio 模块设计方案 v2

> 基于 ref/ 目录下的 8 份贝纳基因分析报告 + ref/mirna_seq/ 脚本，每个组学单独成模块。
> 严格参照 HTML 报告中的分析步骤设计，沿用 omics-rnaseq-bulk 模块的构建模式。
> 建议/改进意见以 ⚠️ 标注，待确认。

---

## 总览：8 个新模块

| # | 模块 ID | 组学类型 | 来源 | 测序平台 | 核心产出 |
|---|---|---|---|---|---|
| 0 | omics-rnaseq-bulk | 二代有参转录组 | (已有) | Illumina | DEG + 富集 + WGCNA |
| 1 | **omics-mirna** | miRNA 测序 | ref/mirna_seq/ 脚本 | Illumina | miRNA鉴定+靶基因 |
| 2 | **omics-ont-transcriptome** | ONT 全长转录组 | Nanopore全长转录组报告 | ONT 三代 | 全长isoform+AS+融合 |
| 3 | **omics-ont-translatome** | ONT 全长翻译组 | 全长翻译组报告 | ONT 三代 | 翻译本+RNC比较 |
| 4 | **omics-ont-lncrna** | ONT 全长 lncRNA | 全长lncRNA报告 | ONT 三代 | lncRNA鉴定+分类 |
| 5 | **omics-ont-lncdrs** | ONT LncDRS | LncDRS报告 | ONT 三代 | lncRNA+polyA+修饰 |
| 6 | **omics-drs** | 真核 Direct RNA-seq | DRS结题报告模板 | ONT 三代 | polyA+RNA修饰 |
| 7 | **omics-tail-iso-seq** | Tail Iso-seq | Tail Iso-seq报告 | ONT 三代 | polyA+全长isoform |
| 8 | **omics-bacteria-drs** | 细菌 DRS | 细菌DRS报告 | ONT 三代 | 操纵子+8种修饰 |

---

## 模块 1：omics-mirna — miRNA 测序分析

**来源**: ref/mirna_seq/ 脚本 (mirna_seq_pipeline.sh + R 脚本 + quantifier_custom.py)

### 1.1 分析流程（严格按脚本步骤）

```
Step 0: 工具预检 (gffread / bowtie / fastp / fastqc / multiqc / samtools / bedtools / miRDeep2)
   │
Step 1: SRA 下载 (Aspera: ascp -QT -l 500m -P33001)      ← 注意:需Aspera密钥
   │
Step 2: bowtie-build → 参考基因组索引
   │
Step 3: gunzip → 解压 fastq.gz (兼容压缩输入)
   │
Step 4: FastQC + MultiQC → 原始数据质量报告
   │
Step 5: fastp → 接头修剪 + 低质量过滤
   │
Step 6: FastQC + MultiQC → 修剪后质量报告
   │
Step 7: 比对统计
   │
Step 8: bowtie (-v 1 -S) → SAM → samtools sort → BAM
        mapper.pl → collapsed.fa + collapsed_vs_genome.arf  ← (miRNA特有)
   │
Step 9: miRDeep2.pl → miRNA 预测 (并行,每样本)               ← 核心步骤
   │
Step 10: quantifier_custom.py → miRNA 表达定量 (原始计数)
   │
Step 11: pandas merge → 合并表达矩阵 (样本×miRNA 矩阵)
   │
Step 12: normalize_counts.R → CPM/RPM 标准化
   │
Step 13: diff_expression.R → DESeq2 差异表达分析
   │
Step 14: miRanda → 靶基因预测 (mRNA 3'UTR 序列)
         enrichment_analysis.R → GO/KEGG 富集
   │
Step 15: cluster_analysis.R → miRNA 表达模式聚类
   │
Step 16: coexpression_network.R → miRNA-mRNA 共表达网络
   │
Step 17: functional_and_enrichment_analysis.R → 功能注释+富集
```

⚠️ **建议/改进意见**:
1. **Aspera 下载** 对国内用户不稳定，应增加 `--skip-download` 选项，支持从本地 fastq 文件开始
2. **bowtie 版本偏老** — 脚本用的是 bowtie1，建议升级到 bowtie2，或同时支持
3. **quantifier_custom.py** 是自定义脚本，需确认功能和兼容性，建议重写为 runner 嵌入
4. **miRNA 预测并行** — miRDeep2 对每个样本独立跑，设计时需注意 CPU 分配 (`PLANTOMICS_JOB_THREADS`)
5. **靶基因预测** 目前仅 miRanda，可扩展支持 TargetScan / PITA 等多工具共识

### 1.2 module.yaml

```yaml
id: omics-mirna
name: miRNA 测序分析
version: 1.0.0
description: |
  基于 miRDeep2 的标准 miRNA 分析流程:
  从 SRA/fastq 到 miRNA 鉴定、定量、差异表达、靶基因预测、
  聚类分析及 miRNA-mRNA 共表达网络。
author: PlantOmics Team
license: GPL-3.0
icon: rna

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: mirna_seq
      name: miRNA 测序
      description: 从 SRA/fastq 到 miRNA 鉴定、靶基因预测及共表达网络
  reference_types:
    - id: mirna_reference
      name: miRNA 参考(基因组+3'UTR)
      description: bowtie 比对用基因组 + 3'UTR 序列(靶基因预测)
      required_files:
        - id: fasta
          label: 基因组 FASTA
          extensions: [fa, fasta, fna, fa.gz]
          required: true
        - id: gff
          label: 基因结构注释 GFF/GTF
          extensions: [gtf, gff, gff3]
          required: true
        - id: utr_fasta
          label: mRNA 3'UTR FASTA (靶基因预测)
          extensions: [fa, fasta]
          required: false
  menu_items:
    - id: mirna
      label: miRNA 分析
      icon: rna
      route: /mirna
      description: 鉴定 / 定量 / 靶基因 / 共表达

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 1.3 conda-deps/env.yaml

```yaml
channels:
  - conda-forge
  - bioconda
  - nodefaults
dependencies:
  - python=3.11
  - fastapi
  - uvicorn
  - pydantic>=2.0
  - python-multipart
  - httpx
  - aiofiles
  - pyyaml
  - conda-pack
  - pip
  # ───── R ─────
  - r-base=4.4
  - r-plumber
  - r-jsonlite
  # ───── 上游工具 ─────
  - sra-tools
  - fastp
  - fastqc
  - multiqc
  - bowtie
  - samtools
  - bedtools
  - gffread
  # ───── miRNA 工具 ─────
  - mirdeep2          # miRNA 预测
  - miranda           # 靶基因预测
  # ───── R 分析包 ─────
  - r-DESeq2
  - r-pheatmap
  - r-ggplot2
  - r-plyr
  - r-reshape2
  - r-gplots
```

### 1.4 Python runners

| Runner | 对应脚本步骤 | 功能 |
|---|---|---|
| `sra_download_runner.py` | Step 1 | SRA 下载 (Aspera, 可跳过) |
| `fastp_runner.py` | Step 5 | 接头修剪 |
| `fastqc_runner.py` | Step 4,6 | 质控 |
| `bowtie_align_runner.py` | Step 2,8 | 索引构建 + bowtie 比对 + mapper.pl |
| `mirdeep2_runner.py` | Step 9 | miRDeep2 miRNA 预测 |
| `quantifier_runner.py` | Step 10 | miRNA 定量 (替代 quantifier_custom.py) |
| `merge_counts_runner.py` | Step 11 | 合并表达矩阵 |

### 1.5 R scripts

| 脚本 | 对应步骤 | 功能 |
|---|---|---|
| `run_normalize.R` | Step 12 | CPM/RPM 标准化 |
| `run_diff_expression.R` | Step 13 | DESeq2 差异表达 |
| `run_target_prediction.R` | Step 14 | miRanda 靶基因 + 富集 |
| `run_clustering.R` | Step 15 | 表达模式聚类 |
| `run_coexpression.R` | Step 16 | miRNA-mRNA 共表达网络 |
| `run_functional_enrich.R` | Step 17 | 功能注释+富集 |

### 1.6 数据模型

```
~/.plantomics/modules/omics-mirna/species/<id>/   (未来扩展物种库)

project module_data:
  omics-mirna:
    raw/                          # SRA/fastq 原始数据
    clean/                        # fastp 过滤后 + 质控报告
    genome_index/                 # bowtie 索引
    alignment/                    # BAM + collapsed.fa + .arf
    mirdeep2/                     # miRDeep2 预测结果(每样本)
    quantification/               # miRNA 表达矩阵
      raw_counts.tsv
      normalized_counts.tsv
    diff_expression/              # 差异表达结果
    target_prediction/            # miRanda 靶基因
    enrichment/                   # GO/KEGG 富集
    clustering/                   # 聚类分析
    coexpression/                 # 共表达网络
```

---

## 模块 2：omics-ont-transcriptome — ONT 全长转录组分析

**来源**: `贝纳基因Nanopore全长转录组分析示例报告-2025.html`

### 2.1 分析流程（严格按报告步骤）

```
ONT raw data (fast5 格式)
  │
  ├── NanoFilt (q≥7, len≥50) + NanoStat → 数据质控
  │    └─ SeqKit → 测序数据量统计 (ont_stats.xls)
  │    └─ 读长/质量分布图
  │
  ├── Pychopper → 全长序列鉴定 + 定向 + 修剪 + 融合修复
  │    └─ full_length_stats.xls + 全长长度分布图
  │
  ├── minimap2 (-ax splice -uf -k14) → 比对参考基因组
  │    └─ align_stats.xls (比对率)
  │
  ├── Pinfish (v0.1.0, default) → 一致性转录本集
  │    ├─ spliced_bam2gff (BAM→GFF)
  │    ├─ cluster_gff (聚类转录本)
  │    ├─ collapse_partials (去冗余)
  │    └─ polish_clusters (校正)
  │    └─ collapse_stats.xls
  │
  ├── StringTie (v2.1.4, --conservative -L -R) → 去冗余 + 重构
  │    └─ 非冗余转录本长度分布图
  │
  ├── gffcompare (v0.12.1, -R -C -K -M) → 新转录本/新基因鉴定
  │    └─ class code 分类统计 + 分布图
  │
  ├── TransDecoder (v5.5.0, -m 50 --single_best_only) → CDS 预测
  │    └─ CDS 长度分布图
  │
  ├── 7 数据库功能注释:
  │    ├─ diamond blastp → Nr / Uniprot
  │    ├─ hmmscan → Pfam (e-value 0.01)
  │    ├─ kofam_scan → KEGG (KOfam HMM)
  │    ├─ GO / KOG/COG / Pathway (数据库关联推断)
  │    └─ 注释统计 (new_transcripts.stat.xls) + GO/KEGG/KOG/NR 分类图
  │
  ├── 表达定量:
  │    ├─ FPKM 密度分布图
  │    ├─ FPKM 箱线图
  │    ├─ 样品相关性 (Pearson/Spearman 热图)
  │    └─ PCA (2D/3D)
  │
  ├── 结构分析:
  │    ├─ SUPPA2 → 可变剪接 (SE/MX/A5/A3/RI/AF/AL, 7种类型)
  │    │    ├─ 各样本 AS 类型统计 (all_sample_AS.xls + 饼图)
  │    │    ├─ 分组 UpSet 集合图
  │    │    └─ 差异可变剪接 (DiffSplice) + 统计表
  │    ├─ 融合转录本分析 (Fusion)
  │    ├─ SSR 分析 + 引物设计 (6种类型: c/p1/p2/p3/p4/p5)
  │    └─ 转录因子分析 (动物: animalTFDB v3.0 / 植物: PlantTFDB v5.0)
  │
  ├── 转录本/基因统计:
  │    ├─ 已知 vs 新鉴定数量图
  │    ├─ 包含不同转录本数的基因统计
  │    └─ 转录本密度 circos 图 (Circlize, 500k窗口)
  │
  └── 结果预览表 (表3)
```

⚠️ **建议/改进意见**:
1. **Pinfish 已较少维护** — 社区更常用 `isONclust + isONcorrect` 或 `IsoSeq_QC3` 做三代转录本聚类纠错。但报告用 Pinfish 则维持 Pinfish，可在后续迭代升级
2. **Basecalling 步骤缺失** — 报告未涉及 raw signal 的 basecalling（Guppy/Dorado），因为这一步通常在测序仪上完成。但模块设计时应保留 `basecall_runner.py` 处理 pod5/fast5 输入
3. **定量方法** 报告用的是 FPKM (Salmon 或自定义)，建议明确使用 Salmon 进行 transcript-level 定量
4. **转录因子鉴定** 依赖动物/植物 TFDB，需要下载外部数据库（~1-2GB），需在 postinst 中处理
5. **SSR 分析** 在报告中只出现一次，可能可用 MISA 或类似工具

### 2.2 module.yaml

```yaml
id: omics-ont-transcriptome
name: ONT 全长转录组分析
version: 1.0.0
description: |
  基于 Oxford Nanopore 三代测序的全长转录组分析流程:
  从 ONT raw data 到全长序列鉴定、转录本组装、新转录本发现、
  表达定量、可变剪接分析、融合基因、SSR 及转录因子分析。
author: PlantOmics Team
license: GPL-3.0
icon: layers

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: ont_transcriptome
      name: ONT 全长转录组
      description: 从 ONT raw data 到全长转录本组装、定量、结构分析
  reference_types:
    - id: ont_transcriptome_reference
      name: 转录组参考(基因组+注释)
      description: minimap2 比对用基因组 FASTA + GTF 注释
      required_files:
        - id: fasta
          label: 基因组 FASTA
          extensions: [fa, fasta, fna, fa.gz]
          required: true
        - id: gtf
          label: 基因结构注释 GTF
          extensions: [gtf, gff, gff3, gtf.gz]
          required: true
  menu_items:
    - id: ont_transcriptome_processing
      label: 数据处理
      icon: cog
      route: /ont-transcriptome
      description: 全长鉴定 / 比对 / 组装 / 新转录本 / 注释
    - id: ont_transcriptome_analysis
      label: 定量与结构
      icon: bar-chart
      route: /ont-transcriptome-analysis
      description: 表达定量 / AS / 融合 / SSR / TF

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 2.3 conda-deps/env.yaml

```yaml
channels:
  - conda-forge
  - bioconda
  - nodefaults
dependencies:
  - python=3.11
  - fastapi / uvicorn / pydantic / ...
  - conda-pack
  - r-base=4.4
  - r-plumber
  - r-jsonlite
  # ───── ONT 基础工具 ─────
  - ont-fast5-api               # fast5 读取
  - nanopack                    # NanoFilt / NanoStat / NanoPlot
  - minimap2                    # 三代比对
  - samtools
  - seqkit                      # 序列统计
  - pychopper                   # 全长 cDNA 鉴定
  # ───── 转录本组装/分析 ─────
  - pinfish                     # 一致性转录本
  - stringtie                   # 去冗余/重构
  - gffcompare                  # 新转录本比较
  - transdecoder                # CDS 预测
  # ───── 定量 ─────
  - salmon                      # 转录本定量
  # ───── 功能注释 ─────
  - diamond                     # blastp 加速
  - hmmer                       # Pfam HMM
  - kofamscan                   # KEGG KOfam
  # ───── 结构分析 ─────
  - suppa                       # 可变剪接 (DiffSplice)
  - gffread                     # GFF/GTF 转换
  # ───── R 图表包 ─────
  - r-pheatmap
  - r-ggplot2
  - r-Circlize                  # circos 图
  # ───── pip 兜底 ═────
  - pip
```

### 2.4 Python runners

| Runner | 功能 |
|---|---|
| `basecall_runner.py` | Dorado/Guppy basecalling (pod5/fast5→fastq) |
| `nanofilt_runner.py` | NanoFilt 质控 + NanoStat 统计 + 图表 |
| `pychopper_runner.py` | 全长序列鉴定 + 定向/修剪/融合修复 |
| `minimap2_align_runner.py` | 参考基因组比对 (-ax splice -uf -k14) |
| `pinfish_runner.py` | 一致性转录本 (bam2gff→cluster→collapse→polish) |
| `stringtie_runner.py` | 转录本去冗余 (--conservative -L -R) |
| `gffcompare_runner.py` | 新转录本鉴定 (class code) |
| `transdecoder_runner.py` | CDS 预测 (-m 50 --single_best_only) |
| `annot_7db_runner.py` | 7数据库注释 (diamond/hmmer/kofam) |
| `salmon_quant_runner.py` | 转录本表达定量 |
| `suppa2_runner.py` | 可变剪接分析 + 差异 |
| `fusion_runner.py` | 融合转录本检测 |
| `ssr_runner.py` | SSR 分析 + 引物设计 |
| `tf_runner.py` | 转录因子鉴定 (动物/植物) |
| `circos_runner.py` | 转录本密度 circos 图 (调用 R) |

### 2.5 R scripts

| 脚本 | 功能 |
|---|---|
| `run_quant_plots.R` | FPKM 密度/箱线图、相关性热图、PCA |
| `run_annotation_plots.R` | GO/KEGG/KOG/NR 分类图 |
| `run_circos.R` | 转录本密度 circos 图 |

### 2.6 数据模型

```
project module_data:
  omics-ont-transcriptome:
    rawdata/                      # 原始 fast5/pod5 + basecalling
      ont_stats.xls
    cleandata/                    # NanoFilt 过滤后
      full_length_stats.xls        # Pychopper 全长鉴定
      full_length_length_dist.png
    alignment/                    # minimap2 BAM
      align_stats.xls
      collapse_stats.xls          # Pinfish 统计
    structure_analysis/
      structure_optimization/     # StringTie 去冗余
        transcript_length_dist.png
        gene_transcript_counts.png
        circos_density.png
      novel_trans/                # gffcompare → 新转录本
        new_code_stat.xls
        cds_length_dist.png
        anno/                     # 7 数据库注释
          go/
          kegg/
          kog/
          nr/
      as/                         # SUPPA2 可变剪接
        each_sample/
        diff_as/
      fusion/                     # 融合基因
      ssr/                        # SSR
      tf/                         # 转录因子
    quantification/               # 表达定量
      tpm/
      fpkm/
      corr/
      pca/
```

---

## 模块 3：omics-ont-translatome — ONT 全长翻译组分析

**来源**: `全长翻译组分析报告模板.html`

### 3.1 分析流程

```
ONT raw data (fast5)
  │
  ├── ONT 数据质控 (同 ont-transcriptome, q≥7)
  │    ├─ 读长/质量分布图
  │    └─ ont_stats.xls
  │
  ├── 比对参考基因组 (minimap2)
  │    └─ 比对率统计
  │
  ├── Pinfish → 一致性翻译本集 (同 ont-transcriptome)
  │    └─ collapse_stats.xls
  │
  ├── StringTie → 去冗余
  │    └─ 非冗余长度分布图
  │
  ├── gffcompare → 新翻译本/新基因
  │    └─ class code + 统计
  │
  ├── TransDecoder → CDS 预测
  │
  ├── 7 数据库功能注释 (同 ont-transcriptome)
  │
  ├── ⭐ 翻译组特有: ref vs all 对比分析
  │    ├─ ref: 只比对区域 → 已知翻译本表达
  │    ├─ all: 完整流程 → 完整翻译本集
  │    ├─ 基因/翻译本 Venn 图对比
  │    ├─ 注释差异金字塔图
  │    └─ 富集差异对比条形图
  │
  ├── 翻译本/基因统计 (已知 vs 新鉴定)
  │    └─ 翻译本密度 circos 图
  │
  ├── 结构分析:
  │    ├─ SUPPA2 → 可变剪接 (7种 + 差异 + UpSet)
  │    ├─ 融合翻译本分析
  │    ├─ SSR 分析 + 引物设计
  │    └─ 转录因子分析 (PlantTFDB/animalTFDB)
  │
  └── 结果预览表
```

⚠️ **建议/改进意见**:
1. ⭐ **翻译组最大的独特价值是 "ref vs all" 对比**，这是其他 ONT 模块没有的。这个对比需要两套分析流程同时跑，计算量翻倍但也是核心卖点
2. **Pychopper 步骤没有出现** — 翻译组的建库流程是 RNC 富集 → 反转录 → PCR，也产生了全长 cDNA，应该也有 Pychopper 步骤？报告中跳过了。**建议补上**
3. **该模块与 ont-transcriptome 的差异仅在前端 RNC 标签 + ref vs all 对比**。如果两个模块都实现，有大量代码重复。如果确认是独立需求，需将核心逻辑抽取为共享库

### 3.2 module.yaml

```yaml
id: omics-ont-translatome
name: ONT 全长翻译组分析
version: 1.0.0
description: |
  基于 ONT 的 RNC 富集翻译组分析流程:
  直接捕获与核糖体结合的 mRNA，鉴定活跃翻译的翻译本及异构体。
  特有 ref vs all 双流程对比分析，揭示翻译组完整图景。
author: PlantOmics Team
license: GPL-3.0
icon: layers

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: ont_translatome
      name: ONT 全长翻译组
      description: RNC 富集的全长翻译本分析(翻译活性 mRNA)
  reference_types:
    - id: ont_translatome_reference
      name: 翻译组参考(基因组+注释)
      required_files:
        - id: fasta
          label: 基因组 FASTA
          required: true
        - id: gtf
          label: 基因结构注释 GTF
          required: true
  menu_items:
    - id: translatome
      label: 翻译组分析
      icon: layers
      route: /translatome
      description: 翻译本鉴定 / ref-vs-all / 定量 / AS

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 3.3 conda-deps/env.yaml

与 ont-transcriptome 基本相同，增加 `r-VennDiagram` 包用于 Venn 图。

### 3.4 Python runners

与 ont-transcriptome 共享大部分，额外:

| Runner | 功能 |
|---|---|
| (复用 ont-transcriptome 全部 runner) | |
| `ref_vs_all_runner.py` | ⭐ ref vs all 双流程对比 (Venn/金字塔/富集对比) |

### 3.5 R scripts

| 脚本 | 功能 |
|---|---|
| (复用 ont-transcriptome R scripts) | |
| `run_ref_vs_all.R` | ⭐ ref vs all 对比分析 (Venn + 金字塔 + 富集对比) |

---

## 模块 4：omics-ont-lncrna — ONT 全长 lncRNA 测序分析

**来源**: `贝纳基因全长lncRNA测序分析示例报告.html`

### 4.1 分析流程

```
ONT raw data (fast5)
  │
  ├── Guppy (v5.0.16) Basecalling → fastq
  │
  ├── NanoFilt (q≥7, len≥50) → 过滤 + NanoStat 质控
  │    ├─ 读长/质量/数据量分布图
  │    └─ ont_stats.xls
  │
  ├── Pychopper (v2.4.0, -Q 7 -z 50) → 全长序列鉴定
  │    └─ full_length_stats.xls + 长度分布图
  │
  ├── ⭐ 比对核糖体数据库 (rRNA 去除):
  │    minimap2 (-ax map-ont -uf -k14) → rRNA DB
  │    samtools flagstat → rRNA 比对统计
  │    └─ rRNA_stats.xls (保留 unmapped 用于后续)
  │
  ├── minimap2 (-ax splice -uf -k14) → 比对参考基因组
  │    └─ align_stats.xls
  │
  ├── Pinfish → 一致性转录本
  │    └─ collapse_stats.xls
  │
  ├── StringTie (--conservative -L -R) → 去冗余
  │
  ├── gffcompare (-R -C -K -M) → 新转录本
  │
  ├── TransDecoder (-m 50 --single_best_only) → CDS 预测
  │
  ├── 7 数据库注释 (Nr/Pfam/Uniprot/KEGG/GO/KOG/Pathway)
  │
  ├── ⭐ lncRNA 鉴定 (核心):
  │    ├─ 筛选: 新转录本长度 ≥200bp ≤20kbp
  │    ├─ 过滤: 有 ORF 的剔除
  │    ├─ CPC2 (v1.0.1) 编码潜能预测
  │    ├─ PLEK 编码潜能预测
  │    └─ Venn 图 → 最终 lncRNA 集合
  │
  ├── 新 lncRNA 分类:
  │    ├─ lincRNA (基因间区 lncRNA)
  │    ├─ Intronic lncRNA (内含子区)
  │    ├─ Antisense lncRNA (反义链)
  │    └─ Sense overlapping lncRNA (正义重叠)
  │
  ├── 表达定量 (同 ont-transcriptome)
  │
  ├── 转录本/基因统计
  │    └─ circos 密度图
  │
  └── 结构分析 (同 ont-transcriptome)
       ├─ SUPPA2 → 可变剪接
       ├─ 融合转录本
       ├─ SSR 分析
       └─ 转录因子
```

⚠️ **建议/改进意见**:
1. **CNCI 未出现** — 在 DRS 和 Tail Iso-seq 报告中有 CNCI，但这里只有 CPC2 + PLEK。建议增加 CNCI，取三个工具的**交集**作为高置信度 lncRNA，这是行业最佳实践
2. **rRNA 数据库** 需要从 Silva 或 RNAcentral 下载，需在 postinst 中处理
3. **lncRNA 筛选阈值** 200bp 和 20kbp 是固定值，可在高级选项中设为可调
4. **此模块与 LncDRS 差异主要在 basecall 方式和 polyA**，核心 lncRNA 鉴定逻辑相同

### 4.2 module.yaml

```yaml
id: omics-ont-lncrna
name: ONT 全长 lncRNA 测序分析
version: 1.0.0
description: |
  基于 ONT 三代测序的全长 lncRNA 分析流程:
  从 ONT raw data 到全长鉴定、rRNA 去除、lncRNA 鉴定(CPC2/PLEK)、
  分类(lincRNA/Intronic/Antisense/Sense)、表达定量及结构分析。
author: PlantOmics Team
license: GPL-3.0
icon: rna

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: ont_lncrna
      name: ONT 全长 lncRNA
      description: 全长 lncRNA 鉴定(rRNA去除+CPC2/PLEK)及分类
  reference_types:
    - id: ont_lncrna_reference
      name: lncRNA 参考(基因组+rRNA数据库+注释)
      description: 比对用基因组 + rRNA DB + GTF
      required_files:
        - id: fasta
          label: 基因组 FASTA
          required: true
        - id: gtf
          label: 基因结构注释 GTF
          required: true
        - id: rrna_db
          label: 核糖体 RNA 数据库 FASTA
          extensions: [fa, fasta, fa.gz]
          required: true
  menu_items:
    - id: lncrna
      label: lncRNA 分析
      icon: rna
      route: /lncrna
      description: rRNA去除 / 鉴定 / 分类 / 定量

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 4.3 conda-deps/env.yaml

```yaml
channels:
  - conda-forge
  - bioconda
  - nodefaults
dependencies:
  - python=3.11
  - fastapi / uvicorn / pydantic / ...
  - conda-pack
  - r-base=4.4
  - r-plumber
  - r-jsonlite
  # ───── ONT 三代工具 ─────
  - ont-fast5-api
  - nanopack
  - minimap2
  - samtools
  - seqkit
  - pychopper
  # ───── 转录本组装/分析 ─────
  - pinfish
  - stringtie
  - gffcompare
  - transdecoder
  - salmon
  # ───── lncRNA 鉴定 ─────
  - cpc2                         # 编码潜能预测
  - plek                         # 编码潜能预测(非编码RNA)
  - libsvm                       # CPC2 依赖
  # ───── 功能注释 ─────
  - diamond
  - hmmer
  - kofamscan
  # ───── 结构分析 ─────
  - suppa
  - gffread
  - r-pheatmap
  - r-ggplot2
  - r-Circlize
  - r-VennDiagram
  - pip
```

### 4.4 Python runners

| Runner | 功能 |
|---|---|
| `basecall_runner.py` | Guppy basecalling |
| `nanofilt_runner.py` | NanoFilt 质控 |
| `pychopper_runner.py` | 全长鉴定 |
| `rrna_remove_runner.py` | ⭐ rRNA 去除 (minimap2→rRNA DB) |
| `minimap2_align_runner.py` | 参考基因组比对 |
| `pinfish_runner.py` | 一致性转录本 |
| `stringtie_runner.py` | 去冗余 |
| `gffcompare_runner.py` | 新转录本 |
| `transdecoder_runner.py` | CDS 预测 |
| `annot_7db_runner.py` | 7数据库注释 |
| `lncrna_identify_runner.py` | ⭐ lncRNA 鉴定 (CPC2+PLEK+筛选+Venn) |
| `lncrna_classify_runner.py` | ⭐ lncRNA 分类 (lincRNA/Intronic/Antisense/Sense) |
| `salmon_quant_runner.py` | 表达定量 |
| `suppa2_runner.py` | 可变剪接 |
| `fusion_runner.py` | 融合基因 |
| `ssr_runner.py` | SSR 分析 |
| `tf_runner.py` | 转录因子 |

### 4.5 数据模型

```
project module_data:
  omics-ont-lncrna:
    rawdata/
    cleandata/
      full_length_stats.xls
      rrna_stats.xls                  # rRNA 去除统计
    alignment/
    consensus/
    novel_transcripts/
    lncrna/
      identification/                 # CPC2/PLEK/Venn
        cpc2_results/
        plek_results/
        lncrna_venn.png
        final_lncrna_set.tsv
      classification/                 # lincRNA/intronic/antisense/sense
    quantification/
    as/
    fusion/
    ssr/
    tf/
```

---

## 模块 5：omics-ont-lncdrs — ONT LncDRS 分析

**来源**: `贝纳LncDRS示例报告(1).html`

### 5.1 分析流程

```
ONT raw data (pod5 格式)
  │
  ├── ⭐ Dorado (v1.2.0) Basecalling (pod5→fastq)    ← 注意:与 lncRNA 的 Guppy 不同
  │
  ├── NanoFilt (q≥10, len≥50) → 过滤                       ← 注意:阈值 q≥10 (lncRNA 是 q≥7)
  │    ├─ 读长/质量/数据量分布图
  │    └─ ont_stats.xls
  │
  ├── ⭐ poly(A) 比例鉴定:
  │    Dorado --estimate-poly-a → poly(A) 长度鉴定
  │    └─ polyA 比例分布图 (len<5bp=无polyA)
  │
  ├── rRNA 去除 (minimap2 → rRNA DB, 同 lncrna)
  │    └─ rRNA_stats.xls
  │
  ├── minimap2 → 比对参考基因组
  │    ├─ align_stats.xls
  │    └─ Reads 注释 (RNA 类型分布图)
  │
  ├── Pinfish → 一致性转录本
  ├── StringTie → 去冗余
  ├── gffcompare → 新转录本
  ├── TransDecoder → CDS 预测
  ├── 7 数据库注释
  │
  ├── ⭐ lncRNA 鉴定:
  │    ├─ CPC2 编码潜能预测
  │    ├─ PLEK 编码潜能预测
  │    └─ Venn 图
  │
  ├── 转录本/基因统计 + circos 密度图
  │
  ├── 结构分析:
  │    ├─ SUPPA2 → 可变剪接 (7种)
  │    └─ 融合转录本
  │
  └── ⭐ poly(A) 分析 (LncDRS 特有):
       ├─ poly(A) 比例 (由开头鉴定)
       ├─ poly(A) 长度统计
       ├─ poly(A) vs 表达关联
       └─ 组间 poly(A) 差异分析
```

⚠️ **建议/改进意见**:
1. **与模块 4 (omics-ont-lncrna) 差异分析**:
   - Basecall: LncDRS 用 Dorado, lncRNA 用 Guppy (但 Dorado 现在已是主流，建议 lncRNA 也升级)
   - QC 阈值: LncDRS q≥10, lncRNA q≥7 → 因为 DRS 直接测 RNA 质量较低
   - LncDRS 多了 poly(A) 比例鉴定和 poly(A) 分析
   - LncDRS 没有 Pychopper 步骤（因为 DRS 不分全长片段)
   - CNCI 没有出现（但其他 DRS 报告有 CNCI）
2. ⭐ **建议统一**: 如果两个模块代码量太大，可考虑 `omics-ont-lncrna` 同时支持 cDNA 和 DRS 两种模式。但按你的要求保持独立

### 5.2 module.yaml

```yaml
id: omics-ont-lncdrs
name: ONT LncRNA Direct RNA Sequencing 分析
version: 1.0.0
description: |
  基于 ONT 直接 RNA 测序的 lncRNA 分析流程:
  不经反转录，直接对 lncRNA 测序。覆盖 lncRNA 鉴定(CPC2/PLEK)、
  poly(A)长度分析、可变剪接、融合基因及 RNA 修饰检测。
author: PlantOmics Team
license: GPL-3.0
icon: rna

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: ont_lncdrs
      name: ONT LncDRS
      description: lncRNA 直接 RNA 测序(含 polyA + RNA 修饰)
  reference_types:
    - id: ont_lncdrs_reference
      name: LncDRS 参考(基因组+rRNA数据库+注释)
      required_files:
        - id: fasta
          label: 基因组 FASTA
          required: true
        - id: gtf
          label: 基因结构注释 GTF
          required: true
        - id: rrna_db
          label: 核糖体 RNA 数据库 FASTA
          required: true
  menu_items:
    - id: lncdrs
      label: LncDRS 分析
      icon: rna
      route: /lncdrs
      description: polyA / lncRNA鉴定 / AS / 修饰

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 5.3 conda-deps/env.yaml

基本同 omics-ont-lncrna，但:
- 不需要 pychopper
- 不需要 cpc2? (报告没用 CNCI，但有 CPC2+PLEK，保持)
- 注意: Dorado 非 conda 包，需单独处理 (pip 或二进制)
- 增加 r-ggplot2 等用于 polyA 图表

### 5.4 Python runners

与 omics-ont-lncrna 共享大部分，差异:

| Runner | 功能 | vs lncrna |
|---|---|---|
| `basecall_runner.py` | ⭐ Dorado basecalling (pod5→fastq) | 不同: Dorado vs Guppy |
| `nanofilt_runner.py` | NanoFilt (q≥10) | 阈值不同 |
| `polya_detect_runner.py` | ⭐ poly(A) 比例鉴定 (Dorado --estimate-poly-a) | 新增 |
| `rrna_remove_runner.py` | rRNA 去除 | 相同 |
| `minimap2_align_runner.py` | 比对 + Reads 注释 | 相同 |
| `pinfish_runner.py` | 一致性转录本 | 相同 |
| `stringtie_runner.py` | 去冗余 | 相同 |
| `gffcompare_runner.py` | 新转录本 | 相同 |
| `transdecoder_runner.py` | CDS 预测 | 相同 |
| `annot_7db_runner.py` | 7数据库注释 | 相同 |
| `lncrna_identify_runner.py` | CPC2/PLEK lncRNA 鉴定 | 相同 |
| `salmon_quant_runner.py` | 表达定量 | 相同 |
| `suppa2_runner.py` | 可变剪接 | 相同 |
| `fusion_runner.py` | 融合基因 | 相同 |
| `polya_analysis_runner.py` | ⭐ poly(A) 长度/差异分析 | 新增 |

---

## 模块 6：omics-drs — 真核 Direct RNA Sequencing 分析

**来源**: `贝纳基因DRS结题报告模板20250331.html`

### 6.1 分析流程

```
ONT raw data (fast5)
  │
  ├── NanoFilt (q≥10) → 质控过滤
  │    ├─ 读长/质量分布图
  │    └─ ont_stats.xls
  │
  ├── minimap2 → 比对参考基因组
  │    └─ align_stats.xls
  │
  ├── ⭐ Flair (v1.5.0, -t 20) → 一致性转录本         ← 注意:此处用 Flair 而非 Pinfish
  │    └─ collapse_stats.xls
  │
  ├── gffcompare → 新转录本
  ├── TransDecoder → CDS 预测
  ├── 7 数据库注释 (同前)
  │
  ├── 表达定量 (TPM)
  │    ├─ 密度/箱线图/相关性/PCA
  │
  ├── 结构分析:
  │    ├─ SUPPA2 → 可变剪接 (7种 + 差异 + UpSet)
  │    ├─ 融合转录本
  │    └─ ⭐ lncRNA 预测 (CNCI + CPC2 + PLEK, Venn)    ← 注意:此处有 CNCI
  │
  ├── ⭐ poly(A) 分析:
  │    ├─ Dorado --estimate-poly-a → 单样品 poly(A) 长度
  │    │    ├─ 长度统计 (mean/Q25/Q50/Q75)
  │    │    └─ 分布图
  │    ├─ poly(A) 与表达量关联
  │    └─ 组间 poly(A) 差异 (Mann-Whitney U 检验)
  │
  ├── ⭐ RNA 修饰分析 (DRS 核心卖点):
  │    ├─ m6A (N6-甲基腺苷)
  │    ├─ m5C (5-甲基胞嘧啶)
  │    ├─ 组间差异修饰
  │
  └── 结果预览表
```

⚠️ **建议/改进意见**:
1. **Flair vs Pinfish** — DRS 用 Flair 而不是 Pinfish。Flair 是专门为 DRS 数据设计（无 PCR、更短 reads），Pinfish 是为 cDNA 数据设计。二者的参数和算法路径不同，不能混用。**建议两个工具都支持，用户选择**
2. **CNCI 出现了** — 在 lncRNA 预测中，DRS 报告用了 CNCI + CPC2 + PLEK。这与模块 4 (lncRNA) 只用 CPC2 + PLEK 不同。**建议模块 4 也加 CNCI**
3. **RNA 修饰** 分析在报告中只写了 m6A 和 m5C，但实际用的具体检测工具（tombo / ELIGOS / DRUMMER）报告未明确。需确定使用哪个工具
4. **无 Pychopper** — DRS 不需要 Pychopper（因为 DRS 直接测全长 RNA，没有 cDNA 引物）

### 6.2 module.yaml

```yaml
id: omics-drs
name: Direct RNA Sequencing 分析
version: 1.0.0
description: |
  基于 ONT Direct RNA Sequencing 的直接 RNA 测序分析流程:
  不经反转录和 PCR 扩增。覆盖全长转录本鉴定(Flair)、
  表达定量、可变剪接、poly(A)尾长分析、lncRNA 预测
  及 RNA 修饰(m6A/m5C)检测。
author: PlantOmics Team
license: GPL-3.0
icon: rna

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: drs
      name: Direct RNA Sequencing
      description: ONT 直接 RNA 测序(无PCR偏好)
  reference_types:
    - id: drs_reference
      name: DRS 参考(基因组+注释)
      required_files:
        - id: fasta
          label: 基因组 FASTA
          required: true
        - id: gtf
          label: 基因结构注释 GTF
          required: true
  menu_items:
    - id: drs
      label: DRS 分析
      icon: rna
      route: /drs
      description: 转录本 / polyA / RNA修饰

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 6.3 conda-deps/env.yaml

```yaml
channels:
  - conda-forge
  - bioconda
  - nodefaults
dependencies:
  - python=3.11
  - fastapi / uvicorn / pydantic / ...
  - conda-pack
  - r-base=4.4
  - r-plumber / r-jsonlite
  # ───── ONT 基础 ═────
  - nanopack
  - minimap2
  - samtools
  - seqkit
  # ───── 转录本组装(Flair) ═────
  - flair                       # FLAIR (Full-Length Alternative Isoform analysis of RNA)
  - gffcompare
  - transdecoder
  # ───── 定量 ═────
  - salmon
  # ───── lncRNA 鉴定 ═────
  - cpc2
  - plek
  - cnci                        # ⭐ DRS 报告特有
  # ───── 功能注释 ─────
  - diamond / hmmer / kofamscan
  # ───── 结构分析 ─────
  - suppa
  - gffread
  # ───── RNA 修饰分析 ─────
  # (tombo/ELIGOS 需 pip/外部安装)
  - pip
  # ───── R 包 ─────
  - r-pheatmap / r-ggplot2 / r-Circlize / r-VennDiagram
```

### 6.4 Python runners

| Runner | 功能 |
|---|---|
| `nanofilt_runner.py` | NanoFilt QC (q≥10) |
| `minimap2_align_runner.py` | 比对参考基因组 |
| `flair_runner.py` | ⭐ Flair 一致性转录本 (align→correct→collapse) |
| `gffcompare_runner.py` | 新转录本 |
| `transdecoder_runner.py` | CDS 预测 |
| `annot_7db_runner.py` | 7数据库注释 |
| `salmon_quant_runner.py` | 表达定量 |
| `suppa2_runner.py` | 可变剪接 |
| `fusion_runner.py` | 融合基因 |
| `lncrna_predict_runner.py` | ⭐ lncRNA 预测 (CNCI+CPC2+PLEK+Venn) |
| `polya_runner.py` | ⭐ poly(A) 长度鉴定 (Dorado --estimate-poly-a) |
| `polya_analysis_runner.py` | poly(A) 统计/相关/差异 (Mann-Whitney U) |
| `rna_mod_runner.py` | ⭐ RNA 修饰检测 (m6A/m5C + 差异) |

---

## 模块 7：omics-tail-iso-seq — Tail Iso-seq 分析

**来源**: `贝纳基因Tail Iso-seq分析示例报告-2025.html`

### 7.1 分析流程

```
ONT raw data (fast5)
  │
  ├── NanoFilt (q≥7) + NanoStat → 质控
  │    └─ ont_stats.xls
  │
  ├── Pychopper → 全长序列鉴定
  │    └─ full_length_stats.xls + 长度分布图
  │
  ├── minimap2 → 比对参考基因组
  │    └─ align_stats.xls
  │
  ├── Pinfish → 一致性转录本
  │    └─ collapse_stats.xls
  │
  ├── StringTie → 去冗余
  │
  ├── gffcompare → 新转录本
  ├── TransDecoder → CDS 预测
  ├── 7 数据库注释
  ├── 表达定量
  │
  ├── 结构分析:
  │    ├─ SUPPA2 → 可变剪接 (7种 + 差异)
  │    ├─ 融合转录本
  │    └─ ⭐ lncRNA 预测 (CNCI + CPC2 + PLEK, Venn)
  │
  └── ⭐ poly(A) 分析:
       ├─ Dorado --estimate-poly-a → poly(A) 长度
       └─ poly(A) 统计/分布/差异
```

⚠️ **建议/改进意见**:
1. **Tail Iso-seq = ONT 全长转录组 + poly(A)**。其流程是 Pychopper→minimap2→Pinfish (与 ont-transcriptome 相同)，加了 poly(A) 分析
2. **lncRNA 预测出现了 CNCI**（与 DRS 一致，但与 lncRNA 报告不同）
3. **冗余提醒**: 如果 `ont-transcriptome` 和 `tail-iso-seq` 两个模块都建，约 70% 代码（Pinfish/StringTie/gffcompare/TransDecoder/SUPPA2）完全重复。但按你的要求保持独立

### 7.2 module.yaml

```yaml
id: omics-tail-iso-seq
name: Tail Iso-seq 分析
version: 1.0.0
description: |
  基于 ONT 的全长转录本测序(Tail Iso-seq)，保留完整 poly(A)尾长度信息。
  覆盖全长转录本鉴定、定量、可变剪接、融合基因、
  lncRNA 预测及 poly(A) 长度定量与差异分析。
author: PlantOmics Team
license: GPL-3.0
icon: rna

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: tail_iso_seq
      name: Tail Iso-seq
      description: 全长转录组+poly(A)尾长分析
  reference_types:
    - id: tail_iso_seq_reference
      name: 参考(基因组+注释)
      required_files:
        - id: fasta
          label: 基因组 FASTA
          required: true
        - id: gtf
          label: 基因结构注释 GTF
          required: true
  menu_items:
    - id: tail_iso_seq
      label: Tail Iso-seq
      icon: rna
      route: /tail-iso-seq
      description: 全长 / polyA / AS / lncRNA

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 7.3 conda-deps/env.yaml

结合 ont-transcriptome + drs 的 polyA + lncRNA 部分。

### 7.4 Python runners

| Runner | 来源 |
|---|---|
| `nanofilt_runner.py` | 同 ont-transcriptome |
| `pychopper_runner.py` | 同 ont-transcriptome |
| `minimap2_align_runner.py` | 同 |
| `pinfish_runner.py` | 同 |
| `stringtie_runner.py` | 同 |
| `gffcompare_runner.py` | 同 |
| `transdecoder_runner.py` | 同 |
| `annot_7db_runner.py` | 同 |
| `salmon_quant_runner.py` | 同 |
| `suppa2_runner.py` | 同 |
| `fusion_runner.py` | 同 |
| `lncrna_predict_runner.py` | ⭐ CNCI+CPC2+PLEK (同 DRS) |
| `polya_runner.py` | ⭐ Dorado polyA (同 DRS) |
| `polya_analysis_runner.py` | polyA 统计/差异 (同 DRS) |

---

## 模块 8：omics-bacteria-drs — 细菌 DRS 分析

**来源**: `贝纳细菌DRS分析报告模板 .html`

### 8.1 分析流程

```
ONT raw data (pod5/fast5)
  │
  ├── NanoFilt (q≥10) → 质控过滤
  │    └─ 数据统计表 (TotalBase/MaxLen/AvgLen/N50/meanQ/MappedReads)
  │
  ├── minimap2 → 比对参考基因组 (非剪接模式)         ← 细菌无内含子
  │    └─ bam_mapping_stats_fixed.tsv (唯一比对率/总比对率)
  │
  ├── ⭐ 转录本重构 (细菌特有):
  │    ├─ 操纵子分析 (Operon)                        ← 核心特色
  │    │    └─ 操作子名/含基因/Reads计数/相对表达量
  │    ├─ 转录本列表 (起止位置/链/支持reads数/类型)
  │    └─ 起始密码子分析 (TSS/预测终止/关联基因)
  │
  ├── 基因表达定量 (TPM)
  │    ├─ TPM 密度分布图
  │    ├─ TPM 箱线图
  │    ├─ Pearson 相关性热图
  │    └─ PCA (2D/3D)
  │
  ├── ⭐ DESeq2 差异表达基因                         ← 细菌也用 DESeq2
  │    ├─ 差异基因统计表 + 柱状图
  │    ├─ 差异基因结果表 (log2FoldChange/pvalue/padj)
  │    ├─ 火山图
  │    ├─ 聚类热图
  │    └─ GO/KEGG 富集 (dotplot/barplot)
  │
  └── ⭐ RNA 修饰分析 (8种修饰, 细菌特有特色):
       ├─ 总览: 8种修饰位点统计表 + 总数柱状图
       ├─ 单样品结果:
       │    ├─ 修饰位点表 (gene_id/位置/fraction/coverage/motif)
       │    ├─ 修饰位点分布图
       │    └─ 修饰位点 motif 互作图
       └─ 差异修饰:
            ├─ 差异修饰汇总表 (meth_type/compare_group/Up/Down)
            ├─ 差异修饰数目柱状图
            └─ 不同修饰类型差异分布图
```

⚠️ **建议/改进意见**:
1. **细菌 DRS 与真核 DRS 差异显著**:
   - 无剪接 → 不需要 SUPPA2
   - 无 poly(A) → 不需要 polya 分析
   - 多操纵子 → 需要 operon 分析工具
   - 多顺反子 → 转录本重构逻辑不同
2. **特殊工具需求**:
   - 操纵子预测可能需要特定工具或自定义逻辑（报告中未明确工具）
   - RNA 修饰 8 种（Am/m6A/m4C/m5C/m6Am/m7G/m1A/Ψ），检测可用 tombo/ELIGOS/DRUMMER
3. **DESeq2** 用于细菌数据在计算上可行，但需要注意细菌基因的 counts 分布特性
4. **此模块是唯一一个**完全独立于其他 ONT 模块的（无 Pinfish/Pychopper/StringTie 等），代码复用少

### 8.2 module.yaml

```yaml
id: omics-bacteria-drs
name: 细菌 Direct RNA Sequencing 分析
version: 1.0.0
description: |
  基于 Nanopore DRS 的细菌 RNA 直接测序分析流程:
  不经逆转录，直接对细菌 RNA 单分子测序。
  覆盖操纵子分析、转录本重构、基因表达定量(DESeq2)、
  GO/KEGG 富集及 8 种 RNA 化学修饰(Am/m6A/m4C/m5C等)检测。
author: PlantOmics Team
license: GPL-3.0
icon: rna

core_required: ">=1.0.0,<2.0.0"

extends:
  project_types:
    - id: bacteria_drs
      name: 细菌 DRS
      description: 细菌 RNA 直接测序(操纵子+修饰+差异)
  reference_types:
    - id: bacteria_reference
      name: 细菌参考基因组
      description: 细菌基因组 FASTA + GFF 注释
      required_files:
        - id: fasta
          label: 基因组 FASTA
          required: true
        - id: gff
          label: 基因结构注释 GFF/GTF
          extensions: [gtf, gff, gff3]
          required: true
  menu_items:
    - id: bacteria_drs
      label: 细菌 DRS
      icon: rna
      route: /bacteria-drs
      description: 操纵子 / 定量 / 差异 / 8种修饰

runtime:
  python:
    entry: backend-py/main.py
    health_path: /health
  r:
    entry: backend-r/plumber.R
    health_path: /health
```

### 8.3 conda-deps/env.yaml

```yaml
channels:
  - conda-forge
  - bioconda
  - nodefaults
dependencies:
  - python=3.11
  - fastapi / uvicorn / pydantic / ...
  - conda-pack
  - r-base=4.4
  - r-plumber / r-jsonlite
  # ───── ONT 工具 ─────
  - nanopack
  - minimap2
  - samtools
  - seqkit
  - bedtools                     # 转录本/基因区间操作
  # ───── 细菌分析 ─────
  # (操纵子预测工具需确认，可能需 pip 安装)
  # ───── R 统计包 ─────
  - r-DESeq2
  - r-pheatmap
  - r-ggplot2
  - r-clusterProfiler
  - r-enrichplot
  # ───── RNA 修饰 ─────
  - pip
  - pip:
    - tombo                     # ONT 修饰检测
```

### 8.4 Python runners

| Runner | 功能 |
|---|---|
| `nanofilt_runner.py` | NanoFilt QC (q≥10) |
| `minimap2_align_runner.py` | 比对 (非剪接模式) |
| `transcript_reconstruct_runner.py` | ⭐ 转录本重构 (TSS/TTS) |
| `operon_runner.py` | ⭐ 操纵子分析 (核心特色) |
| `start_codon_runner.py` | ⭐ 起始密码子分析 |
| `quant_runner.py` | 表达定量 (TPM) |
| `rna_mod_runner.py` | ⭐ 8种RNA修饰检测 + 差异修饰 |

### 8.5 R scripts

| 脚本 | 功能 |
|---|---|
| `run_diff_expression_bacteria.R` | DESeq2 差异表达 (细菌适配) |
| `run_enrichment.R` | GO/KEGG 富集 (可复用 omics-analysis) |
| `run_plots.R` | 密度/箱线图/相关性/PCA/火山图/热图 (可复用模板) |

### 8.6 数据模型

```
project module_data:
  omics-bacteria-drs:
    clean/                        # 过滤数据
    alignment/                    # BAM + 统计
    transcript_reconstruction/    # 细菌特有
      operon/                     # 操纵子
      transcript/                 # 转录本列表
      start_codon/                # 起始密码子
    quantification/               # TPM
      density/
      boxplot/
      correlation/
      pca/
    differential_expression/      # DESeq2
      deg_table/
      volcano/
      heatmap/
      enrichment/
    modification/                 # 8种修饰
      overview/
      single_sample/
        mod_table/
        distribution/
        motif/
      differential/
```

---

## 模块构建模式总结

每个模块遵循 omics-rnaseq-bulk 的标准结构:

```
modules-source/<module-id>/
├── module.yaml                    # 模块清单(必填)
├── README.md                     # 说明文档
├── conda-deps/
│   └── env.yaml                  # 独立 conda 环境(必填)
├── backend-py/
│   ├── main.py                   # FastAPI 入口(必填)
│   ├── jobs/                     # 作业调度
│   │   ├── manager.py
│   │   ├── model.py
│   │   └── resources.py
│   └── runners/                  # 分析 runner (按工具分)
│       ├── base.py               # runner 基类
│       ├── dispatcher.py         # 调度器
│       └── <tool>_runner.py      # 各工具 runner
├── backend-r/                    # R 后端(可选,有R分析时需要)
│   ├── plumber.R                 # Plumber 入口
│   ├── R/
│   │   ├── runner_base.R         # 参数/进度/日志(复用模式)
│   │   ├── api.R                 # plumber endpoints
│   │   └── ...
│   └── scripts/
│       └── run_*.R               # 分析入口脚本
├── frontend/                     # 前端 bundle(可选)
│   └── module.js
└── scripts/
    └── build-deb.sh              # deb 构建脚本(复用 rnaseq-bulk 模式)
```

---

## 待确认清单

请确认以下设计决策:

### 1. 工具选择建议（基于报告观察）

| 报告 | 报告用的工具 | 建议 | 原因 |
|---|---|---|---|
| miRNA-seq | bowtie (v1) | 改为**同时支持 bowtie2** | bowtie1 已停止维护 |
| miRNA-seq | quantifier_custom.py | **重写为 runner** | 自定义脚本，需兼容性保证 |
| 全长 lncRNA | CPC2 + PLEK | **增加 CNCI** | DRS 和 Tail Iso-seq 报告均有，三工具交集更准 |
| lncRNA/LncDRS | Guppy (v5.0.16) | **统一为 Dorado** | Dorado 是 ONT 当前主力，支持 polyA 检测 |
| DRS 修饰 | (未明确) | **确认检测工具** | 需确定用 tombo / ELIGOS / DRUMMER |
| 细菌 DRS 操纵子 | (未明确) | **确认预测工具** | 需确定算法或实现方案 |

### 2. 模块边界问题

- `ont-transcriptome` vs `translatome` vs `tail-iso-seq`: 三个模块流程高度相似（Pinfish→StringTie→gffcompare→TransDecoder→SUPPA2 等），约 60-70% 代码可共享。是否**提取公共 ONT 分析库**（类似 omics-analysis 的 enrichment_lib.R）？

### 3. 构建优先级

| 优先级 | 模块 | 预计 DEB 大小 | 预估构建时间 |
|---|---|---|---|
| **P0** | omics-mirna | ~1.5 GB | 10-15 min |
| **P1** | omics-ont-transcriptome | ~2 GB | 15-20 min |
| **P2** | omics-drs | ~2 GB | 15-20 min |
| **P3** | omics-bacteria-drs | ~1.5 GB | 10-15 min |
| **P4** | omics-ont-lncrna | ~2 GB | 15-20 min |
| **P5** | omics-ont-lncdrs | ~2 GB | 15-20 min |
| **P5** | omics-ont-translatome | ~2 GB | 15-20 min |
| **P5** | omics-tail-iso-seq | ~2 GB | 15-20 min |

### 4. 需要补充的信息

- **RNA 修饰检测工具**: DRS 和细菌 DRS 的修饰分析用什么工具？tombo / ELIGOS / DRUMMER / m6Anet？
- **细菌操纵子预测**: 是否有特定工具偏好（如 Rockhopper / 自定义脚本）？
- **SSR 分析工具**: 是否指定 MISA 或类似工具？
- **转录因子数据库**: 动物用 animalTFDB v3.0 还是 v4.0？植物用 PlantTFDB v5.0？

---

*请确认上面的设计方案，指出需要修改的地方，我再逐一调整。确认后开始构建第一个模块。*
