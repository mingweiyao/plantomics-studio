# 模块开发指南

这份文档面向**模块开发者**。如果你只是想用 PlantOmics Studio 做分析,
请看 [`README.md`](../README.md)。

---

## 一个模块包含什么

```
plantomics-module-<your-id>/
├── module.yaml                  # 必需:模块自报家门
├── conda-deps.yaml              # 必需:模块的 conda 依赖
├── backend-py/                  # 可选:Python 后端
│   └── main.py
├── backend-r/                   # 可选:R 后端
│   └── plumber.R
├── frontend/                    # 必需:前端 bundle
│   ├── module.js                #   编译好的 ESM 模块
│   └── module.css               #   样式
├── scripts/build-deb.sh         # 构建脚本
└── README.md
```

模块**至少**要有 backend-py 或 backend-r 中的一个。

## module.yaml 规范

```yaml
# 模块基本信息
id: omics-rnaseq-bulk             # 唯一 ID,与 deb 包名后缀一致
name: Bulk RNA-seq                # 显示名
version: 1.0.0                    # 模块自身版本
description: 标准 bulk 转录组分析
author: PlantOmics Team
icon: dna                         # lucide-react 图标名
license: GPL-3.0

# 兼容性
core_required: ">=1.0.0,<2.0.0"   # 主程序版本兼容范围

# 模块在主程序里要扩展的能力
extends:
  # 项目类型 - 用户在新建项目时能选这些
  project_types:
    - id: bulk_rnaseq
      name: Bulk RNA-seq 转录组
      description: 从 fastq 到差异表达 + 富集
      
  # 参考资源类型
  reference_types:
    - id: genome_annotation
      name: 基因组+注释
      required_files:
        - id: fasta
          label: 基因组 FASTA
          extensions: [fa, fasta, fna, fa.gz, fasta.gz]
        - id: gtf
          label: 注释 GTF/GFF
          extensions: [gtf, gff, gff3, gtf.gz, gff.gz]
  
  # 项目详情页要显示的菜单项
  menu_items:
    - id: deg
      label: 差异表达
      icon: bar-chart
      route: /deg               # 模块前端路由
    - id: enrichment
      label: 富集分析
      icon: flame
      route: /enrichment

# 后端进程配置
runtime:
  python:                         # 可选,如果模块没有 Python 后端可省
    entry: backend-py/main.py
    health_path: /health          # 主程序用这个判断模块就绪
  r:                              # 可选
    entry: backend-r/plumber.R
    health_path: /health

# 模块需要主程序提供的数据(见下文"上下文协议")
context_required:
  - project_meta                  # 项目基本信息
  - references                    # 项目用到的参考资源
```

## 上下文协议(Context Protocol)

模块进程启动时,主程序会通过环境变量告知:

| 环境变量 | 含义 |
|---|---|
| `MODULE_PY_PORT` | Python 后端应该监听的端口 |
| `MODULE_R_PORT` | R 后端应该监听的端口 |
| `MODULE_DATA_DIR` | 模块可写数据目录(`~/.plantomics/modules/<id>/`) |
| `PLANTOMICS_DATA_DIR` | 主程序数据目录(`~/.plantomics/`),只读 |
| `PLANTOMICS_CORE_API` | 主程序 API 地址(`http://127.0.0.1:<port>`) |

模块需要项目数据/参考时,通过 `PLANTOMICS_CORE_API` 调主程序的:
- `GET /projects/<id>` - 获取项目元信息
- `GET /references/<id>` - 获取参考资源元信息

## 数据隔离

模块**只能**读 `PLANTOMICS_DATA_DIR/projects/<project_id>/` 下的内容,
**只能**写 `MODULE_DATA_DIR` 和**指定的项目目录的子目录**。具体:

| 路径 | 模块的权限 |
|---|---|
| `~/.plantomics/projects/<id>/project.json` | 只读(主程序管理) |
| `~/.plantomics/projects/<id>/modules/<module-id>/` | 模块自己的项目子目录,可读写 |
| `~/.plantomics/references/<id>/` | 只读 |
| `~/.plantomics/modules/<module-id>/` | 模块全局工作区,可读写 |

主程序不强制这些隔离(filesystem-level),但模块如果违反这些约定,
卸载/升级时会出问题。

## 前端集成

模块前端必须打包成单个 ESM 文件 `frontend/module.js`,默认导出符合
以下接口:

```typescript
export default {
  // 注册到主程序的菜单项渲染函数
  routes: {
    deg: () => import("./pages/DEG"),         // lazy load
    enrichment: () => import("./pages/Enrichment"),
  },
  
  // 在新建项目向导里追加自定义步骤(可选)
  projectWizardSteps: [...],
  
  // 在项目详情页扩展显示(可选)
  projectDetailExtensions: [...],
}
```

主程序通过动态 `import()` 加载这个文件。模块前端能用主程序提供的 SDK:

```typescript
import { sdk } from "@plantomics/sdk"

// 调主程序 API
const project = await sdk.getProject(projectId)
const refs = await sdk.listReferences()

// 调本模块自己的后端 API
const result = await sdk.module.callPython("/run-deg", { project_id })
const fig = await sdk.module.callR("/plot-volcano", { ... })
```

## 模块如何被发现

主程序内置了 `modules.json` 清单。要让你的模块出现在主程序的"模块"页:
1. fork 主程序仓库(或提 PR)
2. 在 `core/src-tauri/resources/modules.json` 加一条
3. 主程序下个版本发布会自带

如果用户想装清单外的模块,可以用"从本地 .deb 安装"功能。
