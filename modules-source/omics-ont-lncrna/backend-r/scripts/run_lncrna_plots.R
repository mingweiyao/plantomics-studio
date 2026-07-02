# lncRNA 分析绘图脚本
# =======================
# 生成 Venn 图、分类柱状图、长度分布图
#
# 用法:
#   Rscript run_lncrna_plots.R --job-id <id> --data-dir <dir>
#
# 参数(通过 job.params 传入):
#   cpc2_noncoding_file: str  - CPC2 非编码转录本 ID 列表
#   plek_noncoding_file: str  - PLEK 非编码转录本 ID 列表
#   lncrna_list_file: str     - 最终 lncRNA 列表 TSV
#   classification_file: str  - 分类结果 TSV
#   length_file: str          - 转录本长度 TSV (transcript_id, length)

suppressPackageStartupMessages({
  library(VennDiagram)
  library(ggplot2)
  library(jsonlite)
})

# 加载 runner_base.R
script_dir <- tryCatch({
  dirname(sys.frame(1)$ofile)
}, error = function(e) {
  args <- commandArgs(trailingOnly = FALSE)
  file_arg <- grep("^--file=", args, value = TRUE)
  if (length(file_arg) > 0) {
    dirname(sub("^--file=", "", file_arg[1]))
  } else {
    getwd()
  }
})
source(file.path(dirname(script_dir), "..", "R", "runner_base.R"))

# ── 主逻辑 ──
run <- function() {
  job <- init_runner()
  p <- job$params %||% list()

  cpc2_file <- p$cpc2_noncoding_file %||% ""
  plek_file <- p$plek_noncoding_file %||% ""
  lncrna_file <- p$lncrna_list_file %||% ""
  class_file <- p$classification_file %||% ""
  len_file <- p$length_file %||% ""

  out_dir <- output_dir()

  # ── 1. Venn 图: CPC2 ∩ PLEK ──
  update_progress(10, stage = "Venn 图")

  cpc2_ids <- character()
  plek_ids <- character()

  if (file.exists(cpc2_file)) {
    cpc2_ids <- readLines(cpc2_file, warn = FALSE)
    cpc2_ids <- cpc2_ids[nchar(cpc2_ids) > 0]
  }
  if (file.exists(plek_file)) {
    plek_ids <- readLines(plek_file, warn = FALSE)
    plek_ids <- plek_ids[nchar(plek_ids) > 0]
  }

  if (length(cpc2_ids) > 0 && length(plek_ids) > 0) {
    pdf(file.path(out_dir, "venn_cpc2_plek.pdf"), width = 6, height = 6)
    venn.plot <- draw.pairwise.venn(
      area1 = length(cpc2_ids),
      area2 = length(plek_ids),
      cross.area = length(intersect(cpc2_ids, plek_ids)),
      category = c("CPC2", "PLEK"),
      fill = c("#E41A1C", "#377EB8"),
      alpha = 0.5,
      cat.col = c("#E41A1C", "#377EB8"),
      cex = 1.5,
      cat.cex = 1.5,
      cat.pos = c(-10, 10),
      cat.dist = 0.05,
    )
    dev.off()
    log_msg(sprintf("Venn 图已保存: CPC2=%d, PLEK=%d, 交集=%d",
                    length(cpc2_ids), length(plek_ids),
                    length(intersect(cpc2_ids, plek_ids))))
  } else {
    log_msg("!! 缺少 CPC2 或 PLEK 数据,跳过 Venn 图")
  }

  # ── 2. 分类柱状图 ──
  update_progress(50, stage = "分类柱状图")

  if (file.exists(class_file)) {
    classes <- read.table(class_file, header = TRUE,
                          sep = "\t", stringsAsFactors = FALSE)

    if (nrow(classes) > 0 && "biotype" %in% colnames(classes)) {
      class_counts <- as.data.frame(table(classes$biotype))
      colnames(class_counts) <- c("Biotype", "Count")

      p <- ggplot(class_counts, aes(x = Biotype, y = Count, fill = Biotype)) +
        geom_bar(stat = "identity") +
        geom_text(aes(label = Count), vjust = -0.3) +
        theme_minimal() +
        labs(title = "lncRNA 分类统计",
             x = "lncRNA 类型", y = "数量") +
        scale_fill_brewer(palette = "Set2") +
        theme(legend.position = "none")

      ggsave(file.path(out_dir, "classification_bar.pdf"),
             p, width = 8, height = 6)
      log_msg(sprintf("分类柱状图已保存: %d 种类型", nrow(class_counts)))
    } else {
      log_msg("!! 分类文件格式不正确,跳过分类图")
    }
  } else {
    log_msg("!! 缺少分类文件,跳过分类柱状图")
  }

  # ── 3. 长度分布图 ──
  update_progress(80, stage = "长度分布图")

  lengths <- numeric()
  if (file.exists(len_file)) {
    len_data <- read.table(len_file, header = TRUE,
                           sep = "\t", stringsAsFactors = FALSE)
    if ("length" %in% colnames(len_data)) {
      lengths <- len_data$length
    }
  } else if (file.exists(lncrna_file)) {
    lncrna_data <- read.table(lncrna_file, header = TRUE,
                              sep = "\t", stringsAsFactors = FALSE)
    if ("length" %in% colnames(lncrna_data)) {
      lengths <- lncrna_data$length
    }
  }

  if (length(lengths) > 0) {
    len_df <- data.frame(Length = lengths)
    p <- ggplot(len_df, aes(x = Length)) +
      geom_histogram(bins = 50, fill = "#66C2A5", color = "white", alpha = 0.8) +
      theme_minimal() +
      labs(title = "lncRNA 长度分布",
           x = "转录本长度 (bp)", y = "数量") +
      scale_x_log10()

    ggsave(file.path(out_dir, "length_distribution.pdf"),
           p, width = 8, height = 6)

    mean_len <- mean(lengths, na.rm = TRUE)
    median_len <- median(lengths, na.rm = TRUE)
    log_msg(sprintf("长度分布图已保存: 均值=%.0f, 中位数=%.0f",
                    mean_len, median_len))
  } else {
    log_msg("!! 缺少长度数据,跳过长度分布图")
  }

  update_progress(100, stage = "完成")
  log_msg("=== lncRNA 绘图完成 ===")
}

# ── 执行 ──
run_with_error_handling(run)
