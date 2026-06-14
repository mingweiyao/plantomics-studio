# 改动清单 (vs github main)

GitHub `main` 分支只有 1 个 commit("first sync plantomics-studio data")。本目录在
此基础上有 5 轮迭代改动,以下是相对 `main` 的精确 diff。

## 文件级总览

```
24 个文件改动
   修改:24 个 (insertions: 1630, deletions: 677)
   新增:6 个
   删除:5 个 + 1 个目录
```

## 修改的文件

| 文件 | 主要变更 |
|---|---|
| `core/backend-py/api/projects.py` | (Round 2 等) 项目管理细节 |
| `core/src/components/ProjectLayout.tsx` | (R2) 布局调整 |
| `core/src/lib/rnaseqApi.ts` | **(R5) 大改** - API 类型重构,加 species/enrichment |
| `core/src/routes/Downstream.tsx` | **(R5) 大改** - 富集表单合并、加 SpeciesManagerCard、WGCNA 加物种字段 |
| `core/src/routes/Modules.tsx` | (R2) 模块页 |
| `core/src/routes/Upstream.tsx` | **(R4) 大改** - fastp/STAR/featureCounts 高级参数 + 表格化样本编辑器 |
| `modules-source/omics-rnaseq-bulk/backend-py/jobs/model.py` | (R5) `JobKind` 删 ENRICH_GO/ENRICH_KEGG/GSEA,加 ENRICHMENT/BUILD_SPECIES |
| `modules-source/omics-rnaseq-bulk/backend-py/main.py` | **(R5) 大改** - 删 3 个旧富集 endpoint,加 /submit/enrichment、/submit/build-species、/species CRUD |
| `modules-source/omics-rnaseq-bulk/backend-py/runners/dispatcher.py` | (R5) 注册新 R 脚本 |
| `modules-source/omics-rnaseq-bulk/backend-py/runners/sra_download_runner.py` | (R2) 进度拆段 |
| `modules-source/omics-rnaseq-bulk/backend-r/R/runner_base.R` | (R1) `null="null"` 修 React #31; (R5) 加全局 `%||%` |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_plot_corr.R` | (R4) 重构:数据准备 + source 模板 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_plot_deg_heatmap.R` | (R4) 同上 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_plot_ma.R` | (R4) 同上 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_plot_pca.R` | (R4) 同上 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_plot_volcano.R` | (R4) 同上 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_wgcna.R` | (R5) 加模块自动富集(可选,需要 species_id) |
| `modules-source/omics-rnaseq-bulk/scripts/build-deb.sh` | (R5) R 包验证从 warn 改 fail;镜像走环境变量可覆盖 |
| `scripts/build-deb.sh`, `scripts/clean-everything.sh`, `scripts/uninstall-all.sh` | 权限位修正(+x) |

## 新增的文件

| 文件 | 说明 |
|---|---|
| `README.md` | (本目录顶层)使用说明 + 改动清单索引 |
| `build-and-install.sh` | 一键构建+装+健康检查(本目录顶层,不在 GitHub) |
| `modules-source/omics-rnaseq-bulk/backend-py/runners/pipeline_downstream_runner.py` | (R4) 下游 pipeline 编排器 |
| `modules-source/omics-rnaseq-bulk/backend-r/R/species_lib.R` | (R5) 物种数据库工具 |
| `modules-source/omics-rnaseq-bulk/backend-r/R/enrichment_lib.R` | (R5) 统一富集核心 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/build_species.R` | (R5) 物种构建脚本 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_enrich.R` | (R5) 统一富集入口 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/templates/` | (R4) 图模板目录 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/templates/README.md` | (R4) 模板系统使用说明 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/templates/{pca,corr,ma,volcano,deg_heatmap}/classic.R` | (R4) 5 种图的 classic 模板 |

## 删除的文件

| 文件/目录 | 原因 |
|---|---|
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_enrich_go.R` | (R5) 被 `run_enrich.R` 统一 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_enrich_kegg.R` | 同上 |
| `modules-source/omics-rnaseq-bulk/backend-r/scripts/run_gsea.R` | 同上 |
| `modules-source/omics-rnaseq-bulk/backend-py/utils/` 整目录 | (R5) eggNOG 转换功能并入 R 端 build_species.R |
| `templates/{pca,corr,ma,deg_heatmap}/` 字面目录 | (本轮清理) Round 4 时 sh brace 没展开造成的垃圾空目录 |

## API 变化(影响调用方)

### POST 端点

| 旧 | 新 |
|---|---|
| `/submit/enrich-go` | `/submit/enrichment` |
| `/submit/enrich-kegg` | (同上) |
| `/submit/gsea` | (同上) |
| `/utils/eggnog-to-annotation` | `/submit/build-species` (作为 job 跑) |

新 `/submit/enrichment` 参数:
```json
{
  "params": {
    "method": "ora" | "gsea",
    "species_id": "arabidopsis_tair",
    "ontology": "go_BP" | "go_MF" | "go_CC" | "kegg" | "<custom>",
    "gene_list_file": "...",   // ORA 用
    "deg_file": "...",         // GSEA 用
    "pvalue_cutoff": 0.05,
    "show_top": 20
  }
}
```

### 新增 GET/DELETE 端点

```
GET    /species              列出已构建的物种
GET    /species/{id}         单个物种详情
DELETE /species/{id}         删除物种
```

### `JobKind` 枚举变化

```python
# 旧
ENRICH_GO = "enrich_go"
ENRICH_KEGG = "enrich_kegg"
GSEA = "gsea"

# 新
ENRICHMENT = "enrichment"      # 统一
BUILD_SPECIES = "build_species" # 物种构建
```

## R 脚本调用约定

### 老的(已删)
```bash
Rscript run_enrich_go.R   --org_db org.At.tair.db --keytype TAIR --ont BP ...
Rscript run_enrich_kegg.R --organism ath ...
Rscript run_gsea.R        --gsea_type go --org_db ... ...
```

### 新的(统一)
```bash
Rscript run_enrich.R --species_id arabidopsis_tair --ontology go_BP --method ora ...
Rscript run_enrich.R --species_id arabidopsis_tair --ontology kegg  --method gsea ...
Rscript run_enrich.R --species_id my_wheat        --ontology kegg  --method ora ...
```

参数都从 `<job_id>/job.json` 的 `params` 字段读,不直接命令行传。
统一脚本支持任何物种、任何本体论(只要 `<species_dir>/<ontology>.tsv` 存在)。

## 物种目录约定

新增的概念,文档以代码为准。每个物种是个独立目录,内容:

```
~/.plantomics/modules/omics-rnaseq-bulk/species/<species_id>/
├── meta.json     {id, label, source: "orgdb"|"eggnog", gene_count, kegg_organism, ...}
├── go_BP.tsv               TERM2GENE: 两列 (term, gene),tab 分隔,无表头
├── go_BP_names.tsv         TERM2NAME: 两列 (term, name)
├── go_MF.tsv  go_MF_names.tsv
├── go_CC.tsv  go_CC_names.tsv
├── kegg.tsv   kegg_names.tsv
└── gene_names.tsv          可选: gene_id → SYMBOL,让结果表更可读
```

加新本体论(reactome / mapman / 等):

```bash
# 1. 准备一个 reactome.tsv (格式:reactome_path<TAB>gene_id)
# 2. 可选: reactome_names.tsv (格式:reactome_path<TAB>human_name)
# 3. 扔进物种目录
cp my_reactome.tsv ~/.plantomics/modules/omics-rnaseq-bulk/species/arabidopsis_tair/reactome.tsv

# 4. 重启 module 后,前端富集表单的 ontology 下拉里就多了 "reactome"
# 完全不需要改代码
```
