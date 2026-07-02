#!/usr/bin/env Rscript
#
# DRS 模块 R 分析脚本:poly(A)尾长绘图
# =======================================
# 由 Python runner 调 Rscript 启动。
# 用法:
#   Rscript run_polya_plots.R --input <polya.tsv> --output-dir <dir>
#
# 功能:
#   1. poly(A)长度分布直方图
#   2. poly(A)长度分组比较箱线图
#   3. poly(A)与表达量散点图(含相关性)
#   4. 差异poly(A)火山图

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

# ── 参数解析 ──────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
input_file <- NULL
output_dir <- NULL
group_file <- NULL

i <- 1
while (i <= length(args)) {
  if (args[i] == "--input" && i + 1 <= length(args)) {
    input_file <- args[i + 1]; i <- i + 2
  } else if (args[i] == "--output-dir" && i + 1 <= length(args)) {
    output_dir <- args[i + 1]; i <- i + 2
  } else if (args[i] == "--groups" && i + 1 <= length(args)) {
    group_file <- args[i + 1]; i <- i + 2
  } else {
    i <- i + 1
  }
}

if (is.null(input_file) || !file.exists(input_file)) {
  stop("--input 必填且文件需存在")
}
if (is.null(output_dir)) stop("--output-dir 必填")
dir.create(output_dir, showWarnings = FALSE, recursive = TRUE)

cat(sprintf("输入: %s\n输出: %s\n", input_file, output_dir))

# ── 加载数据 ──────────────────────────────────
polya_data <- read.table(input_file, header = TRUE, sep = "\t",
                          stringsAsFactors = FALSE)
cat(sprintf("加载 %d 条 poly(A) 记录, %d 列\n",
            nrow(polya_data), ncol(polya_data)))

# Determine required columns
has_group <- "group" %in% colnames(polya_data)
has_sample <- "sample" %in% colnames(polya_data)
length_col <- if ("polya_length" %in% colnames(polya_data)) "polya_length" else "length"

# ── 1. poly(A)长度分布直方图 ──────────────────
cat("绘制 poly(A) 长度分布...\n")
pdf(file.path(output_dir, "polya_length_distribution.pdf"), width = 8, height = 6)
p1 <- ggplot(polya_data, aes(x = .data[[length_col]])) +
  geom_histogram(bins = 60, fill = "steelblue", color = "white", alpha = 0.8) +
  labs(title = "poly(A)尾长分布",
       x = "poly(A) 长度 (nt)", y = "频数") +
  theme_bw() +
  scale_x_continuous(limits = c(0, quantile(polya_data[[length_col]], 0.99, na.rm = TRUE)))
print(p1)
dev.off()

# ── 2. 分组比较(箱线图 + 提琴图) ──────────────
if (has_group) {
  cat("绘制分组 poly(A) 长度比较...\n")

  pdf(file.path(output_dir, "polya_length_by_group.pdf"), width = 8, height = 6)
  p2 <- ggplot(polya_data, aes(x = group, y = .data[[length_col]], fill = group)) +
    geom_violin(alpha = 0.6) +
    geom_boxplot(width = 0.25, fill = "white", outlier.size = 0.5) +
    labs(title = "poly(A) 长度分组比较",
         x = "分组", y = "poly(A) 长度 (nt)") +
    theme_bw() +
    theme(legend.position = "none") +
    stat_summary(fun = median, geom = "point", shape = 18,
                 size = 3, color = "red")
  print(p2)
  dev.off()

  # Per-sample within groups
  if (has_sample) {
    pdf(file.path(output_dir, "polya_length_per_sample.pdf"), width = 10, height = 6)
    p3 <- ggplot(polya_data, aes(x = sample, y = .data[[length_col]], fill = group)) +
      geom_boxplot() +
      labs(title = "poly(A) 长度每个样本分布",
           x = "样本", y = "poly(A) 长度 (nt)") +
      theme_bw() +
      theme(axis.text.x = element_text(angle = 45, hjust = 1))
    print(p3)
    dev.off()
  }
} else if (has_sample) {
  # Just per-sample
  pdf(file.path(output_dir, "polya_length_per_sample.pdf"), width = 10, height = 6)
  p3 <- ggplot(polya_data, aes(x = sample, y = .data[[length_col]], fill = sample)) +
    geom_boxplot() +
    labs(title = "poly(A) 长度每个样本分布",
         x = "样本", y = "poly(A) 长度 (nt)") +
    theme_bw() +
    theme(axis.text.x = element_text(angle = 45, hjust = 1),
          legend.position = "none")
  print(p3)
  dev.off()
}

# ── 3. 表达相关性散点图 ───────────────────────
if ("expression" %in% colnames(polya_data)) {
  cat("绘制 poly(A)-表达相关性...\n")
  pdf(file.path(output_dir, "polya_vs_expression.pdf"), width = 7, height = 6)
  cor_val <- cor(polya_data[[length_col]], polya_data$expression,
                 use = "complete.obs", method = "pearson")
  p4 <- ggplot(polya_data, aes(x = .data[[length_col]], y = expression)) +
    geom_point(alpha = 0.3, size = 0.8, color = "steelblue") +
    geom_smooth(method = "lm", color = "red", se = TRUE) +
    labs(title = sprintf("poly(A) 长度 vs 表达量 (r = %.3f)", cor_val),
         x = "poly(A) 长度 (nt)", y = "表达量 (TPM)") +
    theme_bw()
  print(p4)
  dev.off()

  # Per-transcript correlation (if transcript_id available)
  if ("transcript_id" %in% colnames(polya_data)) {
    pdf(file.path(output_dir, "polya_correlation_by_transcript.pdf"),
        width = 8, height = 6)
    trans_summary <- aggregate(
      polya_data[, c(length_col, "expression")],
      by = list(transcript = polya_data$transcript_id),
      FUN = mean, na.rm = TRUE
    )
    colnames(trans_summary)[2:3] <- c("mean_polya", "mean_expression")
    cor_t <- cor(trans_summary$mean_polya, trans_summary$mean_expression,
                 use = "complete.obs")
    p5 <- ggplot(trans_summary, aes(x = mean_polya, y = mean_expression)) +
      geom_point(alpha = 0.4, color = "darkgreen") +
      geom_smooth(method = "lm", color = "red") +
      labs(title = sprintf("转录本平均 poly(A) vs 表达 (r = %.3f)", cor_t),
           x = "平均 poly(A) 长度 (nt)", y = "平均表达量") +
      theme_bw()
    print(p5)
    dev.off()
  }
}

# ── 4. 火山图(如果有差异分析结果) ─────────────
if ("log2FC" %in% colnames(polya_data) && "p_value" %in% colnames(polya_data)) {
  cat("绘制差异 poly(A) 火山图...\n")
  polya_data$significant <- ifelse(
    polya_data$p_value < 0.05 & abs(polya_data$log2FC) > 0.5,
    "Significant", "Not significant"
  )
  pdf(file.path(output_dir, "polya_volcano.pdf"), width = 7, height = 6)
  p6 <- ggplot(polya_data, aes(x = log2FC, y = -log10(p_value + 1e-300),
                                color = significant)) +
    geom_point(alpha = 0.5, size = 1) +
    scale_color_manual(values = c("Not significant" = "grey70",
                                   "Significant" = "red")) +
    labs(title = "差异 poly(A) 长度火山图",
         x = "log2(倍数变化)", y = "-log10(P-value)") +
    geom_hline(yintercept = -log10(0.05), linetype = "dashed", color = "blue") +
    geom_vline(xintercept = c(-0.5, 0.5), linetype = "dashed", color = "blue") +
    theme_bw() +
    theme(legend.position = "bottom")
  print(p6)
  dev.off()
}

cat(sprintf("poly(A) 绘图完成 → %s\n", output_dir))
