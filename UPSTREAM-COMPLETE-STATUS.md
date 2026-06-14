# 本轮:新转录本 / 可变剪接 / lncRNA + 功能注释 + 报错排查

## 先解决错误
- 装了 R 4.3.3,对**全部 16 个 analysis.R + 通用执行器**做了语法解析检查 → **全部通过**
  (之前沙箱没 R,这些 R 一直没验过;现在确认语法干净)。
- 删掉一个我误建的重复 runner(new_transcripts_runner.py;真正接线的是 stringtie_runner.py)。

## 关键发现
排查后发现:新转录本、可变剪接、lncRNA 这三步的**后端早就建好且接线完整**了
(JobKind + dispatcher + /submit 端点 + runner + conda 工具都在),功能注释也已是下游分析。
**唯一缺的是前端 UI**——这三步在界面上没有入口,所以等于"用不了"。本轮把 UI 补上了。

## 新转录本 / 可变剪接 / lncRNA(上游模块,本轮补全 UI → 现在可用)
后端(已存在):
- 新转录本:`stringtie_runner.py`(StringTie 组装 + merge + gffcompare 标新转录本)→ JobKind NEW_TRANSCRIPTS
- 可变剪接:`rmats_runner.py`(rMATS 两组比较,SE/A5SS/A3SS/MXE/RI)→ JobKind ALT_SPLICING
- lncRNA:`lncrna_runner.py`(FEELnc 过滤/编码潜能/分类)→ JobKind LNCRNA
- conda 工具齐全:stringtie / gffcompare / gffread / rmats / feelnc

本轮新增(前端):
- rnaseqApi 三个方法:submitNewTranscripts / submitAltSplicing / submitLncrna
- 上游流程新增 3 个节点 + 抽屉表单:
  - 「新转录本」:自动扫比对 BAM + 用参考 GTF → 提交
  - 「可变剪接」:选两组 BAM(对照/处理)+ 读长 + 双端 → 提交
  - 「lncRNA」:候选 GTF(默认指向新转录本的 merged.gtf)+ 参考 GTF + 基因组 FASTA → 提交
- esbuild 语法检查通过。

上游完整流程现在是:
SRA → FastQC → fastp → STAR 比对 → featureCounts → 标准化 → 文库质控(Qualimap)
→ 新转录本 → 可变剪接 → lncRNA

## 功能注释(下游分析,已存在)
analysis 模块里已有 `func_annotation`(给基因列表查 符号/全名/GO/KEGG 通路,org.At.tair.db),
类别 other,accepts gene_list,R 语法检查通过。→ 符合"功能注释放下游"。
analysis 模块现共 **16 个分析**。

## 验证边界
- 已验:py_compile(两模块后端)、R 语法(16 个 analysis.R + 执行器)、注册表解析(16)、
  esbuild(Upstream.tsx / rnaseqApi.ts / AnalysisHome.tsx)。
- 没验(沙箱无 conda/对应工具):StringTie/rMATS/FEELnc/Qualimap 以及各 R 分析的**实跑**。
  这些需要你 build 后实测;有报错贴给我我修。
- stringtie/rmats/lncrna 这三个 runner 是更早的会话建的,我这轮只读过 + 验了语法/接线,
  没逐行实测逻辑;实跑若有问题告诉我。

## 仍欠
卸载按钮「完全不弹」:还需要 Tauri 窗口 Ctrl+Shift+I 的 Console 报错来定位。
