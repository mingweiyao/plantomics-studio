# 参考商业转录组报告的整合(本轮)

参考《贝纳基因 RNA-seq 分析示例报告》(标准有参转录组结题报告,50 张图),对照后做了两件事。

## 1) 报告里的图 → 补进 analysis 模块(共新增 6 个分析,现共 15 个)
报告的下游/表达/富集类图,之前缺的都补成了可插拔分析:
| 新增分析 | 对应报告图 |
|---|---|
| expr_density 表达量密度分布图 | 图22/36 |
| expr_boxplot 表达量箱线图 | 图23/37 |
| deg_count 差异表达数目图 | 图27/41 |
| enrich_kegg KEGG 通路富集(dotplot+barplot) | 图32/33/46/47 |
| gsea GSEA 基因集富集(GO/KEGG) | 图34/35/48/49 |
| trend 表达趋势分析 | 图26/40 |

加上之前已有的:样本相关性(图24/38)、PCA(图25/39)、火山图(图28/42)、
聚类热图(图29/43)、GO 富集(图30/31/44/45)、WGCNA 模块聚类(图50)、MA、DESeq2/edgeR。
→ **报告里所有下游/表达/富集/网络类的图,analysis 模块现在都覆盖了。**

15 个分析全部用注册表验证了元数据解析正确。

## 2) 加强转录组(上游)模块:新增"文库质量评估"(Qualimap)
报告的"文库质量评估"(随机性分布 图6 / reads 分布 图7 / 测序饱和度 图8)是当前上游模块
缺的一环(原来只有 FastQC 看碱基质量)。本轮加上:
- conda 依赖:`qualimap`
- 新 runner `qualimap_runner.py`:对 STAR 比对的每个 BAM 跑 `qualimap rnaseq -bam -gtf`,
  产出转录本覆盖均匀性、reads 基因组来源、测序饱和度等图 + 汇总表。
- JobKind `LIBRARY_QC` + dispatcher 接线 + `/submit/library-qc` 端点。
- 前端:上游流程里新增「文库质控」节点(抽屉表单:自动从比对结果扫描 BAM + 用参考 GTF → 提交)。
- rnaseqApi 加 `submitLibraryQc`。

## 报告里仍未做的上游分析(需要重型外部工具/大数据库,后续按需做)
这些是商业报告里有、但需要额外大工具或大注释库的,本轮没做(避免半成品):
- **新基因/新转录本发现**(StringTie + gffcompare + TransDecoder):图9,10,15,16,17 —— 较可行,可下一步做。
- **功能注释**(eggNOG / diamond 比对 NR/Swiss/Pfam/KOG):图11-14 —— 需要几十 GB 注释库。
- **可变剪接**(rMATS):图18,19 —— 需要重复样本。
- **新 lncRNA 预测**(CPC2/CNCI + FEELnc):图20,21。

## 验证边界
py_compile(两个模块后端)、注册表解析(15 个分析)、esbuild 语法(Upstream.tsx / AnalysisHome.tsx /
rnaseqApi.ts)都过了。Qualimap、各 analysis.R 的 **R/工具实跑**沙箱里没法验,需要你 build 后实跑。
