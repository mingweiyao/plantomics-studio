#' @plantomics-analysis
#' id: enrich_go
#' label: GO 富集(拟南芥)
#' category: enrich
#' description: 对一组基因做 GO 过表达富集(clusterProfiler + org.At.tair.db),输出富集表 + 点图/条形图。拟南芥 TAIR ID。
#' accepts: gene_list
#' params:
#'   - { key: keytype,  label: "基因 ID 类型", type: select, default: TAIR, options: [TAIR, ENTREZID, SYMBOL] }
#'   - { key: ontology, label: "本体论",       type: select, default: BP, options: [BP, MF, CC, ALL] }
#'   - { key: pvalue_cutoff, label: "p 阈值",  type: number, default: 0.05 }
#'   - { key: qvalue_cutoff, label: "q 阈值",  type: number, default: 0.2 }
#'   - { key: show_top, label: "展示前 N 条",  type: int,    default: 20 }
#' outputs: [go_enrichment.tsv, go_dotplot.png, go_barplot.png]
#'
#' 输入:gene_list —— 含 gene_id 列的表(差异分析的 sig 表即可);取该列做富集。

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(clusterProfiler)
    library(org.At.tair.db)
  })
  gl_file <- inputs$gene_list
  if (is.null(gl_file) || !file.exists(gl_file)) stop("缺 gene_list 文件")

  keytype  <- params$keytype %||% "TAIR"
  ont      <- params$ontology %||% "BP"
  pcut     <- as.numeric(params$pvalue_cutoff %||% 0.05)
  qcut     <- as.numeric(params$qvalue_cutoff %||% 0.2)
  show_top <- as.integer(params$show_top %||% 20)

  df <- tryCatch(read.delim(gl_file, sep = "\t", header = TRUE, check.names = FALSE),
                 error = function(e) read.csv(gl_file, check.names = FALSE))
  gcol <- intersect(c("gene_id", "gene", "id", "GeneID"), colnames(df))
  genes <- if (length(gcol)) as.character(df[[gcol[1]]]) else as.character(df[[1]])
  genes <- unique(genes[nzchar(genes) & !is.na(genes)])
  cat(sprintf("输入基因数: %d\n", length(genes)))
  if (length(genes) < 3) stop("基因太少,无法富集")

  ego <- enrichGO(gene = genes, OrgDb = org.At.tair.db, keyType = keytype,
                  ont = ont, pvalueCutoff = pcut, qvalueCutoff = qcut,
                  readable = FALSE)

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  if (is.null(ego) || nrow(as.data.frame(ego)) == 0) {
    cat("没有显著富集条目\n")
    writeLines("no significant terms", file.path(out_dir, "go_enrichment.tsv"))
    return(invisible(list(files = character(0))))
  }

  res <- as.data.frame(ego)
  write.table(res, file.path(out_dir, "go_enrichment.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)
  cat(sprintf("显著条目: %d\n", nrow(res)))

  ok_plot <- requireNamespace("enrichplot", quietly = TRUE)
  n_show <- min(show_top, nrow(res))
  if (ok_plot) {
    suppressPackageStartupMessages(library(enrichplot))
    p1 <- dotplot(ego, showCategory = n_show) + ggplot2::ggtitle(sprintf("GO %s 富集", ont))
    ggplot2::ggsave(file.path(out_dir, "go_dotplot.png"), p1,
                    width = 8, height = 7, dpi = 150)
    p2 <- barplot(ego, showCategory = n_show) + ggplot2::ggtitle(sprintf("GO %s 富集", ont))
    ggplot2::ggsave(file.path(out_dir, "go_barplot.png"), p2,
                    width = 8, height = 7, dpi = 150)
  }
  invisible(list(files = c("go_enrichment.tsv", "go_dotplot.png", "go_barplot.png")))
}

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
