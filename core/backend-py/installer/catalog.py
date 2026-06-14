"""读取主程序自带的 modules.json 清单文件。

文件位置查找顺序:
  1. /opt/plantomics-studio/resources/modules.json(deb 装好后)
  2. <repo>/core/src-tauri/resources/modules.json(开发模式)
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CATALOG_FILENAME = "modules.json"
CATALOG_LOCATIONS = [
    Path("/opt/plantomics-studio/resources") / CATALOG_FILENAME,
    Path(__file__).resolve().parents[3] / "core/src-tauri/resources" / CATALOG_FILENAME,
    Path(__file__).resolve().parents[2] / "src-tauri/resources" / CATALOG_FILENAME,
]


def load_catalog() -> list[dict]:
    """返回模块清单。每个元素至少包含:
        {
          "id": str,
          "name": str,
          "version": str,
          "description": str,
          "deb_url": str,         # 下载地址
          "deb_size_mb": int,     # 文件大小提示
          "deb_sha256": str,      # 校验
          "icon": str,            # lucide-react 图标
        }
    """
    for path in CATALOG_LOCATIONS:
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "modules" in data:
                    return data["modules"]
                if isinstance(data, list):
                    return data
                logger.warning(f"{path} 格式异常,期望是 list 或 {{modules: list}}")
                return []
            except Exception as e:
                logger.exception(f"读 {path} 失败: {e}")
                return []
    
    logger.warning(f"找不到 modules.json,尝试位置: {CATALOG_LOCATIONS}")
    return []
