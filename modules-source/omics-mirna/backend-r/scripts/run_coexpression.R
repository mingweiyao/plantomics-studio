#!/usr/bin/env Rscript
#
# miRNA-mRNA 共表达网络分析 runner
# 用法: Rscript run_coexpression.R --job-id <id> --data-dir <dir>
#
# 读取 job.params:
#   mirna_expression_file - miRNA 表达矩阵文件
#   mrna_expression_file  - mRNA 表达矩阵文件
#   correlation_method    - 相关方法("pearson", "spearman"),默认 "spearman"
#   cutoff                - 相关系数阈值,默认 0.7
#   pvalue_cutoff         - P 值阈值,默认 0.05
#
# 产出:
#   <output_subdir>/coexpression_edges.csv       - 共表达边列表
#   <output_subdir>/coexpression_summary.txt     - 汇总统计
#   <output_subdir>/coexpression_network.png     - 网络图
#   <output_subdir>/correlation_distribution.png - 相关系数分布

source("../R/runner_base.R")

run_with_error_handling(function() {
  job <- init_runner()
  params <- job$params

  mirna_file <- get_param("mirna_expression_file")
  mrna_file <- get_param("mrna_expression_file")
  cor_method <- get_param("correlation_method", "spearman")
  cutoff <- as.numeric(get_param("cutoff", 0.7))
  pval_cutoff <- as.numeric(get_param("pvalue_cutoff", 0.05))
  out_dir <- output_dir()

  if (!file.exists(mirna_file)) {
    stop(sprintf("miRNA 表达文件不存在: %s", mirna_file))
  }
  if (!file.exists(mrna_file)) {
    stop(sprintf("mRNA 表达文件不存在: %s", mrna_file))
  }

  log_msg(sprintf("miRNA 表达: %s", mirna_file))
  log_msg(sprintf("mRNA 表达: %s", mrna_file))
  log_msg(sprintf("相关方法: %s", cor_method))
  log_msg(sprintf("阈值: |r| >= %.2f, p < %.3f", cutoff, pval_cutoff))

  update_progress(10, "读表达矩阵")

  # 读表达矩阵
  mirna_expr <- as.matrix(read.table(mirna_file, header = TRUE, row.names = 1,
                                     check.names = FALSE, sep = "\t"))
  mrna_expr <- as.matrix(read.table(mrna_file, header = TRUE, row.names = 1,
                                    check.names = FALSE, sep = "\t"))

  log_msg(sprintf("miRNA: %d x %d", nrow(mirna_expr), ncol(mirna_expr)))
  log_msg(sprintf("mRNA: %d x %d", nrow(mrna_expr), ncol(mrna_expr)))

  # 找共同样本
  common_samples <- intersect(colnames(mirna_expr), colnames(mrna_expr))
  if (length(common_samples) < 4) {
    stop(sprintf("共同样本不足(%d),至少需要 4 个", length(common_samples)))
  }
  mirna_expr <- mirna_expr[, common_samples, drop = FALSE]
  mrna_expr <- mrna_expr[, common_samples, drop = FALSE]
  log_msg(sprintf("共同样本: %d", length(common_samples)))

  # 过滤低表达 miRNA (至少一半样本中表达 > 0)
  mirna_keep <- rowSums(mirna_expr > 0) >= ncol(mirna_expr) / 2
  mirna_expr <- mirna_expr[mirna_keep, , drop = FALSE]
  log_msg(sprintf("过滤后 miRNA: %d", nrow(mirna_expr)))

  if (nrow(mirna_expr) == 0) stop("过滤后无 miRNA 剩余")

  # 限制 miRNA 和 mRNA 数量,避免计算爆炸
  # 最多取 top 100 miRNA 和 top 1000 mRNA(按方差)
  if (nrow(mirna_expr) > 100) {
    mirna_vars <- apply(mirna_expr, 1, var)
    mirna_expr <- mirna_expr[order(mirna_vars, decreasing = TRUE)[1:100], ]
    log_msg(sprintf("取 top 100 miRNA (按方差)"))
  }
  if (nrow(mrna_expr) > 1000) {
    mrna_vars <- apply(mrna_expr, 1, var)
    mrna_expr <- mrna_expr[order(mrna_vars, decreasing = TRUE)[1:1000], ]
    log_msg(sprintf("取 top 1000 mRNA (按方差)"))
  }

  update_progress(30, "计算相关性矩阵", indeterminate = TRUE)

  # 计算 miRNA-mRNA 相关矩阵
  # 对每个 miRNA,与所有 mRNA 计算相关系数
  n_mirna <- nrow(mirna_expr)
  n_mrna <- nrow(mrna_expr)
  total_pairs <- n_mirna * n_mrna

  log_msg(sprintf("计算 %d 对相关性...", total_pairs))

  # 用 WGCNA 的 cor 函数(如果可用),否则用 R 内置 cor
  use_fast_cor <- suppressPackageStartupMessages(
    require(WGCNA, quietly = TRUE, warn.conflicts = FALSE)
  )

  if (use_fast_cor) {
    log_msg("使用 WGCNA::cor 加速计算")
    cor_mat <- WGCNA::cor(t(mirna_expr), t(mrna_expr), method = cor_method,
                          nThreads = job_threads(2))
    pval_mat <- WGCNA::corPvalueStudent(cor_mat, n = ncol(mirna_expr))
  } else {
    log_msg("使用 R 内置 cor 计算")
    cor_mat <- matrix(0, nrow = n_mirna, ncol = n_mrna)
    pval_mat <- matrix(1, nrow = n_mirna, ncol = n_mrna)

    for (i in seq_len(n_mirna)) {
      for (j in seq_len(n_mrna)) {
        test <- cor.test(mirna_expr[i, ], mrna_expr[j, ], method = cor_method)
        cor_mat[i, j] <- test$estimate
        pval_mat[i, j] <- test$p.value
      }
      if (i %% 10 == 0) {
        update_progress(30, sprintf("相关性 %d/%d miRNA", i, n_mirna))
      }
    }
  }

  rownames(cor_mat) <- rownames(mirna_expr)
  colnames(cor_mat) <- rownames(mrna_expr)
  rownames(pval_mat) <- rownames(mirna_expr)
  colnames(pval_mat) <- rownames(mrna_expr)

  update_progress(60, "筛选显著边")

  # 筛选显著边
  significant <- which(abs(cor_mat) >= cutoff & pval_mat < pval_cutoff,
                       arr.ind = TRUE)

  if (nrow(significant) > 0) {
    edges <- data.frame(
      miRNA = rownames(cor_mat)[significant[, 1]],
      mRNA = colnames(cor_mat)[significant[, 2]],
      correlation = cor_mat[significant],
      pvalue = pval_mat[significant],
      stringsAsFactors = FALSE
    )

    # 按 |correlation| 降序排序
    edges <- edges[order(abs(edges$correlation), decreasing = TRUE), ]

    log_msg(sprintf("显著边: %d 条", nrow(edges)))

    # 写边列表
    csv_path <- file.path(out_dir, "coexpression_edges.csv")
    write.csv(edges, csv_path, row.names = FALSE)
    log_msg(sprintf("边列表: %s", csv_path))

    # 每个 miRNA 的靶 mRNA 数
    mirna_targets <- aggregate(mRNA ~ miRNA, data = edges,
                               FUN = function(x) length(unique(x)))
    colnames(mirna_targets) <- c("miRNA", "target_mRNAs")
    mirna_targets <- mirna_targets[order(mirna_targets$target_mRNAs,
                                         decreasing = TRUE), ]

    target_path <- file.path(out_dir, "mirna_target_counts.csv")
    write.csv(mirna_targets, target_path, row.names = FALSE)

    # 网络图
    update_progress(80, "网络可视化")

    tryCatch({
      suppressPackageStartupMessages(library(igraph))

      # 构建网络(最多显示 500 条边)
      edges_for_plot <- edges[1:min(500, nrow(edges)), ]
      net <- graph_from_data_frame(edges_for_plot, directed = FALSE)

      # 节点属性
      V(net)$type <- ifelse(V(net)$name %in% rownames(mirna_expr),
                            "miRNA", "mRNA")
      V(net)$color <- ifelse(V(net)$type == "miRNA", "#E41A1C", "#377EB8")
      V(net)$size <- ifelse(V(net)$type == "miRNA", 10, 3)

      png(file.path(out_dir, "coexpression_network.png"),
          width = 14, height = 12, units = "in", res = 150)
      par(mar = c(0, 0, 0, 0))
      set.seed(42)
      plot(net,
           vertex.color = V(net)$color,
           vertex.size = V(net)$size,
           vertex.label = ifelse(V(net)$type == "miRNA", V(net)$name, NA),
           vertex.label.cex = 0.6,
           vertex.label.color = "black",
           edge.width = abs(E(net)$correlation) * 3,
           edge.color = rgb(0.5, 0.5, 0.5, 0.3),
           main = "miRNA-mRNA Co-expression Network",
           layout = layout_with_fr(net))
      legend("topleft", legend = c("miRNA", "mRNA"),
             fill = c("#E41A1C", "#377EB8"), cex = 1, bty = "n")
      dev.off()
      log_msg("网络图已保存")
    }, error = function(e) {
      log_msg(sprintf("网络绘图失败(igraph 可能未安装): %s", e$message))
    })

  } else {
    log_msg("未发现显著共表达对(尝试降低阈值)")
    writeLines("miRNA,mRNA,correlation,pvalue\n",
               file.path(out_dir, "coexpression_edges.csv"))
  }

  # 相关系数分布图
  update_progress(90, "绘制分布图")

  tryCatch({
    suppressPackageStartupMessages(library(ggplot2))
    cor_values <- as.vector(cor_mat)
    cor_df <- data.frame(correlation = cor_values)

    p <- ggplot(cor_df, aes(x = correlation)) +
      geom_histogram(bins = 50, fill = "steelblue", alpha = 0.7) +
      geom_vline(xintercept = c(-cutoff, cutoff), linetype = "dashed",
                 color = "red", linewidth = 0.8) +
      labs(title = "miRNA-mRNA Correlation Distribution",
           x = "Correlation coefficient", y = "Count") +
      theme_bw()

    ggsave(file.path(out_dir, "correlation_distribution.png"),
           p, width = 8, height = 6, dpi = 150)
    log_msg("相关系数分布图已保存")
  }, error = function(e) {
    log_msg(sprintf("分布图绘制失败: %s", e$message))
  })

  # 汇总
  n_mirna_connected <- if (exists("edges")) length(unique(edges$miRNA)) else 0
  n_mrna_connected <- if (exists("edges")) length(unique(edges$mRNA)) else 0

  summary_lines <- c(
    sprintf("miRNA-mRNA 共表达网络分析结果"),
    sprintf("相关方法: %s", cor_method),
    sprintf("输入 miRNA 数: %d", n_mirna),
    sprintf("输入 mRNA 数: %d", n_mrna),
    sprintf("共同样本数: %d", length(common_samples)),
    sprintf("相关系数阈值: |r| >= %.2f", cutoff),
    sprintf("P 值阈值: p < %.3f", pval_cutoff),
    sprintf(""),
    sprintf("显著边数: %d", if (exists("edges")) nrow(edges) else 0),
    sprintf("连接 miRNA 数: %d", n_mirna_connected),
    sprintf("连接 mRNA 数: %d", n_mrna_connected)
  )
  writeLines(summary_lines, file.path(out_dir, "coexpression_summary.txt"))

  log_msg("共表达分析完成")
})
