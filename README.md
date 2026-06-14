# PlantOmics Studio

植物组学分析桌面应用,Tauri 2 (Rust + React) 主程序 + 可插拔模块架构。

仓库:[github.com/mingweiyao/plantomics-studio](https://github.com/mingweiyao/plantomics-studio)

## 当前版本

基于 GitHub `main` 分支(commit "first sync plantomics-studio data") + 已应用的 5 轮迭代改动。

## 一键构建+安装

新机器上(Ubuntu 22.04+ / Debian 12+):

```bash
tar -xzf plantomics-studio-source.tar.gz
cd plantomics-studio
bash build-and-install.sh                    # 全新构建
```

第二次以后(已有 conda env):

```bash
bash build-and-install.sh --skip-env         # 增量构建,省 30 分钟
```

**遇到 conda env 求解错误**(常见,征兆是日志里有 `Using cache` + `does not exist`):

```bash
bash build-and-install.sh --reset            # 清 cache + 删失败 build,重来
```

`build-and-install.sh` 会做 6 步:
1. **预检** — 工具链 / 代理状态 / 关键源可达性 / 既有 env 健康
2. 构建 core deb (10-20 分钟,首次会建 conda env)
3. 构建 module deb (20-40 分钟,首次会装 R + Bioconductor)
4. **独立验证 module env 里 16 个关键 R 包** — 缺失就硬性失败,不会装上残废 deb
5. 用 `dpkg -i` 安装(WSL 上 `apt install ./xxx.deb` 偶尔报 "Unsupported file",`dpkg` 不挑剔)
6. 验证 `plantomics-studio` 命令和 `/opt/plantomics-studio/modules/omics-rnaseq-bulk/` 都在

## 目录结构

```
plantomics-studio/
├── core/                              主程序
│   ├── backend-py/                    Python core (FastAPI,模块管理 / 消息总线)
│   ├── src/                           前端 React/TS
│   ├── src-tauri/                     Rust shell
│   └── conda-env/base.yaml            主程序 conda env spec
├── modules-source/
│   └── omics-rnaseq-bulk/             转录组分析模块
│       ├── backend-py/                Python (SRA / STAR / fastp / featureCounts / ...)
│       ├── backend-r/                 R (DESeq2 / clusterProfiler / WGCNA / ...)
│       │   ├── R/                     工具库
│       │   │   ├── runner_base.R      参数解析、进度更新、日志
│       │   │   ├── species_lib.R      ★ 物种数据库读写 (Round 5)
│       │   │   └── enrichment_lib.R   ★ 统一富集核心 (Round 5)
│       │   └── scripts/               入口脚本
│       │       ├── run_deg_deseq2.R / run_deg_edger.R
│       │       ├── run_plot_*.R       6 种图,模板分发器 (Round 4)
│       │       ├── templates/         图模板库 (Round 4)
│       │       ├── run_enrich.R       ★ 统一富集 (Round 5)
│       │       ├── build_species.R    ★ 物种构建 (Round 5)
│       │       └── run_wgcna.R        ★ 含模块自动富集 (Round 5)
│       ├── frontend/                  模块自己的前端 (可选)
│       ├── conda-deps/env.yaml        模块 conda env spec
│       └── scripts/build-deb.sh       构建模块 deb
├── scripts/
│   ├── build-deb.sh                   构建主程序 deb
│   ├── clean-everything.sh            清掉所有构建中间产物
│   └── uninstall-all.sh               卸载装好的 deb
├── INSTALL.md                         详细安装文档(对应 GitHub Release 流程)
├── LICENSE                            MIT
└── build-and-install.sh               ★ 一键构建+装 + 健康检查
```

## 已应用的迭代改动(Round 1-5)

详细技术决策见 `docs/CHANGES.md`,这里只列影响构建/部署的关键面:

### Round 1-2(用户体验)
- 修 React error #31:R `NULL` 序列化用 `null="null"` 而非 `null="list"`
- SRA 进度拆 download / extract 两段,GFF→GTF 命名修
- 样本编辑器表格化(Array<{id,sample,group}>)+ 从文件导入

### Round 3(自定义物种富集 — 后被 Round 5 取代)
- 自定义注释(eggNOG)模式做 GO/KEGG 富集
- (旧的 `eggnog_to_annotation.py` 已删,功能并入 Round 5 的 `build_species.R`)

### Round 4(高级功能)
- 上游一键 fastp/STAR/featureCounts 高级参数
- **图模板系统**:5 种图(pca/corr/volcano/ma/deg_heatmap)拆分发器 + `templates/<TYPE>/classic.R`
- 下游一键 pipeline runner:DESeq2 → 各种图 → 富集

### Round 5(本轮 — 物种数据库统一架构)★ 重点

**核心理念**:把 GO 富集 / KEGG 富集 / GSEA 三个独立功能合成
**一份代码 × 任意物种 × 任意本体论 × ORA/GSEA**。

**新增**:
- `backend-r/R/species_lib.R` — 物种目录读写(`list_species`/`load_term2gene`/...)
- `backend-r/R/enrichment_lib.R` — `do_enrichment(method, input, species_id, ontology, ...)`
- `backend-r/scripts/run_enrich.R` — 单一富集入口
- `backend-r/scripts/build_species.R` — 物种构建,两种来源 (orgdb / eggnog)
- 前端 `EnrichmentForm` 组件(替代 3 个旧表单)
- 前端 `SpeciesManagerCard` + `SpeciesBuilder` 组件
- WGCNA 跑完支持模块自动富集(给 `species_id` 即可)

**删除**:
- `backend-r/scripts/run_enrich_go.R`、`run_enrich_kegg.R`、`run_gsea.R`
- `backend-py/utils/`(eggNOG 转换 Python 实现)
- 前端 `EnrichForm` / `GseaForm` / `EggnogConverterCard`
- API `/submit/enrich-go` / `/submit/enrich-kegg` / `/submit/gsea` / `/utils/eggnog-to-annotation`

**API 变化**:
- 新增 `/submit/enrichment` (统一)、`/submit/build-species`
- 新增 `/species` (GET 列表) / `/species/{id}` (GET/DELETE)
- `JobKind`: `ENRICH_GO`/`ENRICH_KEGG`/`GSEA` → `ENRICHMENT`,加 `BUILD_SPECIES`

**物种数据库布局** (`~/.plantomics/modules/omics-rnaseq-bulk/species/<id>/`):
```
meta.json
go_BP.tsv  go_MF.tsv  go_CC.tsv          ← TERM2GENE
go_BP_names.tsv  go_MF_names.tsv  go_CC_names.tsv  ← TERM2NAME
kegg.tsv  kegg_names.tsv
gene_names.tsv                            ← 可选,gene_id → SYMBOL
```

加新本体论(reactome / mapman / 自定义)= 扔个 TSV 进物种目录,前端 ontology 下拉自动多一项,**零代码改动**。

### 构建脚本修复
- `modules-source/omics-rnaseq-bulk/scripts/build-deb.sh` 里 R 包验证从 `|| warn` 改成 `|| fail`
  (此前 conda env 残废也只警告,deb 照打,装上去 R 跑不了)
- CRAN/BioC 镜像默认走主站(`cloud.r-project.org` / `bioconductor.org`),
  通过环境变量 `PLANTOMICS_CRAN_REPO` / `PLANTOMICS_BIOC_MIRROR` 覆盖
- `scripts/reset-build.sh`(新)— 清 mamba/conda cache + 删失败 build 残留
- `build-and-install.sh` 加 `--reset` 选项,内部调 reset-build.sh
- conda env 创建失败的错误信息加详细诊断指引(指向 `--reset` 等)

## 排错

### conda env 创建失败:`Could not solve for environment specs`

征兆 — 日志类似:
```
conda-forge/linux-64                                        Using cache
bioconda/linux-64                                           Using cache
error    libmamba Could not solve for environment specs
    └─ gffread =* * does not exist (perhaps a typo or a missing channel).
```

`Using cache` 是关键线索 — mamba 用了上次失败留下的不完整 repodata,新选的 channel
没生效。**99% 是 cache 污染**(实测验证过)。

**修复一步搞定**:
```bash
bash build-and-install.sh --reset
```

`--reset` 会先跑 `scripts/reset-build.sh`(清 mamba/conda cache + 删失败的
build 残留),然后正常构建。

如果不放心,先单独验证 mamba 真能找到 gffread:
```bash
mamba search 'bioconda::gffread' 2>&1 | tail -5
# 期望:有 0.12.x 等结果。没结果就是 channel 配置坏了,见下面"channel 配置错"
```

### channel 配置错(mamba search 也找不到包)

```bash
conda config --remove-key channels
conda config --add channels conda-forge
conda config --add channels bioconda
conda config --set channel_priority strict
bash build-and-install.sh --reset
```

### "Unsupported file" 在 apt install 时

WSL 上偶发。`build-and-install.sh` 默认用 `dpkg -i` 已绕过。
手动也用:
```bash
sudo dpkg -i ./dist/plantomics-studio_1.0.0_amd64.deb
sudo apt-get install -f -y
```

### conda env 装出来缺包(老版会出此问题,新版会硬性失败)

新版的 `build-deb.sh` 在 R 包验证后会硬性 fail,不会让残废 deb 出来。
如果还是命中此问题:
```bash
bash build-and-install.sh --reset
```

### cargo 走 tuna 镜像超时

`~/.cargo/config.toml` 改成 rsproxy(中科大,稳):
```toml
[source.crates-io]
replace-with = 'rsproxy-sparse'
[source.rsproxy-sparse]
registry = "sparse+https://rsproxy.cn/index/"
[net]
git-fetch-with-cli = true
```

或干脆删 `~/.cargo/config.toml` 走官方源。

### 启动黑屏 / WebKit 错误

```bash
WEBKIT_DISABLE_DMABUF_RENDERER=1 plantomics-studio
```

## 卸载

```bash
bash scripts/uninstall-all.sh --purge
```

## 文档

- `INSTALL.md` — 详细安装(含 GitHub Release 流程)
- `docs/ARCHITECTURE.md` — 整体架构
- `docs/MODULE_DEVELOPMENT.md` — 怎么开发新模块
- `docs/RELEASE.md` — 怎么发版
- `modules-source/omics-rnaseq-bulk/backend-r/scripts/templates/README.md` — 图模板系统怎么扩展
