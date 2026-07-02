# omics-mirna 模块 - plumber API 端点
# ======================================
# 由 plumber.R 加载,提供同步 R 端点。
# 异步任务(runner)通过 dispatcher -> Rscript 直接调 scripts/ 下的脚本。

MODULE_ID <- "omics-mirna"
MODULE_VERSION <- "1.0.0"

#* @get /health
function() {
  list(
    status = "ok",
    module_id = MODULE_ID,
    version = MODULE_VERSION,
    backend = "r"
  )
}

#* @get /info
function() {
  list(
    module_id = MODULE_ID,
    version = MODULE_VERSION,
    backend = "r",
    supported_jobs = list(
      "diff_expression",
      "target_prediction",
      "enrichment",
      "clustering",
      "coexpression"
    ),
    data_dir = Sys.getenv("PLANTOMICS_DATA_DIR", ""),
    module_data_dir = Sys.getenv("MODULE_DATA_DIR", "")
  )
}

#* @get /tools/list
function(res) {
  cat("[api.R] /tools/list called\n")
  list(
    tools = list(
      list(
        id = "diff_expression",
        name = "差异表达分析",
        description = "DESeq2 差异表达分析",
        params = list(
          counts_file = list(type = "string", required = TRUE,
                            description = "counts 矩阵文件路径"),
          metadata_file = list(type = "string", required = TRUE,
                              description = "样本元数据文件路径"),
          condition_col = list(type = "string", default = "condition",
                              description = "条件列名"),
          control = list(type = "string", required = TRUE,
                        description = "对照组名"),
          treatment = list(type = "string", required = TRUE,
                          description = "处理组名"),
          fdr_threshold = list(type = "number", default = 0.05),
          log2fc_threshold = list(type = "number", default = 1.0)
        )
      ),
      list(
        id = "target_prediction",
        name = "靶基因预测",
        description = "miRanda 靶基因预测",
        params = list(
          mirna_fasta = list(type = "string", required = TRUE,
                            description = "miRNA 序列 FASTA"),
          utr_fasta = list(type = "string", required = TRUE,
                          description = "3'UTR 序列 FASTA"),
          score_threshold = list(type = "number", default = 140),
          energy_threshold = list(type = "number", default = -20)
        )
      ),
      list(
        id = "enrichment",
        name = "GO/KEGG 富集分析",
        description = "靶基因 GO/KEGG 富集分析",
        params = list(
          gene_list = list(type = "array", required = TRUE,
                          description = "靶基因列表"),
          organism = list(type = "string", default = "ath",
                         description = "物种代码(如 ath, hsa, mmu)"),
          pvalue_cutoff = list(type = "number", default = 0.05),
          qvalue_cutoff = list(type = "number", default = 0.2)
        )
      ),
      list(
        id = "clustering",
        name = "miRNA 表达聚类",
        description = "层次聚类与热图",
        params = list(
          expression_file = list(type = "string", required = TRUE,
                                description = "标准化表达矩阵"),
          n_clusters = list(type = "integer", default = 4),
          distance = list(type = "string", default = "euclidean")
        )
      ),
      list(
        id = "coexpression",
        name = "miRNA-mRNA 共表达",
        description = "miRNA-mRNA 共表达网络分析",
        params = list(
          mirna_expression_file = list(type = "string", required = TRUE),
          mrna_expression_file = list(type = "string", required = TRUE),
          correlation_method = list(type = "string", default = "spearman"),
          cutoff = list(type = "number", default = 0.7),
          pvalue_cutoff = list(type = "number", default = 0.05)
        )
      )
    )
  )
}
