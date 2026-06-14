#' @plantomics-analysis
#' id: expr_boxplot
#' label: 表达量箱线图
#' category: plot
#' description: 各样本表达量(log10)的箱线图,快速看样本间表达水平与离群。对应报告"表达量箱线图"。
#' accepts: normalized_matrix
#' params:
#'   - { key: log_base, label: "对数底", type: select, default: log10, options: [log10, log2, none] }
#' outputs: [expr_boxplot.png, expr_boxplot.pdf]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(ggplot2))
  f <- inputs$normalized_matrix
  if (is.null(f) || !file.exists(f)) stop("缺 normalized_matrix 文件")
  delim <- if (grepl("\\.csv$", f)) "," else "\t"
  m <- as.matrix(read.table(f, sep = delim, header = TRUE, check.names = FALSE, row.names = 1))
  lb <- params$log_base %||% "log10"
  mm <- if (lb == "log10") log10(m + 1) else if (lb == "log2") log2(m + 1) else m
  long <- data.frame(sample = factor(rep(colnames(mm), each = nrow(mm)), levels = colnames(mm)),
                     value = as.vector(mm))
  long <- long[is.finite(long$value), ]
  ylab <- if (lb == "none") "expression" else sprintf("%s(expression + 1)", lb)
  p <- ggplot(long, aes(sample, value, fill = sample)) +
    geom_boxplot(outlier.size = 0.3, linewidth = 0.3) +
    labs(x = NULL, y = ylab) +
    theme_bw(base_size = 11) +
    theme(legend.position = "none",
          axis.text.x = element_text(angle = 45, hjust = 1),
          panel.grid.minor = element_blank())
  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  ggsave(file.path(out_dir, "expr_boxplot.png"), p, width = 7, height = 5, dpi = 150)
  ggsave(file.path(out_dir, "expr_boxplot.pdf"), p, width = 7, height = 5)
  invisible(list(files = c("expr_boxplot.png", "expr_boxplot.pdf")))
}
`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
