#' @plantomics-analysis
#' id: trend
#' label: 表达趋势分析
#' category: plot
#' description: 按分组求平均表达,对高变基因做标准化 + 聚类,画各类基因随分组变化的趋势线。对应报告"趋势分析结果图"。
#' accepts: [normalized_matrix, sample_design]
#' params:
#'   - { key: n_clusters, label: "趋势类别数", type: int, default: 6 }
#'   - { key: top_genes,  label: "高变基因数", type: int, default: 2000 }
#' outputs: [trend_clusters.tsv, trend.png, trend.pdf]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(ggplot2))
  f <- inputs$normalized_matrix
  d <- inputs$sample_design
  if (is.null(f) || !file.exists(f)) stop("缺 normalized_matrix 文件")
  if (is.null(d) || !file.exists(d)) stop("缺 sample_design 文件(用于按组求均值)")
  delim <- if (grepl("\\.csv$", f)) "," else "\t"
  m <- as.matrix(read.table(f, sep = delim, header = TRUE, check.names = FALSE, row.names = 1))
  gi <- .read_design(d)
  m <- log2(m + 1)

  # 按组求均值
  groups <- gi[colnames(m)]
  grp_levels <- unique(stats::na.omit(groups))
  gm <- sapply(grp_levels, function(g) rowMeans(m[, names(groups)[groups == g & !is.na(groups)], drop = FALSE]))
  gm <- gm[, grp_levels, drop = FALSE]

  # 取高变基因 + 行标准化(z-score)
  top_n <- as.integer(params$top_genes %||% 2000)
  v <- apply(gm, 1, var); v[is.na(v)] <- 0
  gm <- gm[order(v, decreasing = TRUE)[seq_len(min(top_n, nrow(gm)))], , drop = FALSE]
  z <- t(scale(t(gm)))
  z <- z[stats::complete.cases(z), , drop = FALSE]

  k <- as.integer(params$n_clusters %||% 6)
  set.seed(1)
  km <- stats::kmeans(z, centers = min(k, nrow(z) - 1), iter.max = 50, nstart = 5)

  assign_df <- data.frame(gene_id = rownames(z), cluster = km$cluster)
  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  write.table(assign_df, file.path(out_dir, "trend_clusters.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)

  # 长表用于画趋势线
  long <- do.call(rbind, lapply(seq_len(nrow(z)), function(i) {
    data.frame(gene_id = rownames(z)[i],
               cluster = paste0("Cluster ", km$cluster[i]),
               group = factor(colnames(z), levels = colnames(z)),
               z = as.numeric(z[i, ]))
  }))
  centers <- do.call(rbind, lapply(sort(unique(km$cluster)), function(c) {
    data.frame(cluster = paste0("Cluster ", c),
               group = factor(colnames(z), levels = colnames(z)),
               z = colMeans(z[km$cluster == c, , drop = FALSE]))
  }))
  p <- ggplot(long, aes(group, z, group = gene_id)) +
    geom_line(alpha = 0.06, color = "grey55") +
    geom_line(data = centers, aes(group, z, group = cluster),
              color = "#d6604d", linewidth = 1, inherit.aes = FALSE) +
    facet_wrap(~ cluster) +
    labs(x = NULL, y = "标准化表达(z-score)") +
    theme_bw(base_size = 11) +
    theme(axis.text.x = element_text(angle = 45, hjust = 1),
          panel.grid.minor = element_blank())
  ggsave(file.path(out_dir, "trend.png"), p, width = 8, height = 6, dpi = 150)
  ggsave(file.path(out_dir, "trend.pdf"), p, width = 8, height = 6)
  invisible(list(files = c("trend.png", "trend.pdf", "trend_clusters.tsv")))
}
`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
.read_design <- function(path) {
  dd <- read.delim(path, sep = "\t", header = TRUE, check.names = FALSE)
  if (ncol(dd) < 2) dd <- read.csv(path, check.names = FALSE)
  cn <- tolower(colnames(dd))
  s_col <- which(cn %in% c("sample", "samples", "id", "sample_id"))
  g_col <- which(cn %in% c("group", "condition", "groups"))
  s_col <- if (length(s_col)) s_col[1] else 1
  g_col <- if (length(g_col)) g_col[1] else 2
  setNames(as.character(dd[[g_col]]), as.character(dd[[s_col]]))
}
