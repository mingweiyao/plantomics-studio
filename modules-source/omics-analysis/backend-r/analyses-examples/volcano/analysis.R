#' @plantomics-analysis
#' id: volcano
#' label: 火山图
#' category: plot
#' description: 从差异分析结果画火山图,按 log2FC 和 p 值上色,标注 top 基因。
#' accepts: deg_table
#' params:
#'   - { key: fc_cutoff, label: "log2FC 阈值", type: number, default: 1 }
#'   - { key: p_cutoff,  label: "p 值阈值",   type: number, default: 0.05 }
#'   - { key: top_n,     label: "标注 top N 基因", type: int, default: 10 }
#'   - { key: use_padj,  label: "用校正后 p 值(padj)", type: bool, default: true }
#' outputs: [volcano.pdf, volcano.png]
#'
#' 约定:run(inputs, params, out_dir)
#'   inputs$deg_table —— 差异结果表的文件路径(tsv/csv),需含
#'                       log2FoldChange / pvalue / padj 列(或同义列,见下方自动识别)
#'   params           —— 上面声明的参数(已带默认值)
#'   out_dir          —— 输出目录(把图写进去)

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages({
    library(ggplot2)
    library(ggrepel)
  })

  path <- inputs$deg_table
  if (is.null(path) || !file.exists(path)) {
    stop(sprintf("找不到差异结果表: %s", path))
  }
  df <- tryCatch(
    read.delim(path, sep = "\t", check.names = FALSE),
    error = function(e) read.csv(path, check.names = FALSE)
  )

  # 自动识别列(兼容 DESeq2 / edgeR / 通用命名)
  pick <- function(cands) {
    hit <- intersect(cands, colnames(df))
    if (length(hit)) hit[1] else NA_character_
  }
  fc_col <- pick(c("log2FoldChange", "log2FC", "logFC"))
  p_col  <- if (isTRUE(params$use_padj))
              pick(c("padj", "FDR", "adj.P.Val", "pvalue", "PValue", "P.Value"))
            else
              pick(c("pvalue", "PValue", "P.Value", "padj", "FDR"))
  if (is.na(fc_col) || is.na(p_col)) {
    stop("无法识别 log2FC / p 值列,请检查差异表列名")
  }
  gene_col <- pick(c("gene", "gene_id", "id", "GeneID", "Gene"))

  df$.fc <- suppressWarnings(as.numeric(df[[fc_col]]))
  df$.p  <- suppressWarnings(as.numeric(df[[p_col]]))
  df <- df[is.finite(df$.fc) & is.finite(df$.p), , drop = FALSE]
  df$.p[df$.p <= 0] <- min(df$.p[df$.p > 0], na.rm = TRUE)
  df$.neglogp <- -log10(df$.p)

  fc_cut <- as.numeric(params$fc_cutoff)
  p_cut  <- as.numeric(params$p_cutoff)
  df$.sig <- ifelse(df$.p < p_cut & df$.fc >= fc_cut, "上调",
              ifelse(df$.p < p_cut & df$.fc <= -fc_cut, "下调", "不显著"))

  # 标注 top N(按显著性 × |FC|)
  lab <- df[df$.sig != "不显著", , drop = FALSE]
  if (nrow(lab) > 0) {
    lab$.score <- lab$.neglogp * abs(lab$.fc)
    lab <- lab[order(-lab$.score), , drop = FALSE]
    lab <- head(lab, as.integer(params$top_n))
    lab$.name <- if (!is.na(gene_col)) as.character(lab[[gene_col]]) else rownames(lab)
  }

  cols <- c("上调" = "#d6604d", "下调" = "#4393c3", "不显著" = "#bbbbbb")
  p <- ggplot(df, aes(x = .fc, y = .neglogp, color = .sig)) +
    geom_point(alpha = 0.7, size = 1.4) +
    scale_color_manual(values = cols, name = NULL) +
    geom_vline(xintercept = c(-fc_cut, fc_cut), linetype = "dashed", color = "grey50") +
    geom_hline(yintercept = -log10(p_cut), linetype = "dashed", color = "grey50") +
    labs(x = "log2 Fold Change", y = "-log10 p") +
    theme_bw(base_size = 12) +
    theme(panel.grid.minor = element_blank())
  if (exists("lab") && nrow(lab) > 0) {
    p <- p + ggrepel::geom_text_repel(
      data = lab, aes(label = .name), size = 3, max.overlaps = 20, color = "black"
    )
  }

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  ggsave(file.path(out_dir, "volcano.pdf"), p, width = 7, height = 6)
  ggsave(file.path(out_dir, "volcano.png"), p, width = 7, height = 6, dpi = 150)

  invisible(list(files = c("volcano.pdf", "volcano.png")))
}
