# omics-ont-transcriptome

PlantOmics Studio 的 ONT 全长转录组分析模块。

## 功能

基于 Oxford Nanopore 三代测序的全长转录组分析流程:

- **数据处理**: Dorado/Guppy 碱基识别, NanoFilt 质控, Pychopper 全长 read 鉴定
- **比对与组装**: minimap2 比对, Pinfish 一致性转录本组装, StringTie 冗余去除
- **新转录本发现**: gffcompare 新转录本分类
- **编码区预测**: TransDecoder CDS 预测 (-m 50 --single_best_only)
- **功能注释**: 7 数据库注释 (diamond->Nr/UniProt, hmmscan->Pfam, kofam_scan->KEGG)
- **表达定量**: Salmon 转录本定量
- **结构分析**: SUPPA2 可变剪接, 融合基因检测
- **基因组特征**: SSR 分析, 转录因子鉴定

## 安装

需要先装主程序 `plantomics-studio`(>= 1.0.0)。然后:

```bash
sudo apt install ./plantomics-module-ont-transcriptome_X.X.X_amd64.deb
```

装到 `/opt/plantomics-studio/modules/omics-ont-transcriptome/`。

## 构建

```bash
bash scripts/build-deb.sh
```

`--skip-env` 跳过 conda env 创建。

## 模块协议

参见主程序文档 `docs/MODULE_DEVELOPMENT.md`。
