#!/usr/bin/env bash
#
# PlantOmics Studio 主程序 deb 构建脚本(v2 瘦壳版)
# ====================================================
# 主程序自包含,但不含任何具体组学模块。
# 模块由用户在主程序里通过"模块管理"页面安装。
#
# 用法:
#   bash scripts/build-deb.sh                # 完整构建
#   bash scripts/build-deb.sh --skip-env     # 复用现有 conda env

set -e

VERSION="1.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
cd "$ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}==> $*${NC}"; }
warn() { echo -e "${YELLOW}!! $*${NC}"; }
fail() { echo -e "${RED}xx $*${NC}" >&2; exit 1; }

SKIP_ENV=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-env) SKIP_ENV=1 ;;
        --help|-h)
            sed -n '2,12p' "$0" | sed 's/^# *//'
            exit 0
            ;;
        *) fail "未知参数: $1" ;;
    esac
    shift
done

log "Step 1/5 检查构建环境"
for cmd in cargo pnpm dpkg-deb python3; do
    command -v $cmd >/dev/null || fail "缺工具: $cmd"
done

CONDA_BIN=""
if command -v mamba >/dev/null; then
    CONDA_BIN="mamba"
elif command -v conda >/dev/null; then
    CONDA_BIN="conda"
else
    fail "没装 mamba 或 conda"
fi
log "  使用 $CONDA_BIN"

# 网络检查:确认能访问清华镜像。代理设置会导致 mamba 死活连不上,
# 提前发现避免浪费几分钟看 mamba retry。
if [[ -n "${http_proxy}${https_proxy}${HTTP_PROXY}${HTTPS_PROXY}${all_proxy}${ALL_PROXY}" ]]; then
    warn "  检测到代理设置,可能干扰国内镜像访问:"
    env | grep -iE "proxy" | sed 's/^/      /'
    warn "  如果构建失败,请先 unset 代理变量后重试:"
    warn "    unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
fi

if ! curl --max-time 5 -fsI https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud/conda-forge/ >/dev/null 2>&1; then
    warn "  访问清华镜像失败,可能影响 conda env 创建。"
    warn "  如果你确实有可用网络,请检查代理设置或 DNS。"
fi

mkdir -p build dist

ENV_DIR="$ROOT/build/conda-env"

if [[ $SKIP_ENV -eq 1 && -d "$ENV_DIR" ]]; then
    log "Step 2/5 跳过 conda env 创建(--skip-env)"
else
    log "Step 2/5 创建主程序 conda env(瘦版,3-5 分钟)"
    rm -rf "$ENV_DIR"

    # 候选镜像(可用 PLANTOMICS_CONDA_MIRROR 强制指定某一个)
    MIRRORS=(
        "https://mirrors.ustc.edu.cn/anaconda"
        "https://mirrors.tuna.tsinghua.edu.cn/anaconda"
        "https://mirrors.bfsu.edu.cn/anaconda"
        "https://mirrors.nju.edu.cn/anaconda"
        "https://mirrors.aliyun.com/anaconda"
        "https://mirrors.westlake.edu.cn/anaconda"
    )
    if [[ -n "${PLANTOMICS_CONDA_MIRROR:-}" ]]; then
        MIRRORS=("$PLANTOMICS_CONDA_MIRROR")
        log "  用环境变量指定的镜像: $PLANTOMICS_CONDA_MIRROR"
    fi

    # 写 .condarc。$1 为镜像 base(空表示官方默认源)。带网络容错:慢镜像多重试,
    # 不要一超时就整体失败。
    write_condarc() {
        local m="$1"
        {
            echo "channels:"
            echo "  - conda-forge"
            echo "show_channel_urls: true"
            if [[ -n "$m" ]]; then
                echo "default_channels:"
                echo "  - $m/pkgs/main"
                echo "  - $m/pkgs/r"
                echo "custom_channels:"
                echo "  conda-forge: $m/cloud"
            fi
            echo "remote_connect_timeout_secs: 30.0"
            echo "remote_read_timeout_secs: 120.0"
            echo "remote_max_retries: 5"
            echo "remote_backoff_factor: 2"
        } > "$ROOT/build/.condarc"
        export CONDARC="$ROOT/build/.condarc"
    }

    # 逐个镜像:探测 → 试建 → 失败(常见是慢到超时)就换下一个;最后兜底用官方源
    log "  探测/试用镜像(失败自动换下一个)..."
    CONDA_RC=1
    for cand in "${MIRRORS[@]}" "OFFICIAL"; do
        if [[ "$cand" == "OFFICIAL" ]]; then
            warn "  国内镜像都没建成,改用 conda 官方源(慢但稳)"
            write_condarc ""
        else
            if ! curl -sI -m 8 "$cand/cloud/conda-forge/noarch/repodata.json" 2>/dev/null \
                 | grep -qiE "HTTP/[12].* 200|200 OK"; then
                log "  ✗ $cand 探测不通,跳过"
                continue
            fi
            log "  ✓ 试用镜像: $cand"
            write_condarc "$cand"
        fi

        set +e
        $CONDA_BIN env create -f "$ROOT/core/conda-env/base.yaml" -p "$ENV_DIR" 2>&1 | \
            tee "$ROOT/build/conda-create.log"
        CONDA_RC=${PIPESTATUS[0]}
        set -e
        [[ $CONDA_RC -eq 0 ]] && break
        warn "  用 $cand 创建失败(多半是太慢/超时),换下一个镜像重试..."
        rm -rf "$ENV_DIR"
    done

    [[ $CONDA_RC -eq 0 ]] || fail "conda env 创建失败(所有镜像都试过了),见 build/conda-create.log
可手动指定一个更快的镜像再来:
  PLANTOMICS_CONDA_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/anaconda bash build-and-install.sh --reset
若错误里有 'Using cache' 字样,清 cache 重来:
  bash build-and-install.sh --reset"

    for tool in python3 uvicorn; do
        [[ -x "$ENV_DIR/bin/$tool" ]] || fail "环境中找不到 $tool"
    done
    log "  ✓ env 创建完成"
fi

log "Step 3/5 conda-pack 打包"
PACK_TAR="$ROOT/build/conda-env-packed.tar.gz"
rm -f "$PACK_TAR"
"$ENV_DIR/bin/conda-pack" -p "$ENV_DIR" -o "$PACK_TAR" \
    --ignore-missing-files --quiet

PACKED_DIR="$ROOT/build/env"
rm -rf "$PACKED_DIR"
mkdir -p "$PACKED_DIR"
tar -xzf "$PACK_TAR" -C "$PACKED_DIR"

log "  ✓ env 已打包"

log "Step 4/5 前端 + Tauri 编译"
cd "$ROOT/core"

if [[ ! -d "node_modules" ]]; then
    log "  装 pnpm 依赖"
    
    # 配 pnpm 用国内镜像(优先级:npmmirror → tencent → 官方)
    PNPM_MIRRORS=(
        "https://registry.npmmirror.com"
        "https://mirrors.cloud.tencent.com/npm/"
        "https://registry.npmjs.org/"
    )
    
    PNPM_OK=0
    for mirror in "${PNPM_MIRRORS[@]}"; do
        log "  尝试镜像: $mirror"
        # pnpm 新版用 --fetch-timeout(旧版叫 --network-timeout)
        # 60秒超时避免快速失败
        if pnpm install \
                --registry="$mirror" \
                --config.fetch-timeout=60000 \
                --config.fetch-retries=3; then
            PNPM_OK=1
            break
        fi
        warn "  镜像 $mirror 失败,尝试下一个"
        # 清掉可能损坏的 node_modules
        rm -rf node_modules
    done
    
    if [[ $PNPM_OK -eq 0 ]]; then
        fail "所有 npm 镜像都失败,请检查网络"
    fi
fi

ICON_PATH="$ROOT/core/src-tauri/icons/icon.png"
if [[ ! -f "$ICON_PATH" ]]; then
    log "  生成占位图标"
    "$ENV_DIR/bin/python3" - <<PYEOF
import struct, zlib
w, h = 32, 32
data = b""
for y in range(h):
    data += b"\x00"
    for x in range(w):
        data += bytes([0x4c, 0xaf, 0x50, 0xff])
def chunk(typ, d):
    return struct.pack(">I", len(d)) + typ + d + struct.pack(">I", zlib.crc32(typ + d) & 0xffffffff)
ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
idat = zlib.compress(data)
out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
with open("$ICON_PATH", "wb") as f:
    f.write(out)
PYEOF
fi

# ── 配 cargo 用国内 crates 镜像(crates.io 在受限网络常 403/超时)──
# 默认 rsproxy.cn(字节跳动,sparse 协议,国内快且稳)。可用 PLANTOMICS_CRATES_MIRROR
# 换别的源;PLANTOMICS_NO_CRATES_MIRROR=1 则直连 crates.io 不改。
# 用 build 内独立的 CARGO_HOME,不动你全局 ~/.cargo;crate 缓存留在 build/ 里,重建复用。
if [[ "${PLANTOMICS_NO_CRATES_MIRROR:-0}" != "1" ]]; then
    CRATES_MIRROR="${PLANTOMICS_CRATES_MIRROR:-sparse+https://rsproxy.cn/index/}"
    export CARGO_HOME="$ROOT/build/cargo-home"
    mkdir -p "$CARGO_HOME"
    cat > "$CARGO_HOME/config.toml" <<EOF
[source.crates-io]
replace-with = "mirror"

[source.mirror]
registry = "$CRATES_MIRROR"

[net]
git-fetch-with-cli = true
EOF
    log "  cargo crates 镜像: $CRATES_MIRROR"
    log "  (若仍 403/超时:PLANTOMICS_CRATES_MIRROR=sparse+https://mirrors.tuna.tsinghua.edu.cn/crates.io-index/ 重试)"
fi

log "  Tauri 编译"
pnpm tauri build --bundles deb

ORIGINAL_DEB=$(find "$ROOT/core/src-tauri/target/release/bundle/deb" -name "*.deb" 2>/dev/null | head -1)
[[ -f "$ORIGINAL_DEB" ]] || fail "Tauri 没生成 deb"
log "  ✓ Tauri deb: $ORIGINAL_DEB"

cd "$ROOT"

log "Step 5/5 嵌入资源并重打 deb"

DEB_NAME="plantomics-studio_${VERSION}_amd64.deb"
WORK_DIR="$ROOT/build/deb-work"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

dpkg-deb -R "$ORIGINAL_DEB" "$WORK_DIR"
# 修復 DEBIAN 目录权限（Tauri 打包有时会用 2755）
chmod 755 "$WORK_DIR/DEBIAN" 2>/dev/null || true

INSTALL_ROOT="$WORK_DIR/opt/plantomics-studio"
mkdir -p "$INSTALL_ROOT"

# 嵌入 conda env
cp -a "$PACKED_DIR" "$INSTALL_ROOT/env"

# 嵌入主程序后端代码
cp -a "$ROOT/core/backend-py" "$INSTALL_ROOT/backend-py"

# 嵌入资源
mkdir -p "$INSTALL_ROOT/resources"
cp "$ROOT/core/src-tauri/resources/modules.json" "$INSTALL_ROOT/resources/"

# modules 目录开始空
mkdir -p "$INSTALL_ROOT/modules"

# postinst
cat > "$WORK_DIR/DEBIAN/postinst" <<POSTINST
#!/bin/bash
set -e
chmod -R a+rX /opt/plantomics-studio
chmod a+x /opt/plantomics-studio/bin/plantomics-studio 2>/dev/null || true
chmod a+x /opt/plantomics-studio/env/bin/* 2>/dev/null || true

OLD_PATH="${PACKED_DIR}"
NEW_PATH="/opt/plantomics-studio/env"

UNPACK_PY="\$NEW_PATH/bin/conda-unpack"
PYTHON_BIN="\$NEW_PATH/bin/python3"
if [ -f "\$UNPACK_PY" ] && [ -x "\$PYTHON_BIN" ]; then
    echo "正在配置嵌入式环境..."
    "\$PYTHON_BIN" "\$UNPACK_PY" 2>/dev/null || echo "警告:conda-unpack 失败"
fi

if [ "\$OLD_PATH" != "\$NEW_PATH" ]; then
    FILES=\$(grep -rlI "\$OLD_PATH" "\$NEW_PATH" 2>/dev/null || true)
    if [ -n "\$FILES" ]; then
        echo "\$FILES" | xargs sed -i "s|\$OLD_PATH|\$NEW_PATH|g" 2>/dev/null || true
    fi
fi

chmod 755 /opt/plantomics-studio/modules 2>/dev/null || true

update-desktop-database -q || true
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
echo ""
echo "PlantOmics Studio 已安装。"
echo "  启动: plantomics-studio"
echo "  数据目录: ~/.plantomics/"
echo ""
echo "主程序仅含基础功能。要做组学分析,在'模块'页装具体分析模块。"
echo ""
exit 0
POSTINST
chmod 755 "$WORK_DIR/DEBIAN/postinst"

cat > "$WORK_DIR/DEBIAN/postrm" <<'POSTRM'
#!/bin/bash
set -e
case "$1" in
    purge)
        rm -rf /opt/plantomics-studio
        ;;
esac
exit 0
POSTRM
chmod 755 "$WORK_DIR/DEBIAN/postrm"

# 再次确保 DEBIAN 目录权限正确（Tauri 生成的 deb 有时带 setgid）
chmod g-s "$WORK_DIR/DEBIAN" 2>/dev/null || true
chmod 755 "$WORK_DIR/DEBIAN" 2>/dev/null || true

cd "$ROOT"
dpkg-deb --build --root-owner-group -Zxz "$WORK_DIR" "dist/${DEB_NAME}"

DEB_SIZE=$(du -h "dist/${DEB_NAME}" | cut -f1)
DEB_SHA256=$(sha256sum "dist/${DEB_NAME}" | cut -d' ' -f1)
echo "${DEB_SHA256}  ${DEB_NAME}" > "dist/${DEB_NAME}.sha256"

echo ""
echo -e "${GREEN}====== 主程序构建完成 ======${NC}"
cat <<EOF


  deb 包:   dist/${DEB_NAME}
  大小:     ${DEB_SIZE}
  SHA256:   ${DEB_SHA256}
  说明:     不含任何分析模块

安装:
  sudo apt install ./dist/${DEB_NAME}

启动:
  plantomics-studio

EOF
