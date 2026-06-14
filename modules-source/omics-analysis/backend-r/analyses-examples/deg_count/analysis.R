#' @plantomics-analysis
#' id: deg_count
#' label: 差异表达数目图
#' category: plot
#' description: 统计差异结果里上调/下调基因数并画条形图。对应报告"差异表达数目图"。输入用差异结果全表(按阈值现算)。
#' accepts: deg_table
#' params:
#'   - { key: p_cutoff,  label: "p/padj 阈值", type: number, default: 0.05 }
#'   - { key: fc_cutoff, label: "log2FC 阈值", type: number, default: 1 }
#' outputs: [deg_count.png, deg_count.pdf, deg_count.tsv]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(ggplot2))
  f <- inputs$deg_table
  if (is.null(f) || !file.exists(f)) stop("缺 deg_table 文件")
  df <- read.table(f, sep = "\t", header = TRUE, check.names = FALSE)
  lfc_col <- if ("log2FoldChange" %in% names(df)) "log2FoldChange" else "logFC"
  p_col   <- if ("padj" %in% names(df)) "padj" else if ("FDR" %in% names(df)) "FDR" else "pvalue"
  lfc <- suppressWarnings(as.numeric(df[[lfc_col]]))
  pv  <- suppressWarnings(as.numeric(df[[p_col]]))
  p_cut <- as.numeric(params$p_cutoff %||% 0.05)
  fc_cut <- as.numeric(params$fc_cutoff %||% 1)
  up   <- sum(!is.na(pv) & pv < p_cut & lfc >  fc_cut)
  down <- sum(!is.na(pv) & pv < p_cut & lfc < -fc_cut)
  cnt <- data.frame(direction = factor(c("上调", "下调"), levels = c("上调", "下调")),
                    n = c(up, down))
  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  write.table(cnt, file.path(out_dir, "deg_count.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)
  p <- ggplot(cnt, aes(direction, n, fill = direction)) +
    geom_col(width = 0.6) +
    geom_text(aes(label = n), vjust = -0.3, size = 4) +
    scale_fill_manual(values = c("上调" = "#d6604d", "下调" = "#4393c3")) +
    labs(x = NULL, y = "差异基因数") +
    theme_bw(base_size = 12) +
    theme(legend.position = "none", panel.grid.minor = element_blank())
  ggsave(file.path(out_dir, "deg_count.png"), p, width = 5, height = 5, dpi = 150)
  ggsave(file.path(out_dir, "deg_count.pdf"), p, width = 5, height = 5)
  invisible(list(files = c("deg_count.png", "deg_count.pdf")))
}
`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
