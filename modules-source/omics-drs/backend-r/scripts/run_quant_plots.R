#!/usr/bin/env Rscript
#
# DRS 模块 R 分析脚本:表达定量绘图
# ==================================
# 由 Python runner 调 Rscript 启动。用法:
#   Rscript run_quant_plots.R --data-dir <dir> --output-dir <dir> [--files <salmon_quant.sf>...]
#
# 功能:
#   1. 表达密度图(TPM)
#   2. 表达盒形图
#   3. 样本间表达相关性热图
#   4. PCA 降维

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
  library(pheatmap)
})

# ── 参数解析 ──────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
data_dir <- NULL
output_dir <- NULL
input_files <- character(0)

i <- 1
while (i <= length(args)) {
  if (args[i] == "--data-dir" && i + 1 <= length(args)) {
    data_dir <- args[i + 1]; i <- i + 2
  } else if (args[i] == "--output-dir" && i + 1 <= length(args)) {
    output_dir <- args[i + 1]; i <- i + 2
  } else if (args[i] == "--files" && i + 1 <= length(args)) {
    j <- i + 1
    while (j <= length(args) && !startsWith(args[j], "--")) {
      input_files <- c(input_files, args[j])
      j <- j + 1
    }
    i <- j
  } else {
    i <- i + 1
  }
}

if (is.null(output_dir)) stop("--output-dir 必填")
dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

cat(sprintf("输出目录: %s\n", output_dir))

# ── 加载表达数据 ──────────────────────────────
load_quant <- function(files) {
  all_data <- list()
  for (f in files) {
    if (!file.exists(f)) {
      cat(sprintf("  !! 跳过: %s\n", f))
      next
    }
    sample_name <- gsub("\\.sf$", "", basename(f))
    tab <- read.table(f, header = TRUE, sep = "\t", stringsAsFactors = FALSE)
    if ("TPM" %in% colnames(tab)) {
      all_data[[sample_name]] <- tab[, c("Name", "TPM")]
      colnames(all_data[[sample_name]]) <- c("transcript_id", sample_name)
    } else if ("NumReads" %in% colnames(tab)) {
      # Normalize to TPM-like
      counts <- tab$NumReads
      eff_len <- tab$EffectiveLength
      tpm <- counts / eff_len * 1e6 / sum(counts / eff_len, na.rm = TRUE) * 1e6
      all_data[[sample_name]] <- data.frame(transcript_id = tab$Name, tpm = tpm)
      colnames(all_data[[sample_name]]) <- c("transcript_id", sample_name)
    }
    cat(sprintf("  加载 %s: %d 个转录本\n", sample_name, nrow(all_data[[sample_name]])))
  }
  if (length(all_data) == 0) stop("没有可用的定量文件")

  merge_matrix <- function(lst) {
    merged <- lst[[1]]
    for (nm in names(lst)[-1]) {
      merged <- merge(merged, lst[[nm]], by = "transcript_id", all = TRUE)
    }
    rownames(merged) <- merged$transcript_id
    merged$transcript_id <- NULL
    merged[is.na(merged)] <- 0
    as.matrix(merged)
  }
  merge_matrix(all_data)
}

if (length(input_files) > 0) {
  expr_mat <- load_quant(input_files)
} else if (!is.null(data_dir)) {
  # Scan for quant.sf files
  sf_files <- list.files(data_dir, pattern = "quant\\.sf$",
                          recursive = TRUE, full.names = TRUE)
  if (length(sf_files) == 0) stop("未找到 quant.sf 文件")
  expr_mat <- load_quant(sf_files)
} else {
  stop("需要 --files 或 --data-dir")
}

cat(sprintf("表达矩阵: %d 个转录本, %d 个样本\n", nrow(expr_mat), ncol(expr_mat)))

# ── 1. 表达密度图 ─────────────────────────────
cat("绘制表达密度图...\n")
log_tpm <- log2(expr_mat + 1)
pdf(file.path(output_dir, "expression_density.pdf"), width = 8, height = 6)
df <- stack(as.data.frame(log_tpm))
colnames(df) <- c("log2TPM", "Sample")
p <- ggplot(df, aes(x = log2TPM, color = Sample)) +
  geom_density(linewidth = 0.8) +
  labs(title = "表达密度分布 (log2(TPM+1))",
       x = "log2(TPM+1)", y = "密度") +
  theme_bw() +
  theme(legend.position = "bottom")
print(p)
dev.off()

# ── 2. 表达盒形图 ─────────────────────────────
cat("绘制表达盒形图...\n")
pdf(file.path(output_dir, "expression_boxplot.pdf"), width = 8, height = 6)
df2 <- stack(as.data.frame(log_tpm))
colnames(df2) <- c("log2TPM", "Sample")
p2 <- ggplot(df2, aes(x = Sample, y = log2TPM, fill = Sample)) +
  geom_boxplot() +
  labs(title = "表达分布盒形图 (log2(TPM+1))",
       x = "", y = "log2(TPM+1)") +
  theme_bw() +
  theme(axis.text.x = element_text(angle = 45, hjust = 1),
        legend.position = "none")
print(p2)
dev.off()

# ── 3. 样本间相关性热图 ───────────────────────
cat("绘制相关性热图...\n")
cor_mat <- cor(log_tpm, method = "pearson")
pdf(file.path(output_dir, "expression_correlation_heatmap.pdf"),
    width = 7, height = 6)
pheatmap(cor_mat, display_numbers = TRUE, number_format = "%.3f",
         main = "样本间 Pearson 表达相关性",
         fontsize_number = 8, cluster_rows = TRUE, cluster_cols = TRUE)
dev.off()

# ── 4. PCA ────────────────────────────────────
cat("绘制 PCA 图...\n")
pca_res <- prcomp(t(log_tpm), center = TRUE, scale. = TRUE)
var_exp <- round(summary(pca_res)$importance[2, 1:2] * 100, 1)
pdf(file.path(output_dir, "expression_pca.pdf"), width = 7, height = 6)
pca_df <- data.frame(
  PC1 = pca_res$x[, 1],
  PC2 = pca_res$x[, 2],
  Sample = colnames(expr_mat)
)
p3 <- ggplot(pca_df, aes(x = PC1, y = PC2, color = Sample, label = Sample)) +
  geom_point(size = 3) +
  ggrepel::geom_text_repel(size = 3, max.overlaps = 20) +
  labs(title = "表达 PCA",
       x = sprintf("PC1 (%s%%)", var_exp[1]),
       y = sprintf("PC2 (%s%%)", var_exp[2])) +
  theme_bw() +
  theme(legend.position = "bottom")
print(p3)
suppressWarnings(dev.off())

cat(sprintf("所有绘图完成 → %s\n", output_dir))
