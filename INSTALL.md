# 安装指南

## 系统要求

- **操作系统**:Ubuntu 22.04 LTS / Ubuntu 24.04 LTS / Debian 12+(amd64)
  - WSL2 也支持(Windows 用户)
  - 其他基于 Debian 的发行版可能能用,但未测试
- **桌面环境**:GNOME / KDE / XFCE / WSLg(没有桌面的服务器装不上)
- **磁盘空间**:
  - 主程序:600 MB
  - 转录组模块:约 4 GB

## 方式一:从 Release 下载(推荐)

去 [Releases 页](../../releases) 下载最新的 deb 文件。

```bash
cd ~/Downloads

# 验证 SHA256(可选但推荐,文件大或网络不稳时)
sha256sum -c plantomics-studio_*.deb.sha256

# 装主程序
sudo apt install ./plantomics-studio_X.X.X_amd64.deb

# 启动
plantomics-studio
```

主程序启动后,在"模块"页 → "从本地 .deb 安装" 装具体的分析模块。

## 方式二:从源码构建

需要装这些工具:

```bash
# Rust(参考 https://rustup.rs)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Node.js(20+)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs

# pnpm
sudo npm install -g pnpm

# conda 或 mamba(任选)
# 推荐 mamba(更快): https://github.com/conda-forge/miniforge

# 系统依赖
sudo apt install -y \
  libwebkit2gtk-4.1-dev \
  libgtk-3-dev \
  libayatana-appindicator3-dev \
  librsvg2-dev \
  pkg-config \
  build-essential
```

然后:

```bash
git clone https://github.com/yourname/plantomics-studio.git
cd plantomics-studio

# 构建主程序(10-20 分钟)
bash scripts/build-deb.sh

# 构建转录组模块(20-40 分钟,因为要装很多 R/bioconductor 包)
cd modules-source/omics-rnaseq-bulk
bash scripts/build-deb.sh
cd ../..

# 装
sudo apt install ./dist/plantomics-studio_*.deb
sudo apt install ./modules-source/omics-rnaseq-bulk/dist/plantomics-module-rnaseq-bulk_*.deb
```

## 启动

装好后,可以:

- **从应用菜单**:按 `Super` 键,搜 "PlantOmics" → 点图标
- **从命令行**:`plantomics-studio`
- **WSL 用户**:从 Windows 开始菜单搜 "PlantOmics"

## 卸载

```bash
sudo apt remove --purge plant-omics-studio
sudo apt remove --purge plantomics-module-rnaseq-bulk

# 删用户数据(项目和参考资源,可选)
rm -rf ~/.plantomics
```

或者用脚本一键清:

```bash
bash scripts/uninstall-all.sh --purge
```

## 常见问题

### 装的时候报错 `libwebkit2gtk-4.1-0 is not available`

你的系统是 Ubuntu 20.04 或更老,**不支持**。需要 22.04+。

### 启动后窗口黑屏 / WebKit 错误

设环境变量绕过 GPU 加速:

```bash
WEBKIT_DISABLE_DMABUF_RENDERER=1 plantomics-studio
```

(注:主程序 v1.0.0+ 已经默认设了这个,如果你的版本还有问题,设 `PLANTOMICS_FORCE_GPU=1` 来恢复测试。)

### 国内安装 conda 包慢

构建脚本会自动探测国内镜像(USTC、清华、北外、西湖)。如果都不通,确保**取消代理**:

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
bash scripts/build-deb.sh
```

### "模块状态:错误"

通常是 R 后端起不来。看日志:

```bash
RUST_LOG=info plantomics-studio
```

错误一般在 `[<module-id>-r] ...` 开头的行里。

## 报问题

[GitHub Issues](../../issues/new/choose)
