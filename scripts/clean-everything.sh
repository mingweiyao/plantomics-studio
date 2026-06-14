#!/usr/bin/env bash
#
# clean-everything.sh
# ====================
# 完全删除 PlantOmics Studio 在系统的所有痕迹,让你能从零状态重测。
#
# 删除范围:
#   - apt 包(plant-omics-studio + 所有 plantomics-module-*)
#   - /opt/plantomics-studio/ 整个目录
#   - ~/.plantomics/(用户数据,**会丢失项目和参考资源**)
#   - 桌面快捷方式
#
# **保留**:
#   - 源码工作区(~/plantomics-studio-v2/)
#   - build/ 缓存(避免重复下载 conda 包,加快重测)
#
# 用法:
#   bash clean-everything.sh

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}==> $*${NC}"; }
warn() { echo -e "${YELLOW}!! $*${NC}"; }

log "PlantOmics Studio 完全清理"
echo ""
echo "将删除:"
echo "  · 所有 plantomics 相关 apt 包"
echo "  · /opt/plantomics-studio/(主程序 + 已装模块)"
echo "  · ~/.plantomics/(用户数据 — 项目和参考资源)"
echo ""

read -r -p "确定继续?这会丢失你创建的所有项目数据 [y/N] " ans
if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "取消"
    exit 0
fi

# 1. 杀掉跑着的进程
log "停止运行中的进程"
pkill -f plantomics-studio 2>/dev/null || true
pkill -f plumber.R 2>/dev/null || true
pkill -f "modules/omics-rnaseq-bulk" 2>/dev/null || true
sleep 1

# 2. 卸载所有相关 apt 包
log "卸载 apt 包"
INSTALLED_PKGS=$(dpkg -l 2>/dev/null | grep -E "^ii\s+(plant-omics-studio|plantomics-module-)" | awk '{print $2}' || true)
if [[ -n "$INSTALLED_PKGS" ]]; then
    echo "  发现已装包:"
    echo "$INSTALLED_PKGS" | sed 's/^/    /'
    sudo apt remove --purge -y $INSTALLED_PKGS
else
    echo "  没有相关 apt 包"
fi

# 3. 强制删 /opt/plantomics-studio/(以防 apt remove 没完全清干净)
if [[ -d /opt/plantomics-studio ]]; then
    log "删除 /opt/plantomics-studio/"
    sudo rm -rf /opt/plantomics-studio
fi

# 4. 删用户数据
if [[ -d ~/.plantomics ]]; then
    log "删除 ~/.plantomics/(用户数据)"
    rm -rf ~/.plantomics
fi

# 5. 删桌面文件残留
sudo rm -f /usr/share/applications/plantomics-studio.desktop
sudo rm -f /usr/share/applications/plant-omics-studio.desktop

# 6. apt 缓存清理
log "刷新 apt 数据库"
sudo apt autoremove -y 2>/dev/null || true
sudo dpkg --configure -a 2>/dev/null || true

# 7. 验证
log "验证清理结果"
echo "  apt 包:"
dpkg -l 2>/dev/null | grep -E "plant-omics|plantomics-module" | sed 's/^/    /' || echo "    (无)"
echo ""
echo "  /opt/plantomics-studio/: $(ls -d /opt/plantomics-studio 2>/dev/null || echo "(已删)")"
echo "  ~/.plantomics/:           $(ls -d ~/.plantomics 2>/dev/null || echo "(已删)")"

cat <<EOF

${GREEN}====== 清理完成 ======${NC}

接下来:
  1. 进入源码目录:
     cd ~/plantomics-studio-v2

  2. 构建主程序(--skip-env 复用已有 env):
     bash scripts/build-deb.sh --skip-env

  3. 装主程序:
     sudo apt install ./dist/plantomics-studio_*.deb

  4. 构建模块(已经构建过的话会复用 conda env):
     cd modules-source/omics-rnaseq-bulk
     bash scripts/build-deb.sh --skip-env

  5. 装模块:
     sudo apt install ./dist/plantomics-module-rnaseq-bulk_*.deb

  6. 启动:
     plantomics-studio
EOF
