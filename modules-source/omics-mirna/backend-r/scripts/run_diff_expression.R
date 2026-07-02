#!/usr/bin/env Rscript
#
# DESeq2 差异表达分析 runner
# 用法: Rscript run_diff_expression.R --job-id <id> --data-dir <dir>
#
# 读取 job.params:
#   counts_file      - 表达矩阵文件(TSV: miRNA_id \t sample1 \t sample2 ...)
#   metadata_file    - 样本元数据文件(TSV: sample \t condition ...)
#   condition_col    - 条件列名(默认 "condition")
#   control          - 对照组名
#   treatment        - 处理组名
#   fdr_threshold    - FDR 阈值(默认 0.05)
#   log2fc_threshold - |log2FC| 阈值(默认 1.0)
#
# 产出:
#   <output_subdir>/diff_expression_results.csv
#   <output_subdir>/diff_expression_summary.txt
#   <output_subdir>/ma_plot.png
#   <output_subdir>/volcano_plot.png
#   <output_subdir>/pca_plot.png

source("../R/runner_base.R")

run_with_error_handling(function() {
  job <- init_runner()
  params <- job$params

  counts_file <- get_param("counts_file")
  metadata_file <- get_param("metadata_file")
  condition_col <- get_param("condition_col", "condition")
  control <- get_param("control")
  treatment <- get_param("treatment")
  fdr_threshold <- as.numeric(get_param("fdr_threshold", 0.05))
  lfc_threshold <- as.numeric(get_param("log2fc_threshold", 1.0))
  out_dir <- output_dir()

  log_msg(sprintf("counts_file: %s", counts_file))
  log_msg(sprintf("metadata_file: %s", metadata_file))
  log_msg(sprintf("comparison: %s vs %s (col: %s)", control, treatment, condition_col))

  if (!file.exists(counts_file)) stop(sprintf("counts_file not found: %s", counts_file))
  if (!file.exists(metadata_file)) stop(sprintf("metadata_file not found: %s", metadata_file))

  update_progress(10, "读表达矩阵")

  # 读 counts
  counts <- as.matrix(read.table(counts_file, header = TRUE, row.names = 1,
                                 check.names = FALSE, sep = "\t"))
  log_msg(sprintf("表达矩阵: %d miRNA x %d 样本", nrow(counts), ncol(counts)))

  update_progress(20, "读元数据")

  # 读元数据
  meta <- read.table(metadata_file, header = TRUE, row.names = 1,
                     check.names = FALSE, sep = "\t")
  if (!condition_col %in% colnames(meta)) {
    stop(sprintf("元数据中无 '%s' 列", condition_col))
  }

  # 确保只取 counts 中出现的样本
  common <- intersect(colnames(counts), rownames(meta))
  if (length(common) < 3) stop("共同样本数不足(<3),无法做差异分析")

  counts <- counts[, common, drop = FALSE]
  meta <- meta[common, , drop = FALSE]
  meta[[condition_col]] <- as.factor(meta[[condition_col]])

  # 检查对照组和处理组都存在
  if (!control %in% levels(meta[[condition_col]])) {
    stop(sprintf("对照组 '%s' 不在 '%s' 列中", control, condition_col))
  }
  if (!treatment %in% levels(meta[[condition_col]])) {
    stop(sprintf("处理组 '%s' 不在 '%s' 列中", treatment, condition_col))
  }

  # 将对照组设为参考水平
  meta[[condition_col]] <- relevel(meta[[condition_col]], ref = control)

  update_progress(40, "DESeq2 分析中", indeterminate = TRUE)

  # DESeq2
  suppressPackageStartupMessages(library(DESeq2))
  dds <- DESeqDataSetFromMatrix(countData = counts,
                                colData = meta,
                                design = as.formula(paste("~", condition_col)))

  # 预过滤:至少 10 个 counts 的 miRNA
  keep <- rowSums(counts(dds)) >= 10
  dds <- dds[keep, ]
  log_msg(sprintf("预过滤后: %d miRNA", nrow(dds)))

  dds <- DESeq(dds)
  res <- results(dds, contrast = c(condition_col, treatment, control),
                 alpha = fdr_threshold)

  update_progress(70, "整理结果")

  # 转 data.frame
  res_df <- as.data.frame(res)
  res_df$miRNA <- rownames(res_df)
  res_df <- res_df[, c("miRNA", "baseMean", "log2FoldChange", "lfcSE",
                        "stat", "pvalue", "padj")]

  # 标记上下调
  res_df$regulation <- "NS"
  res_df$regulation[!is.na(res_df$padj) & res_df$padj < fdr_threshold &
                     res_df$log2FoldChange > lfc_threshold] <- "UP"
  res_df$regulation[!is.na(res_df$padj) & res_df$padj < fdr_threshold &
                     res_df$log2FoldChange < -lfc_threshold] <- "DOWN"

  # 写 CSV
  csv_path <- file.path(out_dir, "diff_expression_results.csv")
  write.csv(res_df, csv_path, row.names = FALSE)
  log_msg(sprintf("结果写入: %s", csv_path))

  # 摘要
  n_up <- sum(res_df$regulation == "UP", na.rm = TRUE)
  n_down <- sum(res_df$regulation == "DOWN", na.rm = TRUE)
  summary_text <- sprintf(
    "DESeq2 差异表达分析结果\n比较: %s vs %s\nFDR 阈值: %.3f\n|log2FC| 阈值: %.2f\n上调: %d\n下调: %d\n不变: %d\n总计: %d",
    treatment, control, fdr_threshold, lfc_threshold,
    n_up, n_down, sum(res_df$regulation == "NS", na.rm = TRUE), nrow(res_df)
  )
  summary_path <- file.path(out_dir, "diff_expression_summary.txt")
  writeLines(summary_text, summary_path)
  log_msg(summary_text)

  # 绘图准备
  update_progress(85, "绘制图表")

  suppressPackageStartupMessages(library(ggplot2))

  # MA plot
  ma <- ggplot(as.data.frame(res), aes(x = baseMean, y = log2FoldChange,
                                       color = ifelse(padj < fdr_threshold &
                                                      abs(log2FoldChange) > lfc_threshold,
                                                      "significant", "ns"))) +
    geom_point(size = 0.8, alpha = 0.6) +
    scale_x_log10() +
    scale_color_manual(values = c("significant" = "red", "ns" = "grey50")) +
    labs(title = paste("MA Plot:", treatment, "vs", control),
         x = "Mean of normalized counts", y = "log2 Fold Change") +
    theme_bw() +
    theme(legend.position = "none")
  ggsave(file.path(out_dir, "ma_plot.png"), ma, width = 8, height = 6, dpi = 150)

  # Volcano plot
  res_df$neg_log10_padj <- -log10(res_df$padj)
  volcano <- ggplot(res_df[!is.na(res_df$padj), ],
                    aes(x = log2FoldChange, y = neg_log10_padj,
                        color = regulation)) +
    geom_point(size = 0.8, alpha = 0.6) +
    scale_color_manual(values = c("UP" = "red", "DOWN" = "blue", "NS" = "grey50")) +
    geom_vline(xintercept = c(-lfc_threshold, lfc_threshold), linetype = "dashed",
               color = "grey40", linewidth = 0.5) +
    geom_hline(yintercept = -log10(fdr_threshold), linetype = "dashed",
               color = "grey40", linewidth = 0.5) +
    labs(title = paste("Volcano Plot:", treatment, "vs", control),
         x = "log2 Fold Change", y = "-log10(padj)") +
    theme_bw() +
    theme(legend.position = "right")
  ggsave(file.path(out_dir, "volcano_plot.png"), volcano, width = 8, height = 6, dpi = 150)

  # PCA plot
  vsd <- tryCatch(vst(dds), error = function(e) rlog(dds))
  pca_data <- plotPCA(vsd, intgroup = condition_col, returnData = TRUE)
  pct_var <- round(100 * attr(pca_data, "percentVar"))
  pca <- ggplot(pca_data, aes(x = PC1, y = PC2, color = get(condition_col))) +
    geom_point(size = 3) +
    labs(title = "PCA Plot",
         x = paste0("PC1: ", pct_var[1], "% variance"),
         y = paste0("PC2: ", pct_var[2], "% variance"),
         color = condition_col) +
    theme_bw()
  ggsave(file.path(out_dir, "pca_plot.png"), pca, width = 8, height = 6, dpi = 150)

  log_msg("差异表达分析完成")
})
