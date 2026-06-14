#' @plantomics-analysis
#' id: func_annotation
#' label: 功能注释
#' category: other
#' description: 给一组基因查询功能注释(基因符号 / 全名 / GO / KEGG 通路),输出每基因一行的注释表。拟南芥 org.At.tair.db。
#' accepts: gene_list
#' params:
#'   - { key: keytype, label: "输入 ID 类型", type: select, default: TAIR, options: [TAIR, ENTREZID, SYMBOL] }
#' outputs: [annotation.tsv]
#'
#' 输入:gene_list —— 含 gene_id 列的表(差异结果/任意基因列表都行)。

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(AnnotationDbi); library(org.At.tair.db)
  })
  f <- inputs$gene_list
  if (is.null(f) || !file.exists(f)) stop("缺 gene_list 文件")
  df <- tryCatch(read.delim(f, sep = "\t", header = TRUE, check.names = FALSE),
                 error = function(e) read.csv(f, check.names = FALSE))
  gcol <- intersect(c("gene_id", "gene", "id", "GeneID"), colnames(df))
  genes <- unique(as.character(if (length(gcol)) df[[gcol[1]]] else df[[1]]))
  genes <- genes[nzchar(genes) & !is.na(genes)]
  if (length(genes) < 1) stop("没有基因")
  keytype <- params$keytype %||% "TAIR"

  want <- c("SYMBOL", "GENENAME", "GO", "PATH")
  cols <- intersect(want, AnnotationDbi::columns(org.At.tair.db))
  ann <- suppressWarnings(
    AnnotationDbi::select(org.At.tair.db, keys = genes, columns = cols, keytype = keytype))

  key <- ann[[keytype]]
  collapse <- function(v) paste(unique(v[!is.na(v) & nzchar(as.character(v))]), collapse = ";")
  out <- do.call(rbind, lapply(split(seq_len(nrow(ann)), key), function(idx) {
    row <- list(gene_id = key[idx][1])
    for (cc in cols) row[[cc]] <- collapse(as.character(ann[[cc]][idx]))
    as.data.frame(row, stringsAsFactors = FALSE)
  }))
  # KEGG PATH 号补成 ath+号,便于查阅
  if ("PATH" %in% colnames(out)) {
    out$PATH <- vapply(strsplit(out$PATH, ";"), function(ps) {
      ps <- ps[nzchar(ps)]
      if (!length(ps)) "" else paste0("ath", ps, collapse = ";")
    }, character(1))
  }

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  write.table(out, file.path(out_dir, "annotation.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)
  cat(sprintf("注释了 %d 个基因\n", nrow(out)))
  invisible(list(files = c("annotation.tsv")))
}
`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
