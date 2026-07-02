# omics-tail-iso-seq — Tail Iso-seq 分析模块

`omics-tail-iso-seq` 模块为 PlantOmics Studio 提供基于 ONT 全长转录本 Iso-seq (cDNA 测序)的全流程分析,包含 poly(A)尾长分析。

## 特点

- **全长转录本识别**:使用 Pychopper 进行全长/嵌合分类
- **一致性转录本**:Pinfish 一致性序列聚类和矫正
- **冗余去除**:StringTie 参考引导的组装和合并
- **Poly(A)分析**:基于 Dorado 的 poly(A)尾长估算和差异分析
- **lncRNA 预测**:CNCI + CPC2 + PLEK 三工具 Venn 取交集

## 分析流程

```
原始 FASTQ
    │
    ▼
NanoFilt QC ──── 质量过滤(q>=7)
    │
    ▼
Pychopper ────── 全长转录本识别
    │             (全长/嵌合/引物)
    ▼
minimap2 ─────── 比对到参考基因组
    │
    ├──→ Pinfish ─→ 一致性转录本
    ├──→ StringTie ─→ 组装 + 冗余去除
    │
    ├──→ gffcompare (新转录本分类)
    ├──→ TransDecoder (CDS预测)
    ├──→ 7数据库注释 (Nr/UniProt/Pfam/KEGG)
    ├──→ Salmon (表达定量)
    ├──→ SUPPA2 (可变剪接)
    ├──→ 融合转录本检测
    ├──→ lncRNA预测 (CNCI+CPC2+PLEK)
    │
    └──→ Poly(A)检测 → 统计分析
```

## 安装

```bash
# 构建 deb 包
bash scripts/build-deb.sh

# 安装
sudo apt install ./dist/plantomics-module-tail-iso-seq_1.0.0_amd64.deb
```

## 模块结构

- `backend-py/` — Python 后端 FastAPI 服务 + 分析 runner
- `backend-r/` — R 后端 plumber 服务
- `frontend/` — 前端 UI 扩展
- `conda-deps/` — Conda 环境依赖定义
- `scripts/` — 构建脚本

## 依赖工具

- NanoFilt / NanoStat
- Pychopper (cdna_classifier.py)
- minimap2 / samtools
- Pinfish
- StringTie
- gffcompare / gffread
- TransDecoder
- Diamond / HMMER / kofam_scan
- Salmon
- SUPPA2
- CNCI / CPC2 / PLEK
- Dorado

## 开发者

PlantOmics Team
