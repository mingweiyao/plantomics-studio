#!/usr/bin/env Rscript
#
# GO/KEGG 富集分析 runner
# 用法: Rscript run_enrichment.R --job-id <id> --data-dir <dir>
#
# 读取 job.params:
#   gene_list        - 靶基因列表(字符向量)
#   organism         - 物种代码(如 "ath", "hsa", "mmu"),默认 "ath"
#   pvalue_cutoff    - P 值阈值(默认 0.05)
#   qvalue_cutoff    - Q 值阈值(默认 0.2)
#
# 产出:
#   <output_subdir>/go_enrichment.csv
#   <output_subdir>/kegg_enrichment.csv
#   <output_subdir>/enrichment_summary.txt
#   <output_subdir>/go_dotplot.png
#   <output_subdir>/kegg_dotplot.png

source("../R/runner_base.R")

run_with_error_handling(function() {
  job <- init_runner()
  params <- job$params

  gene_list <- get_param("gene_list", character(0))
  organism <- get_param("organism", "ath")
  pvalue_cutoff <- as.numeric(get_param("pvalue_cutoff", 0.05))
  qvalue_cutoff <- as.numeric(get_param("qvalue_cutoff", 0.2))
  out_dir <- output_dir()

  if (length(gene_list) == 0) stop("gene_list 为空")

  # 转为字符向量(如果从 JSON 拿到的可能是 list)
  gene_list <- as.character(unlist(gene_list))
  log_msg(sprintf("输入基因数: %d", length(gene_list)))
  log_msg(sprintf("物种: %s", organism))
  log_msg(sprintf("P 阈值: %.3f, Q 阈值: %.3f", pvalue_cutoff, qvalue_cutoff))

  update_progress(10, "加载富集分析包")

  # 尝试加载包
  has_clusterprofiler <- suppressPackageStartupMessages(
    require(clusterProfiler, quietly = TRUE, character.only = TRUE)
  )
  has_orgdb <- FALSE
  org_pkg <- NULL
  kegg_org <- organism

  # 尝试加载物种注释包
  org_pkgs <- list(
    ath = "org.At.tair.db",
    hsa = "org.Hs.eg.db",
    mmu = "org.Mm.eg.db",
    rno = "org.Rn.eg.db",
    dme = "org.Dm.eg.db",
    sce = "org.Sc.sgd.db",
    eco = "org.EcK12.eg.db"
  )

  if (organism %in% names(org_pkgs)) {
    pkg_name <- org_pkgs[[organism]]
    has_orgdb <- suppressPackageStartupMessages(
      require(pkg_name, quietly = TRUE, character.only = TRUE)
    )
    org_pkg <- pkg_name
    if (has_orgdb) {
      log_msg(sprintf("已加载物种注释包: %s", pkg_name))
    } else {
      log_msg(sprintf("物种注释包 %s 未安装,使用模拟模式", pkg_name))
    }
  } else {
    log_msg(sprintf("未知物种代码 '%s',使用模拟模式", organism))
  }

  update_progress(30, "GO 富集分析", indeterminate = TRUE)

  if (has_clusterprofiler && has_orgdb) {
    # 真实富集分析
    tryCatch({
      # gene ID 转换(从 symbol 到 ENTREZID)
      suppressPackageStartupMessages(library(clusterProfiler))
      gene_ids <- tryCatch({
        bitr(gene_list, fromType = "SYMBOL", toType = "ENTREZID",
             OrgDb = org_pkg)
      }, error = function(e) {
        log_msg(sprintf("基因名转换失败(可能已是 ENTREZID): %s", e$message))
        data.frame(SYMBOL = gene_list, ENTREZID = gene_list)
      })

      if (!is.null(gene_ids) && nrow(gene_ids) > 0) {
        entrez_ids <- unique(gene_ids$ENTREZID)

        update_progress(40, "GO 富集分析", indeterminate = TRUE)

        # GO BP 富集
        ego <- tryCatch({
  enrichGO(gene = entrez_ids,
            OrgDb = org_pkg,
            keyType = "ENTREZID",
            ont = "BP",
            pvalueCutoff = pvalue_cutoff,
            qvalueCutoff = qvalue_cutoff,
            readable = TRUE)
        }, error = function(e) {
          log_msg(sprintf("GO BP 富集失败: %s", e$message))
          NULL
        })

        if (!is.null(ego) && nrow(ego@result) > 0) {
          go_df <- as.data.frame(ego@result)
          write.csv(go_df, file.path(out_dir, "go_enrichment.csv"), row.names = FALSE)
          log_msg(sprintf("GO BP 富集: %d 条显著", sum(go_df$pvalue < pvalue_cutoff)))

          # GO dotplot
          tryCatch({
            png(file.path(out_dir, "go_dotplot.png"), width = 10, height = 8,
                units = "in", res = 150)
            print(dotplot(ego, showCategory = 20, title = "GO Biological Process"))
            dev.off()
          }, error = function(e) log_msg(sprintf("GO 绘图失败: %s", e$message)))
        } else {
          writeLines("GO BP 无显著富集结果", file.path(out_dir, "go_enrichment.csv"))
        }

        update_progress(60, "KEGG 富集分析", indeterminate = TRUE)

        # KEGG 富集
        kk <- tryCatch({
  enrichKEGG(gene = entrez_ids,
              organism = kegg_org,
              pvalueCutoff = pvalue_cutoff,
              qvalueCutoff = qvalue_cutoff)
        }, error = function(e) {
          log_msg(sprintf("KEGG 富集失败: %s", e$message))
          NULL
        })

        if (!is.null(kk) && nrow(kk@result) > 0) {
          kegg_df <- as.data.frame(kk@result)
          write.csv(kegg_df, file.path(out_dir, "kegg_enrichment.csv"), row.names = FALSE)
          log_msg(sprintf("KEGG 富集: %d 条显著", sum(kegg_df$pvalue < pvalue_cutoff)))

          tryCatch({
            png(file.path(out_dir, "kegg_dotplot.png"), width = 10, height = 8,
                units = "in", res = 150)
            print(dotplot(kk, showCategory = 20, title = "KEGG Pathway"))
            dev.off()
          }, error = function(e) log_msg(sprintf("KEGG 绘图失败: %s", e$message)))
        } else {
          writeLines("KEGG 无显著富集结果", file.path(out_dir, "kegg_enrichment.csv"))
        }
      } else {
        writeLines("基因 ID 转换失败,无法做富集分析",
                   file.path(out_dir, "go_enrichment.csv"))
      }
    }, error = function(e) {
      log_msg(sprintf("富集分析出错: %s", e$message))
      writeLines(sprintf("富集分析失败: %s", e$message),
                 file.path(out_dir, "go_enrichment.csv"))
    })
  } else {
    # 模拟分析(无包时)
    log_msg("clusterProfiler 或 OrgDb 未安装,使用模拟富集分析")
    sim_go <- data.frame(
      ID = c("GO:0000001", "GO:0000002", "GO:0000003"),
      Description = c("线粒体呼吸链复合体 I 组装",
                      "线粒体翻译", "RNA 加工"),
      GeneRatio = c("5/20", "3/20", "2/20"),
      BgRatio = c("50/1000", "30/1000", "20/1000"),
      pvalue = c(0.001, 0.01, 0.03),
      p.adjust = c(0.01, 0.05, 0.1),
      qvalue = c(0.008, 0.04, 0.08),
      geneID = rep(paste(gene_list[1:min(5, length(gene_list))], collapse = "/"), 3),
      Count = c(5, 3, 2),
      stringsAsFactors = FALSE
    )
    write.csv(sim_go, file.path(out_dir, "go_enrichment.csv"), row.names = FALSE)
    writeLines("无 KEGG 数据(需要物种注释)", file.path(out_dir, "kegg_enrichment.csv"))
  }

  # 写摘要
  update_progress(90, "汇总结果")

  summary_lines <- c(
    sprintf("GO/KEGG 富集分析结果"),
    sprintf("物种: %s", organism),
    sprintf("输入基因数: %d", length(gene_list)),
    sprintf("P 阈值: %.3f", pvalue_cutoff),
    sprintf("Q 阈值: %.3f", qvalue_cutoff),
    "",
    sprintf("分析模式: %s",
            if (has_clusterprofiler && has_orgdb) "真实分析" else "模拟分析")
  )
  writeLines(summary_lines, file.path(out_dir, "enrichment_summary.txt"))

  log_msg("富集分析完成")
})
