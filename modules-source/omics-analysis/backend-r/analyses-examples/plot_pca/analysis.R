#' @plantomics-analysis
#' id: plot_pca
#' label: PCA 图
#' category: plot
#' description: 样本 PCA。VST/log2 变换后取高变基因做主成分分析,有分组表就按组上色。
#' accepts: [count_matrix, sample_design]
#' params:
#'   - { key: transform, label: "变换方式", type: select, default: vst, options: [vst, log2, none] }
#'   - { key: top_var,   label: "高变基因数", type: int, default: 500 }
#' outputs: [pca.png, pca.pdf, pca_data.tsv]
#'
#' 输入:count_matrix 必需;sample_design 可选(给点上色用,没有也能画)。

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(ggplot2)
  })
  counts_file <- inputs$count_matrix
  if (is.null(counts_file) || !file.exists(counts_file)) stop("缺 count_matrix 文件")
  transform <- params$transform %||% "vst"
  top_var <- as.integer(params$top_var %||% 500)

  delim <- if (grepl("\\.csv$", counts_file)) "," else "\t"
  m <- as.matrix(read.table(counts_file, sep = delim, header = TRUE,
                            check.names = FALSE, row.names = 1))

  if (transform == "vst") {
    suppressPackageStartupMessages(library(DESeq2))
    storage.mode(m) <- "integer"
    if (ncol(m) < 2) stop("VST 需要至少 2 样本")
    coldata <- data.frame(row.names = colnames(m), dummy = factor(rep("a", ncol(m))))
    dds <- DESeqDataSetFromMatrix(m, coldata, ~ 1)
    dds <- dds[rowSums(counts(dds)) >= 10, ]
    mat <- if (ncol(m) >= 30) assay(vst(dds, blind = TRUE))
           else assay(varianceStabilizingTransformation(dds, blind = TRUE))
  } else if (transform == "log2") {
    mat <- log2(m + 1)
  } else {
    mat <- m
  }

  vars <- apply(mat, 1, var)
  top <- order(vars, decreasing = TRUE)[seq_len(min(top_var, nrow(mat)))]
  pca <- prcomp(t(mat[top, , drop = FALSE]), scale. = FALSE)
  pc_pct <- 100 * pca$sdev^2 / sum(pca$sdev^2)
  pca_df <- data.frame(sample = colnames(mat), PC1 = pca$x[, 1], PC2 = pca$x[, 2])

  if (!is.null(inputs$sample_design) && file.exists(inputs$sample_design)) {
    gi <- .read_design(inputs$sample_design)
    pca_df$group <- unname(gi[pca_df$sample])
  }

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  write.table(pca_df, file.path(out_dir, "pca_data.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)

  has_repel <- requireNamespace("ggrepel", quietly = TRUE)
  aes_base <- if ("group" %in% names(pca_df))
                aes(x = PC1, y = PC2, color = group, label = sample)
              else aes(x = PC1, y = PC2, label = sample)
  p <- ggplot(pca_df, aes_base) + geom_point(size = 3)
  p <- if (has_repel) p + ggrepel::geom_text_repel(size = 3)
       else p + geom_text(size = 3, vjust = -0.7, check_overlap = TRUE)
  p <- p + labs(title = "PCA",
                x = sprintf("PC1 (%.1f%%)", pc_pct[1]),
                y = sprintf("PC2 (%.1f%%)", pc_pct[2])) +
    theme_bw() + theme(plot.title = element_text(hjust = 0.5))

  ggsave(file.path(out_dir, "pca.png"), p, width = 7, height = 6, dpi = 150)
  ggsave(file.path(out_dir, "pca.pdf"), p, width = 7, height = 6)
  invisible(list(files = c("pca.png", "pca.pdf")))
}

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a

.read_design <- function(path) {
  d <- read.delim(path, sep = "\t", header = TRUE, check.names = FALSE)
  if (ncol(d) < 2) d <- read.csv(path, check.names = FALSE)
  cn <- tolower(colnames(d))
  s_col <- which(cn %in% c("sample", "samples", "id", "sample_id"))
  g_col <- which(cn %in% c("group", "condition", "groups"))
  s_col <- if (length(s_col)) s_col[1] else 1
  g_col <- if (length(g_col)) g_col[1] else 2
  setNames(as.character(d[[g_col]]), as.character(d[[s_col]]))
}
