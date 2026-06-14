#' @plantomics-analysis
#' id: deg_deseq2
#' label: DESeq2 差异分析
#' category: diff
#' description: 用 DESeq2 做两组差异表达。输入计数矩阵 + 样本分组表,输出差异结果表(可喂给火山图/MA/热图)。
#' accepts: [count_matrix, sample_design]
#' params:
#'   - { key: padj_cutoff,  label: "padj 阈值",     type: number, default: 0.05 }
#'   - { key: logfc_cutoff, label: "log2FC 阈值",   type: number, default: 1 }
#'   - { key: contrast,     label: "对比(处理组,对照组,留空自动)", type: text, default: "" }
#' outputs: [deseq2_all.tsv, deseq2_sig.tsv, deseq2_up.tsv, deseq2_down.tsv, deseq2_summary.json]
#'
#' 输入:
#'   count_matrix   计数矩阵(第一列基因 id,其余列为各样本的原始 counts)
#'   sample_design  样本分组表(含 sample / group 两列;没表头则取前两列)

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(DESeq2)
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
  if (length(found) < 2) stop("样本分组表里的样本在计数矩阵列里匹配不到 2 个")
  counts <- counts[, found, drop = FALSE]
  groups <- factor(unlist(group_info[found]))
  if (length(levels(groups)) != 2) {
    stop(sprintf("DESeq2 需要正好 2 组,得到 %d 组: %s",
                 length(levels(groups)), paste(levels(groups), collapse = ", ")))
  }

  contrast <- .parse_contrast(params$contrast, levels(groups))
  cat(sprintf("Contrast: %s vs %s\n", contrast[1], contrast[2]))

  counts_mat <- as.matrix(counts)
  storage.mode(counts_mat) <- "integer"
  coldata <- data.frame(condition = groups, row.names = colnames(counts_mat))
  dds <- DESeqDataSetFromMatrix(countData = counts_mat, colData = coldata,
                                design = ~ condition)
  keep <- rowSums(counts(dds)) >= 10
  dds <- dds[keep, ]
  dds <- DESeq(dds, quiet = TRUE)
  res <- results(dds, contrast = c("condition", contrast[1], contrast[2]))

  res_df <- as.data.frame(res)
  res_df$gene_id <- rownames(res_df)
  res_df <- res_df[, c("gene_id", "baseMean", "log2FoldChange",
                       "lfcSE", "stat", "pvalue", "padj")]
  res_df <- res_df[order(res_df$padj, na.last = TRUE), ]

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  write.table(res_df, file.path(out_dir, "deseq2_all.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)
  sig <- res_df[!is.na(res_df$padj) & res_df$padj < padj_cutoff &
                  abs(res_df$log2FoldChange) > logfc_cutoff, ]
  up <- sig[sig$log2FoldChange > 0, ]
  down <- sig[sig$log2FoldChange < 0, ]
  write.table(sig,  file.path(out_dir, "deseq2_sig.tsv"),  sep = "\t", row.names = FALSE, quote = FALSE)
  write.table(up,   file.path(out_dir, "deseq2_up.tsv"),   sep = "\t", row.names = FALSE, quote = FALSE)
  write.table(down, file.path(out_dir, "deseq2_down.tsv"), sep = "\t", row.names = FALSE, quote = FALSE)
  cat(sprintf("DEG 显著: %d (上 %d, 下 %d)\n", nrow(sig), nrow(up), nrow(down)))

  summ <- list(contrast = contrast,
               cutoffs = list(padj = padj_cutoff, abs_log2FC = logfc_cutoff),
               n_sig = nrow(sig), n_up = nrow(up), n_down = nrow(down),
               samples = found)
  writeLines(toJSON(summ, auto_unbox = TRUE, pretty = TRUE),
             file.path(out_dir, "deseq2_summary.json"))
  invisible(list(files = c("deseq2_all.tsv", "deseq2_sig.tsv")))
}

# ── 小工具(本脚本私有)──
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
  c(levs[2], levs[1])   # 默认:第二组 vs 第一组
}
