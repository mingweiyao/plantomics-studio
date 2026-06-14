# PlantOmics Studio 架构

## 设计原则

**主程序对具体组学概念零假设**。代码里不会出现 `condition` / `fastq` /
`DEG` / `enrichment` 等字眼。所有这些由模块自己定义。

**模块完全独立**。模块自带 conda env,自带 R 包,自带前端 bundle,
自带 backend 代码。卸载模块就是删它自己的目录,不影响主程序也不影响其他模块。

## 进程拓扑

```
┌──────────────────────────────────────────────────────┐
│ Tauri 主进程 (Rust)                                  │
│   - WebView 渲染前端                                  │
│   - 管理子进程生命周期                                │
└──────────────────────────────────────────────────────┘
            ↓ spawn
┌──────────────────────────────────────────────────────┐
│ 主程序后端 (Python / FastAPI)                         │
│ - 监听 127.0.0.1:<py_port>                           │
│ - 管理项目、参考、模块装/卸                           │
│ - 把 /modules/<id>/* 路由转发给模块进程              │
└──────────────────────────────────────────────────────┘
            ↓ spawn (每装一个模块多一组进程)
┌──────────────────────────────────────────────────────┐
│ 模块 A 后端 (Python / FastAPI)                       │
│ - 监听 127.0.0.1:<module_a_py_port>                  │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│ 模块 A 后端 (R / Plumber)  - 可选                    │
│ - 监听 127.0.0.1:<module_a_r_port>                   │
└──────────────────────────────────────────────────────┘
```

## 文件系统布局

```
/opt/plantomics-studio/                          # 主程序 deb 装这里
├── bin/plantomics-studio                        # Tauri 二进制
├── env/                                         # 主程序 conda env
│   ├── bin/python3
│   └── lib/python3.11/site-packages/            # FastAPI 等
├── backend-py/                                  # 主程序后端代码
└── modules/                                     # 模块装这里
    ├── omics-rnaseq-bulk/                       # 模块 A 的 deb 装这里
    │   ├── module.yaml                          # 模块自报家门
    │   ├── env/                                 # 模块自带的 conda env
    │   ├── backend-py/
    │   ├── backend-r/                           # 可选
    │   └── frontend/                            # 模块前端 bundle (.js + .css)
    └── omics-proteomics/
        └── ...

~/.plantomics/                                   # 用户数据
├── projects/<uuid>/...
├── references/<uuid>/...
└── runtime.json                                 # 当前运行端口等
```

## 模块发现与启动流程

1. **启动时**:主程序扫描 `/opt/plantomics-studio/modules/*/module.yaml`
2. **加载**:对每个模块:
   - 检查 `core_required` 版本兼容性,不兼容的不加载
   - 给模块分配端口(从 8011 起)
   - 启动模块的后端进程(用模块**自己**的 env)
3. **就绪检查**:主程序对每个模块的 `/health` 端点轮询,30 秒内必须 200 OK
4. **路由注册**:主程序内部建立 `module_id → port` 表
5. **前端加载**:前端发现已就绪的模块,动态加载它们的 frontend bundle

## API 转发协议

主程序的 `/modules/<module_id>/<rest>` 路径会:
- 查询路由表,找到对应模块的 port
- 透明转发 HTTP 请求到 `127.0.0.1:<port>/<rest>`
- 返回模块的响应

模块**完全不知道**自己被代理,它只是个独立的 HTTP 服务。

## 模块协议

详见 [`MODULE_DEVELOPMENT.md`](MODULE_DEVELOPMENT.md)。

## 主程序数据模型

主程序后端只有 3 个核心实体:

### Project(项目)
```python
{
  "id": "uuid",
  "name": "拟南芥干旱实验",
  "description": "...",
  "created_at": "...",
  "updated_at": "...",
  "modules_used": ["omics-rnaseq-bulk"],   # 该项目用了哪些模块
  "references_used": ["uuid-of-tair10"],    # 该项目用了哪些参考资源
  "module_data": {                          # 模块自定义的项目数据,主程序不解释
    "omics-rnaseq-bulk": { /* 模块 A 自己塞的数据 */ },
  }
}
```

### Reference(参考资源)
```python
{
  "id": "uuid",
  "name": "TAIR10",
  "species": "拟南芥",
  "type": "genome_annotation",              # 由模块声明的类型
  "files": { "fasta": "...", "gtf": "..." },# 字段也由模块声明
  "created_at": "..."
}
```

### Module(已装模块,运行时)
```python
{
  "id": "omics-rnaseq-bulk",
  "version": "1.0.0",
  "manifest": { /* 来自 module.yaml */ },
  "py_port": 8011,
  "r_port": 8012,
  "status": "ready",                        # loading | ready | error | disabled
  "error": null
}
```

## 主程序对模块零信任

主程序**不**信任模块的稳定性。具体:
- 模块崩溃不影响主程序
- 模块的 HTTP 端口异常时,主程序记录错误并降级显示
- 模块的 manifest 字段缺失或非法,模块不加载,主程序记录原因

这跟 VS Code 处理插件失败的方式一样。
