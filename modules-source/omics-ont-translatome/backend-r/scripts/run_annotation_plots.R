#!/usr/bin/env Rscript
#
# 功能注释可视化 — GO/KEGG/KOG/NR 分类图
# =========================================
# 用法: Rscript run_annotation_plots.R --job-id <id> --data-dir <dir>
#
# 从 merged_annotation.tsv 读取注释结果,生成:
#   1. NR 分类饼图/柱状图
#   2. GO 功能分类图(二级分类)
#   3. KEGG 通路分类图
#   4. Pfam 结构域分布图

suppressPackageStartupMessages({
  library(jsonlite)
  library(ggplot2)
})

SCRIPT_DIR <- tryCatch(
  dirname(sys.frame(1)$ofile),
  error = function(e) getwd()
)
source(file.path(SCRIPT_DIR, "..", "R", "runner_base.R"))

run_annotation_plots <- function() {
  job <- init_runner()
  params <- job$params
  out_dir <- output_dir()

  annot_file <- params[["annotation_file"]] %||%
    file.path(out_dir, "merged_annotation.tsv")

  if (!file.exists(annot_file)) {
    stop(sprintf("注释文件不存在: %s", annot_file))
  }

  update_progress(10, "读取注释数据")
  annot <- read.table(annot_file, header = TRUE, sep = "\t",
                      stringsAsFactors = FALSE, quote = "",
                      comment.char = "", fill = TRUE)
  log_msg(sprintf("  读取 %d 条注释记录", nrow(annot)))

  # 1. NR 物种分布
  update_progress(25, "绘制 NR 分类图")
  if ("nr_hit" %in% colnames(annot)) {
    # 从 NR hit 中提取物种信息
    nr_hits <- annot$nr_hit[nchar(annot$nr_hit) > 0]
    if (length(nr_hits) > 0) {
      # 提取物种:通常在 [物种] 中
      species <- gsub(".*\\[(.+)\\]$", "\\1", nr_hits)
      # 清理:保留物种名部分(取第一个空格前的部分作为属)
      genus <- gsub("^([A-Z][a-z]+)\\s.*", "\\1", species)
      genus[nchar(genus) > 30] <- "Other"

      sp_counts <- as.data.frame(table(genus))
      sp_counts <- sp_counts[order(sp_counts$Freq, decreasing = TRUE), ]
      # 只显示 Top 20,其余归为 Other
      if (nrow(sp_counts) > 20) {
        top20 <- head(sp_counts, 20)
        other_count <- sum(sp_counts$Freq[-(1:20)])
        sp_counts <- rbind(top20, data.frame(genus = "Other", Freq = other_count))
      }

      pdf(file.path(out_dir, "nr_species_distribution.pdf"), width = 10, height = 7)
      p <- ggplot(sp_counts, aes(x = reorder(genus, Freq), y = Freq, fill = genus)) +
        geom_bar(stat = "identity") +
        coord_flip() +
        labs(title = "NR 注释物种分布 (Top 20)",
             x = "", y = "序列数") +
        theme_minimal() +
        theme(legend.position = "none")
      print(p)
      dev.off()
      log_msg("  NR 物种分布图已保存")
    }
  }

  # 2. GO 功能分类(如果有单独的 GO 注释)
  update_progress(45, "绘制 GO 分类图")
  go_file <- params[["go_annotation"]] %||% file.path(out_dir, "go_annotation.tsv")
  if (file.exists(go_file)) {
    go <- read.table(go_file, header = TRUE, sep = "\t",
                     stringsAsFactors = FALSE, quote = "")
    if (nrow(go) > 0 && "ontology" %in% colnames(go)) {
      go_counts <- as.data.frame(table(go$ontology))
      colnames(go_counts) <- c("Ontology", "Count")
      pdf(file.path(out_dir, "go_classification.pdf"), width = 8, height = 6)
      p <- ggplot(go_counts, aes(x = Ontology, y = Count, fill = Ontology)) +
        geom_bar(stat = "identity") +
        labs(title = "GO 功能分类", x = "Ontology", y = "基因数") +
        theme_minimal() +
        theme(legend.position = "none")
      print(p)
      dev.off()
      log_msg("  GO 分类图已保存")
    }
  }

  # 3. KEGG 通路分类
  update_progress(65, "绘制 KEGG 分类图")
  if ("kegg_ko" %in% colnames(annot)) {
    kegg_hits <- annot$kegg_ko[nchar(annot$kegg_ko) > 0]
    if (length(kegg_hits) > 0) {
      ko_counts <- as.data.frame(table(kegg_hits))
      ko_counts <- ko_counts[order(ko_counts$Freq, decreasing = TRUE), ]
      if (nrow(ko_counts) > 20) {
        ko_counts <- head(ko_counts, 20)
      }
      pdf(file.path(out_dir, "kegg_pathway_distribution.pdf"), width = 10, height = 7)
      p <- ggplot(ko_counts, aes(x = reorder(kegg_hits, Freq), y = Freq, fill = kegg_hits)) +
        geom_bar(stat = "identity") +
        coord_flip() +
        labs(title = "KEGG KO 分布 (Top 20)", x = "KO", y = "序列数") +
        theme_minimal() +
        theme(legend.position = "none")
      print(p)
      dev.off()
      log_msg("  KEGG KO 分布图已保存")
    }
  }

  # 4. Pfam 结构域分布
  update_progress(80, "绘制 Pfam 结构域分布图")
  if ("pfam_hit" %in% colnames(annot)) {
    pfam_hits <- annot$pfam_hit[nchar(annot$pfam_hit) > 0]
    if (length(pfam_hits) > 0) {
      pfam_counts <- as.data.frame(table(pfam_hits))
      pfam_counts <- pfam_counts[order(pfam_counts$Freq, decreasing = TRUE), ]
      if (nrow(pfam_counts) > 20) {
        pfam_counts <- head(pfam_counts, 20)
      }
      pdf(file.path(out_dir, "pfam_domain_distribution.pdf"), width = 10, height = 7)
      p <- ggplot(pfam_counts, aes(x = reorder(pfam_hits, Freq), y = Freq, fill = pfam_hits)) +
        geom_bar(stat = "identity") +
        coord_flip() +
        labs(title = "Pfam 结构域分布 (Top 20)", x = "Pfam 结构域", y = "序列数") +
        theme_minimal() +
        theme(legend.position = "none")
      print(p)
      dev.off()
      log_msg("  Pfam 结构域分布图已保存")
    }
  }

  update_progress(100, "完成")
  log_msg("=== 注释可视化完成 ===")
}

run_with_error_handling(run_annotation_plots)
