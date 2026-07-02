#!/usr/bin/env Rscript
#
# 表达定量可视化 — 密度图/箱线图/相关性/PCA
# =============================================
# 用法: Rscript run_quant_plots.R --job-id <id> --data-dir <dir>
#
# 从 Salmon quant.sf 读取定量结果,生成:
#   1. 表达密度分布图
#   2. 表达箱线图
#   3. 样本间相关性热图
#   4. PCA 图

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
  library(pheatmap)
})

SCRIPT_DIR <- tryCatch(
  dirname(sys.frame(1)$ofile),
  error = function(e) getwd()
)
source(file.path(SCRIPT_DIR, "..", "R", "runner_base.R"))

run_quant_plots <- function() {
  job <- init_runner()
  params <- job$params
  out_dir <- output_dir()

  # 收集 quant.sf 文件
  quant_files <- params[["quant_files"]] %||% list()
  sample_names <- params[["sample_names"]] %||% names(quant_files)

  if (length(quant_files) == 0) {
    # 扫描输出目录
    sf_files <- list.files(out_dir, pattern = "quant\\.sf$",
                           recursive = TRUE, full.names = TRUE)
    if (length(sf_files) == 0) {
      stop("未找到 quant.sf 文件,请提供 quant_files 参数或确认输出目录")
    }
    quant_files <- as.list(sf_files)
  }

  if (is.null(sample_names) || length(sample_names) != length(quant_files)) {
    sample_names <- basename(dirname(unlist(quant_files)))
  }

  n <- length(quant_files)
  update_progress(10, "读取定量数据", sprintf("%d 个样本", n))

  # 合并 TPM 矩阵
  tpm_list <- list()
  for (i in seq_len(n)) {
    sf_file <- quant_files[[i]]
    if (!file.exists(sf_file)) next
    sf <- read.table(sf_file, header = TRUE, sep = "\t", stringsAsFactors = FALSE)
    tpm_list[[sample_names[i]]] <- setNames(sf$TPM, sf$Name)
    log_msg(sprintf("  读取 %s: %d 条转录本", sample_names[i], nrow(sf)))
  }

  if (length(tpm_list) == 0) {
    stop("没有读取到任何定量数据")
  }

  update_progress(30, "构建表达矩阵")
  # 构建 TPM 矩阵
  all_tx <- unique(unlist(lapply(tpm_list, names)))
  tpm_mat <- do.call(cbind, lapply(tpm_list, function(x) x[all_tx]))
  rownames(tpm_mat) <- all_tx
  tpm_mat[is.na(tpm_mat)] <- 0

  # log2(TPM+1) 转换
  log_tpm <- log2(tpm_mat + 1)

  # 1. 密度分布图
  update_progress(45, "绘制密度分布图")
  pdf(file.path(out_dir, "expression_density.pdf"), width = 10, height = 7)
  df_density <- stack(as.data.frame(log_tpm))
  colnames(df_density) <- c("Expression", "Sample")
  p <- ggplot(df_density, aes(x = Expression, color = Sample)) +
    geom_density(linewidth = 0.8) +
    labs(title = "Expression Density (log2 TPM+1)",
         x = "log2(TPM+1)", y = "Density") +
    theme_minimal() +
    theme(legend.position = "bottom")
  print(p)
  dev.off()
  log_msg("  密度分布图已保存")

  # 2. 箱线图
  update_progress(60, "绘制表达箱线图")
  pdf(file.path(out_dir, "expression_boxplot.pdf"), width = 10, height = 7)
  p <- ggplot(df_density, aes(x = Sample, y = Expression, fill = Sample)) +
    geom_boxplot(outlier.size = 0.5, outlier.alpha = 0.3) +
    labs(title = "Expression Distribution (log2 TPM+1)",
         x = "", y = "log2(TPM+1)") +
    theme_minimal() +
    theme(axis.text.x = element_text(angle = 45, hjust = 1),
          legend.position = "none")
  print(p)
  dev.off()
  log_msg("  箱线图已保存")

  # 3. 相关性热图
  if (n >= 2) {
    update_progress(75, "绘制样本相关性热图")
    cor_mat <- cor(log_tpm, method = "spearman")
    pdf(file.path(out_dir, "sample_correlation.pdf"), width = 9, height = 8)
    pheatmap(cor_mat,
             display_numbers = TRUE,
             number_format = "%.3f",
             main = "Sample Correlation (Spearman, log2 TPM+1)",
             fontsize_number = 8,
             cluster_rows = TRUE,
             cluster_cols = TRUE)
    dev.off()
    log_msg("  相关性热图已保存")

    # 4. PCA
    update_progress(85, "绘制 PCA 图")
    # 筛选高变异基因(前 500 个)
    gene_vars <- apply(log_tpm, 1, var, na.rm = TRUE)
    top_genes <- head(order(gene_vars, decreasing = TRUE), 500)
    pca_data <- t(log_tpm[top_genes, ])
    pca <- prcomp(pca_data, scale. = TRUE, center = TRUE)
    pca_var <- round(summary(pca)$importance[2, 1:2] * 100, 1)

    pca_df <- data.frame(
      PC1 = pca$x[, 1],
      PC2 = pca$x[, 2],
      Sample = colnames(log_tpm)
    )
    pdf(file.path(out_dir, "expression_pca.pdf"), width = 8, height = 7)
    p <- ggplot(pca_df, aes(x = PC1, y = PC2, label = Sample)) +
      geom_point(size = 3, alpha = 0.8) +
      geom_text(vjust = -0.8, hjust = 0.5, size = 3) +
      labs(title = "PCA of Expression Profiles",
           x = sprintf("PC1 (%.1f%%)", pca_var[1]),
           y = sprintf("PC2 (%.1f%%)", pca_var[2])) +
      theme_minimal()
    print(p)
    dev.off()
    log_msg("  PCA 图已保存")
  }

  update_progress(100, "完成")
  log_msg("=== 定量可视化完成 ===")
}

run_with_error_handling(run_quant_plots)
