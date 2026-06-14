#!/usr/bin/env bash
# 重置构建状态 — 清 conda/mamba cache + 删失败的 build 中间产物
# ===============================================================
#
# 什么时候用:
#   - conda env 创建报 "Could not solve for environment specs"
#     (尤其是日志里有 "Using cache" 的话,几乎肯定是这个问题)
#   - mamba clean 看着跑了但下次构建还是同样错
#   - 上一次构建中途崩溃,build/ 目录留了半成品
#
# 这脚本做什么:
#   1. mamba clean --all -y -f        清 mamba 全部 cache
#   2. conda clean --all -y -f        清 conda cache
#   3. rm -rf ~/.cache/mamba/*        手动删 cache 目录(防 mamba clean 漏)
#   4. rm -rf ~/.conda/pkgs/cache/*   同上
#   5. rm -rf modules-source/.../build  删模块构建中间产物
#   6. rm -rf core/build              删主程序构建中间产物
#
# 不清的:
#   - 已构建的 conda env 本体(modules-source/.../build/conda-env)
#     如果这个也想清,先跑 rm -rf modules-source/omics-rnaseq-bulk/build/conda-env
#   - dist/*.deb (留着以防你想看)
#   - cargo target/(rust 缓存,跨次复用很省时间)
#   - node_modules
#
# 用法:
#   bash scripts/reset-build.sh

set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}==> $*${NC}"; }
warn() { echo -e "${YELLOW}!! $*${NC}"; }

log "[1/4] 清 mamba cache"
if command -v mamba >/dev/null 2>&1; then
  mamba clean --all -y -f 2>&1 | tail -2 || true
else
  warn "  没 mamba,跳过"
fi

log "[2/4] 清 conda cache"
conda clean --all -y -f 2>&1 | tail -2 || true

log "[3/4] 手动删残留 cache 目录(防 mamba clean 漏)"
for d in ~/.cache/mamba ~/.conda/pkgs/cache ~/.cache/conda; do
  if [[ -d "$d" ]]; then
    rm -rf "$d"/*
    echo "  ✓ $d/*"
  fi
done

log "[4/4] 删 build 中间产物"
for d in modules-source/omics-rnaseq-bulk/build/conda-create.log \
         modules-source/omics-rnaseq-bulk/build/env \
         modules-source/omics-rnaseq-bulk/build/deb-staging \
         core/build; do
  if [[ -e "$d" ]]; then
    rm -rf "$d"
    echo "  ✓ $d"
  fi
done

# 注意:不删 modules-source/.../build/conda-env 本体,因为重建要 30 分钟
# 想全删的话:rm -rf modules-source/omics-rnaseq-bulk/build
echo ""
log "✓ 重置完成"
echo ""
echo "下一步:bash build-and-install.sh"
echo "  (如果想全新建 env,先 rm -rf modules-source/omics-rnaseq-bulk/build/conda-env)"
