# omics-drs — Direct RNA Sequencing 分析模块

`omics-drs` 模块为 PlantOmics Studio 提供基于 ONT (Oxford Nanopore Technologies) 直接 RNA 测序(DRS)的全流程分析。

## 特点

- **不经反转录和 PCR**:直接对天然 RNA 分子测序,保留 RNA 修饰和 poly(A)尾巴信息
- **全长转录本**:使用 Flair 进行全长转录本鉴定和定量
- **Poly(A)分析**:用 Dorado 精准估算 poly(A)尾长,支持差异分析
- **RNA 修饰检测**:支持 m6A/m5C 修饰检测(Tombo/m6Anet)
- **lncRNA 预测**:CNCI + CPC2 + PLEK 三工具 Venn 取交集

## 分析流程

```
原始数据(POD5/FAST5)
    │
    ▼
NanoFilt QC ──── 质量过滤(q>=10, len>=50) + NanoStat 统计
    │
    ├──→ minimap2 比对 ──→ Flair 全长转录本
    │       (splice-aware)     │
    │                          ├→ gffcompare (新转录本分类)
    │                          ├→ TransDecoder (CDS预测)
    │                          ├→ Salmon (表达定量)
    │                          │    └→ SUPPA2 (可变剪接)
    │                          ├→ lncRNA预测 (CNCI+CPC2+PLEK)
    │                          └→ 7数据库注释 (Nr/UniProt/Pfam/KEGG...)
    │
    ├──→ 融合转录本检测
    │
    ├──→ poly(A)尾长检测 (Dorado)
    │    └→ Poly(A)统计分析 (MW检验/表达相关)
    │
    └──→ RNA修饰检测 (m6A/m5C)
```

## 安装

```bash
# 构建 deb 包
bash scripts/build-deb.sh

# 安装
sudo apt install ./dist/plantomics-module-drs_1.0.0_amd64.deb
```

## 模块结构

- `backend-py/` — Python 后端 FastAPI 服务 + 分析 runner
- `backend-r/` — R 后端 plumber 服务 + 绘图脚本
- `frontend/` — 前端 UI 扩展
- `conda-deps/` — Conda 环境依赖定义
- `scripts/` — 构建脚本

## 依赖工具

- NanoFilt / NanoStat / SeqKit
- minimap2 / samtools
- Flair (v1.5.0+)
- gffcompare / gffread
- TransDecoder
- Diamond / HMMER / kofam_scan
- Salmon
- SUPPA2
- CNCI / CPC2 / PLEK
- Dorado
- Tombo / m6Anet

## 开发者

PlantOmics Team
