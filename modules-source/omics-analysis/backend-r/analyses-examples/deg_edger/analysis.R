#' @plantomics-analysis
#' id: deg_edger
#' label: edgeR 差异分析
#' category: diff
#' description: 用 edgeR(QL-F 检验)做两组差异表达。适合无重复或小样本;输出差异结果表。
#' accepts: [count_matrix, sample_design]
#' params:
#'   - { key: padj_cutoff,  label: "FDR 阈值",    type: number, default: 0.05 }
#'   - { key: logfc_cutoff, label: "log2FC 阈值", type: number, default: 1 }
#'   - { key: contrast,     label: "对比(处理组,对照组,留空自动)", type: text, default: "" }
#' outputs: [edger_all.tsv, edger_sig.tsv, edger_up.tsv, edger_down.tsv, edger_summary.json]

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(edgeR)
    library(jsonlite)
  })
  counts_file <- inputs$count_matrix
  design_file <- inputs$sample_design
  if (is.null(counts_file) || !file.exists(counts_file)) stop("缺 count_matrix 文件")
  if (is.null(design_file) || !file.exists(design_file)) stop("缺 sample_design 文件")

  padj_cutoff  <- as.numeric(params$padj_cutoff %||% 0.05)
  logfc_cutoff <- as.numeric(params$logfc_cutoff %||% 1)

  delim <- if (grepl("\\.csv$", counts_file)) "," else "\t"
  counts <- read.table(counts_file, sep = delim, header = TRUE,
                        check.names = FALSE, row.names = 1)
  group_info <- .read_design(design_file)
  found <- intersect(names(group_info), colnames(counts))
  if (length(found) < 2) stop("样本匹配太少")
  counts <- counts[, found, drop = FALSE]
  groups <- factor(unlist(group_info[found]))
  if (length(levels(groups)) != 2) stop(sprintf("edgeR 需要 2 组,得到 %d", length(levels(groups))))
  contrast <- .parse_contrast(params$contrast, levels(groups))
  cat(sprintf("Contrast: %s vs %s\n", contrast[1], contrast[2]))

  dge <- DGEList(counts = as.matrix(counts), group = groups)
  keep <- filterByExpr(dge)
  dge <- dge[keep, , keep.lib.sizes = FALSE]
  dge <- calcNormFactors(dge)
  design <- model.matrix(~ 0 + groups)
  colnames(design) <- levels(groups)
  dge <- estimateDisp(dge, design)
  fit <- glmQLFit(dge, design)
  con <- makeContrasts(contrasts = paste0(contrast[1], "-", contrast[2]), levels = design)
  qlf <- glmQLFTest(fit, contrast = con)

  tt <- topTags(qlf, n = Inf, sort.by = "PValue")$table
  tt$gene_id <- rownames(tt)
  out_cols <- intersect(c("gene_id", "logFC", "logCPM", "F", "PValue", "FDR"), names(tt))
  tt <- tt[, out_cols]

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  write.table(tt, file.path(out_dir, "edger_all.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)
  sig <- tt[!is.na(tt$FDR) & tt$FDR < padj_cutoff & abs(tt$logFC) > logfc_cutoff, ]
  up <- sig[sig$logFC > 0, ]; down <- sig[sig$logFC < 0, ]
  write.table(sig,  file.path(out_dir, "edger_sig.tsv"),  sep = "\t", row.names = FALSE, quote = FALSE)
  write.table(up,   file.path(out_dir, "edger_up.tsv"),   sep = "\t", row.names = FALSE, quote = FALSE)
  write.table(down, file.path(out_dir, "edger_down.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)
  cat(sprintf("edgeR DEG: %d (上 %d, 下 %d)\n", nrow(sig), nrow(up), nrow(down)))

  summ <- list(contrast = contrast,
               cutoffs = list(padj = padj_cutoff, abs_log2FC = logfc_cutoff),
               n_sig = nrow(sig), n_up = nrow(up), n_down = nrow(down), samples = found)
  writeLines(toJSON(summ, auto_unbox = TRUE, pretty = TRUE),
             file.path(out_dir, "edger_summary.json"))
  invisible(list(files = c("edger_all.tsv", "edger_sig.tsv")))
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

.parse_contrast <- function(contrast_str, levs) {
  if (!is.null(contrast_str) && nzchar(contrast_str)) {
    parts <- trimws(strsplit(contrast_str, ",")[[1]])
    if (length(parts) == 2 && all(parts %in% levs)) return(parts)
  }
  c(levs[2], levs[1])
}
