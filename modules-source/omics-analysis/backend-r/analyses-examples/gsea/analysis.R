#' @plantomics-analysis
#' id: gsea
#' label: GSEA 基因集富集
#' category: enrich
#' description: 用全部基因按 log2FC 排序做 GSEA(GO 或 KEGG),输出富集表 + 点图。对应报告"GSEA_GO / GSEA_PATHWAY"。
#' accepts: deg_table
#' params:
#'   - { key: target,  label: "基因集", type: select, default: GO_BP, options: [GO_BP, GO_MF, GO_CC, KEGG] }
#'   - { key: keytype, label: "输入 ID 类型", type: select, default: TAIR, options: [TAIR, ENTREZID] }
#'   - { key: organism, label: "KEGG 物种码(KEGG 时用)", type: text, default: ath }
#'   - { key: pvalue_cutoff, label: "p 阈值", type: number, default: 0.05 }
#'   - { key: show_top, label: "展示前 N 条", type: int, default: 15 }
#' outputs: [gsea_result.tsv, gsea_dotplot.png]
#'
#' 输入:deg_table 需含 gene_id 与 log2FoldChange/logFC 列(用全表,不要只给显著的)。

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(clusterProfiler); library(org.At.tair.db)
  })
  f <- inputs$deg_table
  if (is.null(f) || !file.exists(f)) stop("缺 deg_table 文件")
  df <- read.table(f, sep = "\t", header = TRUE, check.names = FALSE)
  if (!"gene_id" %in% names(df)) df$gene_id <- rownames(df)
  lfc_col <- if ("log2FoldChange" %in% names(df)) "log2FoldChange" else "logFC"
  df <- df[!is.na(df[[lfc_col]]), ]

  ids <- as.character(df$gene_id); lfc <- as.numeric(df[[lfc_col]])
  keytype <- params$keytype %||% "TAIR"; target <- params$target %||% "GO_BP"
  pcut <- as.numeric(params$pvalue_cutoff %||% 0.05)

  if (keytype == "TAIR" && grepl("^KEGG$", target)) {
    map <- suppressWarnings(bitr(ids, "TAIR", "ENTREZID", org.At.tair.db))
    m <- match(map$TAIR, ids); gl <- setNames(lfc[m], map$ENTREZID)
  } else {
    gl <- setNames(lfc, ids)
  }
  gl <- sort(gl[!duplicated(names(gl))], decreasing = TRUE)
  cat(sprintf("排序基因列表长度: %d\n", length(gl)))

  res <- if (grepl("^GO_", target)) {
    gseGO(geneList = gl, OrgDb = org.At.tair.db, keyType = keytype,
          ont = sub("^GO_", "", target), pvalueCutoff = pcut, verbose = FALSE)
  } else {
    gseKEGG(geneList = gl, organism = params$organism %||% "ath",
            pvalueCutoff = pcut, verbose = FALSE)
  }

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  if (is.null(res) || nrow(as.data.frame(res)) == 0) {
    writeLines("no significant gene sets", file.path(out_dir, "gsea_result.tsv"))
    cat("没有显著基因集\n"); return(invisible(list(files = character(0))))
  }
  write.table(as.data.frame(res), file.path(out_dir, "gsea_result.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)
  if (requireNamespace("enrichplot", quietly = TRUE)) {
    suppressPackageStartupMessages(library(enrichplot))
    n_show <- min(as.integer(params$show_top %||% 15), nrow(as.data.frame(res)))
    ggplot2::ggsave(file.path(out_dir, "gsea_dotplot.png"),
                    dotplot(res, showCategory = n_show) + ggplot2::ggtitle(paste("GSEA", target)),
                    width = 8, height = 7, dpi = 150)
  }
  invisible(list(files = c("gsea_result.tsv", "gsea_dotplot.png")))
}
`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
