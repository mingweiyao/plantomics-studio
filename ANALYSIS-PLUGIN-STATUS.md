# 分析模块 = 纯插件宿主(迁移完成)

## 结论
模块本身**不再内置任何分析代码**。所有分析都是 `analyses/<id>/analysis.R` 自描述脚本:
- 用户从「新增分析」向导丢进来的 → 存到 `~/.plantomics/modules/omics-analysis/analyses/`,重启仍在;
- 模块自带 9 个**示例分析**(首次启动播种到同一用户目录,用户可改可删)——它们和用户自己加的走完全相同的机制,不是写死的代码路径。

## 自带的 9 个示例分析(都已用注册表验证元数据解析正确)
| 类别 | id | 名称 | 输入(accepts) |
|---|---|---|---|
| diff | deg_deseq2 | DESeq2 差异分析 | count_matrix + sample_design |
| diff | deg_edger | edgeR 差异分析 | count_matrix + sample_design |
| plot | volcano | 火山图 | deg_table |
| plot | plot_ma | MA 图 | deg_table |
| plot | plot_pca | PCA 图 | count_matrix (+ sample_design 可选) |
| plot | plot_corr | 样本相关性热图 | count_matrix |
| plot | plot_deg_heatmap | DEG 表达热图 | normalized_matrix + deg_table (+ sample_design) |
| network | wgcna | WGCNA 共表达网络 | normalized_matrix |
| enrich | enrich_go | GO 富集(拟南芥) | gene_list |

数据流示例:DESeq2 出 `deg_table` → 喂给火山图/MA/热图;标准化矩阵 → 喂给 PCA/WGCNA;
sig 基因列表 → 喂给 GO 富集。`sample_design` 是一张两列表(sample, group),当输入文件选。

## 本轮做的迁移 + 清理
- 新写 8 个 analysis.R(deg_deseq2 / deg_edger / plot_ma / plot_pca / plot_corr /
  plot_deg_heatmap / wgcna / enrich_go),把旧脚本里"分发器 + 模板"两层合并成一个自包含的
  `run(inputs, params, out_dir)`,画图代码内联进去。(volcano 上一轮已有)
- **删掉**:`backend-r/scripts/`(全部旧脚本 + 模板)、`runners/pipeline_downstream_runner.py`。
- dispatcher:`R_RUNNERS` 清空、`PY_RUNNERS` 只留 `RUN_ANALYSIS`。
- main.py:删掉所有 `/submit/*`、`/species/*`、`/templates/*` 写死的端点。现在只剩
  通用的 `/analyses*`(列表/新增/删除/预览)、`/run`、`/jobs*`、`/health`、`/info`、`/concurrency`。
- backend-r 现在只剩基础设施:`run_analysis.R`(通用执行器)、`plumber.R`、`R/`(框架),没有分析逻辑。

## 注意 / 取舍(等你检验时定夺)
- **旧的"一键下游" pipeline 被移除了**(它是写死的编排)。如果想要"一键跑一串分析",
  得重新设计成插件友好的方式(例如一个能调用其它分析的 meta 分析),你说要不要。
- 旧的 enrichment/species 子系统(run_enrich + build_species)其实之前就**不完整**
  (它 source 的 `species_lib.R / enrichment_lib.R` 在代码库里根本不存在)。所以我没去救它,
  改成写了个**自包含的 GO 富集**(clusterProfiler + org.At.tair.db,拟南芥)。
  非拟南芥物种以后可以照这个格式加。
- `JobKind` 枚举里还留着一些旧的下游 kind 名(纯字符串常量,已无人引用,无害);
  `R/runner_base.R` 也不再被任何分析用到。属于无害残留,要清我下次清。
- 所有 analysis.R 的 **R 语法/实跑没法在我这边验**(沙箱没有 R)。注册表只验了头部元数据解析。
  这 9 个需要你 build 起来实际跑一下;有报错贴给我我修。

## 还欠你的
卸载按钮「完全不弹」:Tauri 窗口 Ctrl+Shift+I → Console → 点卸载 → 贴红色报错。
