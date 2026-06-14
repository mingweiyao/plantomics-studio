# 重建进度

## ✅ 模块拆分(本轮完成)—— 你最在意的两件事之一
按"组学模块 = 数据处理到标准化;下游单独成独立通用模块"重构:

**omics-rnaseq-bulk(转录组)= 只做数据处理,终点是标准化**
- runners 只剩:sra_download / fastqc / fastp / star_index / star_align /
  feature_counts / merge_counts / normalize / import_counts / pipeline_upstream
- backend-r/scripts 已清空(下游 R 脚本全部搬走)
- dispatcher 的 R_RUNNERS 清空、删了 pipeline_downstream
- main.py 只剩 common + 上游 submit 端点
- module.yaml 只剩"数据处理"一个菜单(删了"下游分析")

**omics-analysis(新建,通用下游分析)= 差异 / 富集 / WGCNA / 画图**
- 搬入全部下游 R 脚本:run_deg_deseq2/edger、run_enrich、run_wgcna、
  run_plot_{pca,corr,volcano,ma,deg_heatmap}、build_species + templates/
- 搬入 pipeline_downstream_runner.py
- 自己的 dispatcher(只注册下游)、main.py(common + 下游 submit 端点 + 物种/模板)
- 自己的 module.yaml(菜单"下游分析",消费标准化矩阵,不绑定具体组学)
- 自己的 conda env(下游 R 全栈:DESeq2/edgeR/clusterProfiler/enrichplot/
  WGCNA/org.At.tair.db/GO.db/pheatmap/ggplot2,无 STAR/fastp 等上游工具)
- 自己的 scripts/build-deb.sh(只验证下游 R 包,不检查 STAR 等工具)

**build-and-install.sh 已注册两个模块**:[3/6] 都构建、[4/6] 各自验证 R 包、
[5/6] 都安装、[6/6] 都检查安装目录。bash 语法已校验。

## ✅ 之前已完成并验证(后端 py_compile + 单测)
- 两个原始 bug:线程调度语义、进度动画
- 项目级计算资源 compute(创建项目设线程/并行,老项目自动迁移)
- 标准化 auto + 排除统计文件 bug 修复、STAR 索引复用、一键到标准化、MultiQC

## ⚠️ 前端尚未经 TS 编译验证(我的环境无法跑 tsc)
- 转录组前端的"下游分析"路由/页面(Downstream.tsx)现在指向已删除的后端端点 —— 
  这块前端要么移到分析模块的前端、要么删掉;属于"第 4 层 UI"一并处理。
- 请 build 一次,tsc 报错就贴给我。

## 还没做
- **UI 重做(第 4 层)**:你最在意的另一件事,还没动。模块结构现在定了,可以开始。
- 把转录组的 conda env 里下游 R 库(DESeq2 等,现在它不用了)裁掉(优化,非必须)。

## 构建
    bash build-and-install.sh          # 首次/装齐依赖(两个模块的 conda env 都会建,较久)
    bash build-and-install.sh --skip-env   # 仅验证代码改动(复用已建 env)
