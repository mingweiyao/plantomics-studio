#' @plantomics-analysis
#' id: enrich_kegg
#' label: KEGG 通路富集
#' category: enrich
#' description: 对一组基因做 KEGG 通路过表达富集(clusterProfiler),输出富集表 + 点图/条形图。对应报告"PATHWAY 富集"。运行时需联网(KEGG)。
#' accepts: gene_list
#' params:
#'   - { key: organism, label: "KEGG 物种码", type: text,   default: ath }
#'   - { key: keytype,  label: "输入 ID 类型", type: select, default: TAIR, options: [TAIR, ENTREZID] }
#'   - { key: pvalue_cutoff, label: "p 阈值", type: number, default: 0.05 }
#'   - { key: qvalue_cutoff, label: "q 阈值", type: number, default: 0.2 }
#'   - { key: show_top, label: "展示前 N 条", type: int, default: 20 }
#' outputs: [kegg_enrichment.tsv, kegg_dotplot.png, kegg_barplot.png]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(clusterProfiler); library(org.At.tair.db)
  })
  f <- inputs$gene_list
  if (is.null(f) || !file.exists(f)) stop("缺 gene_list 文件")
  df <- tryCatch(read.delim(f, sep = "\t", header = TRUE, check.names = FALSE),
                 error = function(e) read.csv(f, check.names = FALSE))
  gcol <- intersect(c("gene_id", "gene", "id", "GeneID"), colnames(df))
  genes <- unique(as.character(if (length(gcol)) df[[gcol[1]]] else df[[1]]))
  genes <- genes[nzchar(genes) & !is.na(genes)]
  organism <- params$organism %||% "ath"
  keytype <- params$keytype %||% "TAIR"

  if (keytype == "TAIR") {
    map <- suppressWarnings(bitr(genes, fromType = "TAIR", toType = "ENTREZID",
                                 OrgDb = org.At.tair.db))
    entrez <- unique(map$ENTREZID)
  } else {
    entrez <- genes
  }
  cat(sprintf("映射到 ENTREZ: %d\n", length(entrez)))
  if (length(entrez) < 3) stop("可用基因太少(ID 映射后)")

  ek <- enrichKEGG(gene = entrez, organism = organism,
                   pvalueCutoff = as.numeric(params$pvalue_cutoff %||% 0.05),
                   qvalueCutoff = as.numeric(params$qvalue_cutoff %||% 0.2))
  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  if (is.null(ek) || nrow(as.data.frame(ek)) == 0) {
    writeLines("no significant pathways", file.path(out_dir, "kegg_enrichment.tsv"))
    cat("没有显著通路\n"); return(invisible(list(files = character(0))))
  }
  write.table(as.data.frame(ek), file.path(out_dir, "kegg_enrichment.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)
  n_show <- min(as.integer(params$show_top %||% 20), nrow(as.data.frame(ek)))
  if (requireNamespace("enrichplot", quietly = TRUE)) {
    suppressPackageStartupMessages(library(enrichplot))
    ggplot2::ggsave(file.path(out_dir, "kegg_dotplot.png"),
                    dotplot(ek, showCategory = n_show) + ggplot2::ggtitle("KEGG 通路富集"),
                    width = 8, height = 7, dpi = 150)
    ggplot2::ggsave(file.path(out_dir, "kegg_barplot.png"),
                    barplot(ek, showCategory = n_show) + ggplot2::ggtitle("KEGG 通路富集"),
                    width = 8, height = 7, dpi = 150)
  }
  invisible(list(files = c("kegg_enrichment.tsv", "kegg_dotplot.png", "kegg_barplot.png")))
}
`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
