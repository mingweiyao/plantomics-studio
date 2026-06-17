/**
 * 新建项目
 * 
 * 草稿持久化:
 *   - 每次表单变化都自动写 localStorage
 *   - 进入页面时如果有未提交草稿,提示"恢复 / 弃置"
 *   - 提交成功后清除草稿
 */
import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { ArrowLeft, FolderOpen, FileText, FolderPlus, Trash2 } from "lucide-react";
import { coreApi } from "../lib/api";
import {
  PageHeader,
  Card,
  Button,
  Field,
  Input,
  Textarea,
  Banner,
} from "../components/ui";
import { extractError } from "../lib/errorMessage";

const DRAFT_KEY = "plantomics:newProjectDraft";

interface Draft {
  name: string;
  description: string;
  workdir: string;
  refFasta: string;
  refAnnotation: string;
  ts: number;
}

function loadDraft(): Draft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw);
    if (
      d.ts &&
      typeof d.name === "string" &&
      // 只有任意一项有内容才认为是有效草稿
      (d.name || d.description || d.workdir || d.refFasta || d.refAnnotation)
    ) {
      return d;
    }
  } catch {}
  return null;
}

function saveDraft(d: Omit<Draft, "ts">) {
  try {
    localStorage.setItem(
      DRAFT_KEY,
      JSON.stringify({ ...d, ts: Date.now() })
    );
  } catch {}
}

function clearDraft() {
  try {
    localStorage.removeItem(DRAFT_KEY);
  } catch {}
}

export function NewProject() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [workdir, setWorkdir] = useState("");
  const [refFasta, setRefFasta] = useState("");
  const [refAnnotation, setRefAnnotation] = useState("");
  const [totalThreads, setTotalThreads] = useState<number>(
    typeof navigator !== "undefined" && navigator.hardwareConcurrency
      ? navigator.hardwareConcurrency
      : 8
  );
  const [error, setError] = useState<string | null>(null);
  const [draftBanner, setDraftBanner] = useState<Draft | null>(null);

  // 进入时检查是否有草稿
  useEffect(() => {
    const d = loadDraft();
    if (d) setDraftBanner(d);
  }, []);

  // 自动保存草稿 — 任何字段改变都触发(debounce)
  const saveTimer = useRef<any>(null);
  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      // 至少有一项不为空才保存
      if (name || description || workdir || refFasta || refAnnotation) {
        saveDraft({ name, description, workdir, refFasta, refAnnotation });
      }
    }, 500);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [name, description, workdir, refFasta, refAnnotation]);

  const createMutation = useMutation({
    mutationFn: () =>
      coreApi.createProject({
        name: name.trim(),
        description: description.trim() || undefined,
        workdir: workdir.trim(),
        reference_fasta: refFasta.trim() || undefined,
        reference_gtf_or_gff: refAnnotation.trim() || undefined,
        total_threads: totalThreads,
      }),
    onSuccess: (project) => {
      clearDraft();
      qc.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${project.id}`);
    },
    onError: (e) => setError(extractError(e)),
  });

  async function pickWorkdir() {
    try {
      const r = await openDialog({
        directory: true,
        multiple: false,
        title: "选择项目工作目录",
      });
      if (typeof r === "string") setWorkdir(r);
    } catch {}
  }

  async function pickFile(setter: (s: string) => void, filters: any) {
    try {
      const r = await openDialog({
        directory: false,
        multiple: false,
        filters,
      });
      if (typeof r === "string") setter(r);
    } catch {}
  }

  function handleSubmit() {
    setError(null);
    if (!name.trim()) {
      setError("请填项目名");
      return;
    }
    if (!workdir.trim()) {
      setError("请选工作目录");
      return;
    }
    createMutation.mutate();
  }

  function restoreDraft(d: Draft) {
    setName(d.name);
    setDescription(d.description);
    setWorkdir(d.workdir);
    setRefFasta(d.refFasta);
    setRefAnnotation(d.refAnnotation);
    setDraftBanner(null);
  }

  function discardDraft() {
    clearDraft();
    setDraftBanner(null);
  }

  const isGff = refAnnotation
    .toLowerCase()
    .match(/\.(gff3?|gff\.gz|gff3\.gz)$/);

  return (
    <div className="p-6 max-w-3xl">
      <PageHeader
        title="新建项目"
        back={
          <button
            onClick={() => navigate("/projects")}
            className="text-ink-faint hover:text-ink"
            aria-label="返回"
          >
            <ArrowLeft size={18} />
          </button>
        }
      />

      {/* 草稿恢复提示 */}
      {draftBanner && (
        <div className="mb-4">
          <Banner variant="info">
            <div className="flex items-center justify-between gap-3">
              <div className="text-xs">
                <strong>检测到未完成的草稿</strong>{" "}
                {draftBanner.name && (
                  <span className="text-ink-muted">
                    ({draftBanner.name || "未命名"} · {formatRelative(draftBanner.ts)})
                  </span>
                )}
              </div>
              <div className="flex gap-1">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => restoreDraft(draftBanner)}
                >
                  恢复
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={discardDraft}
                  className="text-red-500"
                >
                  <Trash2 size={11} />
                </Button>
              </div>
            </div>
          </Banner>
        </div>
      )}

      <Card>
        <div className="space-y-4">
          <div className="text-xs text-ink-faint">
            提示:支持把文件 / 文件夹拖到窗口里自动填路径。
          </div>

          <Field label="项目名称" required>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例:拟南芥干旱实验 2024"
              autoFocus
            />
          </Field>

          <Field label="描述">
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="(可选)简短描述这个项目的研究内容"
              rows={2}
            />
          </Field>

          <Field
            label="工作目录"
            required
            hint="项目所有数据(fastq / BAM / counts / 下游结果)放在这里。app 创建标准子文件夹(raw/qc/trimmed/aligned/...)"
          >
            <div className="flex gap-2">
              <Input
                value={workdir}
                onChange={(e) => setWorkdir(e.target.value)}
                placeholder="/home/user/projects/拟南芥干旱"
                className="flex-1"
              />
              <Button variant="secondary" size="sm" onClick={pickWorkdir}>
                <FolderOpen size={12} />
                选目录
              </Button>
            </div>
          </Field>

          <div className="border-t border-bg-muted pt-4">
            <div className="text-xs font-medium text-ink-muted mb-3">
              参考资源(强烈建议填,后续上游分析需要)
            </div>

            <Field
              label="基因组 FASTA"
              hint="例:Arabidopsis_thaliana.TAIR10.dna.toplevel.fa(.gz 也可以)"
            >
              <div className="flex gap-2">
                <Input
                  value={refFasta}
                  onChange={(e) => setRefFasta(e.target.value)}
                  placeholder="/path/to/genome.fa"
                  className="flex-1"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    pickFile(setRefFasta, [
                      {
                        name: "FASTA",
                        extensions: ["fa", "fasta", "fna", "fa.gz", "fasta.gz"],
                      },
                    ])
                  }
                >
                  <FileText size={12} />
                  选文件
                </Button>
              </div>
            </Field>

            <Field
              label="基因组注释 GTF/GFF"
              hint={
                isGff
                  ? "✓ 检测到 GFF — 创建项目时会自动转 GTF 放到工作目录的 reference/ 下"
                  : "推荐 GTF。GFF/GFF3 也可以,后台会自动转换。"
              }
            >
              <div className="flex gap-2">
                <Input
                  value={refAnnotation}
                  onChange={(e) => setRefAnnotation(e.target.value)}
                  placeholder="/path/to/annotation.gtf 或 .gff"
                  className="flex-1"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    pickFile(setRefAnnotation, [
                      {
                        name: "Annotation",
                        extensions: [
                          "gtf",
                          "gff",
                          "gff3",
                          "gtf.gz",
                          "gff.gz",
                          "gff3.gz",
                        ],
                      },
                    ])
                  }
                >
                  <FileText size={12} />
                  选文件
                </Button>
              </div>
            </Field>

            <div className="pt-2 mt-2 border-t border-bg-muted">
              <div className="text-xs font-medium text-ink-muted mb-2">
                计算资源
              </div>
              <Field
                label="总线程预算"
                hint="这个项目最多占用多少 CPU 线程。运行时若并行跑多个任务,这个预算会按并行度均分给各任务。"
              >
                <Input
                  type="number"
                  min={1}
                  value={totalThreads}
                  onChange={(e) =>
                    setTotalThreads(Math.max(1, parseInt(e.target.value) || 1))
                  }
                />
              </Field>
              <div className="text-[11px] text-ink-faint mt-1.5">
                建议比本机逻辑核心数少留 1–2 个给系统。之后可在项目设置里修改;
                「同时运行几个任务」是全局设置(在「设置」里调,默认 1)。
              </div>
            </div>
          </div>

          {error && (
            <Banner variant="error">
              <div className="text-xs">{error}</div>
            </Banner>
          )}

          <div className="flex justify-end gap-2 border-t border-bg-muted pt-4">
            <Button
              variant="secondary"
              onClick={() => {
                discardDraft();
                navigate("/projects");
              }}
            >
              取消
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={createMutation.isPending}
            >
              <FolderPlus size={12} />
              {createMutation.isPending ? "创建中..." : "创建项目"}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

function formatRelative(ts: number): string {
  const diff = Date.now() - ts;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "刚刚";
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  return new Date(ts).toLocaleString();
}
