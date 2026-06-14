#' @plantomics-analysis
#' id: plot_deg_heatmap
#' label: DEG 表达热图
#' category: plot
#' description: 取 top N 显著差异基因,在标准化矩阵上画跨样本表达热图(可按组注释、按行标准化)。
#' accepts: [normalized_matrix, deg_table, sample_design]
#' params:
#'   - { key: top_n, label: "Top N 基因", type: int, default: 50 }
#'   - { key: scale, label: "标准化",     type: select, default: row, options: [row, none] }
#' outputs: [deg_heatmap.png, deg_heatmap.pdf]
#'
#' 输入:normalized_matrix(TPM/CPM 等)+ deg_table(取 top 基因);sample_design 可选(列注释)。

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(pheatmap))
  norm_file <- inputs$normalized_matrix
  deg_file  <- inputs$deg_table
  if (is.null(norm_file) || !file.exists(norm_file)) stop("缺 normalized_matrix 文件")
  if (is.null(deg_file)  || !file.exists(deg_file))  stop("缺 deg_table 文件")
  top_n <- as.integer(params$top_n %||% 50)
  scale_mode <- params$scale %||% "row"

  deg <- read.table(deg_file, sep = "\t", header = TRUE, check.names = FALSE)
  p_col <- if ("padj" %in% names(deg)) "padj" else if ("FDR" %in% names(deg)) "FDR" else "pvalue"
  if (!"gene_id" %in% names(deg)) deg$gene_id <- rownames(deg)
  deg <- deg[!is.na(deg[[p_col]]), ]
  deg <- deg[order(deg[[p_col]]), ]
  top_genes <- head(deg$gene_id, top_n)

  delim <- if (grepl("\\.csv$", norm_file)) "," else "\t"
  m <- as.matrix(read.table(norm_file, sep = delim, header = TRUE,
                            check.names = FALSE, row.names = 1))
  m <- log2(m + 1)
  m_sub <- m[rownames(m) %in% top_genes, , drop = FALSE]
  if (nrow(m_sub) < 2) stop("匹配到的 top 基因太少(检查 deg_table 的 gene_id 是否与矩阵行名一致)")

  ann_col <- NA
  if (!is.null(inputs$sample_design) && file.exists(inputs$sample_design)) {
    gi <- .read_design(inputs$sample_design)
    s <- intersect(colnames(m_sub), names(gi))
    if (length(s)) ann_col <- data.frame(group = unname(gi[s]), row.names = s)
  }

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  args <- list(mat = m_sub, scale = scale_mode,
               show_rownames = nrow(m_sub) <= 60, show_colnames = TRUE,
               annotation_col = ann_col, main = sprintf("Top %d DEG", nrow(m_sub)))
  do.call(pheatmap, c(args, list(filename = file.path(out_dir, "deg_heatmap.png"), width = 7, height = 8)))
  do.call(pheatmap, c(args, list(filename = file.path(out_dir, "deg_heatmap.pdf"), width = 7, height = 8)))
  invisible(list(files = c("deg_heatmap.png", "deg_heatmap.pdf")))
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
