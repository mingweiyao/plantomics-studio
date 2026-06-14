#' @plantomics-analysis
#' id: wgcna
#' label: WGCNA 共表达网络
#' category: network
#' description: 在标准化表达矩阵上构建加权基因共表达网络,自动选软阈值、划分模块,导出模块归属、模块特征基因与聚类树图。
#' accepts: normalized_matrix
#' params:
#'   - { key: soft_power,       label: "软阈值(0=自动选)",   type: int,    default: 0 }
#'   - { key: min_module_size,  label: "最小模块基因数",       type: int,    default: 30 }
#'   - { key: top_var_genes,    label: "保留高变基因数",       type: int,    default: 5000 }
#'   - { key: merge_cut_height, label: "模块合并高度",         type: number, default: 0.25 }
#' outputs: [module_assignment.tsv, module_eigengenes.tsv, soft_threshold.png, dendrogram.png]
#'
#' 输入:normalized_matrix(基因 × 样本,建议 TPM/CPM/VST 等标准化值)。

run <- function(inputs, params, out_dir) {
  suppressPackageStartupMessages(library(WGCNA))
  norm_file <- inputs$normalized_matrix
  if (is.null(norm_file) || !file.exists(norm_file)) stop("缺 normalized_matrix 文件")

  soft_power     <- as.integer(params$soft_power %||% 0)
  min_mod_size   <- as.integer(params$min_module_size %||% 30)
  top_var_genes  <- as.integer(params$top_var_genes %||% 5000)
  merge_height   <- as.numeric(params$merge_cut_height %||% 0.25)

  nthreads <- suppressWarnings(as.integer(Sys.getenv("PLANTOMICS_JOB_THREADS", "1")))
  if (is.na(nthreads) || nthreads < 1) nthreads <- 1
  WGCNA::allowWGCNAThreads(nThreads = nthreads)

  delim <- if (grepl("\\.csv$", norm_file)) "," else "\t"
  m <- as.matrix(read.table(norm_file, sep = delim, header = TRUE,
                            check.names = FALSE, row.names = 1))
  # 取高变基因,降维
  if (nrow(m) > top_var_genes) {
    vars <- apply(m, 1, var)
    m <- m[order(vars, decreasing = TRUE)[seq_len(top_var_genes)], , drop = FALSE]
  }
  datExpr <- t(m)                    # WGCNA 要 样本 × 基因

  gsg <- goodSamplesGenes(datExpr, verbose = 0)
  if (!gsg$allOK) datExpr <- datExpr[gsg$goodSamples, gsg$goodGenes, drop = FALSE]
  cat(sprintf("WGCNA 输入: %d 样本 × %d 基因\n", nrow(datExpr), ncol(datExpr)))
  if (nrow(datExpr) < 4) stop("WGCNA 至少需要 4 个样本")

  dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

  # ── 选软阈值 ──
  powers <- c(1:10, seq(12, 20, 2))
  sft <- pickSoftThreshold(datExpr, powerVector = powers, verbose = 0,
                           networkType = "signed")
  chosen <- soft_power
  if (chosen <= 0) {
    chosen <- sft$powerEstimate
    if (is.na(chosen)) chosen <- 6L          # 兜底
  }
  cat(sprintf("软阈值 power = %d\n", chosen))

  png(file.path(out_dir, "soft_threshold.png"), width = 900, height = 450, res = 110)
  par(mfrow = c(1, 2))
  plot(sft$fitIndices[, 1], -sign(sft$fitIndices[, 3]) * sft$fitIndices[, 2],
       xlab = "Soft power", ylab = "Scale-free R^2", type = "n", main = "无标度拟合")
  text(sft$fitIndices[, 1], -sign(sft$fitIndices[, 3]) * sft$fitIndices[, 2],
       labels = powers, col = "red")
  abline(h = 0.85, col = "red", lty = 2)
  plot(sft$fitIndices[, 1], sft$fitIndices[, 5], xlab = "Soft power",
       ylab = "Mean connectivity", type = "n", main = "平均连通度")
  text(sft$fitIndices[, 1], sft$fitIndices[, 5], labels = powers, col = "red")
  dev.off()

  # ── 构网 + 划模块 ──
  net <- blockwiseModules(datExpr, power = chosen, networkType = "signed",
                          TOMType = "signed", minModuleSize = min_mod_size,
                          mergeCutHeight = merge_height, numericLabels = TRUE,
                          saveTOMs = FALSE, verbose = 0,
                          maxBlockSize = min(ncol(datExpr), 8000))
  module_colors <- labels2colors(net$colors)
  cat(sprintf("模块数: %d\n", length(unique(module_colors))))

  assign_df <- data.frame(gene_id = colnames(datExpr),
                          module = module_colors,
                          module_label = net$colors)
  write.table(assign_df, file.path(out_dir, "module_assignment.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)

  MEs <- moduleEigengenes(datExpr, colors = module_colors)$eigengenes
  MEs <- orderMEs(MEs)
  me_out <- data.frame(sample = rownames(datExpr), MEs, check.names = FALSE)
  write.table(me_out, file.path(out_dir, "module_eigengenes.tsv"),
              sep = "\t", row.names = FALSE, quote = FALSE)

  png(file.path(out_dir, "dendrogram.png"), width = 900, height = 500, res = 110)
  plotDendroAndColors(net$dendrograms[[1]],
                      module_colors[net$blockGenes[[1]]],
                      "模块", dendroLabels = FALSE, addGuide = TRUE,
                      main = "基因聚类树与模块")
  dev.off()

  invisible(list(files = c("module_assignment.tsv", "module_eigengenes.tsv",
                           "soft_threshold.png", "dendrogram.png")))
}

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
