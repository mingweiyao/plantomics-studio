"""分析注册表 —— 把"分析"做成可插拔的文件夹,而不是写死在代码里。

设计:每个"分析"是一个**文件夹**,放在扫描目录下:

    <analyses_dir>/<analysis_id>/
        analysis.R       # 自描述头部 + run() 函数(必需)
        preview.png      # 输出效果预览图(可选,前端卡片上展示)
        examples/        # 示例输入文件(可选,引导用户准备数据)
        README.md        # 说明(可选)

analysis.R 头部用注释块声明元数据(YAML),例如:

    #' @plantomics-analysis
    #' id: volcano
    #' label: 火山图
    #' category: plot
    #' accepts: deg_table
    #' params:
    #'   - { key: fc_cutoff, label: "log2FC 阈值", type: number, default: 1 }

模块**不内置任何分析代码**:本注册表扫描用户目录
(~/.plantomics/modules/omics-analysis/analyses/),用户丢进去的文件夹下次启动就在。
模块自带的 examples 在首次启动时**播种**到用户目录,用户可随意改/删。
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# 用户可扩展的分析目录(持久,重装/重启都在)
USER_ANALYSES_DIR = (
    Path(os.path.expanduser("~")) / ".plantomics" / "modules" / "omics-analysis" / "analyses"
)

# 支持的语义化输入类型(跨组学复用的关键)
DATASET_TYPES = {
    "count_matrix": "原始计数矩阵(基因 × 样本)",
    "normalized_matrix": "标准化矩阵(TPM/FPKM/CPM)",
    "deg_table": "差异分析结果表",
    "sample_design": "样本分组表",
    "enrichment_result": "富集分析结果",
    "gene_list": "基因列表",
}

# 参数控件类型(前端据此自动生成表单)
PARAM_TYPES = {"number", "int", "bool", "select", "text", "column"}

HEADER_MARK = "@plantomics-analysis"


def seed_examples_if_empty(examples_src: Path) -> None:
    """首次启动:若用户分析目录为空,把模块自带的示例分析播种过去。

    播种的是**普通分析文件夹**,用户能随便改/删 —— 模块本身不含写死的分析逻辑。
    """
    try:
        USER_ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
        has_any = any(p.is_dir() for p in USER_ANALYSES_DIR.iterdir())
        if has_any or not examples_src.is_dir():
            return
        for sub in sorted(examples_src.iterdir()):
            if sub.is_dir() and (sub / "analysis.R").exists():
                shutil.copytree(sub, USER_ANALYSES_DIR / sub.name, dirs_exist_ok=True)
                logger.info("播种示例分析: %s", sub.name)
    except Exception as e:
        logger.warning("播种示例分析失败(忽略): %s", e)


def _parse_header(analysis_r: Path) -> Optional[dict]:
    """从 analysis.R 头部抽出 `#' ...` 注释里的 YAML 元数据块。"""
    lines = analysis_r.read_text(encoding="utf-8", errors="ignore").splitlines()
    started = False
    yaml_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if not started:
            if s.startswith("#'") and HEADER_MARK in s:
                started = True
            continue
        # 元数据块 = 从 @plantomics-analysis 起连续的 #' 行,
        # 到第一行"空 #' 行"或非 #' 行为止。空 #' 行之后通常是说明文字,不算元数据。
        if not s.startswith("#'"):
            break
        after = s[2:]
        if after.startswith(" "):
            after = after[1:]          # 去掉 #' 后惯例的一个空格,保留相对缩进
        if after.strip() == "":
            break                       # 空 #' 行 → 元数据块结束
        yaml_lines.append(after)
    if not yaml_lines:
        return None
    try:
        meta = yaml.safe_load("\n".join(yaml_lines))
        return meta if isinstance(meta, dict) else None
    except Exception as e:
        logger.warning("解析 %s 头部 YAML 失败: %s", analysis_r, e)
        return None


def _normalize_params(raw: Any) -> list[dict]:
    """把 params 规整成统一结构,带默认值,过滤非法类型。"""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for p in raw:
        if not isinstance(p, dict) or "key" not in p:
            continue
        ptype = str(p.get("type", "text"))
        if ptype not in PARAM_TYPES:
            ptype = "text"
        item = {
            "key": str(p["key"]),
            "label": str(p.get("label", p["key"])),
            "type": ptype,
            "default": p.get("default"),
            "help": p.get("help"),
        }
        if ptype == "select":
            opts = p.get("options") or []
            item["options"] = [str(o) for o in opts] if isinstance(opts, list) else []
        out.append(item)
    return out


def _build_manifest(folder: Path, source: str) -> Optional[dict]:
    """把一个分析文件夹解析成清单(给前端用)。"""
    analysis_r = folder / "analysis.R"
    if not analysis_r.exists():
        return None
    meta = _parse_header(analysis_r)
    if not meta or "id" not in meta:
        logger.warning("跳过 %s:缺 @plantomics-analysis 头部或 id", folder)
        return None

    accepts = meta.get("accepts")
    accepts_list = [accepts] if isinstance(accepts, str) else (accepts or [])
    outputs = meta.get("outputs") or []

    examples_dir = folder / "examples"
    examples = (
        sorted(p.name for p in examples_dir.iterdir() if p.is_file())
        if examples_dir.is_dir()
        else []
    )
    return {
        "id": str(meta["id"]),
        "label": str(meta.get("label", meta["id"])),
        "category": str(meta.get("category", "other")),
        "description": meta.get("description"),
        "accepts": [str(a) for a in accepts_list],
        "params": _normalize_params(meta.get("params")),
        "outputs": [str(o) for o in outputs] if isinstance(outputs, list) else [],
        "has_preview": (folder / "preview.png").exists(),
        "examples": examples,
        "source": source,             # "user" / "example"
        "folder": str(folder),
    }


class AnalysisRegistry:
    """扫描分析文件夹,提供清单 + 路径解析。"""

    def __init__(self, examples_src: Optional[Path] = None):
        # examples_src = 模块自带的示例分析(用于首次播种)
        if examples_src:
            seed_examples_if_empty(examples_src)
        self._cache: dict[str, dict] = {}
        self.rescan()

    def rescan(self) -> None:
        """重新扫描用户分析目录(新增/删除文件夹后调用)。"""
        found: dict[str, dict] = {}
        if USER_ANALYSES_DIR.is_dir():
            for sub in sorted(USER_ANALYSES_DIR.iterdir()):
                if not sub.is_dir():
                    continue
                m = _build_manifest(sub, source="user")
                if m:
                    found[m["id"]] = m
        self._cache = found
        logger.info("分析注册表:发现 %d 个分析 %s", len(found), list(found))

    def list_manifests(self) -> list[dict]:
        return list(self._cache.values())

    def get(self, analysis_id: str) -> Optional[dict]:
        return self._cache.get(analysis_id)

    def folder_of(self, analysis_id: str) -> Optional[Path]:
        m = self._cache.get(analysis_id)
        return Path(m["folder"]) if m else None
