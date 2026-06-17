"""项目管理 - 重新设计版

项目 = 工作目录 + 一套参考资源 + 历次任务参数。

主程序在 ~/.plantomics/projects/<uuid>/project.json 存"项目元数据"(指针 + 参数),
真实数据(fastq/bam/counts 等)放在用户指定的"工作目录"(workdir)下。

数据模型:
{
  "id": "uuid",
  "name": "拟南芥干旱实验",
  "description": "",
  "workdir": "/home/ymw/projects/拟南芥干旱",     # 用户指定的工作目录(必填)
  "reference_fasta": "/path/to/genome.fa",        # 项目用的基因组 FASTA
  "reference_gtf": "/path/to/genome.gtf",         # 项目用的 GTF(GFF 已自动转)
  "modules_used": ["omics-rnaseq-bulk"],
  "module_data": {                                # 模块自己塞的数据
    "omics-rnaseq-bulk": {...}
  },
  "upstream_params": {                            # 每个 step 最近一次的参数,UI 用来回填
    "fastp": {...},
    "star_align": {...}
  },
  "created_at": "...",
  "updated_at": "..."
}

工作目录约定子目录(模块 / 任务在这里读写):
  raw/         (SRA 解压 / 原始 fastq)
  qc/          (FastQC 报告)
  trimmed/     (fastp 输出)
  star_index/  (索引)
  aligned/     (BAM)
  counts/      (featureCounts / normalize)
  downstream/  (差异 / 富集 / WGCNA)
  reference/   (项目级参考文件,如转换后的 GTF)
  logs/

删除项目:只删 ~/.plantomics/projects/<uuid>/(元数据),工作目录留给用户。
"""
import json
import logging
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from installer import gff_to_gtf

logger = logging.getLogger(__name__)
router = APIRouter()


# 工作目录的标准子文件夹
WORKDIR_SUBDIRS = [
    "00_raw", "01_qc", "02_trimmed", "03_star_index",
    "04_aligned", "05_counts", "06_normalized",
    "07_library_qc", "08_new_transcripts", "09_alt_splicing", "10_lncrna",
    "downstream", "reference", "logs",
]


# ============================================================================
# 数据模型
# ============================================================================

class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    workdir: str
    # 参考资源(任一可空但创建时强烈建议都填)
    reference_fasta: Optional[str] = None
    reference_gtf_or_gff: Optional[str] = None  # GFF 自动转 GTF
    # 计算资源(创建时只指定"总线程预算";并行度是运行时的全局设置,不在这里设)
    total_threads: Optional[int] = None  # 该项目可用的总线程预算


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    reference_fasta: Optional[str] = None
    reference_gtf: Optional[str] = None  # 直接给 GTF,不再自动转(转换由专门端点做)
    total_threads: Optional[int] = None


class SetUpstreamParamsRequest(BaseModel):
    step: str            # 例如 "fastp", "star_align"
    params: dict[str, Any]


class SetModuleDataRequest(BaseModel):
    data: dict[str, Any]


# ============================================================================
# 端点
# ============================================================================

@router.get("/")
async def list_projects(request: Request):
    """列出所有项目。"""
    pdir = _projects_dir(request)
    out = []
    if not pdir.exists():
        return {"projects": []}
    for sub in pdir.iterdir():
        if not sub.is_dir():
            continue
        meta_file = sub / "project.json"
        if meta_file.exists():
            try:
                with open(meta_file, encoding="utf-8") as f:
                    out.append(_migrate(json.load(f)))
            except Exception as e:
                logger.warning(f"读项目 {sub.name} 失败: {e}")
    out.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    return {"projects": out}


@router.post("/")
async def create_project(req: CreateProjectRequest, request: Request):
    """创建项目。
    
    步骤:
      1. 校验 workdir 存在(或创建)
      2. 在 workdir 下创建标准子文件夹
      3. 校验 fasta/gtf 文件存在
      4. 如果给的是 GFF,后台转成 GTF 放到 workdir/reference/
      5. 写 project.json
    """
    if not req.name.strip():
        raise HTTPException(400, "项目名不能为空")
    if not req.workdir.strip():
        raise HTTPException(400, "必须指定工作目录")
    
    workdir = Path(req.workdir).expanduser().resolve()
    
    # 创建/验证工作目录
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise HTTPException(400, f"工作目录无法创建: {e}")
    if not workdir.is_dir():
        raise HTTPException(400, f"工作目录不是目录: {workdir}")
    
    # 创建子文件夹
    for d in WORKDIR_SUBDIRS:
        (workdir / d).mkdir(exist_ok=True)
    
    # 验证参考资源文件
    if req.reference_fasta:
        if not Path(req.reference_fasta).exists():
            raise HTTPException(400, f"FASTA 不存在: {req.reference_fasta}")
    
    reference_gtf = None
    if req.reference_gtf_or_gff:
        src = Path(req.reference_gtf_or_gff)
        if not src.exists():
            raise HTTPException(400, f"注释文件不存在: {req.reference_gtf_or_gff}")
        
        # 如果是 GFF,转成 GTF;否则直接用
        is_gff = _is_gff(src)
        if is_gff:
            try:
                # 用 GFF 的 stem 当 GTF 文件名(test.gff3 -> test.gtf,
                # test.gff.gz -> test.gtf),避免不同物种用 annotation.gtf 互相覆盖。
                stem = src.name
                for suffix in (".gz",):
                    if stem.lower().endswith(suffix):
                        stem = stem[: -len(suffix)]
                for suffix in (".gff3", ".gff"):
                    if stem.lower().endswith(suffix):
                        stem = stem[: -len(suffix)]
                        break
                gtf_filename = f"{stem}.gtf"
                
                # 优先放在 GFF 同目录,如果不可写就退到 workdir/reference/
                same_dir_target = src.parent / gtf_filename
                workdir_target = workdir / "reference" / gtf_filename
                
                import os
                if os.access(src.parent, os.W_OK):
                    target = same_dir_target
                else:
                    target = workdir_target
                    logger.info(f"GFF 所在目录不可写,GTF 改放到 {target}")
                
                # 同名 GTF 已经存在且非空 → 复用,不重转
                if target.exists() and target.stat().st_size > 0:
                    logger.info(f"GTF 已存在,直接复用: {target}")
                    reference_gtf = str(target)
                else:
                    modules_dir = getattr(request.app.state, "modules_dir", None)
                    gff_to_gtf.convert(src, target, modules_dir=modules_dir)
                    reference_gtf = str(target)
                    logger.info(f"GFF 已转换: {src} -> {target}")
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(500, f"GFF→GTF 转换失败: {e}")
        else:
            reference_gtf = str(src)
    
    # 创建项目元数据
    pid = str(uuid.uuid4())[:12]
    pdir = _projects_dir(request) / pid
    pdir.mkdir(parents=True, exist_ok=True)
    
    # 计算资源:用户给了总线程预算就用,没给用默认(本机逻辑核心数)
    _comp = _default_compute()
    if req.total_threads is not None:
        _comp["total_threads"] = max(1, int(req.total_threads))

    meta = {
        "id": pid,
        "name": req.name.strip(),
        "description": req.description,
        "workdir": str(workdir),
        "reference_fasta": req.reference_fasta or None,
        "reference_gtf": reference_gtf,
        "modules_used": [],
        "module_data": {},
        "upstream_params": {},
        "compute": _comp,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _save(pdir, meta)
    return meta


@router.get("/{project_id}")
async def get_project(project_id: str, request: Request):
    pdir = _pdir(request, project_id)
    return _load(pdir)


@router.patch("/{project_id}")
async def update_project(project_id: str, req: UpdateProjectRequest,
                          request: Request):
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    if req.name is not None:
        meta["name"] = req.name.strip()
    if req.description is not None:
        meta["description"] = req.description
    if req.reference_fasta is not None:
        if req.reference_fasta and not Path(req.reference_fasta).exists():
            raise HTTPException(400, f"FASTA 不存在")
        meta["reference_fasta"] = req.reference_fasta or None
    if req.reference_gtf is not None:
        if req.reference_gtf and not Path(req.reference_gtf).exists():
            raise HTTPException(400, f"GTF 不存在")
        meta["reference_gtf"] = req.reference_gtf or None
    # 计算资源(项目设置页可改):只有总线程预算
    if req.total_threads is not None:
        meta.setdefault("compute", _default_compute())
        meta["compute"]["total_threads"] = max(1, int(req.total_threads))
    meta["updated_at"] = _now()
    _save(pdir, meta)
    return meta


@router.delete("/{project_id}")
async def delete_project(project_id: str, request: Request):
    """删除项目。
    
    只删除 ~/.plantomics/projects/<uuid>/(元数据)。
    工作目录留给用户自己处理(可能含原始数据上百 GB)。
    """
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    workdir = meta.get("workdir", "")
    
    shutil.rmtree(pdir)
    return {
        "deleted": project_id,
        "workdir_preserved": workdir,
        "message": f"项目元数据已删除。工作目录 {workdir} 仍保留,可手动清理。",
    }


# ─── 扫描工作目录 ─────────────────────────────

@router.get("/{project_id}/scan-samples")
async def scan_samples(project_id: str, stage: str, request: Request):
    """扫描项目工作目录的某个 stage 目录,返回检测到的样本结构。
    
    stage:
      - "raw"      → 扫 workdir/raw/ 下的 fastq 文件
      - "trimmed"  → 扫 workdir/trimmed/ 的 *.clean_*.fq.gz
      - "aligned"  → 扫 workdir/aligned/ 的 *.bam
    
    返回:
      {
        "samples": [
          {"name": "SRR123", "r1": "...", "r2": "..."},  # paired
          {"name": "SRR456", "r1": "...", "r2": null},   # single
          ...
        ],
        "bams": [...]   # 仅 stage=aligned 时
      }
    
    样本名识别规则:
      - 双端: <name>_1.fastq.gz / <name>_2.fastq.gz (或 _R1 / _R2)
      - 单端: <name>.fastq.gz(没有 _1 / _2)
      - 比对后: <name>.bam 或 <name>.Aligned.out.bam(STAR 默认)
    """
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    workdir = meta.get("workdir")
    if not workdir or not Path(workdir).is_dir():
        raise HTTPException(400, "项目工作目录无效")
    
    stage_dirs = {
        "raw": Path(workdir) / "00_raw",
        "trimmed": Path(workdir) / "02_trimmed",
        "aligned": Path(workdir) / "04_aligned",
    }
    if stage not in stage_dirs:
        raise HTTPException(400, f"未知 stage: {stage}")
    
    target = stage_dirs[stage]
    if not target.exists():
        return {"samples": [], "bams": []}
    
    if stage == "aligned":
        bams = sorted([str(p) for p in target.rglob("*.bam")])
        # 也尝试推断成"样本"
        samples = []
        for bam in bams:
            name = Path(bam).stem.replace(".Aligned.out", "")
            samples.append({"name": name, "bam": bam})
        return {"bams": bams, "samples": samples}
    
    # raw / trimmed: 扫 fastq
    samples = _detect_fastq_samples(target)
    return {"samples": samples, "bams": []}


def _detect_fastq_samples(target: Path) -> list[dict]:
    """扫一个目录下的 fastq 文件,识别 paired-end 配对和 single-end。"""
    import re
    
    # 匹配 fastq 文件
    fastq_exts = ("fq", "fq.gz", "fastq", "fastq.gz")
    all_fastqs = []
    for ext in fastq_exts:
        all_fastqs.extend(target.rglob(f"*.{ext}"))
    all_fastqs = sorted(set(all_fastqs))
    
    # 识别 _1 / _2 / _R1 / _R2 后缀
    paired_pattern = re.compile(
        r"^(.+?)[._-](?:R?[12]|read[12])\.(?:f(?:ast)?q)(?:\.gz)?$",
        re.IGNORECASE,
    )
    
    paired_buckets: dict[str, dict] = {}
    singles: list[dict] = []
    
    for f in all_fastqs:
        match = paired_pattern.match(f.name)
        if match:
            base = match.group(1)
            # 区分 _1 和 _2
            is_r1 = bool(re.search(r"[._-](R?1|read1)\.", f.name, re.IGNORECASE))
            key = base
            if key not in paired_buckets:
                paired_buckets[key] = {"name": base, "r1": None, "r2": None}
            if is_r1:
                paired_buckets[key]["r1"] = str(f)
            else:
                paired_buckets[key]["r2"] = str(f)
        else:
            # 看名字像不像 cleaned (来自 fastp)
            base = re.sub(r"\.(?:clean|fastq|fq)(?:\.gz)?$", "", f.name)
            base = re.sub(r"\.(?:fastq|fq)(?:\.gz)?$", "", base)
            singles.append({"name": base, "r1": str(f), "r2": None})
    
    # paired_buckets 里只有 r1 或只有 r2 的当 single
    samples = []
    for entry in paired_buckets.values():
        if entry["r1"] and entry["r2"]:
            samples.append(entry)
        elif entry["r1"]:
            samples.append({"name": entry["name"], "r1": entry["r1"], "r2": None})
        elif entry["r2"]:
            samples.append({"name": entry["name"], "r1": entry["r2"], "r2": None})
    samples.extend(singles)
    
    # 去重(可能 paired 的 base name 跟 single 同名)
    seen = set()
    deduped = []
    for s in samples:
        key = s["name"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    
    return deduped


@router.get("/{project_id}/scan-sra")
async def scan_sra(project_id: str, request: Request):
    """扫描项目 raw/ 目录,返回有哪些 .sra 文件 + 已有哪些 fastq。
    
    用于"SRA 处理"页给用户决定是"解压本地 sra"还是"下载新的"。
    """
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    workdir = meta.get("workdir")
    if not workdir or not Path(workdir).is_dir():
        raise HTTPException(400, "项目工作目录无效")
    
    raw = Path(workdir) / "00_raw"
    if not raw.exists():
        return {"sra_files": [], "fastq_files": [], "scan_dir": str(raw)}
    
    sra_files = sorted([str(p) for p in raw.rglob("*.sra")])
    fastq_files = []
    for ext in ("fq", "fq.gz", "fastq", "fastq.gz"):
        fastq_files.extend([str(p) for p in raw.rglob(f"*.{ext}")])
    fastq_files.sort()
    
    return {
        "sra_files": sra_files,
        "fastq_files": fastq_files,
        "scan_dir": str(raw),
    }


@router.get("/{project_id}/scan-readlengths")
async def scan_readlengths(project_id: str, subdir: str,
                              request: Request):
    """扫某子目录下所有 fastq 的读长,按 sample 聚合 + 归到标准档。
    
    返回 {records: [{sample, files, raw_read_length, read_length, sjdb_overhang}],
          unique_overhangs: [...]}
    """
    if subdir not in ("raw", "trimmed"):
        raise HTTPException(400, "subdir 只能是 raw 或 trimmed")
    
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    workdir = meta.get("workdir")
    if not workdir or not Path(workdir).is_dir():
        raise HTTPException(400, "项目工作目录无效")
    
    target = Path(workdir) / subdir
    if not target.is_dir():
        return {"records": [], "unique_overhangs": [], "scan_dir": str(target)}
    
    # 内联实现(主程序不依赖模块代码)
    # 标准读长档,实际探测值会归到最近的
    STANDARD_READ_LENGTHS = [36, 50, 75, 100, 125, 150, 250, 300]
    
    import gzip
    import re as _re
    from collections import Counter, defaultdict
    
    def normalize_to_std(L: int) -> int:
        if L <= 0:
            return STANDARD_READ_LENGTHS[0]
        return min(STANDARD_READ_LENGTHS, key=lambda x: abs(x - L))
    
    def detect_one(p: Path) -> int | None:
        try:
            opener = gzip.open if p.name.endswith(".gz") else open
            lengths = []
            with opener(p, "rt", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= 2000:  # 500 reads
                        break
                    if i % 4 == 1:
                        lengths.append(len(line.rstrip()))
            if not lengths:
                return None
            return Counter(lengths).most_common(1)[0][0]
        except Exception:
            return None
    
    def sample_name_of(p: Path) -> str:
        name = p.name
        name = _re.sub(r"\.(fq|fastq)(\.gz)?$", "", name)
        name = _re.sub(r"[._-](?:R?[12]|read[12])$", "", name,
                        flags=_re.IGNORECASE)
        return name
    
    candidates = []
    for ext in ("fq.gz", "fastq.gz", "fq", "fastq"):
        candidates.extend(target.rglob(f"*.{ext}"))
    
    # 按 sample 分组
    by_sample = defaultdict(list)
    for fq in sorted(set(candidates)):
        by_sample[sample_name_of(fq)].append(fq)
    
    records = []
    for sample, files in by_sample.items():
        per_file = []
        for f in files:
            L = detect_one(f)
            if L is not None:
                per_file.append(L)
        if not per_file:
            continue
        raw_max = max(per_file)
        std_L = normalize_to_std(raw_max)
        records.append({
            "sample": sample,
            "files": [str(f) for f in files],
            "raw_read_length": raw_max,
            "read_length": std_L,
            "sjdb_overhang": std_L - 1,
        })
    
    unique_ovs = sorted(set(r["sjdb_overhang"] for r in records))
    return {
        "records": records,
        "unique_overhangs": unique_ovs,
        "scan_dir": str(target),
    }


# ─── 模块数据 ────────────────────────────────

@router.put("/{project_id}/module-data/{module_id}")
async def set_module_data(project_id: str, module_id: str,
                           req: SetModuleDataRequest, request: Request):
    """模块更新自己在项目里的数据。"""
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    
    if "module_data" not in meta:
        meta["module_data"] = {}
    meta["module_data"][module_id] = req.data
    
    if module_id not in meta.get("modules_used", []):
        meta.setdefault("modules_used", []).append(module_id)
    
    meta["updated_at"] = _now()
    _save(pdir, meta)
    return meta


@router.delete("/{project_id}/module-data/{module_id}")
async def remove_module_data(project_id: str, module_id: str, request: Request):
    """从项目里移除某个模块的数据。"""
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    
    if "module_data" in meta and module_id in meta["module_data"]:
        del meta["module_data"][module_id]
    if module_id in meta.get("modules_used", []):
        meta["modules_used"].remove(module_id)
    
    meta["updated_at"] = _now()
    _save(pdir, meta)
    return meta


# ─── 上游参数(每 step 最近一次,UI 回填用)──

@router.put("/{project_id}/upstream-params")
async def set_upstream_params(project_id: str,
                                req: SetUpstreamParamsRequest,
                                request: Request):
    """记录某个上游 step 最近一次用的参数。下次 UI 进来回填。"""
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    if "upstream_params" not in meta:
        meta["upstream_params"] = {}
    meta["upstream_params"][req.step] = req.params
    meta["updated_at"] = _now()
    _save(pdir, meta)
    return meta


# ─── 扫工作目录(给前端做"自动识别"用)──────

@router.get("/{project_id}/scan/{subdir}")
async def scan_subdir(project_id: str, subdir: str, request: Request):
    """扫工作目录的某个子文件夹,返回内容详情。
    
    - subdir = "raw":返回 sra / fastq 文件分类 + 自动配对的样本
    - subdir = "trimmed":扫 fastp 输出的清洗后 fastq
    - subdir = "aligned":扫 BAM
    
    用于前端做"自动识别工作目录内容"。
    """
    pdir = _pdir(request, project_id)
    meta = _load(pdir)
    workdir = Path(meta["workdir"]) / subdir
    
    if not workdir.exists():
        return {"exists": False, "files": [], "samples": []}
    
    if subdir == "raw":
        return _scan_raw(workdir)
    elif subdir == "trimmed":
        return _scan_trimmed(workdir)
    elif subdir == "aligned":
        return _scan_aligned(workdir)
    else:
        # 通用列文件
        files = sorted([str(p) for p in workdir.iterdir() if p.is_file()])
        return {"exists": True, "files": files, "samples": []}


def _scan_raw(workdir: Path) -> dict:
    """扫 raw 目录,返回 sra/fastq 分类 + 配对样本。"""
    sras = sorted([p for p in workdir.glob("*.sra")])
    fastqs = sorted(
        [p for p in workdir.iterdir()
         if p.is_file() and p.name.lower().endswith(
             (".fq", ".fq.gz", ".fastq", ".fastq.gz"))]
    )
    
    # 也扫子目录(prefetch 可能创建 SRR1234/SRR1234.sra)
    for sub in workdir.iterdir():
        if sub.is_dir():
            sras.extend(sorted([p for p in sub.glob("*.sra")]))
    
    samples = _pair_fastqs(fastqs)
    return {
        "exists": True,
        "sra_files": [str(p) for p in sras],
        "fastq_files": [str(p) for p in fastqs],
        "samples": samples,
    }


def _scan_trimmed(workdir: Path) -> dict:
    """扫 trimmed 目录(fastp 输出)。每个样本一个子目录。"""
    samples = []
    for sub in sorted(workdir.iterdir()):
        if not sub.is_dir():
            continue
        # 找 .clean_1.fq.gz / .clean_2.fq.gz / .clean.fq.gz
        clean_files = sorted([
            p for p in sub.iterdir()
            if p.is_file() and ".clean" in p.name and
            p.name.endswith((".fq.gz", ".fastq.gz"))
        ])
        if not clean_files:
            continue
        
        r1 = next((str(p) for p in clean_files if "_1" in p.name), None)
        r2 = next((str(p) for p in clean_files if "_2" in p.name), None)
        if r1 and r2:
            samples.append({"name": sub.name, "r1": r1, "r2": r2})
        elif r1:
            samples.append({"name": sub.name, "r1": r1})
        elif clean_files:
            samples.append({"name": sub.name, "r1": str(clean_files[0])})
    
    return {"exists": True, "samples": samples}


def _scan_aligned(workdir: Path) -> dict:
    """扫 aligned 目录(STAR 输出)。返回 BAM 文件列表。"""
    bams = []
    # STAR Align 的 manager 会创建 <jobid>_star_align_<ts>/<sample>/<sample>.bam
    for p in workdir.rglob("*.bam"):
        bams.append(str(p))
    return {"exists": True, "bam_files": sorted(bams)}


def _pair_fastqs(fastqs: list) -> list:
    """根据文件名规律自动配对 paired-end / 识别 single-end。
    
    规则:
      - SRR1234_1.fastq.gz + SRR1234_2.fastq.gz → paired
      - SRR1234.fastq.gz → single
      - sample_R1.fq.gz + sample_R2.fq.gz → paired (大写 R)
    """
    import re
    samples_dict = {}  # sample_name -> {"r1": ..., "r2": ...}
    
    for p in fastqs:
        name = p.name
        # 去掉 .fq.gz / .fastq.gz / .fq / .fastq
        stem = re.sub(r"\.(fq|fastq)(\.gz)?$", "", name)
        
        # 看是不是 _1 / _2 / _R1 / _R2 结尾
        m = re.match(r"^(.*?)[._]([Rr]?[12])$", stem)
        if m:
            sample_name, mate = m.group(1), m.group(2).upper().lstrip("R")
            d = samples_dict.setdefault(sample_name, {"name": sample_name})
            d[f"r{mate}"] = str(p)
        else:
            # 单端
            samples_dict[stem] = {"name": stem, "r1": str(p)}
    
    return [
        {"name": k, "r1": v.get("r1", ""), **({"r2": v["r2"]} if "r2" in v else {})}
        for k, v in samples_dict.items()
        if v.get("r1")
    ]


# ============================================================================
# 工具
# ============================================================================

def _projects_dir(request: Request) -> Path:
    return request.app.state.data_dir / "projects"


def _pdir(request: Request, project_id: str) -> Path:
    pdir = _projects_dir(request) / project_id
    if not pdir.exists():
        raise HTTPException(404, f"项目不存在: {project_id}")
    return pdir


def _default_compute() -> dict:
    """计算资源默认值:总线程预算 = 本机逻辑核心数。

    total_threads —— 该项目允许同时占用的 CPU 线程总量。运行时若并行跑多个
    任务,这个预算会被均分给各并行任务(并行度是全局设置,见 Settings)。
    """
    import os
    try:
        n = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") \
            else (os.cpu_count() or 4)
    except Exception:
        n = os.cpu_count() or 4
    return {"total_threads": max(1, n)}


def _migrate(meta: dict) -> dict:
    """老项目自动补字段(打开/列出时调用,不弹窗)。

    旧 compute 是 {threads(单任务), parallel_jobs(并发)};新模型只保留
    total_threads(总线程预算)。迁移时把旧的"单任务核数 × 并发数"当作用户
    当初想占用的总核数。
    """
    d = _default_compute()
    comp = meta.get("compute")
    if not isinstance(comp, dict):
        meta["compute"] = dict(d)
        return meta
    if "total_threads" not in comp:
        old_t = int(comp.get("threads", 0) or 0)
        old_p = int(comp.get("parallel_jobs", 0) or 0)
        comp["total_threads"] = max(1, old_t * max(1, old_p)) if old_t > 0 else d["total_threads"]
    # 清掉旧字段(下次保存即落盘)
    comp.pop("threads", None)
    comp.pop("parallel_jobs", None)
    return meta


def _load(pdir: Path) -> dict:
    f = pdir / "project.json"
    if not f.exists():
        raise HTTPException(404, "项目元数据丢失")
    with open(f, encoding="utf-8") as fh:
        return _migrate(json.load(fh))


def _save(pdir: Path, meta: dict):
    f = pdir / "project.json"
    tmp = f.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, ensure_ascii=False)
    tmp.replace(f)


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _is_gff(path: Path) -> bool:
    """通过文件头/扩展名判断是 GFF 还是 GTF。"""
    name = path.name.lower()
    if name.endswith((".gff", ".gff3", ".gff.gz", ".gff3.gz")):
        return True
    if name.endswith((".gtf", ".gtf.gz")):
        return False
    # 不确定,看文件头几行
    try:
        import gzip
        opener = gzip.open if name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                if line.startswith("#"):
                    if "##gff-version" in line:
                        return True
                    continue
                # 看属性列(第 9 列)的格式:GFF3 用 "key=value;",GTF 用 'key "value";'
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 9:
                    attrs = parts[8]
                    # GTF:'gene_id "ABC";'
                    # GFF:'ID=gene:ABC'
                    if '="' in attrs or " \"" in attrs:
                        return False
                    if "=" in attrs and ";" in attrs and '"' not in attrs:
                        return True
                    break
    except Exception:
        pass
    # 默认按扩展名判断
    return name.endswith(".gff") or name.endswith(".gff3")
