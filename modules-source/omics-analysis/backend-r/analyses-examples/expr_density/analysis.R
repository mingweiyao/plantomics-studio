#' @plantomics-analysis
#' id: expr_density
#' label: 表达量密度分布图
#' category: plot
#' description: 各样本表达量(log10)的密度分布曲线,用于看整体表达分布是否一致。对应报告"表达量密度分布图"。
#' accepts: normalized_matrix
#' params:
#'   - { key: log_base, label: "对数底", type: select, default: log10, options: [log10, log2, none] }
#' outputs: [expr_density.png, expr_density.pdf]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(ggplot2))
  f <- inputs$normalized_matrix
  if (is.null(f) || !file.exists(f)) stop("缺 normalized_matrix 文件")
  delim <- if (grepl("\\.csv$", f)) "," else "\t"
  m <- as.matrix(read.table(f, sep = delim, header = TRUE, check.names = FALSE, row.names = 1))
  lb <- params$log_base %||% "log10"
  mm <- if (lb == "log10") log10(m + 1) else if (lb == "log2") log2(m + 1) else m
  long <- data.frame(sample = rep(colnames(mm), each = nrow(mm)), value = as.vector(mm))
  long <- long[is.finite(long$value), ]
  xlab <- if (lb == "none") "expression" else sprintf("%s(expression + 1)", lb)
  p <- ggplot(long, aes(value, color = sample)) +
    geom_density(linewidth = 0.6) +
    labs(x = xlab, y = "Density", color = NULL) +
    theme_bw(base_size = 11) + theme(panel.grid.minor = element_blank())
  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  ggsave(file.path(out_dir, "expr_density.png"), p, width = 7, height = 5, dpi = 150)
  ggsave(file.path(out_dir, "expr_density.pdf"), p, width = 7, height = 5)
  invisible(list(files = c("expr_density.png", "expr_density.pdf")))
}
`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
