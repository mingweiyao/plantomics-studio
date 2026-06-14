#!/usr/bin/env bash
#
# PlantOmics Studio 完全干净重装脚本
# ====================================
#
# 这个脚本会把所有与 plantomics-studio 相关的痕迹清干净,包括:
#   1. 卸载主程序 deb (plant-omics-studio)
#   2. 卸载所有模块 deb (plantomics-module-*)
#   3. 删除安装目录 /opt/plantomics-studio/
#   4. 删除用户数据 ~/.plantomics/
#   5. 删除构建产物 ~/plantomics-studio*/build/
#
# 用法:
#   bash uninstall-all.sh            # 默认:不删用户数据 (~/.plantomics)
#   bash uninstall-all.sh --purge    # 全部清干净,包括用户的项目和参考资源数据

set -e

PURGE=0
if [[ "${1:-}" == "--purge" ]]; then
    PURGE=1
fi

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}==> $*${NC}"; }
warn() { echo -e "${YELLOW}!! $*${NC}"; }

# 1. 杀进程
log "Step 1/6 杀掉运行中的 plantomics 相关进程"
pkill -f plantomics-studio 2>/dev/null || true
pkill -f "modules/omics-" 2>/dev/null || true
pkill -f "backend-py/main.py" 2>/dev/null || true
pkill -f "plumber.R" 2>/dev/null || true
sleep 2

# 2. 卸载所有模块 deb (plantomics-module-*)
log "Step 2/6 卸载模块 deb"
MODULE_PKGS=$(dpkg -l 2>/dev/null | grep -E '^ii\s+plantomics-module-' | awk '{print $2}' || true)
if [[ -n "$MODULE_PKGS" ]]; then
    for pkg in $MODULE_PKGS; do
        log "  卸载 $pkg"
        sudo apt remove --purge -y "$pkg" || warn "$pkg 卸载失败,继续"
    done
else
    log "  无模块 deb"
fi

# 3. 卸载主程序 deb (注意 Tauri 把 productName "PlantOmics Studio" 转成 plant-omics-studio)
log "Step 3/6 卸载主程序 deb"
for pkg in plant-omics-studio plantomics-studio; do
    if dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
        log "  卸载 $pkg"
        sudo apt remove --purge -y "$pkg" || warn "$pkg 卸载失败"
    fi
done

# 4. 强制删除安装目录(以防 deb 卸载没清干净)
log "Step 4/6 删除 /opt/plantomics-studio"
sudo rm -rf /opt/plantomics-studio
log "  ✓ 已删"

# 5. 删除用户数据(可选)
log "Step 5/6 用户数据"
if [[ $PURGE -eq 1 ]]; then
    if [[ -d ~/.plantomics ]]; then
        warn "  --purge: 删除 ~/.plantomics(项目、参考资源等用户数据)"
        rm -rf ~/.plantomics
    fi
else
    if [[ -d ~/.plantomics ]]; then
        warn "  保留 ~/.plantomics(用 --purge 可清掉)"
        warn "    其中:"
        warn "      项目: $(ls ~/.plantomics/projects 2>/dev/null | wc -l) 个"
        warn "      资源: $(ls ~/.plantomics/references 2>/dev/null | wc -l) 个"
    fi
fi

# 6. 删 desktop entry / 桌面快捷方式残留
log "Step 6/6 清残留"
sudo rm -f /usr/share/applications/plantomics-studio.desktop
sudo rm -f /usr/share/applications/plant-omics-studio.desktop
sudo rm -f /usr/share/applications/PlantOmics\ Studio.desktop
sudo rm -f /usr/share/icons/hicolor/*/apps/plantomics-studio.png
sudo update-desktop-database -q 2>/dev/null || true

# 验证
echo ""
log "===== 重装清理完成 ====="
echo ""
echo "状态检查:"
echo "  /opt/plantomics-studio: $([[ -d /opt/plantomics-studio ]] && echo '仍存在 ✗' || echo '已删 ✓')"
echo "  ~/.plantomics:           $([[ -d ~/.plantomics ]] && echo "仍存在($(du -sh ~/.plantomics 2>/dev/null | cut -f1))" || echo '已删')"
echo "  dpkg 主程序:              $(dpkg -l plant-omics-studio plantomics-studio 2>/dev/null | grep -c ^ii) 个"
echo "  dpkg 模块:                $(dpkg -l 2>/dev/null | grep -cE '^ii\s+plantomics-module-' || echo 0) 个"
echo ""
echo "现在可以重新构建并安装:"
echo "  cd ~/plantomics-studio-v2"
echo "  bash scripts/build-deb.sh --skip-env       # 主程序"
echo "  cd modules-source/omics-rnaseq-bulk"
echo "  bash scripts/build-deb.sh --skip-env       # 模块"
