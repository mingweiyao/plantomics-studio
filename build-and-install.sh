#!/usr/bin/env bash
# PlantOmics Studio — 一键构建+安装 (Ubuntu 22.04+ / Debian 12+)
# ===================================================================
#
# 流程:
#   [1/6] 预检:依赖 + 代理 + 网络 + 镜像可达性
#   [2/6] 构建 core deb
#   [3/6] 构建 module deb
#   [4/6] 验证 module 里 conda env 的关键 R 包(关键!不要拿到残废 deb)
#   [5/6] 用 dpkg -i 安装 (apt 在 WSL 偶尔报 "Unsupported file")
#   [6/6] 启动验证
#
# 选项:
#   --skip-env       conda env 已存在(从前一次 build),复用
#                    第二次以后跑都用这个,省 30 分钟
#   --reset          构建前先清 mamba/conda cache + 删失败的 build 残留
#                    遇到 "Could not solve for environment specs" 用这个
#   --skip-install   只构建,不 sudo 安装
#   --core-only      只构建+装主程序
#   --no-precheck    跳过预检(老司机用)
#
# 用法:
#   bash build-and-install.sh                # 全新构建
#   bash build-and-install.sh --skip-env     # 增量构建(推荐第二次以后用)
#   bash build-and-install.sh --reset        # 上次失败,清干净重来

set -e
set -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()   { echo -e "${GREEN}==> $*${NC}"; }
note()  { echo -e "${BLUE}    $*${NC}"; }
warn()  { echo -e "${YELLOW}!!  $*${NC}"; }
fail()  { echo -e "${RED}xx  $*${NC}" >&2; exit 1; }
hr()    { echo -e "${BLUE}────────────────────────────────────────${NC}"; }

SKIP_ENV=0
SKIP_INSTALL=0
CORE_ONLY=0
NO_PRECHECK=0
DO_RESET=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-env)     SKIP_ENV=1 ;;
    --skip-install) SKIP_INSTALL=1 ;;
    --core-only)    CORE_ONLY=1 ;;
    --no-precheck)  NO_PRECHECK=1 ;;
    --reset)        DO_RESET=1 ;;
    --help|-h)      sed -n '2,30p' "$0" | sed 's/^# *//'; exit 0 ;;
    *) fail "未知参数: $1" ;;
  esac
  shift
done

build_args=""
[[ $SKIP_ENV -eq 1 ]] && build_args="--skip-env"

# ════════════════════════════════════════════════════════════
#  [0/6] (可选) 重置构建状态
# ════════════════════════════════════════════════════════════
if [[ $DO_RESET -eq 1 ]]; then
  hr; log "[0/6] --reset:清 cache + 删失败 build"; hr
  bash "$ROOT/scripts/reset-build.sh"
  echo ""
fi

# ════════════════════════════════════════════════════════════
#  [1/6] 预检
# ════════════════════════════════════════════════════════════
if [[ $NO_PRECHECK -eq 0 ]]; then
hr; log "[1/6] 预检"; hr

# ── 1a. 工具链 ────────────────────────────────
note "1a. 工具链"
missing=()
command -v cargo >/dev/null 2>&1 || \
  missing+=("rust  → curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")
command -v node  >/dev/null 2>&1 || \
  missing+=("node  → curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - ; sudo apt install -y nodejs")
command -v pnpm  >/dev/null 2>&1 || \
  missing+=("pnpm  → sudo npm install -g pnpm")
if ! command -v conda >/dev/null 2>&1 && ! command -v mamba >/dev/null 2>&1; then
  missing+=("conda 或 mamba  → 推荐 mamba: https://github.com/conda-forge/miniforge")
fi

sys_pkgs=(libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev
          librsvg2-dev pkg-config build-essential)
sys_missing=()
for p in "${sys_pkgs[@]}"; do
  dpkg -s "$p" >/dev/null 2>&1 || sys_missing+=("$p")
done
[[ ${#sys_missing[@]} -gt 0 ]] && \
  missing+=("系统包  → sudo apt install -y ${sys_missing[*]}")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo ""; fail "依赖缺失:
  $(printf '  - %s\n' "${missing[@]}")
全部装好后重跑此脚本"
fi
note "  ✓ 工具链齐"

# ── 1b. 代理状态 ─────────────────────────────
note "1b. 代理 / 网络"
proxy_set=0
for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; do
  [[ -n "${!v:-}" ]] && proxy_set=1
done
if [[ $proxy_set -eq 1 ]]; then
  warn "  检测到代理变量(http_proxy 等),如果是国内 Clash/V2Ray,
      它对国内镜像域名(mirrors.tuna / mirrors.ustc 等)经常路由错,
      导致 conda 装包失败。
      建议先在当前 shell unset:
        unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
  read -p "    继续? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || { warn "退出"; exit 0; }
fi

# ── 1c. 关键源可达性 ──────────────────────────
note "1c. 关键源可达"
declare -A urls=(
  [bioconda]="https://conda.anaconda.org/bioconda/linux-64/repodata.json"
  [conda-forge]="https://conda.anaconda.org/conda-forge/linux-64/repodata.json"
  [crates-io]="https://crates.io"
  [npm-registry]="https://registry.npmjs.org"
)
ok_count=0
fail_urls=()
for name in "${!urls[@]}"; do
  code=$(curl -sIo /dev/null -w '%{http_code}' --max-time 8 "${urls[$name]}" || echo "000")
  if [[ "$code" =~ ^(200|301|302|404)$ ]]; then
    note "  ✓ $name  ($code)"
    ok_count=$((ok_count + 1))
  else
    note "  ✗ $name  ($code)"
    fail_urls+=("$name")
  fi
done

# crates.io 不通不致命:core 的 Tauri 编译会自动改用国内 crates 镜像(rsproxy)
if printf '%s\n' "${fail_urls[@]}" 2>/dev/null | grep -qx "crates-io"; then
  note "  (crates.io ✗ 没关系:core 构建会自动用 rsproxy 国内 crates 镜像)"
fi

if [[ $ok_count -lt 3 ]]; then
  warn "  关键源 $((4 - ok_count))/4 不通,构建很可能失败。
      - conda 源慢/超时:脚本会自动在 USTC/清华/北外/南大/阿里 等镜像间换着重试;
        也可手动指定其一:
          PLANTOMICS_CONDA_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/anaconda bash build-and-install.sh
      - cargo/crates 走默认镜像仍超时,换清华源:
          PLANTOMICS_CRATES_MIRROR=sparse+https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/ bash build-and-install.sh"
  read -p "    强行继续? [y/N] " ans
  [[ "$ans" =~ ^[Yy]$ ]] || exit 0
fi

# ── 1d. 既有 conda env 健康 ──────────────────
if [[ $SKIP_ENV -eq 1 ]]; then
  note "1d. 既有 conda env 健康(--skip-env)"
  mod_env="$ROOT/modules-source/omics-rnaseq-bulk/build/conda-env"
  if [[ ! -d "$mod_env" ]]; then
    warn "  指定了 --skip-env 但 $mod_env 不存在 —
      要么去掉 --skip-env(让脚本重建 env),要么手动确认 env 路径"
    fail "中止"
  fi
  if [[ -x "$mod_env/bin/Rscript" ]]; then
    note "  抽查关键 R 包..."
    "$mod_env/bin/Rscript" -e '
      pkgs <- c("DESeq2","clusterProfiler","GO.db","org.At.tair.db",
                "GenomeInfoDbData","WGCNA")
      bad <- pkgs[!sapply(pkgs, requireNamespace, quietly=TRUE)]
      if (length(bad) > 0) {
        cat("  ✗ 缺失:", paste(bad, collapse=", "), "\n")
        quit(status=1)
      } else cat("  ✓ 关键 R 包齐全\n")
    ' || fail "既有 env 残废,去掉 --skip-env 重建,或 rm -rf modules-source/omics-rnaseq-bulk/build && 重跑"
  fi
fi

fi  # NO_PRECHECK

# ════════════════════════════════════════════════════════════
#  [2/6] 构建 core deb
# ════════════════════════════════════════════════════════════
hr; log "[2/6] 构建 core deb $build_args"; hr
bash scripts/build-deb.sh $build_args

core_deb=$(ls -t "$ROOT/dist/plantomics-studio_"*.deb 2>/dev/null | head -1)
[[ -z "$core_deb" ]] && fail "core deb 没出来,看上面输出"
log "  ✓ $(basename "$core_deb")"

# ════════════════════════════════════════════════════════════
#  [3/6] 构建 module deb
# ════════════════════════════════════════════════════════════
mod_deb=""
mod_deb2=""
build_failures=""
if [[ $CORE_ONLY -eq 0 ]]; then
  # ── 转录组模块(独立构建,失败不阻塞 analysis)──
  m1args="$build_args"
  if [[ ! -x "$ROOT/modules-source/omics-rnaseq-bulk/build/conda-env/bin/python3" ]]; then
    m1args="${build_args/--skip-env/}"
    [[ "$build_args" == *--skip-env* ]] && \
      log "  omics-rnaseq-bulk 还没建过 conda env → 本次为它创建(忽略 --skip-env)"
  fi
  hr; log "[3/6] 构建 omics-rnaseq-bulk module deb $m1args"; hr
  set +e
  bash modules-source/omics-rnaseq-bulk/scripts/build-deb.sh $m1args
  rc1=$?
  set -e
  if [[ $rc1 -eq 0 ]]; then
    mod_deb=$(ls -t "$ROOT/dist/plantomics-module-rnaseq-bulk_"*.deb 2>/dev/null | head -1)
    [[ -n "$mod_deb" ]] && log "  ✓ $(basename "$mod_deb")" || \
      { warn "  rnaseq 构建跑完了但没找到 deb"; build_failures="$build_failures omics-rnaseq-bulk"; }
  else
    warn "  omics-rnaseq-bulk 构建失败(rc=$rc1)— 继续尝试 analysis 模块,不中断"
    build_failures="$build_failures omics-rnaseq-bulk"
  fi

  # ── 分析模块(独立构建;新模块第一次 conda env 不存在则现建)──
  m2args="$build_args"
  if [[ ! -x "$ROOT/modules-source/omics-analysis/build/conda-env/bin/python3" ]]; then
    m2args="${build_args/--skip-env/}"
    [[ "$build_args" == *--skip-env* ]] && \
      log "  omics-analysis 还没建过 conda env → 本次为它创建(忽略 --skip-env)"
  fi
  hr; log "[3/6] 构建 omics-analysis module deb $m2args"; hr
  set +e
  bash modules-source/omics-analysis/scripts/build-deb.sh $m2args
  rc2=$?
  set -e
  if [[ $rc2 -eq 0 ]]; then
    mod_deb2=$(ls -t "$ROOT/dist/plantomics-module-analysis_"*.deb 2>/dev/null | head -1)
    [[ -n "$mod_deb2" ]] && log "  ✓ $(basename "$mod_deb2")" || \
      { warn "  analysis 构建跑完了但没找到 deb"; build_failures="$build_failures omics-analysis"; }
  else
    warn "  omics-analysis 构建失败(rc=$rc2)"
    build_failures="$build_failures omics-analysis"
  fi
else
  hr; log "[3/6] (--core-only) 跳过模块"; hr
fi

# ════════════════════════════════════════════════════════════
#  [4/6] 独立验证 conda env 健康
# ════════════════════════════════════════════════════════════
if [[ $CORE_ONLY -eq 0 ]]; then
hr; log "[4/6] 验证 module conda env 关键 R 包(独立检查)"; hr

mod_env="$ROOT/modules-source/omics-rnaseq-bulk/build/conda-env"
if [[ -x "$mod_env/bin/Rscript" ]]; then
  "$mod_env/bin/Rscript" - <<'RCHECK' || \
    warn "rnaseq module conda env 关键 R 包缺失(见上面列表)。deb 仍会装,但跑相关分析前需补 R 包"
pkgs <- c(
  # 下游 Bioconductor 栈已迁到 omics-analysis 模块;本模块 R 只剩 /health plumber 服务
  "plumber", "jsonlite"
)
bad <- character()
for (p in pkgs) {
  ok <- tryCatch({
    suppressPackageStartupMessages(suppressWarnings(library(p, character.only=TRUE)))
    TRUE
  }, error = function(e) FALSE)
  cat(sprintf("  %s %s\n", if (ok) "✓" else "✗", p))
  if (!ok) bad <- c(bad, p)
}
if (length(bad) > 0) {
  cat(sprintf("\n!! %d 个包缺失\n", length(bad)))
  quit(status=1)
}
cat("\n✓ rnaseq 模块 R 服务依赖(plumber/jsonlite)就位\n")
RCHECK
fi

mod_env2="$ROOT/modules-source/omics-analysis/build/conda-env"
if [[ -x "$mod_env2/bin/Rscript" ]]; then
  log "  验证 omics-analysis 模块 R 包"
  "$mod_env2/bin/Rscript" - <<'RCHECK2' || \
    warn "analysis module conda env 关键 R 包缺失(见上面列表)。deb 仍会装,但跑相关分析前需补 R 包"
pkgs <- c(
  # 可插拔宿主:分析包运行时按需装,这里只确认框架依赖
  "jsonlite", "plumber"
)
bad <- character()
for (p in pkgs) {
  ok <- tryCatch({
    suppressPackageStartupMessages(suppressWarnings(library(p, character.only=TRUE)))
    TRUE
  }, error = function(e) FALSE)
  cat(sprintf("  %s %s\n", if (ok) "✓" else "✗", p))
  if (!ok) bad <- c(bad, p)
}
if (length(bad) > 0) { cat(sprintf("\n!! %d 个包缺失\n", length(bad))); quit(status=1) }
cat("\n✓ analysis 模块框架依赖(jsonlite/plumber)就位;分析包运行时按需装\n")
RCHECK2
fi
fi

# ════════════════════════════════════════════════════════════
#  [5/6] 安装(用 dpkg -i 绕开 apt 的 Unsupported file)
# ════════════════════════════════════════════════════════════
if [[ $SKIP_INSTALL -eq 1 ]]; then
  hr; log "[5/6] (--skip-install) 跳过安装"; hr
  echo "deb 在:"
  echo "    $core_deb"
  [[ -n "$mod_deb" ]] && echo "    $mod_deb"
  [[ -n "$mod_deb2" ]] && echo "    $mod_deb2"
  exit 0
fi

hr; log "[5/6] 安装 deb (用 dpkg -i,会问 sudo 密码)"; hr
sudo dpkg -i "$core_deb" || true
[[ -n "$mod_deb" ]] && { sudo dpkg -i "$mod_deb" || true; }
[[ -n "$mod_deb2" ]] && { sudo dpkg -i "$mod_deb2" || true; }
sudo apt-get install -f -y    # 自动补缺失的系统依赖

# ════════════════════════════════════════════════════════════
#  [6/6] 验证安装
# ════════════════════════════════════════════════════════════
hr; log "[6/6] 安装验证"; hr

if command -v plantomics-studio >/dev/null 2>&1; then
  note "  ✓ plantomics-studio 命令在 PATH 里"
else
  warn "  ✗ plantomics-studio 命令找不到 — core deb 没装上?"
fi
if [[ -d "/opt/plantomics-studio/modules/omics-rnaseq-bulk" ]]; then
  note "  ✓ module 装到 /opt/plantomics-studio/modules/omics-rnaseq-bulk/"
elif [[ $CORE_ONLY -eq 0 ]]; then
  warn "  ✗ module 没装上"
fi
if [[ -d "/opt/plantomics-studio/modules/omics-analysis" ]]; then
  note "  ✓ analysis module 装到 /opt/plantomics-studio/modules/omics-analysis/"
elif [[ $CORE_ONLY -eq 0 ]]; then
  warn "  ✗ analysis module 没装上"
fi

echo ""
if [[ -n "$build_failures" ]]; then
  warn "以下模块构建未成功:$build_failures"
  warn "其余已构建的模块已照常安装。修掉上面的报错后,可单独重建未成功的模块:"
  for m in $build_failures; do
    warn "    bash modules-source/$m/scripts/build-deb.sh --skip-env && sudo dpkg -i dist/plantomics-module-*_*.deb"
  done
  echo ""
fi
log "✓ 全部完成"
echo ""
echo "启动:plantomics-studio"
echo ""
echo "首次用富集功能:"
echo "  - 项目 → 下游分析 → 富集分析 → 顶上'物种数据库'卡片"
echo "  - 模式物种(如拟南芥):走 OrgDb 模式 (org.At.tair.db / TAIR / ath)"
echo "  - 非模式物种:跑 eggNOG-mapper 后导入 .annotations 文件"
