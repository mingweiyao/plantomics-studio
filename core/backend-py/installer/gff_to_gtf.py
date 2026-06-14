"""GFF → GTF 转换工具

用 gffread(subread / cufflinks 套件)。

调用方需提供 modules_dir(可在 app.state.modules_dir 拿到),
我们会扫这个目录下每个模块的 env/bin/gffread 找一个可用的。
也兜底找系统 PATH 上的 gffread。
"""
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def find_gffread(modules_dir: Optional[Path] = None) -> str:
    """找一个可用的 gffread 命令。返回绝对路径。
    
    搜索顺序:
      1. modules_dir/<id>/env/bin/gffread (传入的 modules_dir)
      2. /opt/plantomics-studio/modules/<id>/env/bin/gffread (兜底)
      3. 系统 PATH
    
    搜索失败抛 RuntimeError,错误信息包含已搜过的路径。
    """
    searched = []
    candidates = []
    
    # 1. 传入的 modules_dir(主程序运行时通过 app.state.modules_dir 给)
    if modules_dir is not None and Path(modules_dir).exists():
        for mod_dir in Path(modules_dir).iterdir():
            if mod_dir.is_dir():
                cand = mod_dir / "env" / "bin" / "gffread"
                searched.append(str(cand))
                if cand.exists() and cand.is_file():
                    candidates.append(cand)
    
    # 2. 标准 deb 安装位置(就算上面没找到也试试,以防 modules_dir 给错了)
    fallback_dir = Path("/opt/plantomics-studio/modules")
    if fallback_dir.exists() and fallback_dir != modules_dir:
        for mod_dir in fallback_dir.iterdir():
            if mod_dir.is_dir():
                cand = mod_dir / "env" / "bin" / "gffread"
                searched.append(str(cand))
                if cand.exists() and cand.is_file():
                    candidates.append(cand)
    
    # 3. 系统 PATH
    sys_gffread = shutil.which("gffread")
    if sys_gffread:
        candidates.append(Path(sys_gffread))
        searched.append(f"PATH: {sys_gffread}")
    else:
        searched.append("PATH (未找到)")
    
    if candidates:
        logger.info(f"使用 gffread: {candidates[0]}")
        return str(candidates[0])
    
    raise RuntimeError(
        "找不到 gffread 命令。已搜索:\n  " + "\n  ".join(searched) +
        "\n请确保至少安装了一个分析模块(它的 conda env 里包含 gffread)。"
    )


def convert(gff_path: Path, gtf_path: Path,
             modules_dir: Optional[Path] = None):
    """把 GFF 文件转换成 GTF。
    
    - gff_path: 输入(GFF / GFF3,可以是 .gz)
    - gtf_path: 输出(.gtf)
    - modules_dir: 模块根目录(主程序传入,用于找 gffread)
    
    抛 RuntimeError 如果失败。
    """
    gff_path = Path(gff_path).resolve()
    gtf_path = Path(gtf_path).resolve()
    if not gff_path.exists():
        raise FileNotFoundError(f"GFF 不存在: {gff_path}")
    
    gtf_path.parent.mkdir(parents=True, exist_ok=True)
    
    gffread = find_gffread(modules_dir=modules_dir)
    logger.info(f"用 {gffread} 转换 {gff_path} -> {gtf_path}")
    
    cmd = [gffread, str(gff_path), "-T", "-o", str(gtf_path)]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("GFF 转换超时(10 分钟)")
    except FileNotFoundError as e:
        raise RuntimeError(f"运行 gffread 失败: {e}")
    
    if result.returncode != 0:
        raise RuntimeError(
            f"gffread 退出码 {result.returncode}:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    
    if not gtf_path.exists() or gtf_path.stat().st_size == 0:
        raise RuntimeError("gffread 完成但产出文件为空")
    
    logger.info(f"✓ GTF 已生成: {gtf_path} ({gtf_path.stat().st_size} 字节)")
