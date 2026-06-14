#' @plantomics-analysis
#' id: plot_corr
#' label: 样本相关性热图
#' category: plot
#' description: 计算样本两两相关(Pearson/Spearman)并画热图,用于看样本聚类/离群。
#' accepts: count_matrix
#' params:
#'   - { key: method, label: "相关方法", type: select, default: pearson, options: [pearson, spearman] }
#' outputs: [sample_corr.png, sample_corr.pdf, sample_corr.tsv]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(pheatmap))
  counts_file <- inputs$count_matrix
  if (is.null(counts_file) || !file.exists(counts_file)) stop("缺 count_matrix 文件")
  method <- params$method %||% "pearson"

  delim <- if (grepl("\\.csv$", counts_file)) "," else "\t"
  m <- as.matrix(read.table(counts_file, sep = delim, header = TRUE,
                            check.names = FALSE, row.names = 1))
  m <- log2(m + 1)
  cor_mat <- cor(m, method = method)

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  write.table(cor_mat, file.path(out_dir, "sample_corr.tsv"),
              sep = "\t", quote = FALSE, col.names = NA)

  pheatmap(cor_mat, display_numbers = TRUE, number_format = "%.2f",
           main = sprintf("Sample correlation (%s)", method),
           filename = file.path(out_dir, "sample_corr.png"),
           width = 7, height = 6)
  pheatmap(cor_mat, display_numbers = TRUE, number_format = "%.2f",
           main = sprintf("Sample correlation (%s)", method),
           filename = file.path(out_dir, "sample_corr.pdf"),
           width = 7, height = 6)
  invisible(list(files = c("sample_corr.png", "sample_corr.pdf")))
}

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
