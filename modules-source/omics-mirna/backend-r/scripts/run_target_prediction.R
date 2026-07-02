#!/usr/bin/env Rscript
#
# miRanda 靶基因预测 runner
# 用法: Rscript run_target_prediction.R --job-id <id> --data-dir <dir>
#
# 读取 job.params:
#   mirna_fasta      - miRNA 序列 FASTA
#   utr_fasta        - 3'UTR 序列 FASTA
#   score_threshold  - 打分阈值(默认 140)
#   energy_threshold - 自由能阈值(默认 -20 kcal/mol)
#
# 产出:
#   <output_subdir>/target_predictions.csv
#   <output_subdir>/target_summary.txt

source("../R/runner_base.R")

run_with_error_handling(function() {
  job <- init_runner()
  params <- job$params

  mirna_fasta <- get_param("mirna_fasta")
  utr_fasta <- get_param("utr_fasta")
  score_threshold <- as.numeric(get_param("score_threshold", 140))
  energy_threshold <- as.numeric(get_param("energy_threshold", -20))
  out_dir <- output_dir()

  if (!file.exists(mirna_fasta)) stop(sprintf("miRNA FASTA 不存在: %s", mirna_fasta))
  if (!file.exists(utr_fasta)) stop(sprintf("UTR FASTA 不存在: %s", utr_fasta))

  log_msg(sprintf("miRNA FASTA: %s", mirna_fasta))
  log_msg(sprintf("UTR FASTA: %s", utr_fasta))
  log_msg(sprintf("阈值: score >= %.0f, energy <= %.1f kcal/mol",
                  score_threshold, energy_threshold))

  update_progress(10, "检查 miRanda")

  # 检查 miRanda 是否可用
  miranda_path <- Sys.which("miranda")
  if (miranda_path == "") {
    stop("miRanda 未安装或不在 PATH 中")
  }
  log_msg(sprintf("miRanda 路径: %s", miranda_path))

  update_progress(20, "运行 miRanda 预测")

  # miRanda 命令: miranda <miRNA.fa> <UTR.fa> -sc <score> -en <energy> -out <output>
  # 输出到临时文件,再解析为 CSV
  miranda_out <- file.path(out_dir, "miranda_raw.txt")
  system2(
    miranda_path,
    args = c(
      mirna_fasta,
      utr_fasta,
      "-sc", as.character(score_threshold),
      "-en", as.character(energy_threshold),
      "-out", miranda_out,
      "-quiet"
    ),
    stdout = TRUE,
    stderr = TRUE
  )

  if (!file.exists(miranda_out)) {
    stop("miRanda 运行失败,未生成输出文件")
  }

  update_progress(60, "解析 miRanda 结果")

  # 解析 miRanda 输出
  lines <- readLines(miranda_out, warn = FALSE)

  # miRanda 输出格式:
  # miRNA: <name>
  # ...
  # <target_gene>  Score: <score>  Energy: <energy>  ...
  results <- data.frame(
    miRNA = character(),
    target_gene = character(),
    score = numeric(),
    energy = numeric(),
    stringsAsFactors = FALSE
  )

  current_mirna <- ""
  i <- 0

  for (line in lines) {
    if (grepl("^miRNA:\\s+", line)) {
      current_mirna <- trimws(sub("^miRNA:\\s+", "", line))
      next
    }
    # 目标行: "GeneName\tScore: 150.00\tEnergy: -25.3  ..."
    if (grepl("Score:", line)) {
      parts <- strsplit(line, "\\s+")[[1]]
      # 第一个字段是基因名
      gene <- parts[1]
      score <- as.numeric(gsub("Score:", "", parts[grep("Score:", parts)]))
      energy <- as.numeric(gsub("Energy:", "", parts[grep("Energy:", parts)]))
      results <- rbind(results, data.frame(
        miRNA = current_mirna,
        target_gene = gene,
        score = score,
        energy = energy,
        stringsAsFactors = FALSE
      ))
      i <- i + 1
      if (i %% 100 == 0) {
        update_progress(60, sprintf("解析中... %d 个靶基因", i))
      }
    }
  }

  log_msg(sprintf("总预测结果: %d 条", nrow(results)))

  if (nrow(results) > 0) {
    # 按 miRNA 分组统计
    mirna_summary <- aggregate(target_gene ~ miRNA, data = results,
                               FUN = function(x) length(unique(x)))
    colnames(mirna_summary) <- c("miRNA", "target_count")
    mirna_summary <- mirna_summary[order(mirna_summary$target_count, decreasing = TRUE), ]

    update_progress(80, "写入结果")

    # 写完整预测结果
    csv_path <- file.path(out_dir, "target_predictions.csv")
    write.csv(results, csv_path, row.names = FALSE)
    log_msg(sprintf("预测结果: %s", csv_path))

    # 写摘要
    summary_path <- file.path(out_dir, "target_summary.txt")
    summary_lines <- c(
      sprintf("miRanda 靶基因预测结果"),
      sprintf("阈值: score >= %.0f, energy <= %.1f kcal/mol",
              score_threshold, energy_threshold),
      sprintf("miRNA 数: %d", length(unique(results$miRNA))),
      sprintf("靶基因数: %d", length(unique(results$target_gene))),
      sprintf("总预测数: %d", nrow(results)),
      "",
      "各 miRNA 靶基因数:"
    )
    for (i in seq_len(min(20, nrow(mirna_summary)))) {
      summary_lines <- c(summary_lines,
                         sprintf("  %s: %d", mirna_summary$miRNA[i],
                                 mirna_summary$target_count[i]))
    }
    writeLines(summary_lines, summary_path)

    # 按 miRNA 写出分文件
    for (mir in unique(results$miRNA)) {
      sub <- results[results$miRNA == mir, ]
      mir_file <- file.path(out_dir, paste0("targets_", gsub("/", "_", mir), ".csv"))
      write.csv(sub, mir_file, row.names = FALSE)
    }
    log_msg(sprintf("按 miRNA 分文件: %d 个", length(unique(results$miRNA))))
  } else {
    log_msg("未发现靶基因预测结果")
    writeLines("未发现靶基因预测结果(阈值可能太严格)", file.path(out_dir, "target_summary.txt"))
  }

  log_msg("靶基因预测完成")
})
