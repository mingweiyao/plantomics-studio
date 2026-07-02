# omics-ont-lncrna

PlantOmics Studio 的 ONT 全长 lncRNA 测序分析模块。

## 这是什么

基于 ONT 三代测序的全长 lncRNA 分析流程:
- 从 ONT raw data 到全长鉴定、rRNA 去除
- lncRNA 鉴定 (CPC2 + PLEK) 及分类 (lincRNA/Intronic/Antisense/Sense)
- 表达定量 (Salmon) 及结构分析 (SUPPA2 alternative splicing)
- 融合基因检测、SSR 分析、转录因子鉴定

## 安装

需要先装主程序 `plantomics-studio` (>= 1.0.0)。然后:

```bash
sudo apt install ./plantomics-module-ont-lncrna_X.X.X_amd64.deb
```

或者在主程序的"模块"页选择"从本地 .deb 安装"。

装到 `/opt/plantomics-studio/modules/omics-ont-lncrna/`。

## 构建

```bash
bash scripts/build-deb.sh
```

第一次构建因为要下载所有 conda 包,需要 10-20 分钟。

`--skip-env` 跳过 env 创建(复用 `build/conda-env/`)。

## 模块协议

参见主程序文档 `docs/MODULE_DEVELOPMENT.md`。
