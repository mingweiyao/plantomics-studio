/**
 * 转录组上游分析页(新版)
 * 
 * URL: /projects/:projectId/m/omics-rnaseq-bulk/upstream
 * 
 * 关键改动:
 *   - 输入输出路径**自动从项目工作目录推断**(raw/qc/trimmed/aligned/...)
 *   - 参考资源**自动从项目读 reference_fasta + reference_gtf**
 *   - 表单**只问分析参数**(线程数、quality 阈值等)
 *   - 上次用过的参数**自动回填**(从 upstream_params 字段读)
 *   - 提交后参数会保存,下次再进来表单是上次的参数
 * 
 * SRA 流程的特殊设计:
 *   用户给一个目录或填 accession 列表。
 *   - 已有 .sra 文件 → 只解压
 *   - 已有 .fastq → 跳过
 *   - 都没有但有 accession → 下载 + 解压
 *   - 输出统一进 workdir/raw/
 */
import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import {
  ArrowLeft,
  FileSearch,
  Filter as FilterIcon,
  Database as DatabaseIcon,
  GitBranch,
  BarChart3,
  Download,
  TrendingUp,
  FolderOpen,
  Plus,
  X,
  AlertCircle,
  Folder,
} from "lucide-react";
import { coreApi } from "../lib/api";
import { PipelineFlow, type FlowStep, type StepStatus } from "../components/PipelineFlow";
import { Drawer } from "../components/Drawer";
import { rnaseqApi, Job } from "../lib/rnaseqApi";
import {
  PageHeader,
  Card,
  Loading,
  Banner,
  Button,
  Field,
  Input,
  NumberInput,
  Textarea,
  Select,
} from "../components/ui";
import { extractError } from "../lib/errorMessage";

type TabId =
  | "sra"
  | "fastqc"
  | "fastp"
  | "star_index"
  | "star_align"
  | "feature_counts"
  | "normalize"
  | "library_qc"
  | "new_transcripts"
  | "alt_splicing"
  | "lncrna";

// 抽屉标题/图标用
const STEP_META: Record<string, { label: string; sub?: string; icon: any }> = {
  sra: { label: "SRA / fastq 准备", icon: Download },
  fastqc: { label: "质控", sub: "FastQC · 原始数据", icon: FileSearch },
  fastp: { label: "过滤", sub: "fastp", icon: FilterIcon },
  star_index: { label: "STAR 索引", icon: DatabaseIcon },
  star_align: { label: "比对", sub: "STAR", icon: GitBranch },
  feature_counts: { label: "定量", sub: "featureCounts", icon: BarChart3 },
  normalize: { label: "标准化", sub: "TPM / FPKM / CPM", icon: TrendingUp },
  library_qc: { label: "文库质量评估", sub: "Qualimap", icon: FileSearch },
  new_transcripts: { label: "新转录本发现", sub: "StringTie + gffcompare", icon: GitBranch },
  alt_splicing: { label: "可变剪接", sub: "rMATS", icon: FilterIcon },
  lncrna: { label: "lncRNA 预测", sub: "FEELnc", icon: DatabaseIcon },
};

// 一键运行默认勾选的核心线性步骤(顺序即执行顺序)。高级分析(文库质控/新转录本/
// lncRNA/可变剪接)也在圆环上、可勾选,但默认不勾,需要时再勾上一起跑。
const CORE_STEP_IDS = ["sra", "fastqc", "fastp", "star_align", "feature_counts", "normalize"];

// job.kind → 圆环步骤 id(一个步骤可能对应多个 kind)
const KIND_TO_STEP: Record<string, string> = {
  sra_download: "sra", sra_extract: "sra",
  fastqc: "fastqc",
  fastp: "fastp",
  star_index: "star_align", star_align: "star_align",
  feature_counts: "feature_counts", merge_counts: "feature_counts",
  normalize: "normalize",
  library_qc: "library_qc",
  new_transcripts: "new_transcripts",
  alt_splicing: "alt_splicing",
  lncrna: "lncrna",
};

// 步骤的规范先后顺序(一键运行进行中时用来判断"已跑过 / 正在跑 / 还没轮到")
const STEP_ORDER = [
  "sra", "fastqc", "fastp", "star_align", "feature_counts", "normalize",
  "library_qc", "new_transcripts", "lncrna", "alt_splicing",
];

// 一键运行(pipeline_upstream)进行中,从 stage 文字猜当前在跑哪一步
const PIPE_STAGE_KEYWORDS: [RegExp, string][] = [
  [/SRA/i, "sra"],
  [/FastQC|质控/, "fastqc"],
  [/fastp|过滤/, "fastp"],
  [/STAR|比对/, "star_align"],
  [/featureCounts|定量/, "feature_counts"],
  [/标准化/, "normalize"],
  [/文库|Qualimap/i, "library_qc"],
  [/StringTie|新转录本/, "new_transcripts"],
  [/lncRNA/i, "lncrna"],
  [/剪接|rMATS/i, "alt_splicing"],
];

export function Upstream() {
  const { id: projectId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<TabId>("sra");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const openStep = (id: TabId) => {
    setTab(id);
    setDrawerOpen(true);
  };

  // 一键运行勾选的步骤(默认勾上核心 6 步;高级分析默认不勾)
  const [selectedSteps, setSelectedSteps] = useState<Set<string>>(
    () => new Set(CORE_STEP_IDS)
  );
  const toggleStep = (id: string) =>
    setSelectedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => coreApi.getProject(projectId!),
    enabled: !!projectId,
  });

  // 任务列表(驱动圆环节点的"运行中/已完成/失败"状态);跑着的时候轮询刷新
  const { data: jobsData } = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => rnaseqApi.listJobs(projectId!),
    enabled: !!projectId,
    refetchInterval: 2500,
  });

  const qc = useQueryClient();
  const [pipeResult, setPipeResult] = useState<{ ok: boolean; message: string } | null>(null);
  // 一键运行:只跑圆环上勾选的步骤,线程用项目级计算资源
  const pipelineMutation = useMutation({
    mutationFn: () =>
      rnaseqApi.submitPipelineUpstream({
        project_id: project!.id,
        output_path: project!.workdir,
        params: {
          // 各步骤「保存参数」存下的设置(pipeline 只取其中的选项值,
          // 文件路径类的键会被忽略,所以提前配好的参数能被一键运行用上)
          ...(project!.upstream_params || {}),
          workdir: project!.workdir,
          fasta: project!.reference_fasta || undefined,
          gtf: project!.reference_gtf || "",
          steps: [...selectedSteps],
          star_align: {
            threads: project!.compute?.threads ?? 8,
            ...(project!.upstream_params?.star_align || {}),
          },
          feature_counts: {
            threads: project!.compute?.threads ?? 8,
            ...(project!.upstream_params?.feature_counts || {}),
          },
        },
      }),
    onSuccess: (j: any) => {
      setPipeResult({
        ok: true,
        message: `一键流程已提交 (id=${j.id})，按顺序跑你在圆环上勾选的步骤(共 ${selectedSteps.size} 步)。`,
      });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => setPipeResult({ ok: false, message: extractError(e) }),
  });

  if (!project) return <Loading />;
  if (!project.workdir) {
    return (
      <div className="p-6 max-w-3xl">
        <Banner variant="error">
          这个项目没有工作目录。重建项目并指定工作目录。
        </Banner>
      </div>
    );
  }

  return (
    <div className="p-6">
      <PageHeader
        title="上游分析"
        subtitle={project.name}
        back={
          <button
            onClick={() => navigate(`/projects/${projectId}`)}
            className="text-ink-faint hover:text-ink"
            aria-label="返回"
          >
            <ArrowLeft size={18} />
          </button>
        }
      />

      <Card>
        <div className="text-xs text-ink-faint flex items-center gap-2">
          <Folder size={11} />
          工作目录: <code className="font-mono">{project.workdir}</code>
        </div>
        <div className="text-xs text-ink-muted mt-1">
          任务产出按生成顺序写入编号子文件夹(00_raw / 01_qc / 02_trimmed / 04_aligned / 05_counts / 06_normalized ...)
        </div>
      </Card>

      <NextStepHint project={project} onJumpTab={(t) => openStep(t)} />
      
      {(() => {
        const up: any = project.upstream_params || {};
        const isCfg = (...keys: string[]) =>
          keys.some((k) => up[k] && Object.keys(up[k]).length > 0);

        const jobs: any[] = jobsData?.jobs || [];
        // 每个步骤取最近一个对应的"单独运行" job
        const latest: Record<string, any> = {};
        for (const j of jobs) {
          const sid = KIND_TO_STEP[j.kind];
          if (!sid) continue;
          if (!latest[sid] || new Date(j.created_at) > new Date(latest[sid].created_at))
            latest[sid] = j;
        }
        // 最近一次一键运行
        const pipeJob = jobs
          .filter((j) => j.kind === "pipeline_upstream")
          .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
        const pipeRunning =
          !!pipeJob && (pipeJob.status === "running" || pipeJob.status === "pending");
        const pipeCurStep = (() => {
          if (!pipeRunning) return null;
          const stage = pipeJob.progress?.stage || "";
          for (const [re, sid] of PIPE_STAGE_KEYWORDS) if (re.test(stage)) return sid;
          // stage 文字没匹配上(下载/解压/过渡阶段没关键词)→ 用进度百分比兜底,
          // 保证一键运行进行中始终有一个节点在转,不会出现"动画消失"
          const ran = (pipeJob.params?.steps || [])
            .filter((s: string) => STEP_ORDER.includes(s))
            .sort((a: string, b: string) => STEP_ORDER.indexOf(a) - STEP_ORDER.indexOf(b));
          if (!ran.length) return null;
          const pct = Math.max(0, Math.min(100, pipeJob.progress?.pct || 0));
          return ran[Math.min(ran.length - 1, Math.floor((pct / 100) * ran.length))];
        })();
        // 一键运行对某一步的状态贡献(这步在本次勾选里才算)
        const pipeStatusFor = (id: string): StepStatus | null => {
          if (!pipeJob) return null;
          const ran: string[] = pipeJob.params?.steps || [];
          if (!ran.includes(id)) return null;
          const s = pipeJob.status;
          if (s === "completed") return "done";
          const ii = STEP_ORDER.indexOf(id);
          const ci = pipeCurStep ? STEP_ORDER.indexOf(pipeCurStep) : -1;
          if (s === "running" || s === "pending") {
            if (ci >= 0 && ii < ci) return "done";
            if (ci >= 0 && ii === ci) return "running";
            return null;
          }
          if (s === "failed" || s === "cancelled" || s === "interrupted") {
            if (ci >= 0 && ii < ci) return "done";
            if (ci >= 0 && ii === ci) return "failed";
            return null;
          }
          return null;
        };
        const statusOf = (id: string, ...cfgKeys: string[]): StepStatus => {
          const indiv = latest[id];
          const indivNewer =
            indiv && pipeJob && new Date(indiv.created_at) > new Date(pipeJob.created_at);
          if (indiv && (indivNewer || !pipeJob)) {
            const s = indiv.status;
            if (s === "running" || s === "pending") return "running";
            if (s === "completed") return "done";
            if (s === "failed" || s === "cancelled" || s === "interrupted") return "failed";
          }
          const ps = pipeStatusFor(id);
          if (ps) return ps;
          if (indiv) {
            const s = indiv.status;
            if (s === "running" || s === "pending") return "running";
            if (s === "completed") return "done";
            if (s === "failed" || s === "cancelled" || s === "interrupted") return "failed";
          }
          return isCfg(...cfgKeys) ? "configured" : "pending";
        };
        const steps: FlowStep[] = [
          { id: "sra", label: "SRA / fastq", icon: Download, status: statusOf("sra", "sra", "sra_download") },
          { id: "fastqc", label: "质控", sublabel: "FastQC", icon: FileSearch, status: statusOf("fastqc", "fastqc") },
          { id: "fastp", label: "过滤", sublabel: "fastp", icon: FilterIcon, status: statusOf("fastp", "fastp") },
          { id: "star_align", label: "比对", sublabel: "STAR", icon: GitBranch, status: statusOf("star_align", "star_align", "star_index") },
          { id: "feature_counts", label: "定量", sublabel: "featureCounts", icon: BarChart3, status: statusOf("feature_counts", "feature_counts") },
          { id: "normalize", label: "标准化", icon: TrendingUp, status: statusOf("normalize", "normalize") },
          { id: "library_qc", label: "文库质控", sublabel: "Qualimap", icon: FileSearch, status: statusOf("library_qc", "library_qc") },
          { id: "new_transcripts", label: "新转录本", sublabel: "StringTie", icon: GitBranch, status: statusOf("new_transcripts", "new_transcripts") },
          { id: "lncrna", label: "lncRNA", icon: DatabaseIcon, status: statusOf("lncrna", "lncrna") },
          { id: "alt_splicing", label: "可变剪接", sublabel: "rMATS", icon: FilterIcon, status: statusOf("alt_splicing", "alt_splicing") },
        ];
        return (
          <div className="mt-2">
            <PipelineFlow
              steps={steps}
              activeId={drawerOpen ? tab : undefined}
              onSelectStep={(id) => openStep(id as TabId)}
              onRunAll={() => pipelineMutation.mutate()}
              running={pipelineMutation.isPending}
              canRun={!!project.reference_gtf && selectedSteps.size > 0}
              selected={selectedSteps}
              onToggleSelect={toggleStep}
            />
            <div className="mx-auto mt-1 max-w-[560px] text-center text-xs text-ink-faint">
              每个步骤都能单独跑:点圆环上的步骤图标 → 配置后「保存参数」(不用等前面的文件生成)
              或「运行这一步」。想连着跑多步,就勾上要跑的步骤(已选 {selectedSteps.size} 步),
              点圆心「一键运行」按顺序执行 —— 6 个核心步骤不是必选的,勾几个跑几个。
              高级分析默认不勾;可变剪接要先分两组样本,只能单独运行。
            </div>
          </div>
        );
      })()}

      {pipeResult && (
        <div className="mx-auto mt-2 max-w-[540px]">
          <Banner variant={pipeResult.ok ? "success" : "error"}>{pipeResult.message}</Banner>
        </div>
      )}

      <Drawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        title={STEP_META[tab]?.label || tab}
        subtitle={STEP_META[tab]?.sub}
        icon={(() => {
          const I = STEP_META[tab]?.icon;
          return I ? <I size={18} /> : null;
        })()}
        accent="rgb(var(--accent))"
      >
        {tab === "sra" && <SraForm project={project} />}
        {tab === "fastqc" && <FastqcForm project={project} initialTarget="raw" />}
        {tab === "fastp" && <FastpForm project={project} />}
        {tab === "star_index" && <StarIndexForm project={project} />}
        {tab === "star_align" && <StarAlignForm project={project} />}
        {tab === "feature_counts" && <FeatureCountsForm project={project} />}
        {tab === "normalize" && <NormalizeForm project={project} />}
        {tab === "library_qc" && <LibraryQcForm project={project} />}
        {tab === "new_transcripts" && <NewTranscriptsForm project={project} />}
        {tab === "alt_splicing" && <AltSplicingForm project={project} />}
        {tab === "lncrna" && <LncrnaForm project={project} />}
      </Drawer>
    </div>
  );
}

// ─────────────────────────────────────────────
// 共用:工作目录子路径辅助
// ─────────────────────────────────────────────

function joinPath(...parts: string[]): string {
  return parts.join("/").replace(/\/+/g, "/");
}

// ─── 共用组件 ───

function SubmitForm({
  formContent,
  onSubmit,
  onSaveParams,
  saveMsg,
  pending,
  result,
  prevParams,
}: {
  formContent: React.ReactNode;
  onSubmit: () => void;
  onSaveParams?: () => void;
  saveMsg?: string | null;
  pending: boolean;
  result: { ok: boolean; message: string } | null;
  prevParams?: any;
}) {
  const { id: projectId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  return (
    <Card>
      <div className="space-y-4">
        {prevParams && (
          <Banner variant="info">
            <div className="text-xs">
              <strong>上次运行的参数:</strong> 已自动回填,可以直接修改后再跑。
            </div>
          </Banner>
        )}
        {formContent}
        {result && (
          <Banner variant={result.ok ? "success" : "error"}>
            <div className="flex items-center gap-2 justify-between">
              <span>{result.message}</span>
              {result.ok && (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => navigate(`/projects/${projectId}`)}
                >
                  回项目看进度
                </Button>
              )}
            </div>
          </Banner>
        )}
        {saveMsg && (
          <Banner variant={saveMsg.startsWith("保存失败") ? "error" : "success"}>
            <span className="text-xs">{saveMsg}</span>
          </Banner>
        )}
        <div className="flex justify-end gap-2">
          {onSaveParams && (
            <Button variant="secondary" onClick={onSaveParams} disabled={pending}>
              保存参数
            </Button>
          )}
          <Button onClick={onSubmit} disabled={pending}>
            {pending ? "提交中..." : "运行这一步"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

function useStepSubmit(
  submitFn: () => Promise<Job>,
  saveParamsFn: () => void,
  qc: any
) {
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  // 只保存参数、不运行,也不要求输入文件已存在 —— 支持提前把后面步骤的参数配好
  const saveOnly = async () => {
    setSaveMsg(null);
    try {
      await saveParamsFn();
      qc.invalidateQueries({ queryKey: ["project"] });
      setSaveMsg("参数已保存。等前面步骤产出文件后可单独运行这一步,或直接用「一键运行」。");
    } catch (e) {
      setSaveMsg(`保存失败: ${extractError(e)}`);
    }
  };
  const mutation = useMutation({
    mutationFn: submitFn,
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      saveParamsFn();
      setResult({
        ok: true,
        message: `任务已提交(id=${job.id})。完成后产出会出现在工作目录的对应子文件夹。`,
      });
    },
    onError: (e) => {
      setResult({ ok: false, message: `提交失败: ${extractError(e)}` });
    },
  });
  return { mutation, result, setResult, saveOnly, saveMsg };
}

function PathPicker({
  label,
  value,
  onChange,
  hint,
  required,
  isDir = false,
  filters,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  hint?: string;
  required?: boolean;
  isDir?: boolean;
  filters?: { name: string; extensions: string[] }[];
}) {
  async function pick() {
    try {
      const f = await openDialog({
        multiple: false,
        directory: isDir,
        title: `选择${label}`,
        filters,
      });
      if (typeof f === "string") onChange(f);
    } catch {}
  }
  return (
    <Field label={label} required={required} hint={hint}>
      <div className="flex gap-2">
        <Input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={isDir ? "/path/to/directory" : "/path/to/file"}
          className="flex-1"
        />
        <Button variant="secondary" size="sm" onClick={pick}>
          <FolderOpen size={12} />
          {isDir ? "选目录" : "选文件"}
        </Button>
      </div>
    </Field>
  );
}

function MultiPathPicker({
  label,
  values,
  onChange,
  filters,
  hint,
  required,
}: {
  label: string;
  values: string[];
  onChange: (vs: string[]) => void;
  filters?: { name: string; extensions: string[] }[];
  hint?: string;
  required?: boolean;
}) {
  async function pickMore() {
    try {
      const f = await openDialog({
        multiple: true,
        title: `选择${label}`,
        filters,
      });
      if (Array.isArray(f)) onChange([...values, ...f]);
      else if (typeof f === "string") onChange([...values, f]);
    } catch {}
  }
  return (
    <Field label={label} required={required} hint={hint}>
      <div className="space-y-1.5">
        {values.length > 0 && (
          <div className="space-y-1">
            {values.map((v, i) => (
              <div
                key={i}
                className="flex items-center gap-2 px-2 py-1 bg-bg-muted rounded text-xs"
              >
                <span className="font-mono truncate flex-1" title={v}>
                  {v}
                </span>
                <button
                  onClick={() => onChange(values.filter((_, idx) => idx !== i))}
                  className="text-ink-faint hover:text-red-500"
                >
                  <X size={11} />
                </button>
              </div>
            ))}
          </div>
        )}
        <Button variant="secondary" size="sm" onClick={pickMore}>
          <Plus size={11} />
          添加文件
        </Button>
      </div>
    </Field>
  );
}

function MissingReferenceWarning({
  field,
  projectId,
}: {
  field: "fasta" | "gtf";
  projectId: string;
}) {
  const navigate = useNavigate();
  return (
    <Banner variant="warning">
      <div className="flex items-center gap-2 text-xs">
        <AlertCircle size={11} />
        <span>
          这一步需要项目设置 <strong>{field === "fasta" ? "基因组 FASTA" : "GTF 注释"}</strong>。
        </span>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => navigate(`/projects/${projectId}`)}
        >
          回项目设置
        </Button>
      </div>
    </Banner>
  );
}

// ─── SRA / fastq 准备 ───

function SraForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.sra_download || {};
  const [accessions, setAccessions] = useState<string>(
    prev.accessions ? prev.accessions.join("\n") : ""
  );
  const [threads, setThreads] = useState<number>(prev.threads || 8);

  // 扫描 raw/ 看里面已有什么
  const { data: scan, refetch: refetchScan, isLoading: scanLoading } = useQuery({
    queryKey: ["scan-sra", project.id],
    queryFn: () => coreApi.scanSra(project.id),
    refetchInterval: 5000,  // 提交任务后会自动刷新
  });

  const accList: string[] = accessions
    .split(/[\s,;\n]+/)
    .map((s: string) => s.trim())
    .filter(Boolean);

  const sraFiles = scan?.sra_files || [];
  const fastqFiles = scan?.fastq_files || [];
  const hasSra = sraFiles.length > 0;
  const hasFastq = fastqFiles.length > 0;

  // 自动判断默认模式
  const defaultMode: "scan" | "download" =
    hasSra ? "scan" : "download";
  const [mode, setMode] = useState<"scan" | "download">(defaultMode);
  // 模式跟着扫描结果走(用户没手动选过)
  const [userPickedMode, setUserPickedMode] = useState(false);
  useEffect(() => {
    if (!userPickedMode && scan) {
      setMode(hasSra ? "scan" : "download");
    }
  }, [scan, hasSra, userPickedMode]);

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () => {
      const params: any = { threads };
      if (mode === "scan") {
        params.scan_dir = scan?.scan_dir || joinPath(project.workdir, "00_raw");
      } else {
        params.accessions = accList;
      }
      return rnaseqApi.submitSra({
        project_id: project.id,
        output_path: joinPath(project.workdir, "00_raw"),
        params,
      });
    },
    () =>
      coreApi.setUpstreamParams(project.id, "sra_download", {
        accessions: accList,
        threads,
        mode,
      }),
    qc
  );

  // 提交后,延迟刷新扫描结果
  useEffect(() => {
    if (result?.ok) {
      setTimeout(() => refetchScan(), 2000);
    }
  }, [result, refetchScan]);

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (mode === "scan" && !hasSra) {
          setResult({
            ok: false,
            message: "工作目录里没有 .sra 文件,无法扫描+解压。请切到下载模式。",
          });
          return;
        }
        if (mode === "download" && accList.length === 0) {
          setResult({ ok: false, message: "下载模式需要填 SRA accession" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          {/* 扫描状态 */}
          <Card>
            <div className="text-xs font-medium mb-2 flex items-center justify-between">
              <span>当前 raw/ 目录状态</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => refetchScan()}
                disabled={scanLoading}
              >
                重新扫描
              </Button>
            </div>
            <div className="text-xs space-y-1">
              <div>
                <code className="font-mono">{scan?.scan_dir || joinPath(project.workdir, "00_raw")}</code>
              </div>
              {scanLoading ? (
                <div className="text-ink-faint">扫描中...</div>
              ) : (
                <div className="space-y-0.5 mt-1">
                  <div>
                    <strong>{sraFiles.length}</strong> 个 .sra 文件
                    {hasSra && (
                      <span className="text-ink-faint ml-1">
                        (例: {Path1(sraFiles[0]).name})
                      </span>
                    )}
                  </div>
                  <div>
                    <strong>{fastqFiles.length}</strong> 个 fastq 文件
                    {hasFastq && (
                      <span className="text-ink-faint ml-1">
                        (例: {Path1(fastqFiles[0]).name})
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>
          </Card>

          {/* 模式选择 */}
          <Field label="操作模式">
            <div className="flex gap-2">
              <button
                onClick={() => {
                  setMode("scan");
                  setUserPickedMode(true);
                }}
                disabled={!hasSra}
                className={`flex-1 px-3 py-2 text-xs rounded border text-left ${
                  mode === "scan"
                    ? "bg-accent/10 border-accent text-ink"
                    : "border-bg-muted hover:border-ink-faint"
                } ${!hasSra ? "opacity-50 cursor-not-allowed" : ""}`}
              >
                <div className="font-medium">解压本地 SRA</div>
                <div className="text-ink-faint mt-0.5">
                  {hasSra
                    ? `扫描 raw/ 找到的 ${sraFiles.length} 个 .sra,解压成 fastq.gz`
                    : "raw/ 目录里没有 .sra,这个模式不可用"}
                </div>
              </button>
              <button
                onClick={() => {
                  setMode("download");
                  setUserPickedMode(true);
                }}
                className={`flex-1 px-3 py-2 text-xs rounded border text-left ${
                  mode === "download"
                    ? "bg-accent/10 border-accent text-ink"
                    : "border-bg-muted hover:border-ink-faint"
                }`}
              >
                <div className="font-medium">从 SRA 下载</div>
                <div className="text-ink-faint mt-0.5">
                  填 accession 列表,prefetch 下载后解压
                </div>
              </button>
            </div>
          </Field>

          {/* 模式特定的字段 */}
          {mode === "download" && (
            <Field
              label="SRA Accession 列表"
              required
              hint={`一行一个,或用空格、逗号、分号分隔。当前 ${accList.length} 个。`}
            >
              <Textarea
                value={accessions}
                onChange={(e) => setAccessions(e.target.value)}
                placeholder="SRR1234567&#10;SRR1234568"
                rows={5}
                className="font-mono text-xs"
              />
            </Field>
          )}

          {mode === "scan" && hasFastq && (
            <Banner variant="info">
              <div className="text-xs">
                目录里也有 {fastqFiles.length} 个 fastq 文件 — 已存在的 fastq 不会被覆盖。
              </div>
            </Banner>
          )}

          <Field label="并发线程数" hint="fasterq-dump 解压用,默认 8">
            <NumberInput value={threads} min={1} max={32} onChange={setThreads} />
          </Field>
        </>
      }
    />
  );
}

// 路径工具:从绝对路径取 basename
function Path1(p: string) {
  const parts = p.split(/[/\\]/);
  return { name: parts[parts.length - 1] };
}

// ─── FastQC ───

function FastqcForm({ project, initialTarget }: { project: any; initialTarget?: "raw" | "trimmed" }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.fastqc || {};
  const [target, setTarget] = useState<"raw" | "trimmed">(
    initialTarget || (prev.target === "trimmed" ? "trimmed" : "raw")
  );
  const [files, setFiles] = useState<string[]>(prev.fastq_files || []);
  const [threads, setThreads] = useState<number>(prev.threads || 4);

  // 用 scanSra 扫 raw 下的 fastq;trimmed 用 scanSamples 拿 r1/r2 平铺
  const { data: rawScan } = useQuery({
    queryKey: ["scan-sra-fastqc", project.id],
    queryFn: () => coreApi.scanSra(project.id),
  });
  const { data: trimmedScan } = useQuery({
    queryKey: ["scan-samples-fastqc-trimmed", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "trimmed"),
  });

  // 当用户切换 target,自动重填文件列表(除非 prev 有保存且匹配)
  useEffect(() => {
    if (target === "raw") {
      const raw_files = rawScan?.fastq_files || [];
      if (raw_files.length > 0) setFiles(raw_files);
    } else {
      // trimmed:拿 sample.r1/r2 拼成扁平列表
      const tfiles: string[] = [];
      for (const s of trimmedScan?.samples || []) {
        if (s.r1) tfiles.push(s.r1);
        if (s.r2) tfiles.push(s.r2);
      }
      if (tfiles.length > 0) setFiles(tfiles);
    }
    // eslint-disable-next-line
  }, [target, rawScan?.fastq_files?.length, trimmedScan?.samples?.length]);

  // 输出目录:qc/raw/ 或 qc/trimmed/
  const outputPath = joinPath(project.workdir, "01_qc", target);
  const summary_label = target;  // raw/trimmed

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () =>
      rnaseqApi.submitFastqc({
        project_id: project.id,
        output_path: outputPath,
        params: {
          fastq_files: files,
          // 这个字段语义是"并行数"(同时跑几个 fastqc),不是单进程线程数。
          // 以前同时塞了 threads 和 parallel 两个键,容易让人误解;现在只发 parallel。
          parallel: threads,
          summary_label,
        } as any,
      }),
    () =>
      coreApi.setUpstreamParams(project.id, "fastqc", {
        target,
        fastq_files: files,
        threads,
      }),
    qc
  );

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (files.length === 0) {
          setResult({ ok: false, message: "请至少选 1 个 fastq 文件" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          <Field label="目标阶段" hint="raw 看原始数据,trimmed 看 fastp 过滤后效果">
            <div className="flex gap-2">
              <button
                onClick={() => setTarget("raw")}
                className={`flex-1 px-3 py-2 text-xs rounded border ${
                  target === "raw"
                    ? "bg-accent/10 border-accent"
                    : "border-bg-muted hover:border-ink-faint"
                }`}
              >
                <div className="font-medium">raw/(过滤前)</div>
                <div className="text-ink-faint mt-0.5">
                  原始下载的 fastq
                </div>
              </button>
              <button
                onClick={() => setTarget("trimmed")}
                className={`flex-1 px-3 py-2 text-xs rounded border ${
                  target === "trimmed"
                    ? "bg-accent/10 border-accent"
                    : "border-bg-muted hover:border-ink-faint"
                }`}
              >
                <div className="font-medium">trimmed/(过滤后)</div>
                <div className="text-ink-faint mt-0.5">
                  fastp 处理后的 fastq
                </div>
              </button>
            </div>
          </Field>
          <Banner variant="info">
            <div className="text-xs">
              输出位置:<code>{outputPath}/</code>
              <br />
              汇总文件: <code>fastqc_{summary_label}_summary.tsv</code>
              (所有样本一份,看 PASS/WARN/FAIL)
            </div>
          </Banner>
          <MultiPathPicker
            label="FASTQ 文件"
            required
            values={files}
            onChange={setFiles}
            filters={[
              {
                name: "FASTQ",
                extensions: ["fq", "fq.gz", "fastq", "fastq.gz"],
              },
            ]}
            hint={`默认从 ${project.workdir}/${target}/ 自动填入`}
          />
          <Field label="并行数(同时跑几个 FastQC)">
            <NumberInput value={threads} min={1} max={16} onChange={setThreads} />
          </Field>
        </>
      }
    />
  );
}

// ─── fastp ───

function FastpForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.fastp || {};
  const [samplesText, setSamplesText] = useState<string>(prev.samples_text || "");
  const [q, setQ] = useState<number>(prev.qualified_quality_phred || 15);
  const [u, setU] = useState<number>(prev.unqualified_percent_limit || 40);
  const [L, setL] = useState<number>(prev.length_required || 30);
  const [adapter1, setAdapter1] = useState<string>(prev.adapter_sequence_r1 || "");
  const [adapter2, setAdapter2] = useState<string>(prev.adapter_sequence_r2 || "");
  const [threads, setThreads] = useState<number>(prev.threads || 4);

  // 扫 raw/ 看有什么样本
  const { data: scan, refetch: refetchScan } = useQuery({
    queryKey: ["scan-samples-raw", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "raw"),
  });

  // 自动预填:扫描结果到了 + 用户没填过(prev 空且 samplesText 空) → 自动填入
  useEffect(() => {
    if (
      scan?.samples &&
      scan.samples.length > 0 &&
      !samplesText &&
      !prev.samples_text
    ) {
      const text = scan.samples
        .map((s: any) => {
          const parts = [s.name, s.r1];
          if (s.r2) parts.push(s.r2);
          return parts.join(" ");
        })
        .join("\n");
      setSamplesText(text);
    }
    // eslint-disable-next-line
  }, [scan]);

  function applyScannedSamples() {
    if (!scan?.samples || scan.samples.length === 0) return;
    const text = scan.samples
      .map((s: any) => {
        const parts = [s.name, s.r1];
        if (s.r2) parts.push(s.r2);
        return parts.join(" ");
      })
      .join("\n");
    setSamplesText(text);
  }

  const samples = parseSamplesText(samplesText);

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () =>
      rnaseqApi.submitFastp({
        project_id: project.id,
        output_path: joinPath(project.workdir, "02_trimmed"),
        params: {
          samples,
          qualified_quality_phred: q,
          unqualified_percent_limit: u,
          length_required: L,
          adapter_sequence_r1: adapter1,
          adapter_sequence_r2: adapter2,
          threads,
        },
      }),
    () =>
      coreApi.setUpstreamParams(project.id, "fastp", {
        samples_text: samplesText,
        qualified_quality_phred: q,
        unqualified_percent_limit: u,
        length_required: L,
        adapter_sequence_r1: adapter1,
        adapter_sequence_r2: adapter2,
        threads,
      }),
    qc
  );

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (samples.length === 0) {
          setResult({ ok: false, message: "请填至少 1 个样本" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          <Banner variant="info">
            <div className="text-xs">
              输出位置:<code>{project.workdir}/trimmed/</code>
            </div>
          </Banner>
          <Card>
            <div className="text-xs font-medium mb-2 flex items-center justify-between">
              <span>从 raw/ 自动识别样本</span>
              <Button size="sm" variant="ghost" onClick={() => refetchScan()}>
                重新扫描
              </Button>
            </div>
            <div className="text-xs space-y-0.5">
              {scan?.samples && scan.samples.length > 0 ? (
                <>
                  <div className="text-ink-muted mb-1">
                    检测到 {scan.samples.length} 个样本:
                  </div>
                  {scan.samples.slice(0, 5).map((s: any) => (
                    <div key={s.name} className="font-mono text-ink-faint">
                      {s.name} {s.r2 ? "(双端)" : "(单端)"}
                    </div>
                  ))}
                  {scan.samples.length > 5 && (
                    <div className="text-ink-faint">
                      ...另 {scan.samples.length - 5} 个
                    </div>
                  )}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={applyScannedSamples}
                    className="mt-2"
                  >
                    把这 {scan.samples.length} 个样本填入下方
                  </Button>
                </>
              ) : (
                <div className="text-ink-faint">
                  raw/ 目录里没找到 fastq 文件。先跑 SRA 处理产出 fastq,或者在下方手动填路径。
                </div>
              )}
            </div>
          </Card>

          <Field
            label="样本列表"
            required
            hint={
              `每行一个: 样本名 r1路径 [r2路径]。识别到 ${samples.length} 个。`
            }
          >
            <Textarea
              value={samplesText}
              onChange={(e) => setSamplesText(e.target.value)}
              placeholder={`S1 ${project.workdir}/raw/SRR123_1.fastq.gz ${project.workdir}/raw/SRR123_2.fastq.gz`}
              rows={6}
              className="font-mono text-xs"
            />
          </Field>
          <div className="border-t border-bg-muted pt-3">
            <div className="text-xs font-medium text-ink-muted mb-3">过滤参数</div>
            <div className="grid grid-cols-3 gap-3">
              <Field label="quality (-q)">
                <NumberInput value={q} onChange={setQ} />
              </Field>
              <Field label="unqualified % (-u)">
                <NumberInput value={u} onChange={setU} />
              </Field>
              <Field label="min length (-l)">
                <NumberInput value={L} onChange={setL} />
              </Field>
            </div>
            <div className="grid grid-cols-2 gap-3 mt-3">
              <Field label="接头 R1(空 = 自动)">
                <Input value={adapter1} onChange={(e) => setAdapter1(e.target.value)} />
              </Field>
              <Field label="接头 R2(空 = 自动)">
                <Input value={adapter2} onChange={(e) => setAdapter2(e.target.value)} />
              </Field>
            </div>
            <Field label="线程数">
              <NumberInput value={threads} min={1} onChange={setThreads} />
            </Field>
          </div>
        </>
      }
    />
  );
}

function parseSamplesText(text: string): { name: string; r1: string; r2?: string }[] {
  return text
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(/\s+/);
      if (parts.length < 2) return null;
      return {
        name: parts[0],
        r1: parts[1],
        ...(parts[2] ? { r2: parts[2] } : {}),
      };
    })
    .filter(Boolean) as any;
}

// ─── STAR Index ───

function StarIndexForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.star_index || {};
  const [threads, setThreads] = useState<number>(prev.threads || 8);
  // 默认"auto":扫读长后自动决定 overhang 列表
  // 用户可切到"manual",手动输入 overhang 列表(逗号分隔)
  const [mode, setMode] = useState<"auto" | "manual">(
    prev.mode === "manual" ? "manual" : "auto"
  );
  const [manualOverhangsText, setManualOverhangsText] = useState<string>(
    Array.isArray(prev.sjdb_overhangs)
      ? prev.sjdb_overhangs.join(", ")
      : "100"
  );

  const fasta = project.reference_fasta;
  const gtf = project.reference_gtf;

  // 扫读长(自动模式时显示给用户预览)
  const { data: rlScan } = useQuery({
    queryKey: ["scan-readlengths", project.id, "raw"],
    queryFn: () => coreApi.scanReadlengths(project.id, "raw"),
    enabled: mode === "auto",
  });

  // 把"100, 149"解析成 [100, 149]
  function parseManualOverhangs(): number[] {
    return manualOverhangsText
      .split(",")
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => !isNaN(n) && n > 0);
  }

  const detectedOverhangs = rlScan?.unique_overhangs || [];
  const willBuildOverhangs = mode === "auto"
    ? detectedOverhangs
    : parseManualOverhangs();

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () =>
      rnaseqApi.submitStarIndex({
        project_id: project.id,
        output_path: joinPath(project.workdir, "03_star_index"),
        params: {
          fasta,
          gtf: gtf || undefined,
          threads,
          sjdb_overhangs: mode === "auto"
            ? "auto"
            : parseManualOverhangs(),
          sample_fastq_dir: mode === "auto"
            ? joinPath(project.workdir, "00_raw")
            : undefined,
        } as any,
      }),
    () =>
      coreApi.setUpstreamParams(project.id, "star_index", {
        threads,
        mode,
        sjdb_overhangs: mode === "manual" ? parseManualOverhangs() : "auto",
      }),
    qc
  );

  if (!fasta) return <MissingReferenceWarning field="fasta" projectId={project.id} />;

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (mode === "manual" && parseManualOverhangs().length === 0) {
          setResult({ ok: false, message: "manual 模式下要至少 1 个有效 sjdbOverhang" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          <Banner variant="info">
            <div className="text-xs space-y-1">
              <div>
                FASTA: <code>{fasta}</code>
              </div>
              <div>
                GTF: {gtf ? <code>{gtf}</code> : <span className="text-ink-faint">未设置(可选,但推荐)</span>}
              </div>
              <div>
                输出: <code>{project.workdir}/star_index/&lt;overhang&gt;/</code>
                (每个 overhang 一个子目录)
              </div>
            </div>
          </Banner>

          <Field
            label="sjdbOverhang 策略"
            hint="混合数据(不同读长样本)时,A 模式会自动给每个读长建独立索引"
          >
            <div className="flex gap-2">
              <button
                onClick={() => setMode("auto")}
                className={`flex-1 px-3 py-2 text-xs rounded border text-left ${
                  mode === "auto"
                    ? "bg-accent/10 border-accent"
                    : "border-bg-muted hover:border-ink-faint"
                }`}
              >
                <div className="font-medium">A: 自动(扫 raw/)</div>
                <div className="text-ink-faint mt-0.5">
                  扫 raw/ 看读长分布,unique 后每个读长建一个索引
                </div>
              </button>
              <button
                onClick={() => setMode("manual")}
                className={`flex-1 px-3 py-2 text-xs rounded border text-left ${
                  mode === "manual"
                    ? "bg-accent/10 border-accent"
                    : "border-bg-muted hover:border-ink-faint"
                }`}
              >
                <div className="font-medium">手动指定</div>
                <div className="text-ink-faint mt-0.5">
                  逗号分隔多个 overhang,例 "99, 149"
                </div>
              </button>
            </div>
          </Field>

          {mode === "auto" ? (
            <Card>
              <div className="text-xs font-medium mb-2">从 raw/ 扫到的读长分布</div>
              {!rlScan ? (
                <div className="text-xs text-ink-faint">扫描中...</div>
              ) : rlScan.records.length === 0 ? (
                <div className="text-xs text-ink-faint">
                  raw/ 还没有 fastq。请先解压 SRA 或上传 fastq。
                </div>
              ) : (
                <>
                  <div className="text-xs space-y-0.5 max-h-40 overflow-y-auto mb-2">
                    {rlScan.records.map((r, i) => (
                      <div key={i} className="font-mono">
                        <code className="text-ink">{r.sample}</code>
                        <span className="text-ink-faint">
                          {" "}({r.files.length} 文件)
                        </span>
                        {" "}→ 实际 {r.raw_read_length}bp
                        {r.raw_read_length !== r.read_length && (
                          <span className="text-ink-faint">
                            {" → 归档 "}
                            <strong>{r.read_length}bp</strong>
                          </span>
                        )}
                        {r.raw_read_length === r.read_length && (
                          <strong> = {r.read_length}bp</strong>
                        )}
                        {" (overhang "}
                        <strong>{r.sjdb_overhang}</strong>)
                      </div>
                    ))}
                  </div>
                  <div className="text-xs text-ink">
                    将构建 <strong>{detectedOverhangs.length}</strong> 个索引,
                    sjdbOverhang = [{detectedOverhangs.join(", ")}]
                  </div>
                </>
              )}
            </Card>
          ) : (
            <Field label="sjdbOverhang 列表" hint="逗号分隔,例 99, 149">
              <Input
                value={manualOverhangsText}
                onChange={(e) => setManualOverhangsText(e.target.value)}
                placeholder="99, 149"
              />
              <div className="text-xs text-ink-faint mt-1">
                解析为: [{parseManualOverhangs().join(", ") || "(无效)"}]
              </div>
            </Field>
          )}

          <Field label="线程数">
            <NumberInput value={threads} min={1} onChange={setThreads} />
          </Field>

          {willBuildOverhangs.length > 1 && (
            <Banner variant="warning">
              <div className="text-xs">
                ⚠️ 将构建 {willBuildOverhangs.length} 个索引(每个约 30-50 GB,
                每个 30 分钟+)。总共需要 {willBuildOverhangs.length}× 时间和空间。
              </div>
            </Banner>
          )}
        </>
      }
    />
  );
}

// ─── STAR Align ───

function StarAlignForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.star_align || {};
  const [samplesText, setSamplesText] = useState<string>(prev.samples_text || "");
  const [threads, setThreads] = useState<number>(prev.threads || 8);

  // 扫 trimmed/(优先)和 raw/(兜底)
  const { data: scanTrimmed, refetch: refetchTrimmed } = useQuery({
    queryKey: ["scan-samples-trimmed", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "trimmed"),
  });
  const { data: scanRaw } = useQuery({
    queryKey: ["scan-samples-raw-for-align", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "raw"),
  });

  // 优先用 trimmed,如果空再用 raw
  const samplesAvailable =
    (scanTrimmed?.samples && scanTrimmed.samples.length > 0)
      ? scanTrimmed.samples
      : scanRaw?.samples || [];
  const sourceLabel =
    (scanTrimmed?.samples && scanTrimmed.samples.length > 0)
      ? "trimmed/"
      : "raw/";

  // 自动预填:扫描有结果且用户没填过 → 自动填入
  useEffect(() => {
    if (
      samplesAvailable.length > 0 &&
      !samplesText &&
      !prev.samples_text
    ) {
      const text = samplesAvailable
        .map((s: any) => {
          const parts = [s.name, s.r1];
          if (s.r2) parts.push(s.r2);
          return parts.join(" ");
        })
        .join("\n");
      setSamplesText(text);
    }
    // eslint-disable-next-line
  }, [samplesAvailable.length]);

  function applyScannedSamples() {
    if (samplesAvailable.length === 0) return;
    const text = samplesAvailable
      .map((s: any) => {
        const parts = [s.name, s.r1];
        if (s.r2) parts.push(s.r2);
        return parts.join(" ");
      })
      .join("\n");
    setSamplesText(text);
  }

  const samples = parseSamplesText(samplesText);
  const indexRoot = joinPath(project.workdir, "03_star_index");

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () =>
      rnaseqApi.submitStarAlign({
        project_id: project.id,
        output_path: joinPath(project.workdir, "04_aligned"),
        params: { index_root: indexRoot, samples, threads },
      }),
    () =>
      coreApi.setUpstreamParams(project.id, "star_align", {
        samples_text: samplesText,
        threads,
      }),
    qc
  );

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (samples.length === 0) {
          setResult({ ok: false, message: "请填至少 1 个样本" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          <Banner variant="info">
            <div className="text-xs space-y-1">
              <div>
                索引根目录: <code>{indexRoot}</code>(自动按读长选合适的子索引)
              </div>
              <div>
                输出: <code>{project.workdir}/aligned/</code>
              </div>
            </div>
          </Banner>

          <Card>
            <div className="text-xs font-medium mb-2 flex items-center justify-between">
              <span>从 {sourceLabel} 自动识别样本</span>
              <Button size="sm" variant="ghost" onClick={() => refetchTrimmed()}>
                重新扫描
              </Button>
            </div>
            <div className="text-xs">
              {samplesAvailable.length > 0 ? (
                <>
                  <div className="text-ink-muted mb-1">
                    检测到 {samplesAvailable.length} 个样本
                    (源:{sourceLabel})
                  </div>
                  {samplesAvailable.slice(0, 5).map((s: any) => (
                    <div key={s.name} className="font-mono text-ink-faint">
                      {s.name} {s.r2 ? "(双端)" : "(单端)"}
                    </div>
                  ))}
                  {samplesAvailable.length > 5 && (
                    <div className="text-ink-faint">
                      ...另 {samplesAvailable.length - 5} 个
                    </div>
                  )}
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={applyScannedSamples}
                    className="mt-2"
                  >
                    把这 {samplesAvailable.length} 个样本填入
                  </Button>
                </>
              ) : (
                <div className="text-ink-faint">
                  trimmed/ 和 raw/ 都没找到样本。请先跑 fastp 或 SRA 处理。
                </div>
              )}
            </div>
          </Card>

          <Field
            label="样本列表"
            required
            hint={`每行: 样本名 r1 [r2]。识别到 ${samples.length} 个。`}
          >
            <Textarea
              value={samplesText}
              onChange={(e) => setSamplesText(e.target.value)}
              placeholder={`S1 ${project.workdir}/trimmed/S1/S1.clean_1.fq.gz ${project.workdir}/trimmed/S1/S1.clean_2.fq.gz`}
              rows={6}
              className="font-mono text-xs"
            />
          </Field>
          <Field label="线程数">
            <NumberInput value={threads} min={1} onChange={setThreads} />
          </Field>
        </>
      }
    />
  );
}

// ─── featureCounts ───

function FeatureCountsForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.feature_counts || {};
  const [bams, setBams] = useState<string[]>(prev.bam_files || []);
  const [strand, setStrand] = useState<number>(prev.strand ?? 0);
  const [threads, setThreads] = useState<number>(prev.threads || 8);
  const [mergeMsg, setMergeMsg] = useState<string | null>(null);

  // 扫 aligned/ 找 BAM
  const { data: scanAligned, refetch: refetchAligned } = useQuery({
    queryKey: ["scan-samples-aligned", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "aligned"),
  });

  // 自动预填 BAM(扫到 + 用户没填过)
  useEffect(() => {
    if (
      scanAligned?.bams &&
      scanAligned.bams.length > 0 &&
      bams.length === 0 &&
      !(prev.bam_files && prev.bam_files.length > 0)
    ) {
      setBams(scanAligned.bams);
    }
    // eslint-disable-next-line
  }, [scanAligned?.bams?.length]);

  const gtf = project.reference_gtf;

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () =>
      rnaseqApi.submitFeatureCounts({
        project_id: project.id,
        output_path: joinPath(project.workdir, "05_counts"),
        params: { bam_files: bams, gtf, strand, threads } as any,
      }),
    () =>
      coreApi.setUpstreamParams(project.id, "feature_counts", {
        bam_files: bams,
        strand,
        threads,
      }),
    qc
  );

  // 合并 counts mutation
  const mergeMutation = useMutation({
    mutationFn: () =>
      rnaseqApi.submitMergeCounts({
        project_id: project.id,
        output_path: joinPath(project.workdir, "05_counts"),
        params: {
          counts_dir: joinPath(project.workdir, "05_counts"),
          output_name: "counts_merged.tsv",
        },
      }),
    onSuccess: (job) => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setMergeMsg(
        `合并任务已提交 (id=${job.id})。完成后 counts_merged.tsv 会出现在 counts/ 下。`
      );
    },
    onError: (e) => {
      setMergeMsg(`合并失败: ${extractError(e)}`);
    },
  });

  if (!gtf) return <MissingReferenceWarning field="gtf" projectId={project.id} />;

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (bams.length === 0) {
          setResult({ ok: false, message: "请选 BAM 文件" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          <Card>
            <div className="text-xs font-medium mb-2">
              合并已有 counts 结果
            </div>
            <div className="text-xs text-ink-muted mb-2">
              扫 counts/ 下所有 *.tsv,以 gene_id outer-join 合并成
              counts_merged.tsv(用于后续 normalize / 差异分析)
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  setMergeMsg(null);
                  mergeMutation.mutate();
                }}
                disabled={mergeMutation.isPending}
              >
                {mergeMutation.isPending ? "提交中..." : "合并 counts"}
              </Button>
              {mergeMsg && (
                <span className="text-xs text-ink-muted">{mergeMsg}</span>
              )}
            </div>
          </Card>

          <Banner variant="info">
            <div className="text-xs space-y-1">
              <div>
                GTF: <code>{gtf}</code>
              </div>
              <div>
                输出: <code>{project.workdir}/counts/</code>(每个样本一个 .tsv,重跑同名样本会覆盖)
              </div>
            </div>
          </Banner>

          <Card>
            <div className="text-xs font-medium mb-2 flex items-center justify-between">
              <span>从 aligned/ 自动识别 BAM</span>
              <Button size="sm" variant="ghost" onClick={() => refetchAligned()}>
                重新扫描
              </Button>
            </div>
            <div className="text-xs">
              {scanAligned?.bams && scanAligned.bams.length > 0 ? (
                <>
                  <div className="text-ink-muted mb-1">
                    检测到 {scanAligned.bams.length} 个 BAM 文件
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setBams(scanAligned.bams)}
                  >
                    把这 {scanAligned.bams.length} 个全选
                  </Button>
                </>
              ) : (
                <div className="text-ink-faint">
                  aligned/ 目录里没找到 BAM。请先跑 STAR 比对。
                </div>
              )}
            </div>
          </Card>

          <MultiPathPicker
            label="BAM 文件"
            required
            values={bams}
            onChange={setBams}
            filters={[{ name: "BAM", extensions: ["bam"] }]}
            hint={`已选 ${bams.length} 个 BAM`}
          />
          <div className="grid grid-cols-2 gap-3">
            <Field label="链特异性 (-s)" hint="0=不区分,1=正,2=反">
              <Select
                value={strand.toString()}
                onChange={(e) => setStrand(parseInt(e.target.value))}
              >
                <option value="0">0 (unstranded)</option>
                <option value="1">1 (stranded)</option>
                <option value="2">2 (reversely stranded)</option>
              </Select>
            </Field>
            <Field label="线程数" hint="featureCounts 上限 64,超过自动 cap">
              <NumberInput value={threads} min={1} max={64} onChange={setThreads} />
            </Field>
          </div>
          <div className="text-xs text-ink-faint">
            ℹ️ 单/双端 自动从 BAM 检测,不需要手动选
          </div>
        </>
      }
    />
  );
}

// ─── Normalize ───

function NewTranscriptsForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.new_transcripts || {};
  const [bams, setBams] = useState<string[]>(prev.bam_files || []);
  const { data: scanAligned } = useQuery({
    queryKey: ["scan-samples-aligned", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "aligned"),
  });
  useEffect(() => {
    if (scanAligned?.bams?.length && bams.length === 0 && !(prev.bam_files && prev.bam_files.length > 0)) {
      setBams(scanAligned.bams);
    }
    // eslint-disable-next-line
  }, [scanAligned?.bams?.length]);
  const gtf = project.reference_gtf;
  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () => rnaseqApi.submitNewTranscripts({
      project_id: project.id,
      output_path: joinPath(project.workdir, "08_new_transcripts"),
      params: { bam_files: bams, gtf } as any,
    }),
    () => coreApi.setUpstreamParams(project.id, "new_transcripts", { bam_files: bams }),
    qc
  );
  async function addBams() {
    const f = await openDialog({ multiple: true, title: "选择 BAM 文件" });
    if (Array.isArray(f)) setBams((s) => Array.from(new Set([...s, ...(f as string[])])));
    else if (typeof f === "string") setBams((s) => Array.from(new Set([...s, f])));
  }
  if (!gtf) return <MissingReferenceWarning field="gtf" projectId={project.id} />;
  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg} pending={mutation.isPending} result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => { setResult(null); if (bams.length === 0) { setResult({ ok: false, message: "请选 BAM 文件" }); return; } mutation.mutate(); }}
      formContent={
        <>
          <div className="text-xs text-ink-muted">
            StringTie 参考引导组装各样本转录本并合并,再用 gffcompare 标出新转录本,产出 merged.gtf + 新转录本统计。BAM 自动从比对结果扫描。
          </div>
          <Card>
            <div className="flex items-center justify-between">
              <div className="text-sm">已选 <b>{bams.length}</b> 个 BAM</div>
              <Button variant="secondary" size="sm" onClick={addBams}>添加 BAM</Button>
            </div>
            {bams.length > 0 && (
              <div className="mt-2 max-h-32 space-y-0.5 overflow-auto text-xs text-ink-faint">
                {bams.map((b) => (
                  <div key={b} className="flex items-center justify-between gap-2">
                    <span className="truncate">{b.split("/").pop()}</span>
                    <button onClick={() => setBams((s) => s.filter((x) => x !== b))} className="text-ink-faint hover:text-state-failed">✕</button>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      } />
  );
}

function AltSplicingForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.alt_splicing || {};
  const [g1, setG1] = useState<string[]>(prev.bam_files_g1 || []);
  const [g2, setG2] = useState<string[]>(prev.bam_files_g2 || []);
  const [readLen, setReadLen] = useState<number>(prev.read_length || 150);
  const [paired, setPaired] = useState<boolean>(prev.paired ?? true);
  const gtf = project.reference_gtf;
  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () => rnaseqApi.submitAltSplicing({
      project_id: project.id,
      output_path: joinPath(project.workdir, "09_alt_splicing"),
      params: { bam_files_g1: g1, bam_files_g2: g2, gtf, read_length: readLen, paired } as any,
    }),
    () => coreApi.setUpstreamParams(project.id, "alt_splicing", { bam_files_g1: g1, bam_files_g2: g2, read_length: readLen, paired }),
    qc
  );
  async function pick(setter: (u: (s: string[]) => string[]) => void) {
    const f = await openDialog({ multiple: true, title: "选择 BAM 文件" });
    if (Array.isArray(f)) setter((s) => Array.from(new Set([...s, ...(f as string[])])));
    else if (typeof f === "string") setter((s) => Array.from(new Set([...s, f])));
  }
  const renderGroup = (label: string, arr: string[], setter: (u: (s: string[]) => string[]) => void) => (
    <Card>
      <div className="flex items-center justify-between">
        <div className="text-sm">{label}:已选 <b>{arr.length}</b></div>
        <Button variant="secondary" size="sm" onClick={() => pick(setter)}>添加 BAM</Button>
      </div>
      {arr.length > 0 && (
        <div className="mt-2 max-h-24 space-y-0.5 overflow-auto text-xs text-ink-faint">
          {arr.map((b) => (
            <div key={b} className="flex items-center justify-between gap-2">
              <span className="truncate">{b.split("/").pop()}</span>
              <button onClick={() => setter((s) => s.filter((x) => x !== b))} className="text-ink-faint hover:text-state-failed">✕</button>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
  if (!gtf) return <MissingReferenceWarning field="gtf" projectId={project.id} />;
  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg} pending={mutation.isPending} result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => { setResult(null); if (g1.length === 0 || g2.length === 0) { setResult({ ok: false, message: "两组 BAM 都要选" }); return; } mutation.mutate(); }}
      formContent={
        <>
          <div className="text-xs text-ink-muted">
            rMATS 比较两组样本,检出 5 类可变剪接事件(SE/A5SS/A3SS/MXE/RI)。请分别选两组(如对照 vs 处理)的 BAM。
          </div>
          {renderGroup("组 1(对照)", g1, setG1)}
          {renderGroup("组 2(处理)", g2, setG2)}
          <div className="flex items-center gap-4">
            <label className="text-xs text-ink-muted">读长
              <input type="number" value={readLen} onChange={(e) => setReadLen(parseInt(e.target.value) || 150)} className="ml-2 w-20 rounded border border-border bg-bg-surface px-2 py-1 text-sm" />
            </label>
            <label className="flex items-center gap-1.5 text-xs text-ink-muted">
              <input type="checkbox" checked={paired} onChange={(e) => setPaired(e.target.checked)} /> 双端测序
            </label>
          </div>
        </>
      } />
  );
}

function LncrnaForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.lncrna || {};
  const defaultCand = joinPath(project.workdir, "new_transcripts/merged.gtf");
  const [candGtf, setCandGtf] = useState<string>(prev.candidate_gtf || defaultCand);
  const gtf = project.reference_gtf;
  const fasta = project.reference_fasta;
  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () => rnaseqApi.submitLncrna({
      project_id: project.id,
      output_path: joinPath(project.workdir, "10_lncrna"),
      params: { candidate_gtf: candGtf, gtf, genome_fasta: fasta } as any,
    }),
    () => coreApi.setUpstreamParams(project.id, "lncrna", { candidate_gtf: candGtf }),
    qc
  );
  async function pickGtf() {
    const f = await openDialog({ multiple: false, title: "选择候选转录本 GTF" });
    if (typeof f === "string") setCandGtf(f);
  }
  if (!fasta) return <MissingReferenceWarning field="fasta" projectId={project.id} />;
  if (!gtf) return <MissingReferenceWarning field="gtf" projectId={project.id} />;
  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg} pending={mutation.isPending} result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => { setResult(null); mutation.mutate(); }}
      formContent={
        <>
          <div className="text-xs text-ink-muted">
            用 FEELnc 从候选转录本里过滤 + 判定 lncRNA(编码潜能)并分类。候选 GTF 用"新转录本"步骤产出的 merged.gtf。
          </div>
          <Card>
            <label className="mb-1 block text-xs text-ink-muted">候选转录本 GTF</label>
            <div className="flex gap-2">
              <input value={candGtf} onChange={(e) => setCandGtf(e.target.value)} className="w-full rounded border border-border bg-bg-surface px-2 py-1 text-xs" />
              <Button variant="secondary" size="sm" onClick={pickGtf}>选择</Button>
            </div>
            <div className="mt-1 text-[11px] text-ink-faint">默认指向 new_transcripts/merged.gtf,需先跑完"新转录本"步骤。</div>
          </Card>
        </>
      } />
  );
}

function LibraryQcForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.library_qc || {};
  const [bams, setBams] = useState<string[]>(prev.bam_files || []);

  const { data: scanAligned } = useQuery({
    queryKey: ["scan-samples-aligned", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "aligned"),
  });
  useEffect(() => {
    if (
      scanAligned?.bams?.length &&
      bams.length === 0 &&
      !(prev.bam_files && prev.bam_files.length > 0)
    ) {
      setBams(scanAligned.bams);
    }
    // eslint-disable-next-line
  }, [scanAligned?.bams?.length]);

  const gtf = project.reference_gtf;

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () =>
      rnaseqApi.submitLibraryQc({
        project_id: project.id,
        output_path: joinPath(project.workdir, "07_library_qc"),
        params: { bam_files: bams, gtf } as any,
      }),
    () => coreApi.setUpstreamParams(project.id, "library_qc", { bam_files: bams }),
    qc
  );

  async function addBams() {
    const f = await openDialog({ multiple: true, title: "选择 BAM 文件" });
    if (Array.isArray(f)) setBams((s) => Array.from(new Set([...s, ...f])));
    else if (typeof f === "string") setBams((s) => Array.from(new Set([...s, f])));
  }

  if (!gtf) return <MissingReferenceWarning field="gtf" projectId={project.id} />;

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (bams.length === 0) {
          setResult({ ok: false, message: "请选 BAM 文件" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          <div className="text-xs text-ink-muted">
            用 Qualimap 对每个 BAM 评估文库质量:转录本 5'→3' 覆盖均匀性、reads 基因组来源
            (外显子/内含子/基因间)、测序饱和度——对应报告的"文库质量评估"。BAM 自动从比对结果扫描预填。
          </div>
          <Card>
            <div className="flex items-center justify-between">
              <div className="text-sm">
                已选 <b>{bams.length}</b> 个 BAM
              </div>
              <Button variant="secondary" size="sm" onClick={addBams}>
                添加 BAM
              </Button>
            </div>
            {bams.length > 0 && (
              <div className="mt-2 max-h-32 space-y-0.5 overflow-auto text-xs text-ink-faint">
                {bams.map((b) => (
                  <div key={b} className="flex items-center justify-between gap-2">
                    <span className="truncate">{b.split("/").pop()}</span>
                    <button
                      onClick={() => setBams((s) => s.filter((x) => x !== b))}
                      className="text-ink-faint hover:text-state-failed"
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      }
    />
  );
}

function NormalizeForm({ project }: { project: any }) {
  const qc = useQueryClient();
  const prev = project.upstream_params?.normalize || {};
  const [mode, setMode] = useState<"matrix" | "per_sample">(
    prev.mode || "matrix"
  );
  const [countsFile, setCountsFile] = useState<string>(prev.counts_file || "");
  const [countsFiles, setCountsFiles] = useState<string[]>(
    prev.counts_files || []
  );
  const [methods, setMethods] = useState<("TPM" | "FPKM" | "CPM")[]>(
    prev.methods || ["TPM", "FPKM"]
  );

  const gtf = project.reference_gtf;

  const { mutation, result, setResult, saveOnly, saveMsg } = useStepSubmit(
    () =>
      rnaseqApi.submitNormalize({
        project_id: project.id,
        output_path: joinPath(project.workdir, "06_normalized"),
        params:
          mode === "matrix"
            ? { mode, counts_file: countsFile, gtf: gtf || undefined, methods }
            : { mode, counts_files: countsFiles, gtf: gtf || undefined, methods },
      }),
    () =>
      coreApi.setUpstreamParams(project.id, "normalize", {
        mode,
        counts_file: countsFile,
        counts_files: countsFiles,
        methods,
      }),
    qc
  );

  function toggleMethod(m: "TPM" | "FPKM" | "CPM") {
    setMethods(
      methods.includes(m) ? methods.filter((x) => x !== m) : [...methods, m]
    );
  }

  if (!gtf && (methods.includes("TPM") || methods.includes("FPKM"))) {
    return <MissingReferenceWarning field="gtf" projectId={project.id} />;
  }

  return (
    <SubmitForm onSaveParams={saveOnly} saveMsg={saveMsg}
      pending={mutation.isPending}
      result={result}
      prevParams={Object.keys(prev).length > 0 ? prev : undefined}
      onSubmit={() => {
        setResult(null);
        if (mode === "matrix" && !countsFile) {
          setResult({ ok: false, message: "矩阵模式请选 counts 矩阵文件" });
          return;
        }
        if (mode === "per_sample" && countsFiles.length === 0) {
          setResult({ ok: false, message: "单样本模式请选至少 1 个 counts 文件" });
          return;
        }
        if (methods.length === 0) {
          setResult({ ok: false, message: "请至少选 1 种方法" });
          return;
        }
        mutation.mutate();
      }}
      formContent={
        <>
          <Banner variant="info">
            <div className="text-xs">
              输出: <code>{project.workdir}/normalized/</code>
            </div>
          </Banner>

          <Field label="输入模式">
            <div className="flex gap-2">
              <button
                onClick={() => setMode("matrix")}
                className={`flex-1 px-3 py-2 text-xs rounded border text-left ${
                  mode === "matrix"
                    ? "bg-accent/10 border-accent text-ink"
                    : "border-bg-muted hover:border-ink-faint"
                }`}
              >
                <div className="font-medium">矩阵模式</div>
                <div className="text-ink-faint mt-0.5">
                  输入合并好的 counts 矩阵(多列样本),输出 tpm.tsv/fpkm.tsv 矩阵
                </div>
              </button>
              <button
                onClick={() => setMode("per_sample")}
                className={`flex-1 px-3 py-2 text-xs rounded border text-left ${
                  mode === "per_sample"
                    ? "bg-accent/10 border-accent text-ink"
                    : "border-bg-muted hover:border-ink-faint"
                }`}
              >
                <div className="font-medium">单样本模式</div>
                <div className="text-ink-faint mt-0.5">
                  输入 N 个 SRR.tsv,输出 N 个 SRR.tpm.tsv / SRR.fpkm.tsv
                </div>
              </button>
            </div>
          </Field>

          {mode === "matrix" ? (
            <PathPicker
              label="counts 矩阵文件"
              required
              value={countsFile}
              onChange={setCountsFile}
              filters={[{ name: "Tabular", extensions: ["tsv", "csv", "txt"] }]}
              hint={`从 ${project.workdir}/counts/ 选合并后的 counts_merged.tsv`}
            />
          ) : (
            <MultiPathPicker
              label="单样本 counts 文件"
              required
              values={countsFiles}
              onChange={setCountsFiles}
              filters={[{ name: "Tabular", extensions: ["tsv", "csv", "txt"] }]}
              hint={`从 ${project.workdir}/counts/ 选多个 SRR.tsv`}
            />
          )}

          <Field label="标准化方法">
            <div className="flex gap-2">
              {(["TPM", "FPKM", "CPM"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => toggleMethod(m)}
                  className={`px-3 py-1 text-xs rounded border ${
                    methods.includes(m)
                      ? "bg-accent text-white border-accent"
                      : "border-bg-muted text-ink-muted"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </Field>
        </>
      }
    />
  );
}

// ─── Import counts ───


// ─── 推荐下一步 hint ────────────────────────

function NextStepHint({
  project,
  onJumpTab,
}: {
  project: any;
  onJumpTab: (t: TabId) => void;
}) {
  const { data: scanRaw } = useQuery({
    queryKey: ["scan-samples-raw-hint", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "raw"),
    refetchInterval: 5000,
  });
  const { data: scanTrimmed } = useQuery({
    queryKey: ["scan-samples-trimmed-hint", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "trimmed"),
    refetchInterval: 5000,
  });
  const { data: scanAligned } = useQuery({
    queryKey: ["scan-samples-aligned-hint", project.id],
    queryFn: () => coreApi.scanSamples(project.id, "aligned"),
    refetchInterval: 5000,
  });
  const { data: scanSra } = useQuery({
    queryKey: ["scan-sra-hint", project.id],
    queryFn: () => coreApi.scanSra(project.id),
    refetchInterval: 5000,
  });

  // 决定下一步
  const hasSraFiles = (scanSra?.sra_files || []).length > 0;
  const hasRawFastq = (scanRaw?.samples || []).length > 0;
  const hasTrimmed = (scanTrimmed?.samples || []).length > 0;
  const hasAligned = (scanAligned?.bams || []).length > 0;
  const hasIndex =
    project.reference_fasta && project.reference_gtf;

  let nextTab: TabId | null = null;
  let hint = "";

  if (!hasRawFastq && !hasSraFiles) {
    nextTab = "sra";
    hint = "raw/ 目录还是空的 — 建议从 SRA 下载或添加 fastq 开始";
  } else if (hasSraFiles && !hasRawFastq) {
    nextTab = "sra";
    hint = `检测到 ${scanSra?.sra_files.length} 个 .sra,建议先解压成 fastq`;
  } else if (hasRawFastq && !hasTrimmed) {
    nextTab = "fastp";
    hint = `raw/ 有 ${scanRaw?.samples.length} 个样本,建议下一步:fastp 质量过滤`;
  } else if (hasTrimmed && !hasAligned && hasIndex) {
    nextTab = "star_align";
    hint = `trimmed/ 有 ${scanTrimmed?.samples.length} 个样本,建议下一步:STAR 比对`;
  } else if (hasTrimmed && !hasAligned && !hasIndex) {
    nextTab = "star_index";
    hint = "比对前需要先建 STAR 索引";
  } else if (hasAligned) {
    nextTab = "feature_counts";
    hint = `aligned/ 有 ${scanAligned?.bams.length} 个 BAM,建议下一步:featureCounts 量化`;
  }

  if (!nextTab) return null;

  return (
    <div className="mt-4">
      <Banner variant="info">
        <div className="flex items-center gap-3 text-xs">
          <span className="flex-1">💡 {hint}</span>
          <Button size="sm" variant="secondary" onClick={() => onJumpTab(nextTab!)}>
            去那一步
          </Button>
        </div>
      </Banner>
    </div>
  );
}


// ─── 一键运行按钮 ───

function OneClickPipelineButton({ project }: { project: any }) {
  const qc = useQueryClient();
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(
    null
  );
  const [advOpen, setAdvOpen] = useState(false);
  
  // 高级参数(默认值跟后端 pipeline_upstream_runner.py 里的默认值保持一致)
  const [fastpQ, setFastpQ] = useState(15);
  const [fastpU, setFastpU] = useState(40);
  const [fastpL, setFastpL] = useState(30);
  const [fastpThreadsPerSample, setFastpThreadsPerSample] = useState(4);
  const [fastpParallel, setFastpParallel] = useState(2);
  const [starThreads, setStarThreads] = useState(8);
  const [fcThreads, setFcThreads] = useState(8);
  const [fcStrand, setFcStrand] = useState(0);
  
  const fasta = project.reference_fasta;
  const gtf = project.reference_gtf;
  
  const m = useMutation({
    mutationFn: () =>
      rnaseqApi.submitPipelineUpstream({
        project_id: project.id,
        output_path: project.workdir,
        params: {
          workdir: project.workdir,
          fasta: fasta || undefined,
          gtf: gtf || "",
          fastp: {
            q: fastpQ,
            u: fastpU,
            l: fastpL,
            threads_per_sample: fastpThreadsPerSample,
            parallel: fastpParallel,
          },
          star_align: { threads: starThreads },
          feature_counts: { threads: fcThreads, strand: fcStrand },
        },
      }),
    onSuccess: (j: any) => {
      setResult({
        ok: true,
        message: `一键 pipeline 已提交 (id=${j.id})。会按 SRA→fastp→STAR→featureCounts→合并 顺序跑,完成后 counts/counts_merged.tsv 可用`,
      });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => setResult({ ok: false, message: extractError(e) }),
  });
  
  if (!gtf) {
    return null;  // 没 GTF 没法一键
  }
  
  return (
    <Card className="mt-4 border-accent border-2">
      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-sm font-medium mb-1">一键运行</div>
          <div className="text-xs text-ink-muted">
            自动跑:SRA 解压 → fastp → STAR 比对 → featureCounts → 合并 counts。
            不需要分组信息,跑到 counts_merged.tsv 便停。
          </div>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button variant="ghost" size="sm" onClick={() => setAdvOpen(!advOpen)}>
            {advOpen ? "收起" : "高级参数"}
          </Button>
          <Button onClick={() => { setResult(null); m.mutate(); }}
                  disabled={m.isPending}>
            {m.isPending ? "提交中..." : "一键运行"}
          </Button>
        </div>
      </div>
      
      {advOpen && (
        <div className="mt-4 pt-4 border-t border-bg-muted space-y-3">
          <div className="text-xs font-medium text-ink-muted">fastp 参数</div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="质量分阈值 (q)"
              hint="低于该 phred 值视为低质量碱基,默认 15">
              <NumberInput value={fastpQ} onChange={setFastpQ} min={0} max={40} />
            </Field>
            <Field label="低质量比例上限 % (u)"
              hint="允许低质量碱基占总长的最大百分比,默认 40">
              <NumberInput value={fastpU} onChange={setFastpU} min={0} max={100} />
            </Field>
            <Field label="最短读长 (l)"
              hint="过滤后短于此长度的 read 丢弃,默认 30">
              <NumberInput value={fastpL} onChange={setFastpL} min={0} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="fastp 单样本线程"
              hint="单个 fastp 进程用几个线程,默认 4">
              <NumberInput value={fastpThreadsPerSample}
                onChange={setFastpThreadsPerSample} min={1} max={32} />
            </Field>
            <Field label="fastp 并行样本数"
              hint="同时跑几个 fastp,默认 2(总线程数 = 单样本线程 × 并行数)">
              <NumberInput value={fastpParallel}
                onChange={setFastpParallel} min={1} max={16} />
            </Field>
          </div>
          
          <div className="text-xs font-medium text-ink-muted pt-2">STAR + featureCounts</div>
          <div className="grid grid-cols-3 gap-3">
            <Field label="STAR 比对线程"
              hint="STAR 是单进程多线程,默认 8">
              <NumberInput value={starThreads} onChange={setStarThreads} min={1} max={64} />
            </Field>
            <Field label="featureCounts 线程"
              hint="默认 8">
              <NumberInput value={fcThreads} onChange={setFcThreads} min={1} max={64} />
            </Field>
            <Field label="链特异性 (strand)"
              hint="0=非链特异(默认), 1=正向, 2=反向。dUTP 法测序选 2">
              <Select value={fcStrand}
                onChange={(e) => setFcStrand(parseInt(e.target.value))}>
                <option value={0}>0(非链特异,默认)</option>
                <option value={1}>1(正向)</option>
                <option value={2}>2(反向,dUTP)</option>
              </Select>
            </Field>
          </div>
        </div>
      )}
      
      {result && (
        <Banner variant={result.ok ? "success" : "error"} className="mt-3">
          <div className="text-xs">{result.message}</div>
        </Banner>
      )}
    </Card>
  );
}
