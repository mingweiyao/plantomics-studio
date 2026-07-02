#!/usr/bin/env Rscript
#
# 转录本密度 circos 图
# ====================
# 用法: Rscript run_circos.R --job-id <id> --data-dir <dir>
#
# 基于比对 BAM 或转录本 GTF 绘制转录本在染色体上的密度分布 circos 图。

suppressPackageStartupMessages({
  library(jsonlite)
  library(circlize)
})

SCRIPT_DIR <- tryCatch(
  dirname(sys.frame(1)$ofile),
  error = function(e) getwd()
)
source(file.path(SCRIPT_DIR, "..", "R", "runner_base.R"))

run_circos <- function() {
  job <- init_runner()
  params <- job$params
  out_dir <- output_dir()

  gtf_file <- params[["gtf_file"]] %||% file.path(out_dir, "merged.gtf")
  genome_file <- params[["genome_file"]]
  window_size <- as.integer(params[["window_size"]] %||% 1000000)

  if (!file.exists(gtf_file)) {
    stop(sprintf("GTF 文件不存在: %s", gtf_file))
  }

  update_progress(20, "读取 GTF/注释数据")

  # 读取 GTF,提取转录本位置
  gtf <- read.table(gtf_file, sep = "\t", stringsAsFactors = FALSE,
                    quote = "", comment.char = "#", fill = TRUE)
  colnames(gtf) <- c("seqid", "source", "type", "start", "end",
                      "score", "strand", "phase", "attributes")

  # 只保留 transcript/exon/CDS 等
  tx <- gtf[gtf$type == "transcript", ]
  if (nrow(tx) == 0) {
    tx <- gtf[gtf$type == "exon", ]
  }
  if (nrow(tx) == 0) {
    stop("GTF 中没有 transcript 或 exon 行")
  }

  log_msg(sprintf("  GTF 中 %d 条转录本", nrow(tx)))

  update_progress(40, "计算染色体密度")

  # 按染色体分组计算密度(用窗口)
  chrom_list <- split(tx, tx$seqid)
  # 取大于 10 条转录本的染色体
  chrom_list <- chrom_list[sapply(chrom_list, nrow) > 10]

  if (length(chrom_list) == 0) {
    stop("没有染色体包含足够的转录本(>10 条)")
  }

  # 准备 circos 数据
  circos_data <- list()
  for (chr_name in names(chrom_list)) {
    df <- chrom_list[[chr_name]]
    chr_max <- max(df$end)
    # 窗口化
    breaks <- seq(1, chr_max + window_size, by = window_size)
    counts <- hist(df$start, breaks = breaks, plot = FALSE)$counts
    midpoints <- breaks[-length(breaks)] + diff(breaks) / 2

    circos_data[[chr_name]] <- data.frame(
      chr = chr_name,
      start = breaks[-length(breaks)],
      end = breaks[-1],
      mid = midpoints,
      density = counts
    )
  }

  update_progress(65, "绘制 Circos 图")

  pdf(file.path(out_dir, "transcript_density_circos.pdf"),
      width = 10, height = 10)

  # 初始化 circos
  # 染色体长度
  chr_lengths <- sapply(circos_data, function(d) max(d$end))
  # 按长度排序
  chr_order <- names(sort(chr_lengths, decreasing = TRUE))
  # 只取前 12 条最长染色体(避免太挤)
  if (length(chr_order) > 12) {
    chr_order <- chr_order[1:12]
  }

  circos.clear()
  circos.par(
    start.degree = 90,
    gap.degree = 6,
    cell.padding = c(0.02, 0, 0.02, 0),
    track.margin = c(0.01, 0.01)
  )

  # 染色体坐标轴
  chr_bed <- data.frame(
    chr = chr_order,
    start = rep(0, length(chr_order)),
    end = chr_lengths[chr_order]
  )
  rownames(chr_bed) <- chr_order
  circos.initialize(
    factors = factor(chr_order, levels = chr_order),
    xlim = as.matrix(chr_bed[, c("start", "end")])
  )

  # 染色体标签轨道
  circos.track(
    factors = factor(chr_order, levels = chr_order),
    ylim = c(0, 1),
    track.height = 0.08,
    bg.border = NA,
    panel.fun = function(x, y) {
      chr <- CELL_META$sector.index
      xlim <- CELL_META$xlim
      ylim <- CELL_META$ylim
      circos.text(mean(xlim), mean(ylim), chr, cex = 0.7,
                  facing = "bending.inside", niceFacing = TRUE)
    }
  )

  # 密度轨道
  max_density <- max(sapply(circos_data[chr_order], function(d) max(d$density)))

  circos.track(
    factors = factor(chr_order, levels = chr_order),
    ylim = c(0, max_density),
    track.height = 0.25,
    bg.border = "grey80",
    bg.col = "grey95",
    panel.fun = function(x, y) {
      chr <- CELL_META$sector.index
      if (chr %in% names(circos_data)) {
        d <- circos_data[[chr]]
        circos.lines(d$mid / 1e6, d$density, type = "l",
                     col = ifelse(d$density > 0, "#E41A1C", "grey70"),
                     area = TRUE, border = "#E41A1C", lwd = 1.5)
      }
    }
  )

  # 添加轴标签
  circos.track(
    factors = factor(chr_order, levels = chr_order),
    ylim = c(0, max_density),
    track.height = 0.05,
    bg.border = NA,
    panel.fun = function(x, y) {
      circos.axis(h = "bottom", labels.cex = 0.5,
                  major.at = seq(0, CELL_META$xlim[2], by = 10e6),
                  labels = paste0(seq(0, round(CELL_META$xlim[2] / 1e6), by = 10), "Mb"))
    }
  )

  circos.clear()
  dev.off()

  log_msg("  Circos 图已保存")

  update_progress(100, "完成")
  log_msg("=== Circos 图绘制完成 ===")

  # 写入摘要
  summary <- list(
    n_chromosomes = length(chr_order),
    n_transcripts = nrow(tx),
    window_size = window_size,
    circos_pdf = file.path(out_dir, "transcript_density_circos.pdf")
  )
  writeLines(jsonlite::toJSON(summary, auto_unbox = TRUE, pretty = TRUE),
             file.path(out_dir, "circos_summary.json"))
}

run_with_error_handling(run_circos)
