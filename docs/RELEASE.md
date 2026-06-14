# 发版流程

## 主程序发版

```bash
# 1. 更新版本号
# - core/src-tauri/Cargo.toml
# - core/src-tauri/tauri.conf.json
# - core/package.json
# - scripts/build-deb.sh (VERSION=)

# 2. 提交 + 打 tag
git add -A
git commit -m "chore: bump core to v1.0.1"
git tag v1.0.1
git push && git push --tags

# 3. GitHub Actions 自动构建并发到 Release
# 大约 15 分钟后可以在 Releases 页看到 deb
```

## 模块发版

每个模块独立发版,tag 用 `module-<id>-v<version>` 格式:

```bash
# 1. 更新模块版本号
# - modules-source/omics-rnaseq-bulk/module.yaml (version:)
# - modules-source/omics-rnaseq-bulk/scripts/build-deb.sh (VERSION=)

# 2. 提交 + 打 tag
git tag module-rnaseq-bulk-v1.0.1
git push --tags

# 3. GitHub Actions 自动构建并发到 Release
```

## 手动构建上传(没用 GH Actions 时)

```bash
# 主程序
bash scripts/build-deb.sh
# 产物在 dist/

# 模块
cd modules-source/omics-rnaseq-bulk
bash scripts/build-deb.sh
cd ../..

# 一起上传(需要装 gh CLI: https://cli.github.com)
gh release create v1.0.0 \
  dist/plantomics-studio_*.deb \
  dist/plantomics-studio_*.deb.sha256 \
  modules-source/omics-rnaseq-bulk/dist/plantomics-module-*.deb \
  modules-source/omics-rnaseq-bulk/dist/plantomics-module-*.deb.sha256 \
  --generate-notes
```

## 版本号约定

主程序遵循 [Semantic Versioning](https://semver.org/):
- `1.0.0` → `1.0.1`:bug 修复
- `1.0.0` → `1.1.0`:新功能,向后兼容
- `1.0.0` → `2.0.0`:破坏性改动(模块协议变化等)

模块的 `core_required` 字段声明了它兼容的主程序范围。例如:

```yaml
core_required: ">=1.0.0,<2.0.0"   # 兼容 1.x 全系列
```
