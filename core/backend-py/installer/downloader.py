"""下载模块 deb 文件。

支持多镜像 fallback:
  1. 优先用 modules.json 里指定的 deb_url
  2. 失败时尝试镜像列表
  3. 边下载边校验 sha256

(批次 1 仅占位,实装放到下次)
"""
import hashlib
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def download_with_progress(url: str, dest: Path,
                                   expected_sha256: Optional[str] = None,
                                   progress_cb=None) -> bool:
    """下载 url 到 dest。progress_cb(downloaded_bytes, total_bytes)。"""
    raise NotImplementedError("下次实装")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
