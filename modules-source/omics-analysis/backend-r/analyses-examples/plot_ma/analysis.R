#' @plantomics-analysis
#' id: plot_ma
#' label: MA 图
#' category: plot
#' description: 从差异结果画 MA 图(平均表达 vs log2FC)。需要 baseMean 列(DESeq2 结果最直接)。
#' accepts: deg_table
#' params:
#'   - { key: p_cutoff,  label: "p/padj 阈值", type: number, default: 0.05 }
#'   - { key: fc_cutoff, label: "log2FC 阈值", type: number, default: 1 }
#' outputs: [ma.png, ma.pdf]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(ggplot2))
  path <- inputs$deg_table
  if (is.null(path) || !file.exists(path)) stop("缺 deg_table 文件")
  df <- read.table(path, sep = "\t", header = TRUE, check.names = FALSE)
  if (!"baseMean" %in% names(df)) stop("MA 图需要 baseMean 列(DESeq2 结果有;edgeR 没有)")

  lfc_col <- if ("log2FoldChange" %in% names(df)) "log2FoldChange" else "logFC"
  p_col   <- if ("padj" %in% names(df)) "padj" else if ("FDR" %in% names(df)) "FDR" else "pvalue"
  df$.lfc <- suppressWarnings(as.numeric(df[[lfc_col]]))
  df$.p   <- suppressWarnings(as.numeric(df[[p_col]]))

  p_cut <- as.numeric(params$p_cutoff %||% 0.05)
  fc_cut <- as.numeric(params$fc_cutoff %||% 1)
  df$cat <- "ns"
  df$cat[!is.na(df$.p) & df$.p < p_cut & df$.lfc >  fc_cut] <- "up"
  df$cat[!is.na(df$.p) & df$.p < p_cut & df$.lfc < -fc_cut] <- "down"
  df$cat <- factor(df$cat, levels = c("up", "down", "ns"))

  p <- ggplot(df, aes(x = log10(baseMean + 1), y = .lfc, color = cat)) +
    geom_point(alpha = 0.6, size = 1.3, stroke = 0) +
    scale_color_manual(values = c(up = "#d6604d", down = "#4393c3", ns = "grey75"),
                       labels = c(up = "Up", down = "Down", ns = "ns")) +
    geom_hline(yintercept = c(-fc_cut, fc_cut), linetype = "dashed", color = "grey55", linewidth = 0.4) +
    geom_hline(yintercept = 0, color = "grey40", linewidth = 0.3) +
    labs(x = expression(log[10]~"(baseMean + 1)"), y = expression(log[2]~"Fold Change"), color = NULL) +
    theme_bw(base_size = 11) + theme(panel.grid.minor = element_blank())

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  ggsave(file.path(out_dir, "ma.png"), p, width = 7, height = 6, dpi = 150)
  ggsave(file.path(out_dir, "ma.pdf"), p, width = 7, height = 6)
  invisible(list(files = c("ma.png", "ma.pdf")))
}

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
