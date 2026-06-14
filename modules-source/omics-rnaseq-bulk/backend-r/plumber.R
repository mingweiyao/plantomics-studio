# omics-rnaseq-bulk 模块 - R 后端入口
# ====================================
# 由主程序启动,监听环境变量 MODULE_R_PORT 指定的端口。
# 子批次 3.1 只有 /health,真正的分析 plumber endpoints 放到子批次 3.2。

suppressPackageStartupMessages({
  library(plumber)
  library(jsonlite)
})

MODULE_ID <- "omics-rnaseq-bulk"
MODULE_VERSION <- "1.0.0"

port <- as.integer(Sys.getenv("MODULE_R_PORT", "0"))
if (port == 0) {
  stop("MODULE_R_PORT 环境变量未设置")
}

cat(sprintf("[%s-r] 启动 plumber on port %d\n", MODULE_ID, port))
cat(sprintf("[%s-r] data_dir=%s\n", MODULE_ID,
            Sys.getenv("PLANTOMICS_DATA_DIR", "(none)")))
cat(sprintf("[%s-r] module_data_dir=%s\n", MODULE_ID,
            Sys.getenv("MODULE_DATA_DIR", "(none)")))

# 找到脚本所在目录 - Rscript 模式下用 commandArgs 是最可靠的办法
get_script_dir <- function() {
  # 方法 1: source() 模式时 sys.frame 有 ofile
  this_file <- tryCatch(sys.frame(1)$ofile, error = function(e) NULL)
  
  # 方法 2: Rscript 模式 - commandArgs 里有 --file=...
  if (is.null(this_file)) {
    args <- commandArgs(trailingOnly = FALSE)
    file_arg <- grep("^--file=", args, value = TRUE)
    if (length(file_arg) > 0) {
      this_file <- sub("^--file=", "", file_arg[1])
    }
  }
  
  if (is.null(this_file) || !nzchar(this_file)) {
    stop("无法定位 plumber.R 脚本路径")
  }
  
  dirname(normalizePath(this_file))
}

script_dir <- get_script_dir()
cat(sprintf("[%s-r] script_dir=%s\n", MODULE_ID, script_dir))

api_file <- file.path(script_dir, "R", "api.R")
if (!file.exists(api_file)) {
  stop(sprintf("找不到 plumber 文件: %s", api_file))
}

pr <- plumber::pr(api_file)
plumber::pr_run(pr, host = "127.0.0.1", port = port, docs = FALSE)
