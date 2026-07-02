#!/usr/bin/env Rscript
#
# miRNA 表达聚类分析 runner (层次聚类 + 热图)
# 用法: Rscript run_clustering.R --job-id <id> --data-dir <dir>
#
# 读取 job.params:
#   expression_file - 标准化表达矩阵文件(TSV)
#   n_clusters      - 聚类数(默认 4)
#   distance        - 距离方法(默认 "euclidean")
#
# 产出:
#   <output_subdir>/cluster_hierarchical.png    - 层次聚类树状图
#   <output_subdir>/cluster_heatmap.png         - 热图(含行列聚类)
#   <output_subdir>/cluster_assignments.csv     - 聚类结果
#   <output_subdir>/cluster_stats.txt           - 统计信息

source("../R/runner_base.R")

run_with_error_handling(function() {
  job <- init_runner()
  params <- job$params

  expression_file <- get_param("expression_file")
  n_clusters <- as.integer(get_param("n_clusters", 4))
  dist_method <- get_param("distance", "euclidean")
  out_dir <- output_dir()

  if (!file.exists(expression_file)) {
    stop(sprintf("表达矩阵文件不存在: %s", expression_file))
  }

  log_msg(sprintf("表达矩阵: %s", expression_file))
  log_msg(sprintf("聚类数: %d", n_clusters))
  log_msg(sprintf("距离方法: %s", dist_method))

  update_progress(20, "读表达矩阵")

  # 读表达矩阵
  expr <- as.matrix(read.table(expression_file, header = TRUE, row.names = 1,
                               check.names = FALSE, sep = "\t"))
  log_msg(sprintf("矩阵: %d miRNA x %d 样本", nrow(expr), ncol(expr)))

  # 过滤低表达 miRNA(所有样本中位数 > 0)
  expr_filtered <- expr[rowMedians(expr) > 0, , drop = FALSE]
  if (nrow(expr_filtered) < 2) {
    stop("过滤后 miRNA 数不足,无法聚类")
  }

  log_msg(sprintf("过滤后: %d miRNA", nrow(expr_filtered)))

  # 可选:取 top 变异 miRNA (最多 1000 个)
  if (nrow(expr_filtered) > 1000) {
    row_vars <- apply(expr_filtered, 1, var, na.rm = TRUE)
    top_idx <- order(row_vars, decreasing = TRUE)[1:1000]
    expr_filtered <- expr_filtered[top_idx, ]
    log_msg(sprintf("取 top 1000 高变异 miRNA 聚类"))
  }

  # 标准化(scale per gene)
  expr_scaled <- t(scale(t(expr_filtered)))
  expr_scaled[is.na(expr_scaled)] <- 0

  update_progress(40, "计算距离矩阵")

  # 距离矩阵
  dist_genes <- dist(expr_scaled, method = dist_method)
  dist_samples <- dist(t(expr_scaled), method = dist_method)

  update_progress(50, "层次聚类", indeterminate = TRUE)

  # 层次聚类
  hc_genes <- hclust(dist_genes, method = "ward.D2")
  hc_samples <- hclust(dist_samples, method = "ward.D2")

  # 剪枝得到 miRNA 簇
  if (n_clusters < 2) n_clusters <- 2
  if (n_clusters > nrow(expr_scaled)) n_clusters <- min(4, nrow(expr_scaled))

  gene_clusters <- cutree(hc_genes, k = n_clusters)

  update_progress(65, "绘图", indeterminate = TRUE)

  # 绘图
  suppressPackageStartupMessages(library(pheatmap))
  suppressPackageStartupMessages(library(grDevices))
  suppressPackageStartupMessages(library(RColorBrewer))

  # 颜色
  colors <- colorRampPalette(rev(brewer.pal(11, "RdBu")))(100)

  # 热图(较大时只显示部分)
  max_show <- min(500, nrow(expr_scaled))
  plot_data <- expr_scaled[1:max_show, ]

  pheatmap(plot_data,
           color = colors,
           clustering_distance_rows = dist_method,
           clustering_distance_cols = dist_method,
           clustering_method = "ward.D2",
           cutree_rows = n_clusters,
           main = paste("miRNA Expression Heatmap (top", max_show, "miRNAs)"),
           filename = file.path(out_dir, "cluster_heatmap.png"),
           width = 10, height = 14, dpi = 150,
           show_rownames = FALSE,
           fontsize_row = 4)

  log_msg("热图已保存")

  # 树状图
  png(file.path(out_dir, "cluster_dendrogram.png"),
      width = 12, height = 6, units = "in", res = 150)
  par(mar = c(4, 4, 2, 2))
  plot(hc_genes, labels = FALSE, main = "miRNA Hierarchical Clustering Dendrogram",
       xlab = "miRNAs", ylab = "Height", sub = "")
  rect.hclust(hc_genes, k = n_clusters, border = 2:(n_clusters + 1))
  dev.off()

  log_msg("树状图已保存")

  update_progress(80, "写聚类结果")

  # 写聚类结果
  cluster_df <- data.frame(
    miRNA = names(gene_clusters),
    cluster = gene_clusters,
    stringsAsFactors = FALSE
  )
  # 加各簇的 miRNA 数
  cluster_stats <- as.data.frame(table(cluster_df$cluster))
  colnames(cluster_stats) <- c("cluster", "count")
  cluster_stats$cluster <- as.integer(as.character(cluster_stats$cluster))

  # 按簇排序
  cluster_df <- cluster_df[order(cluster_df$cluster), ]

  write.csv(cluster_df, file.path(out_dir, "cluster_assignments.csv"),
            row.names = FALSE)
  log_msg(sprintf("聚类结果: %d 个簇, %d 个 miRNA",
                  nrow(cluster_stats), nrow(cluster_df)))

  # 统计
  stats_lines <- c(
    sprintf("miRNA 表达聚类分析结果"),
    sprintf("输入矩阵: %s", basename(expression_file)),
    sprintf("距离方法: %s", dist_method),
    sprintf("聚类数: %d", n_clusters),
    sprintf("参与聚类 miRNA 数: %d", nrow(expr_scaled)),
    sprintf(""),
    sprintf("各簇大小:")
  )
  for (i in seq_len(nrow(cluster_stats))) {
    stats_lines <- c(stats_lines,
                     sprintf("  簇 %d: %d 个 miRNA",
                             cluster_stats$cluster[i], cluster_stats$count[i]))
  }
  writeLines(stats_lines, file.path(out_dir, "cluster_stats.txt"))

  # 按簇写分文件
  for (k in unique(cluster_df$cluster)) {
    cluster_mirnas <- cluster_df$miRNA[cluster_df$cluster == k]
    writeLines(cluster_mirnas,
               file.path(out_dir, sprintf("cluster_%d_mirnas.txt", k)))
  }

  log_msg("聚类分析完成")
})
