# 下游分析模块 = 可插拔 R 脚本库

## 核心:分析 = 一个文件夹,丢进去就持久
扫描目录:`~/.plantomics/modules/omics-analysis/analyses/`
每个分析是一个文件夹:
    <id>/
      analysis.R     # 头部元数据(#' @plantomics-analysis ...)+ run(inputs, params, out_dir)
      preview.png    # 可选,输出预览图
      examples/      # 可选,示例输入文件
你把一个这样的文件夹丢进扫描目录,**下次打开还在**(就在磁盘上,不随重装/重启消失)。
模块本身不内置分析逻辑,只是个"宿主"。

## 后端(已完成,Python 编译 + 注册表解析测试通过)
- `analysis_registry.py`:扫描用户目录,解析 analysis.R 头部 → 清单(给前端自动生成表单)。
- `run_analysis.R` + `analysis_runner.py`:通用执行器,source 任意 analysis.R 并跑它的 run()。
- API:GET /analyses、POST /run、POST /analyses-json(向导新增,文件走 base64)、
  DELETE /analyses/{id}、GET /analyses/{id}/preview、/analyses/{id}/examples/{file}、/analyses/rescan。
- 语义化输入类型(跨组学复用):count_matrix / normalized_matrix / deg_table /
  sample_design / enrichment_result / gene_list。脚本声明 accepts,任何来源的同类型数据都能喂进来。

## 前端(本轮新增)
- `src/routes/AnalysisHome.tsx`:
  - 分析卡片列表(按类别分组);
  - 点"运行"→ 右侧抽屉,按脚本声明的 accepts + params **自动生成表单**(数字/整数/布尔/下拉/文本)+ 文件选择;
  - **"新增分析"向导**:填 ID/名称/类别 + 勾选需要的输入类型 + 写参数(JSON)+ 贴 R 代码 +
    可选上传预览图/示例文件 → 写进扫描目录,立即出现一张新卡片。
- 路由:项目里点"下游分析" → `/projects/:id/m/omics-analysis/downstream`。
- 走主程序 JSON 代理(core_call),所以向导的文件用浏览器 FileReader 转 base64 再传(不用 multipart)。

## 关于"示例"
模块源码里带了一个火山图示例(backend-r/analyses-examples/volcano),首次启动会**播种**到你的分析目录,
方便你立刻看到一张卡片、理解格式 —— 你可以直接删掉或当模板改。
**如果你要完全空白、连这个示例都不要,告诉我,我把播种关掉。**

## 验证
后端:py_compile 通过 + 注册表成功解析示例(id=volcano, 参数 fc_cutoff/p_cutoff/top_n/use_padj)。
前端:esbuild 语法检查全过;tsc 类型检查我这边跑不了,build 报错就贴给我。
