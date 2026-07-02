#!/usr/bin/env Rscript
#
# Ref vs All 对比可视化 — Venn图/金字塔图/富集对比柱状图
# ========================================================
# 用法: Rscript run_ref_vs_all.R --job-id <id> --data-dir <dir>
#
# 从 ref_vs_all 步骤输出的数据文件生成:
#   1. Venn 图(转录本/基因)
#   2. 金字塔图(数量对比)
#   3. 富集对比柱状图(如含注释数据)

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

SCRIPT_DIR <- tryCatch(
  dirname(sys.frame(1)$ofile),
  error = function(e) getwd()
)
source(file.path(SCRIPT_DIR, "..", "R", "runner_base.R"))

# 如果没有 VennDiagram 包,尝试用 ggplot2 画替代品
has_venn <- suppressPackageStartupMessages(
  tryCatch({ library(VennDiagram); TRUE }, error = function(e) FALSE)
)

run_ref_vs_all <- function() {
  job <- init_runner()
  params <- job$params
  out_dir <- output_dir()

  # 数据目录和对比文件
  data_dir <- params[["data_dir"]] %||% file.path(out_dir, "data")
  summary_file <- params[["summary_file"]] %||%
    file.path(out_dir, "ref_vs_all_summary.json")

  if (!dir.exists(data_dir)) {
    stop(sprintf("数据目录不存在: %s", data_dir))
  }
  if (!file.exists(summary_file)) {
    stop(sprintf("汇总文件不存在: %s", summary_file))
  }

  update_progress(10, "读取对比数据")
  summary <- jsonlite::fromJSON(summary_file, simplifyVector = FALSE)
  log_msg(sprintf("  参考: %d 转录本, 全流程: %d 转录本",
                  summary$ref_transcripts %||% 0,
                  summary$full_transcripts %||% 0))

  venn <- summary$venn %||% list()

  # ── 1. Venn 图(转录本) ──
  update_progress(30, "绘制转录本 Venn 图")
  common_tx <- venn$common_transcripts %||% 0
  ref_only_tx <- venn$ref_only_transcripts %||% 0
  full_only_tx <- venn$full_only_transcripts %||% 0

  if (has_venn) {
    pdf(file.path(out_dir, "venn_transcripts.pdf"), width = 6, height = 6)
    VennDiagram::venn.diagram(
      x = list(
        Ref = seq_len(ref_only_tx + common_tx),
        Full = seq_len(full_only_tx + common_tx)
      ),
      category.names = c("Ref-only", "Full Pipeline"),
      filename = NULL,
      lwd = 2,
      fill = c("#E41A1C", "#377EB8"),
      alpha = 0.5,
      cex = 1.5,
      cat.cex = 1.2,
      cat.pos = c(-20, 20),
      main = "Transcript Overlap: Ref vs Full Pipeline",
      main.cex = 1.2
    )
    dev.off()
    log_msg("  Venn 图已保存 (venn_transcripts.pdf)")
  } else {
    # 无 VennDiagram 包时,用 ggplot2 画柱状图表示
    venn_df <- data.frame(
      Category = c("Ref Only", "Shared", "Full Only"),
      Count = c(ref_only_tx, common_tx, full_only_tx)
    )
    pdf(file.path(out_dir, "venn_transcripts.pdf"), width = 7, height = 6)
    p <- ggplot(venn_df, aes(x = Category, y = Count, fill = Category)) +
      geom_bar(stat = "identity", width = 0.6) +
      geom_text(aes(label = Count), vjust = -0.3, size = 5) +
      labs(title = "Transcript Comparison: Ref vs Full Pipeline",
           x = "", y = "Count") +
      scale_fill_manual(values = c("#E41A1C", "#4DAF4A", "#377EB8")) +
      theme_minimal() +
      theme(legend.position = "none")
    print(p)
    dev.off()
    log_msg("  Venn 替代图已保存 (缺少 VennDiagram 包)")
  }

  # ── 2. 金字塔图(数量对比) ──
  update_progress(55, "绘制金字塔对比图")

  # 准备对比数据
  compare_items <- c("Transcripts", "Genes")
  ref_vals <- c(summary$ref_transcripts %||% 0, summary$ref_genes %||% 0)
  full_vals <- c(summary$full_transcripts %||% 0, summary$full_genes %||% 0)

  pyramid_df <- data.frame(
    Item = rep(compare_items, 2),
    Pipeline = c(rep("Ref-only", 2), rep("Full Pipeline", 2)),
    Count = c(ref_vals, full_vals)
  )
  pyramid_df$Item <- factor(pyramid_df$Item, levels = compare_items)

  pdf(file.path(out_dir, "pyramid_comparison.pdf"), width = 8, height = 6)
  p <- ggplot(pyramid_df, aes(x = Item, y = Count, fill = Pipeline)) +
    geom_bar(stat = "identity", position = position_dodge(width = 0.7),
             width = 0.6) +
    geom_text(aes(label = Count),
              position = position_dodge(width = 0.7),
              vjust = -0.3, size = 4.5) +
    labs(title = "Ref vs Full Pipeline: Count Comparison",
         x = "", y = "Count") +
    scale_fill_manual(values = c("#E41A1C", "#377EB8")) +
    theme_minimal() +
    theme(legend.position = "top")
  print(p)
  dev.off()
  log_msg("  金字塔对比图已保存")

  # ── 3. 富集对比柱状图(如含注释数据) ──
  annot_comp <- summary$annot_comparison
  if (!is.null(annot_comp) && length(annot_comp) > 0) {
    update_progress(75, "绘制注释率对比图")

    n_total <- annot_comp$n_total %||% 0
    n_annot <- annot_comp$n_annotated %||% 0
    rate <- annot_comp$annotation_rate %||% 0

    annot_df <- data.frame(
      Category = c("Annotated", "Unannotated"),
      Count = c(n_annot, max(0, n_total - n_annot))
    )

    pdf(file.path(out_dir, "annotation_enrichment.pdf"), width = 6, height = 6)
    p <- ggplot(annot_df, aes(x = Category, y = Count, fill = Category)) +
      geom_bar(stat = "identity", width = 0.5) +
      geom_text(aes(label = Count), vjust = -0.3, size = 5) +
      labs(title = sprintf("Full Pipeline Annotation Rate: %.1f%%", rate),
           x = "", y = "Transcripts") +
      scale_fill_manual(values = c("#4DAF4A", "grey60")) +
      theme_minimal() +
      theme(legend.position = "none")
    print(p)
    dev.off()
    log_msg(sprintf("  注释率图已保存 (%.1f%%)", rate))
  }

  # ── 4. 如果有基因 Venn 数据,也画一个 ──
  common_genes <- venn$common_genes %||% 0
  ref_only_genes <- venn$ref_only_genes %||% 0
  full_only_genes <- venn$full_only_genes %||% 0

  if (common_genes > 0 || ref_only_genes > 0 || full_only_genes > 0) {
    if (has_venn) {
      update_progress(88, "绘制基因 Venn 图")
      pdf(file.path(out_dir, "venn_genes.pdf"), width = 6, height = 6)
      VennDiagram::venn.diagram(
        x = list(
          Ref = seq_len(ref_only_genes + common_genes),
          Full = seq_len(full_only_genes + common_genes)
        ),
        category.names = c("Ref-only", "Full Pipeline"),
        filename = NULL,
        lwd = 2,
        fill = c("#E41A1C", "#377EB8"),
        alpha = 0.5,
        cex = 1.5,
        cat.cex = 1.2,
        main = "Gene Overlap: Ref vs Full Pipeline",
        main.cex = 1.2
      )
      dev.off()
      log_msg("  基因 Venn 图已保存")
    }
  }

  # ── 写入摘要 ──
  plot_summary <- list(
    venn_transcripts = file.path(out_dir, "venn_transcripts.pdf"),
    pyramid = file.path(out_dir, "pyramid_comparison.pdf"),
    annotation_plot = file.path(out_dir, "annotation_enrichment.pdf")
  )
  writeLines(jsonlite::toJSON(plot_summary, auto_unbox = TRUE, pretty = TRUE),
             file.path(out_dir, "ref_vs_all_plots_summary.json"))

  update_progress(100, "完成")
  log_msg("=== Ref vs All 对比可视化完成 ===")
}

run_with_error_handling(run_ref_vs_all)
