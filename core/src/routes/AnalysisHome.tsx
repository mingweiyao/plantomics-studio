/**
 * 下游分析模块首页(omics-analysis)。
 *
 * 模块本身不内置任何分析代码 —— 所有分析都是 ~/.plantomics/modules/omics-analysis/analyses/
 * 下的自描述 R 脚本(analysis.R 头部声明元数据 + run() 函数)。本页:
 *   - 把已注册的分析渲染成卡片;
 *   - 点"运行"→ 右侧抽屉,按脚本声明的 accepts(输入)+ params 自动生成表单;
 *   - "新增分析"向导:引导用户填元数据 + 贴 R 代码 + 传示例文件/预览图,写进扫描目录;
 *   - 丢进目录的分析重启后仍在(持久)。
 */
import { useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import {
  ArrowLeft,
  Plus,
  RefreshCw,
  Play,
  Trash2,
  FolderOpen,
  FlaskConical,
  Puzzle,
} from "lucide-react";
import { coreApi } from "../lib/api";
import {
  analysisApi,
  type AnalysisManifest,
  type AnalysisParam,
} from "../lib/analysisApi";
import { PageHeader, Card, Button, Banner, Modal, Loading } from "../components/ui";
import { Drawer } from "../components/Drawer";

const CATEGORY_LABEL: Record<string, string> = {
  plot: "图表",
  diff: "差异分析",
  enrich: "富集",
  network: "共表达网络",
  other: "其他",
};

function dataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

export function AnalysisHome() {
  const { id: projectId } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data: project } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => coreApi.getProject(projectId!),
    enabled: !!projectId,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["analyses"],
    queryFn: analysisApi.list,
  });

  const [runFor, setRunFor] = useState<AnalysisManifest | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; msg: string } | null>(null);

  const rescanMut = useMutation({
    mutationFn: analysisApi.rescan,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["analyses"] }),
  });
  const removeMut = useMutation({
    mutationFn: (id: string) => analysisApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["analyses"] }),
  });

  const analyses = data?.analyses ?? [];
  const datasetTypes = data?.dataset_types ?? {};

  const grouped = useMemo(() => {
    const g: Record<string, AnalysisManifest[]> = {};
    for (const a of analyses) (g[a.category] ||= []).push(a);
    return g;
  }, [analyses]);

  if (!project) return <Loading />;

  return (
    <div className="p-6">
      <PageHeader
        title="下游分析"
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
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-2 text-sm text-ink-muted">
            <Puzzle size={16} className="mt-0.5 shrink-0 text-accent" />
            <div>
              这里的每个分析都是一个可插拔的 R 脚本。点"新增分析"把你的 R 代码 + 示例数据 + 预览图加进来,
              它会存到本机的分析目录,<span className="text-ink">下次打开仍在</span>,可反复复用。
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => rescanMut.mutate()}
              disabled={rescanMut.isPending}
            >
              <RefreshCw size={13} className={rescanMut.isPending ? "animate-spin" : ""} />
              刷新
            </Button>
            <Button size="sm" onClick={() => setWizardOpen(true)}>
              <Plus size={14} />
              新增分析
            </Button>
          </div>
        </div>
      </Card>

      {notice && (
        <div className="mt-3">
          <Banner variant={notice.ok ? "success" : "error"}>{notice.msg}</Banner>
        </div>
      )}

      {isLoading ? (
        <Loading />
      ) : analyses.length === 0 ? (
        <div className="mt-10 flex flex-col items-center text-center text-ink-faint">
          <FlaskConical size={28} className="mb-3" />
          <div className="text-sm text-ink-muted">还没有任何分析</div>
          <div className="mt-1 text-xs">点右上角"新增分析",按向导加入你的第一个 R 分析。</div>
        </div>
      ) : (
        <div className="mt-5 space-y-6">
          {Object.entries(grouped).map(([cat, items]) => (
            <div key={cat}>
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-faint">
                {CATEGORY_LABEL[cat] || cat}
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {items.map((a) => (
                  <AnalysisCard
                    key={a.id}
                    a={a}
                    onRun={() => setRunFor(a)}
                    onRemove={() => {
                      if (confirm(`删除分析「${a.label}」?`)) removeMut.mutate(a.id);
                    }}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 运行抽屉 */}
      <Drawer
        open={!!runFor}
        onClose={() => setRunFor(null)}
        title={runFor?.label || "运行分析"}
        subtitle={runFor ? CATEGORY_LABEL[runFor.category] || runFor.category : undefined}
        icon={<Play size={18} />}
        accent="rgb(var(--accent))"
      >
        {runFor && (
          <RunPanel
            analysis={runFor}
            project={project}
            datasetTypes={datasetTypes}
            onSubmitted={(jobId) => {
              setRunFor(null);
              setNotice({ ok: true, msg: `已提交:${runFor.label}(任务 ${jobId})。进度见任务面板。` });
              qc.invalidateQueries({ queryKey: ["jobs"] });
            }}
            onError={(msg) => setNotice({ ok: false, msg })}
          />
        )}
      </Drawer>

      {/* 新增向导 */}
      <AddAnalysisWizard
        open={wizardOpen}
        datasetTypes={datasetTypes}
        onClose={() => setWizardOpen(false)}
        onCreated={(man) => {
          setWizardOpen(false);
          setNotice({ ok: true, msg: `已新增分析:${man.label}` });
          qc.invalidateQueries({ queryKey: ["analyses"] });
        }}
      />
    </div>
  );
}

// ── 分析卡片 ──
function AnalysisCard({
  a,
  onRun,
  onRemove,
}: {
  a: AnalysisManifest;
  onRun: () => void;
  onRemove: () => void;
}) {
  const { data: pv } = useQuery({
    queryKey: ["analysis-preview", a.id],
    queryFn: () => analysisApi.preview(a.id),
    enabled: a.has_preview,
    staleTime: 5 * 60 * 1000,
  });
  const previewSrc = pv?.preview ?? null;

  return (
    <div className="flex flex-col rounded-xl border border-border bg-bg-surface p-4 shadow-card">
      {a.has_preview && previewSrc && (
        <div className="mb-3 overflow-hidden rounded-lg border border-border bg-bg-muted">
          <img
            src={previewSrc}
            alt={`${a.label} 输出预览`}
            className="h-28 w-full object-cover"
          />
        </div>
      )}
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium text-ink">{a.label}</div>
        <button
          onClick={onRemove}
          className="text-ink-faint transition hover:text-state-failed"
          title="删除"
        >
          <Trash2 size={13} />
        </button>
      </div>
      {a.description && (
        <div className="mt-1 line-clamp-2 text-xs text-ink-muted">{a.description}</div>
      )}
      <div className="mt-3 flex flex-wrap gap-1.5 text-[10px] text-ink-faint">
        {a.accepts.map((t) => (
          <span key={t} className="rounded bg-bg-muted px-1.5 py-0.5">
            需要 {t}
          </span>
        ))}
        {a.params.length > 0 && (
          <span className="rounded bg-bg-muted px-1.5 py-0.5">{a.params.length} 个参数</span>
        )}
      </div>
      <div className="mt-3 flex-1" />
      <Button size="sm" className="mt-2 w-full" onClick={onRun}>
        <Play size={13} />
        运行
      </Button>
    </div>
  );
}

// ── 运行面板(抽屉里):输入选择 + 参数自动表单 ──
function RunPanel({
  analysis,
  project,
  datasetTypes,
  onSubmitted,
  onError,
}: {
  analysis: AnalysisManifest;
  project: any;
  datasetTypes: Record<string, string>;
  onSubmitted: (jobId: string) => void;
  onError: (msg: string) => void;
}) {
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [params, setParams] = useState<Record<string, any>>(() => {
    const p: Record<string, any> = {};
    for (const pr of analysis.params) p[pr.key] = pr.default;
    return p;
  });

  const runMut = useMutation({
    mutationFn: () =>
      analysisApi.run({
        analysis_id: analysis.id,
        project_id: project.id,
        output_path: project.workdir,
        inputs,
        params,
      }),
    onSuccess: (j) => onSubmitted(j.id),
    onError: (e: any) => onError(e?.message || "提交失败"),
  });

  async function pickFile(type: string) {
    const f = await openDialog({ multiple: false, title: `选择 ${type} 文件` });
    if (typeof f === "string") setInputs((s) => ({ ...s, [type]: f }));
  }

  const inputCls =
    "w-full rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent";

  return (
    <div className="space-y-5">
      {/* 输入 */}
      {analysis.accepts.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium text-ink-faint">输入数据</div>
          <div className="space-y-3">
            {analysis.accepts.map((t) => (
              <div key={t}>
                <label className="mb-1 block text-xs text-ink-muted">
                  {datasetTypes[t] || t}
                </label>
                <div className="flex gap-2">
                  <input
                    value={inputs[t] || ""}
                    onChange={(e) => setInputs((s) => ({ ...s, [t]: e.target.value }))}
                    className={inputCls}
                    placeholder={`选择 ${t} 文件路径`}
                  />
                  <Button variant="secondary" size="sm" onClick={() => pickFile(t)}>
                    <FolderOpen size={13} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 参数自动表单 */}
      {analysis.params.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium text-ink-faint">参数</div>
          <div className="space-y-3">
            {analysis.params.map((p) => (
              <ParamField
                key={p.key}
                p={p}
                value={params[p.key]}
                onChange={(v) => setParams((s) => ({ ...s, [p.key]: v }))}
              />
            ))}
          </div>
        </div>
      )}

      <Button
        className="w-full"
        disabled={runMut.isPending}
        onClick={() => runMut.mutate()}
      >
        <Play size={14} />
        {runMut.isPending ? "提交中…" : "运行分析"}
      </Button>
    </div>
  );
}

function ParamField({
  p,
  value,
  onChange,
}: {
  p: AnalysisParam;
  value: any;
  onChange: (v: any) => void;
}) {
  const inputCls =
    "w-full rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent";

  if (p.type === "bool") {
    return (
      <label className="flex items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        {p.label}
        {p.help && <span className="text-xs text-ink-faint">— {p.help}</span>}
      </label>
    );
  }
  return (
    <div>
      <label className="mb-1 block text-xs text-ink-muted">
        {p.label}
        {p.help && <span className="ml-1 text-ink-faint">({p.help})</span>}
      </label>
      {p.type === "select" ? (
        <select
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
        >
          {(p.options || []).map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      ) : p.type === "number" || p.type === "int" ? (
        <input
          type="number"
          value={value ?? ""}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") return onChange(undefined);
            onChange(p.type === "int" ? parseInt(raw, 10) : parseFloat(raw));
          }}
          className={inputCls}
        />
      ) : (
        <input
          type="text"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className={inputCls}
        />
      )}
    </div>
  );
}

// ── 新增分析向导 ──
function AddAnalysisWizard({
  open,
  datasetTypes,
  onClose,
  onCreated,
}: {
  open: boolean;
  datasetTypes: Record<string, string>;
  onClose: () => void;
  onCreated: (man: AnalysisManifest) => void;
}) {
  const [id, setId] = useState("");
  const [label, setLabel] = useState("");
  const [category, setCategory] = useState("plot");
  const [accepts, setAccepts] = useState<string[]>([]);
  const [paramsText, setParamsText] = useState("[]");
  const [code, setCode] = useState(STARTER_CODE);
  const [previewFile, setPreviewFile] = useState<File | null>(null);
  const [exampleFiles, setExampleFiles] = useState<File[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const createMut = useMutation({
    mutationFn: async () => {
      let params: any[] = [];
      try {
        params = JSON.parse(paramsText || "[]");
        if (!Array.isArray(params)) throw new Error();
      } catch {
        throw new Error('参数格式错误:应为 JSON 数组,如 [{"key":"fc","label":"阈值","type":"number","default":1}]');
      }
      const preview_b64 = previewFile ? await dataUrl(previewFile) : null;
      const examples = await Promise.all(
        exampleFiles.map(async (f) => ({ name: f.name, content_b64: await dataUrl(f) }))
      );
      return analysisApi.create({
        id,
        label,
        category,
        accepts,
        params,
        code,
        preview_b64,
        examples,
      });
    },
    onSuccess: (man) => {
      // 重置
      setId("");
      setLabel("");
      setAccepts([]);
      setParamsText("[]");
      setCode(STARTER_CODE);
      setPreviewFile(null);
      setExampleFiles([]);
      setErr(null);
      onCreated(man);
    },
    onError: (e: any) => setErr(e?.message || "新增失败"),
  });

  const inputCls =
    "w-full rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent";
  const canSubmit = /^[A-Za-z0-9_-]+$/.test(id) && label.trim() && code.trim();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="新增分析"
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={!canSubmit || createMut.isPending}
            onClick={() => createMut.mutate()}
          >
            {createMut.isPending ? "创建中…" : "创建"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="text-xs text-ink-muted">
          填写下面的信息并贴上你的 R 代码。代码需定义一个 <code className="rounded bg-bg-muted px-1">run(inputs, params, out_dir)</code> 函数;
          元数据头部会自动加在代码前面。创建后会写入本机分析目录,重启仍在。
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs text-ink-muted">ID(英文/数字/-/_)</label>
            <input value={id} onChange={(e) => setId(e.target.value)} className={inputCls} placeholder="my_analysis" />
          </div>
          <div>
            <label className="mb-1 block text-xs text-ink-muted">名称</label>
            <input value={label} onChange={(e) => setLabel(e.target.value)} className={inputCls} placeholder="我的分析" />
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs text-ink-muted">类别</label>
          <select value={category} onChange={(e) => setCategory(e.target.value)} className={inputCls}>
            {Object.entries(CATEGORY_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs text-ink-muted">需要的输入数据(可多选)</label>
          <div className="flex flex-wrap gap-2">
            {Object.entries(datasetTypes).map(([k, desc]) => {
              const on = accepts.includes(k);
              return (
                <button
                  key={k}
                  type="button"
                  title={desc}
                  onClick={() =>
                    setAccepts((s) => (on ? s.filter((x) => x !== k) : [...s, k]))
                  }
                  className={`rounded-md px-2.5 py-1 text-xs ring-1 transition ${
                    on
                      ? "bg-accent-soft text-ink ring-accent"
                      : "text-ink-muted ring-border hover:bg-bg-muted"
                  }`}
                >
                  {k}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs text-ink-muted">
            参数定义(JSON 数组,可留空 [])
          </label>
          <textarea
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
            rows={3}
            spellCheck={false}
            className={`${inputCls} font-mono text-xs`}
            placeholder='[{"key":"fc_cutoff","label":"log2FC 阈值","type":"number","default":1}]'
          />
        </div>

        <div>
          <label className="mb-1 block text-xs text-ink-muted">R 代码(需定义 run 函数)</label>
          <textarea
            value={code}
            onChange={(e) => setCode(e.target.value)}
            rows={10}
            spellCheck={false}
            className={`${inputCls} font-mono text-xs`}
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs text-ink-muted">预览图(可选 PNG)</label>
            <input
              type="file"
              accept="image/png,image/*"
              onChange={(e) => setPreviewFile(e.target.files?.[0] || null)}
              className="text-xs text-ink-muted"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-ink-muted">示例输入文件(可选,可多选)</label>
            <input
              type="file"
              multiple
              onChange={(e) => setExampleFiles(Array.from(e.target.files || []))}
              className="text-xs text-ink-muted"
            />
          </div>
        </div>

        {err && <Banner variant="error">{err}</Banner>}
      </div>
    </Modal>
  );
}

const STARTER_CODE = `# 需定义 run(inputs, params, out_dir)
# inputs: 命名列表,key 是上面选的数据类型,value 是文件路径
# params: 命名列表,key 是上面定义的参数 key
# out_dir: 输出目录(图/表写到这里)

run <- function(inputs, params, out_dir) {
  # 示例:读入 deg_table,画个简单的图
  # df <- read.delim(inputs$deg_table)
  # library(ggplot2)
  # p <- ggplot(df, aes(log2FoldChange, -log10(pvalue))) + geom_point()
  # ggsave(file.path(out_dir, "plot.png"), p, width = 6, height = 5, dpi = 150)
}
`;
