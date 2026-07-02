#!/usr/bin/env bash
#
# miRNA 模块 deb 构建脚本
# ========================
# 产物:dist/plantomics-module-mirna_X.X.X_amd64.deb
#
# deb 装到 /opt/plantomics-studio/modules/omics-mirna/
# 主程序启动时会自动扫描这个目录,加载并启动模块进程。
#
# 用法:
#   bash scripts/build-deb.sh                # 完整构建
#   bash scripts/build-deb.sh --skip-env     # 复用现有 env

set -e

MODULE_ID="omics-mirna"
VERSION="1.0.0"
DEB_PACKAGE_NAME="plantomics-module-mirna"
INSTALL_DIR="/opt/plantomics-studio/modules/$MODULE_ID"

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
            sed -n '2,15p' "$0" | sed 's/^# *//'
            exit 0
            ;;
        *) fail "未知参数: $1" ;;
    esac
    shift
done

log "Step 1/4 检查工具"
for cmd in dpkg-deb python3; do
    command -v $cmd >/dev/null || fail "缺工具: $cmd"
done

CONDA_BIN=""
if command -v mamba >/dev/null; then CONDA_BIN="mamba"
elif command -v conda >/dev/null; then CONDA_BIN="conda"
else fail "没装 mamba 或 conda"
fi
log "  使用 $CONDA_BIN"

mkdir -p build dist

ENV_DIR="$ROOT/build/conda-env"

if [[ $SKIP_ENV -eq 1 && -d "$ENV_DIR" ]]; then
    log "Step 2/4 跳过 conda env 创建(--skip-env)"
else
    log "Step 2/4 创建模块 conda env(全量,~10-20 分钟,首次构建慢)"
    rm -rf "$ENV_DIR"

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

    write_condarc() {
        local m="$1"
        {
            echo "channels:"
            echo "  - bioconda"
            echo "  - conda-forge"
            echo "show_channel_urls: true"
            if [[ -n "$m" ]]; then
                echo "default_channels:"
                echo "  - $m/pkgs/main"
                echo "  - $m/pkgs/r"
                echo "custom_channels:"
                echo "  conda-forge: $m/cloud"
                echo "  bioconda: $m/cloud"
            fi
            echo "remote_connect_timeout_secs: 30.0"
            echo "remote_read_timeout_secs: 120.0"
            echo "remote_max_retries: 5"
            echo "remote_backoff_factor: 2"
        } > "$ROOT/build/.condarc"
        export CONDARC="$ROOT/build/.condarc"
    }

    log "  探测/试用镜像(失败自动换下一个)..."
    CONDA_RC=1
    for cand in "${MIRRORS[@]}" "OFFICIAL"; do
        if [[ "$cand" == "OFFICIAL" ]]; then
            warn "  国内镜像都没建成,改用官方源(慢但稳)"
            write_condarc ""
        else
            if ! curl -sI -m 8 "$cand/cloud/bioconda/noarch/repodata.json" 2>/dev/null \
                 | grep -qiE "HTTP/[12].* 200|200 OK"; then
                log "  x $cand 探测不通,跳过"
                continue
            fi
            log "  v 试用镜像: $cand"
            write_condarc "$cand"
        fi

        set +e
        $CONDA_BIN env create -f "$ROOT/conda-deps/env.yaml" -p "$ENV_DIR" 2>&1 | \
            tee "$ROOT/build/conda-create.log"
        CONDA_RC=${PIPESTATUS[0]}
        set -e
        [[ $CONDA_RC -eq 0 ]] && break
        warn "  用 $cand 创建失败(多半太慢/超时),换下一个镜像重试..."
        rm -rf "$ENV_DIR"
    done

    [[ $CONDA_RC -eq 0 ]] || fail "conda env 创建失败,见 build/conda-create.log"

    for tool in python3 R Rscript bowtie samtools fastp fastqc miRDeep2.pl; do
        if ! [[ -x "$ENV_DIR/bin/$tool" ]]; then
            warn "$tool 没有装上,继续构建但运行时可能有问题"
        fi
    done
    log "  v env 创建完成"
fi

# -- R 包深度验证 --
log "  R 包深度验证(避免运行时缺包)"
: "${PLANTOMICS_CRAN_REPO:=https://mirrors.ustc.edu.cn/CRAN}"
: "${PLANTOMICS_BIOC_MIRROR:=https://mirrors.ustc.edu.cn/bioc}"
export PLANTOMICS_CRAN_REPO PLANTOMICS_BIOC_MIRROR

"$ENV_DIR/bin/Rscript" - <<'RVERIFY' || fail "R 包验证失败"
cran <- Sys.getenv("PLANTOMICS_CRAN_REPO", "https://mirrors.ustc.edu.cn/CRAN")
bioc_base <- Sys.getenv("PLANTOMICS_BIOC_MIRROR", "https://mirrors.ustc.edu.cn/bioc")
options(repos = c(CRAN = cran))

needed <- c(
  "plumber",
  "jsonlite",
  "DESeq2",
  "pheatmap",
  "ggplot2",
  "plyr",
  "reshape2",
  "gplots"
)

missing <- character(0)
for (pkg in needed) {
  ok <- tryCatch({
    suppressPackageStartupMessages(suppressWarnings(library(pkg, character.only = TRUE)))
    TRUE
  }, error = function(e) FALSE)
  cat(sprintf(if (ok) "  [OK] %s\n" else "  [MISSING] %s\n", pkg))
  if (!ok) missing <- c(missing, pkg)
}

if (length(missing) > 0) {
  biocver <- tryCatch(sub("^(\\d+\\.\\d+).*", "\\1",
                          as.character(packageVersion("BiocVersion"))),
                      error = function(e) NA_character_)
  if (is.na(biocver)) {
    rv <- getRversion()
    biocver <- if (rv >= "4.4") "3.20" else if (rv >= "4.3") "3.18" else "3.16"
  }
  bioc_repos <- c(
    BioCsoft = paste0(bioc_base, "/packages/", biocver, "/bioc"),
    BioCann  = paste0(bioc_base, "/packages/", biocver, "/data/annotation"),
    BioCexp  = paste0(bioc_base, "/packages/", biocver, "/data/experiment"),
    CRAN     = cran
  )
  cat(sprintf("\n!! 缺 %d 个包,从镜像装(Bioc %s @ %s)\n",
              length(missing), biocver, bioc_base))
  install.packages(missing, repos = bioc_repos)

  still <- missing
  for (pkg in missing) {
    ok <- tryCatch({
      suppressPackageStartupMessages(suppressWarnings(library(pkg, character.only = TRUE)))
      TRUE
    }, error = function(e) FALSE)
    cat(sprintf(if (ok) "  [OK] %s\n" else "  [MISSING] %s\n", pkg))
    if (ok) still <- setdiff(still, pkg)
  }
  if (length(still) > 0) {
    cat(sprintf("!! 仍有 %d 个包装不上: %s\n", length(still), paste(still, collapse = ", ")))
    quit(status = 1)
  }
}

cat("\nv 所有 R 包验证通过\n")
RVERIFY

log "Step 3/4 conda-pack 打包"
PACK_TAR="$ROOT/build/conda-env-packed.tar.gz"
rm -f "$PACK_TAR"
"$ENV_DIR/bin/conda-pack" -p "$ENV_DIR" -o "$PACK_TAR" \
    --ignore-missing-files --quiet

PACKED_DIR="$ROOT/build/env"
rm -rf "$PACKED_DIR"
mkdir -p "$PACKED_DIR"
tar -xzf "$PACK_TAR" -C "$PACKED_DIR"
log "  v env 已打包到 $PACKED_DIR"

log "Step 4/4 打包 deb"
DEB_NAME="${DEB_PACKAGE_NAME}_${VERSION}_amd64.deb"
WORK_DIR="$ROOT/build/deb-work"
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR/DEBIAN"
mkdir -p "$WORK_DIR$INSTALL_DIR"

# 拷贝模块内容
cp "$ROOT/module.yaml" "$WORK_DIR$INSTALL_DIR/"
cp -r "$ROOT/backend-py" "$WORK_DIR$INSTALL_DIR/"
cp -r "$ROOT/backend-r" "$WORK_DIR$INSTALL_DIR/"
cp -r "$ROOT/frontend" "$WORK_DIR$INSTALL_DIR/"
cp "$ROOT/README.md" "$WORK_DIR$INSTALL_DIR/" 2>/dev/null || true

# 嵌入 conda env
cp -a "$PACKED_DIR" "$WORK_DIR$INSTALL_DIR/env"

# DEBIAN/control
INSTALLED_SIZE_KB=$(du -sk "$WORK_DIR/opt" | cut -f1)
cat > "$WORK_DIR/DEBIAN/control" <<CONTROL
Package: $DEB_PACKAGE_NAME
Version: $VERSION
Section: science
Priority: optional
Architecture: amd64
Installed-Size: $INSTALLED_SIZE_KB
Depends: plant-omics-studio (>= 1.0.0)
Maintainer: PlantOmics Team
Description: miRNA analysis module for PlantOmics Studio
 Provides miRNA-seq analysis: SRA download, quality control (fastp/FastQC),
 bowtie alignment, miRDeep2 prediction, quantification, DESeq2 differential
 expression, miRanda target prediction, GO/KEGG enrichment, clustering, and
 miRNA-mRNA co-expression network analysis.
 Requires plant-omics-studio core (>= 1.0.0).
CONTROL

# DEBIAN/postinst
cat > "$WORK_DIR/DEBIAN/postinst" <<POSTINST
#!/bin/bash
set -e

MODULE_DIR="$INSTALL_DIR"
chmod -R a+rX "\$MODULE_DIR"
chmod a+x "\$MODULE_DIR/env/bin/"* 2>/dev/null || true

OLD_PATH="${PACKED_DIR}"
NEW_PATH="\$MODULE_DIR/env"

UNPACK_PY="\$NEW_PATH/bin/conda-unpack"
PYTHON_BIN="\$NEW_PATH/bin/python3"
if [ -f "\$UNPACK_PY" ] && [ -x "\$PYTHON_BIN" ]; then
    echo "[$DEB_PACKAGE_NAME] 配置嵌入式环境..."
    "\$PYTHON_BIN" "\$UNPACK_PY" 2>/dev/null || echo "  警告:conda-unpack 失败"
fi

if [ "\$OLD_PATH" != "\$NEW_PATH" ]; then
    FILES=\$(grep -rlI "\$OLD_PATH" "\$NEW_PATH" 2>/dev/null || true)
    if [ -n "\$FILES" ]; then
        echo "\$FILES" | xargs sed -i "s|\$OLD_PATH|\$NEW_PATH|g" 2>/dev/null || true
    fi
fi

echo "[$DEB_PACKAGE_NAME] 模块已安装到 \$MODULE_DIR"
echo "提示:重启 PlantOmics Studio 即可看到新模块。"
exit 0
POSTINST
chmod 755 "$WORK_DIR/DEBIAN/postinst"

# DEBIAN/postrm
cat > "$WORK_DIR/DEBIAN/postrm" <<POSTRM
#!/bin/bash
set -e
case "\$1" in
    purge|remove)
        rm -rf "$INSTALL_DIR"
        ;;
esac
exit 0
POSTRM
chmod 755 "$WORK_DIR/DEBIAN/postrm"

# 打包
cd "$ROOT"
dpkg-deb --build --root-owner-group -Zxz "$WORK_DIR" "dist/${DEB_NAME}"

DEB_SIZE=$(du -h "dist/${DEB_NAME}" | cut -f1)
DEB_SHA256=$(sha256sum "dist/${DEB_NAME}" | cut -d' ' -f1)
echo "${DEB_SHA256}  ${DEB_NAME}" > "dist/${DEB_NAME}.sha256"
INSTALLED_SIZE_HUMAN=$(du -sh "$WORK_DIR$INSTALL_DIR" | cut -f1)

echo ""
echo -e "${GREEN}====== 模块构建完成 ======${NC}"
cat <<EOF

  模块 ID:    $MODULE_ID v$VERSION
  deb 包:     dist/${DEB_NAME}
  deb 大小:   ${DEB_SIZE}
  装后大小:  ${INSTALLED_SIZE_HUMAN}
  安装位置:   $INSTALL_DIR

安装(需要先装好主程序):
  sudo apt install ./dist/${DEB_NAME}

或在主程序"模块"页"从本地 .deb 安装"。

EOF
