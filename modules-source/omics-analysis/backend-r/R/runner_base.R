# R 端 runner 基础库 — 参数解析、状态/进度更新、日志写入
# =====================================================
#
# 所有 R runner 都是这样:
#   1. 在脚本顶部 source("R/runner_base.R")
#   2. 调 init_runner() 拿到 job + data_dir + params
#   3. 用 update_progress(pct, stage, detail) 更新进度
#   4. 用 log(line) 写日志
#   5. 出错 stop(),正常 return invisible()
#
# 主程序通过 sys.exit() 之类机制不可靠,我们在最外层加 tryCatch。

suppressPackageStartupMessages({
  library(jsonlite)
})

# 全局小工具
`%||%` <- function(a, b) {
  if (is.null(a) || (length(a) == 1 && is.na(a))) b else a
}

# 把这些放在父环境,各 runner 脚本能直接用
.runner_state <- new.env()


parse_runner_args <- function() {
  args <- commandArgs(trailingOnly = TRUE)
  job_id <- NULL
  data_dir <- NULL
  
  for (i in seq_along(args)) {
    if (args[i] == "--job-id" && i + 1 <= length(args)) {
      job_id <- args[i + 1]
    } else if (args[i] == "--data-dir" && i + 1 <= length(args)) {
      data_dir <- args[i + 1]
    }
  }
  
  if (is.null(job_id) || is.null(data_dir)) {
    stop("用法: Rscript ... --job-id <id> --data-dir <dir>")
  }
  list(job_id = job_id, data_dir = data_dir)
}


init_runner <- function() {
  args <- parse_runner_args()
  .runner_state$job_id <- args$job_id
  .runner_state$data_dir <- args$data_dir
  
  job_path <- file.path(args$data_dir, "jobs", args$job_id, "job.json")
  if (!file.exists(job_path)) {
    stop(sprintf("job.json 不存在: %s", job_path))
  }
  
  job <- jsonlite::fromJSON(job_path, simplifyVector = FALSE)
  .runner_state$job <- job
  .runner_state$job_path <- job_path
  
  # 输出目录(由 manager 创建)
  .runner_state$out_dir <- job$output_subdir
  if (is.null(.runner_state$out_dir) || .runner_state$out_dir == "") {
    stop("job.output_subdir 为空,manager 应该已经设了")
  }
  if (!dir.exists(.runner_state$out_dir)) {
    dir.create(.runner_state$out_dir, recursive = TRUE)
  }
  
  log_msg(sprintf("R runner 启动 (pid=%d)", Sys.getpid()))
  
  invisible(job)
}


# 内部:写 job.json
#
# 注意 null/na 必须显式指定为 "null":
#   jsonlite::toJSON 默认 null="list",会把 R 的 NULL 写成 JSON `{}`(空对象),
#   而不是 JSON `null`。Python 一开始写的 job.json 里 error/started_at/
#   finished_at/pid 都是 null,R 这边 fromJSON 后是 NULL,如果再用默认参数
#   写回去就会变成 `{}`,前端拿到 `error: {}` 渲染时会触发 React #31
#   ("Objects are not valid as a React child")。
.save_job <- function() {
  j <- .runner_state$job
  tmp <- paste0(.runner_state$job_path, ".tmp")
  writeLines(jsonlite::toJSON(j, auto_unbox = TRUE, pretty = TRUE,
                                null = "null", na = "null"), tmp)
  file.rename(tmp, .runner_state$job_path)
}


update_progress <- function(pct, stage = "", detail = "",
                             indeterminate = FALSE) {
  j <- .runner_state$job
  j$progress <- list(
    pct = as.integer(pct),
    stage = stage,
    detail = detail,
    # 无法估算进度的长步骤(DESeq() / WGCNA blockwiseModules)设 TRUE,
    # 前端切到"流动动画"。R 是单线程,长调用期间无法发心跳,但前端动画是
    # 纯 CSS,客户端自己一直滑动,所以即便后端不更新也不会看起来卡死。
    indeterminate = isTRUE(indeterminate),
    heartbeat = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  )
  .runner_state$job <- j
  .save_job()
  invisible()
}


log_msg <- function(line) {
  log_path <- file.path(
    .runner_state$data_dir, "jobs", .runner_state$job_id, "log.txt"
  )
  ts <- format(Sys.time(), "%H:%M:%S")
  cat(sprintf("[%s] %s\n", ts, line), file = log_path, append = TRUE)
  invisible()
}


# 本任务的线程配额(由 JobManager 通过环境变量注入,= 全局 CPU 预算 // 并发数)。
# R 端的多线程步骤(如 WGCNA)应该用它,而不是自动抓满所有核心,
# 否则多个并发任务会把机器超额订阅。没注入时返回 default。
job_threads <- function(default = 1L) {
  v <- Sys.getenv("PLANTOMICS_JOB_THREADS", unset = "")
  if (nzchar(v) && !is.na(suppressWarnings(as.integer(v)))) {
    max(1L, as.integer(v))
  } else {
    as.integer(default)
  }
}


# 取参数(带默认值)
get_param <- function(key, default = NULL) {
  v <- .runner_state$job$params[[key]]
  if (is.null(v)) default else v
}


output_dir <- function() {
  .runner_state$out_dir
}


# 标记完成。子脚本结束时调一次。
mark_completed <- function() {
  j <- .runner_state$job
  j$status <- "completed"
  j$finished_at <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  .runner_state$job <- j
  .save_job()
  log_msg("任务完成")
}


# 标记失败。在 tryCatch 错误处理里调。
mark_failed <- function(err) {
  j <- .runner_state$job
  j$status <- "failed"
  j$error <- as.character(err$message %||% err)
  j$finished_at <- format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  .runner_state$job <- j
  .save_job()
  log_msg(sprintf("!! 任务失败: %s", j$error))
}


# 包一层 tryCatch,统一错误处理
run_with_error_handling <- function(body_fn) {
  tryCatch({
    body_fn()
    mark_completed()
  }, error = function(e) {
    mark_failed(e)
    quit(save = "no", status = 1)
  })
}


# %||% 兜底(R 4.4+ 内置,但保险)
`%||%` <- function(a, b) if (is.null(a)) b else a
