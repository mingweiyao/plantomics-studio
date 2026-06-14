#!/usr/bin/env Rscript
# 通用分析执行器 —— 不含任何具体分析逻辑,只负责:
#   1. 读取 io.json(由 Python 端写好:inputs 路径 + params + out_dir)
#   2. 扫描目标 analysis.R 需要哪些 R 包,缺的按需装(USTC 镜像,装到用户可写目录)
#   3. source 目标 analysis.R(它定义了 run())
#   4. 调 run(inputs, params, out_dir)
#
# 用法: Rscript run_analysis.R <analysis.R 路径> <io.json 路径>

suppressPackageStartupMessages(library(jsonlite))

# ── 用户可写的 R 库目录(env 在 /opt 下只读,装包要装到这里)──
.userlib <- Sys.getenv("PLANTOMICS_R_USERLIB",
                       file.path(Sys.getenv("HOME"), ".plantomics", "analysis-r-libs"))
dir.create(.userlib, showWarnings = FALSE, recursive = TRUE)
.libPaths(c(.userlib, .libPaths()))

# ── 扫描 analysis.R 声明/使用了哪些包 ──
.extract_pkgs <- function(script_path) {
  txt <- readLines(script_path, warn = FALSE)
  pkgs <- character(0)
  # 1) 头部可选声明:#' dependencies: [a, b, c]
  for (ln in grep("dependencies\\s*:", txt, value = TRUE)) {
    m <- regmatches(ln, regexec("\\[([^]]*)\\]", ln))[[1]]
    if (length(m) >= 2) {
      parts <- trimws(gsub("[\"']", "", strsplit(m[2], ",")[[1]]))
      pkgs <- c(pkgs, parts[nzchar(parts)])
    }
  }
  # 2) library() / require() / requireNamespace()
  pat <- "(?:library|require|requireNamespace)\\s*\\(\\s*[\"']?([A-Za-z][A-Za-z0-9._]*)"
  for (hits in regmatches(txt, gregexpr(pat, txt, perl = TRUE))) {
    for (h in hits) pkgs <- c(pkgs, sub(pat, "\\1", h, perl = TRUE))
  }
  # 3) pkg::fun(基础包会被 requireNamespace 判定已存在,不会被装)
  pat2 <- "([A-Za-z][A-Za-z0-9._]*)::"
  for (hits in regmatches(txt, gregexpr(pat2, txt, perl = TRUE))) {
    for (h in hits) pkgs <- c(pkgs, sub("::$", "", h))
  }
  unique(pkgs[nzchar(pkgs)])
}

# ── 缺啥装啥(CRAN + Bioconductor 都走 USTC,不碰 bioconductor.org)──
ensure_deps <- function(script_path) {
  pkgs <- .extract_pkgs(script_path)
  if (length(pkgs) == 0) return(invisible())
  miss <- pkgs[!vapply(pkgs, function(p) requireNamespace(p, quietly = TRUE), logical(1))]
  if (length(miss) == 0) return(invisible())
  cat(sprintf("[analysis] 该分析缺以下 R 包,按需安装到 %s: %s\n",
              .userlib, paste(miss, collapse = ", ")))
  cran <- Sys.getenv("PLANTOMICS_CRAN_REPO", "https://mirrors.ustc.edu.cn/CRAN")
  bioc_base <- Sys.getenv("PLANTOMICS_BIOC_MIRROR", "https://mirrors.ustc.edu.cn/bioc")
  biocver <- tryCatch(sub("^(\\d+\\.\\d+).*", "\\1",
                          as.character(packageVersion("BiocVersion"))),
                      error = function(e) NA_character_)
  if (is.na(biocver)) {
    rv <- getRversion()
    biocver <- if (rv >= "4.4") "3.20" else if (rv >= "4.3") "3.18" else "3.16"
  }
  repos <- c(
    BioCsoft = paste0(bioc_base, "/packages/", biocver, "/bioc"),
    BioCann  = paste0(bioc_base, "/packages/", biocver, "/data/annotation"),
    BioCexp  = paste0(bioc_base, "/packages/", biocver, "/data/experiment"),
    CRAN     = cran
  )
  install.packages(miss, repos = repos, lib = .userlib,
                   Ncpus = max(1L, parallel::detectCores()))
  still <- miss[!vapply(miss, function(p) requireNamespace(p, quietly = TRUE), logical(1))]
  if (length(still) > 0) {
    stop(sprintf("这些依赖装不上(检查网络或缺系统库): %s", paste(still, collapse = ", ")))
  }
  cat("[analysis] 依赖就绪\n")
}

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("用法: run_analysis.R <analysis.R> <io.json>")
script_path <- args[1]
io_path <- args[2]

if (!file.exists(script_path)) stop(sprintf("分析脚本不存在: %s", script_path))
if (!file.exists(io_path)) stop(sprintf("io.json 不存在: %s", io_path))

io <- fromJSON(io_path, simplifyVector = TRUE, simplifyDataFrame = FALSE)
inputs <- if (is.null(io$inputs)) list() else io$inputs
params <- if (is.null(io$params)) list() else io$params
out_dir <- io$out_dir
if (is.null(out_dir)) stop("io.json 缺 out_dir")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

# 先确保依赖就绪,再 source
ensure_deps(script_path)

# 在隔离环境里 source 分析脚本,要求它定义 run()
env <- new.env(parent = globalenv())
sys.source(script_path, envir = env)
if (!exists("run", envir = env, inherits = FALSE) ||
    !is.function(get("run", envir = env))) {
  stop(sprintf("%s 必须定义一个 run(inputs, params, out_dir) 函数", script_path))
}

cat(sprintf("[analysis] 执行 %s\n", basename(dirname(script_path))))
res <- get("run", envir = env)(inputs, params, out_dir)

# 落一份运行结果清单
files <- list.files(out_dir, recursive = FALSE)
writeLines(toJSON(list(ok = TRUE, out_dir = out_dir, files = files),
                  auto_unbox = TRUE, pretty = TRUE),
           file.path(out_dir, "_analysis_result.json"))
cat("[analysis] 完成\n")
