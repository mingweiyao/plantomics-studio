/**
 * 项目详情页
 * 
 * 顶部:基本信息(名称/描述/工作目录/参考资源)+ 编辑按钮 + 删除按钮
 * 
 * 主体 = tab 切换:
 *   - 任务历史:这个项目跑过的所有任务时间线 (新增,核心)
 *   - 模块菜单:启用的模块 + 它们提供的入口(上游分析等)
 */
import { useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import {
  ArrowLeft,
  Edit2,
  Trash2,
  Save,
  X,
  Package,
  Plus,
  ChevronRight,
  Folder,
  FileText,
  Clock,
} from "lucide-react";
import { coreApi, InstalledModule, Project } from "../lib/api";
import {
  PageHeader,
  Button,
  Card,
  Pill,
  Loading,
  Banner,
  Field,
  Input,
  Textarea,
  ConfirmDialog,
  Modal,
} from "../components/ui";
import { extractError } from "../lib/errorMessage";


export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [editing, setEditing] = useState(false);
  const [showEnableModule, setShowEnableModule] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", id],
    queryFn: () => coreApi.getProject(id!),
    enabled: !!id,
  });

  const { data: modulesData } = useQuery({
    queryKey: ["installed-modules"],
    queryFn: coreApi.listInstalledModules,
  });
  const installedModules = modulesData?.modules ?? [];

  const deleteMutation = useMutation({
    mutationFn: () => coreApi.deleteProject(id!),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      navigate("/projects", { replace: true });
      // 用 alert 替代 toast,简单告知用户
      setTimeout(() => {
        alert(res.message);
      }, 100);
    },
    onError: (e) => setError(extractError(e)),
  });

  if (isLoading || !project) return <Loading />;

  const enabledModules = (project.modules_used || [])
    .map((mid) => installedModules.find((m) => m.id === mid))
    .filter(Boolean) as InstalledModule[];

  return (
    <div className="p-6 max-w-5xl">
      <PageHeader
        title={project.name}
        subtitle={project.description || "(无描述)"}
        back={
          <button
            onClick={() => navigate("/projects")}
            className="text-ink-faint hover:text-ink"
            aria-label="返回"
          >
            <ArrowLeft size={18} />
          </button>
        }
        action={
          editing ? null : (
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setEditing(true)}
              >
                <Edit2 size={12} />
                编辑
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmDelete(true)}
                className="text-red-500"
              >
                <Trash2 size={12} />
                删除
              </Button>
            </div>
          )
        }
      />

      {error && (
        <div className="mb-4">
          <Banner variant="error">{error}</Banner>
        </div>
      )}

      {/* 基本信息卡片 */}
      <BasicInfoCard
        project={project}
        editing={editing}
        onCancel={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          qc.invalidateQueries({ queryKey: ["project", id] });
          qc.invalidateQueries({ queryKey: ["projects"] });
        }}
      />

      {/* 模块 - 直接显示,不再切 tab(任务列表移到右侧固定面板) */}
      <div className="mt-6">
        <ModulesTab
          project={project}
          enabledModules={enabledModules}
          installedModules={installedModules}
          onAddClick={() => setShowEnableModule(true)}
          onModuleMenuClick={(modId, route) => {
            navigate(`/projects/${id}/m/${modId}${route}`);
          }}
          onUnlinkModule={(modId) => {
            coreApi
              .removeProjectModuleData(id!, modId)
              .then(() => {
                qc.invalidateQueries({ queryKey: ["project", id] });
                qc.invalidateQueries({ queryKey: ["projects"] });
              })
              .catch((e) => setError(extractError(e)));
          }}
        />
      </div>

      {/* 启用模块 */}
      <EnableModuleDialog
        open={showEnableModule}
        onClose={() => setShowEnableModule(false)}
        project={project}
        installedModules={installedModules}
        onEnabled={() => {
          setShowEnableModule(false);
          qc.invalidateQueries({ queryKey: ["project", id] });
          qc.invalidateQueries({ queryKey: ["projects"] });
        }}
      />

      {/* 删除确认 */}
      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => {
          deleteMutation.mutate();
          setConfirmDelete(false);
        }}
        title="删除项目?"
        message={
          <div className="space-y-2 text-xs">
            <div>
              将删除项目 <strong>{project.name}</strong> 的元数据(参数 / 任务历史 / 模块配置)。
            </div>
            <div className="text-ink-muted">
              工作目录{" "}
              <code className="px-1 bg-bg-muted rounded">{project.workdir}</code>{" "}
              下的文件不会动,你可以手动决定是否清理。
            </div>
          </div>
        }
        confirmLabel="删除元数据"
        danger
      />
    </div>
  );
}


function BasicInfoCard({
  project,
  editing,
  onCancel,
  onSaved,
}: {
  project: Project;
  editing: boolean;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description);
  const [refFasta, setRefFasta] = useState(project.reference_fasta || "");
  const [refGtf, setRefGtf] = useState(project.reference_gtf || "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(project.name);
    setDescription(project.description);
    setRefFasta(project.reference_fasta || "");
    setRefGtf(project.reference_gtf || "");
  }, [project, editing]);

  const updateMutation = useMutation({
    mutationFn: () =>
      coreApi.updateProject(project.id, {
        name: name.trim(),
        description: description,
        reference_fasta: refFasta.trim() || "",
        reference_gtf: refGtf.trim() || "",
      }),
    onSuccess: onSaved,
    onError: (e) => setError(extractError(e)),
  });

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

  if (editing) {
    return (
      <Card>
        <div className="space-y-3">
          <Field label="项目名称">
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="描述">
            <Textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
            />
          </Field>
          
          <div className="border-t border-bg-muted pt-3">
            <div className="text-xs font-medium text-ink-muted mb-2">参考资源</div>
            <Field label="基因组 FASTA" hint="重新选择 / 改路径,留空清除">
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
                  <FileText size={11} />
                  选文件
                </Button>
              </div>
            </Field>
            <Field
              label="基因组 GTF"
              hint="只接受 GTF。需要转换 GFF 请重建项目。留空清除。"
            >
              <div className="flex gap-2">
                <Input
                  value={refGtf}
                  onChange={(e) => setRefGtf(e.target.value)}
                  placeholder="/path/to/annotation.gtf"
                  className="flex-1"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    pickFile(setRefGtf, [
                      { name: "GTF", extensions: ["gtf", "gtf.gz"] },
                    ])
                  }
                >
                  <FileText size={11} />
                  选文件
                </Button>
              </div>
            </Field>
          </div>
          
          {error && (
            <Banner variant="error">
              <div className="text-xs">{error}</div>
            </Banner>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={onCancel}>
              <X size={11} />
              取消
            </Button>
            <Button
              size="sm"
              onClick={() => updateMutation.mutate()}
              disabled={updateMutation.isPending}
            >
              <Save size={11} />
              保存
            </Button>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="grid grid-cols-2 gap-4 text-xs">
        <InfoRow icon={<Folder size={11} />} label="工作目录">
          <code className="break-all">{project.workdir}</code>
        </InfoRow>
        <InfoRow icon={<Clock size={11} />} label="更新时间">
          {new Date(project.updated_at).toLocaleString()}
        </InfoRow>
        <InfoRow icon={<FileText size={11} />} label="基因组 FASTA">
          {project.reference_fasta ? (
            <code className="break-all">{project.reference_fasta}</code>
          ) : (
            <span className="text-ink-faint">未设置</span>
          )}
        </InfoRow>
        <InfoRow icon={<FileText size={11} />} label="基因组 GTF">
          {project.reference_gtf ? (
            <code className="break-all">{project.reference_gtf}</code>
          ) : (
            <span className="text-ink-faint">未设置</span>
          )}
        </InfoRow>
      </div>
    </Card>
  );
}

function InfoRow({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-ink-faint flex items-center gap-1 mb-1">
        {icon}
        {label}
      </div>
      <div className="text-ink">{children}</div>
    </div>
  );
}



function ModulesTab({
  project,
  enabledModules,
  installedModules,
  onAddClick,
  onModuleMenuClick,
  onUnlinkModule,
}: {
  project: Project;
  enabledModules: InstalledModule[];
  installedModules: InstalledModule[];
  onAddClick: () => void;
  onModuleMenuClick: (modId: string, route: string) => void;
  onUnlinkModule: (modId: string) => void;
}) {
  const canAdd = installedModules.some(
    (m) => m.status === "ready" && !enabledModules.some((em) => em.id === m.id)
  );

  return (
    <div className="space-y-2">
      {enabledModules.length === 0 && (
        <Card>
          <div className="text-center py-6 text-ink-faint text-sm">
            <Package size={24} className="mx-auto mb-2 opacity-50" />
            <div>项目还没启用任何模块</div>
          </div>
        </Card>
      )}

      {enabledModules.map((mod) => (
        <EnabledModuleBlock
          key={mod.id}
          mod={mod}
          onMenuClick={onModuleMenuClick}
          onUnlink={() => onUnlinkModule(mod.id)}
        />
      ))}

      <div>
        <Button
          variant="secondary"
          size="sm"
          onClick={onAddClick}
          disabled={!canAdd}
          title={canAdd ? "" : "没有可启用的模块"}
        >
          <Plus size={12} />
          启用模块
        </Button>
      </div>
    </div>
  );
}

function EnabledModuleBlock({
  mod,
  onMenuClick,
  onUnlink,
}: {
  mod: InstalledModule;
  onMenuClick: (modId: string, route: string) => void;
  onUnlink: () => void;
}) {
  const menuItems = mod.manifest?.extends?.menu_items ?? [];
  const status = mod.status;

  return (
    <Card>
      <div className="flex justify-between items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <Package size={14} className="text-accent" />
            <span className="font-medium text-sm">
              {mod.manifest?.name || mod.id}
            </span>
            <Pill
              variant={
                status === "ready"
                  ? "success"
                  : status === "error"
                  ? "error"
                  : "neutral"
              }
            >
              {status}
            </Pill>
          </div>
          <div className="text-xs text-ink-faint">v{mod.version}</div>

          {menuItems.length > 0 && (
            <div className="mt-3 space-y-1">
              {menuItems.map((item: any) => (
                <button
                  key={item.id || item.route}
                  onClick={() =>
                    onMenuClick(mod.id, item.route || `/${item.id}`)
                  }
                  disabled={status !== "ready"}
                  className={`w-full flex items-center justify-between px-3 py-2 text-xs rounded ${
                    status === "ready"
                      ? "hover:bg-bg-muted cursor-pointer"
                      : "opacity-50 cursor-not-allowed"
                  }`}
                >
                  <span>{item.label}</span>
                  <ChevronRight size={11} className="text-ink-faint" />
                </button>
              ))}
            </div>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onUnlink}
          className="text-red-500"
        >
          <X size={11} />
        </Button>
      </div>
    </Card>
  );
}


function EnableModuleDialog({
  open,
  onClose,
  project,
  installedModules,
  onEnabled,
}: {
  open: boolean;
  onClose: () => void;
  project: Project;
  installedModules: InstalledModule[];
  onEnabled: () => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const candidates = installedModules.filter(
    (m) =>
      m.status === "ready" &&
      !(project.modules_used || []).includes(m.id)
  );

  const enableMutation = useMutation({
    mutationFn: (modId: string) =>
      coreApi.setProjectModuleData(project.id, modId, {}),
    onSuccess: onEnabled,
    onError: (e) => setError(extractError(e)),
  });

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="启用模块"
      footer={<Button onClick={onClose}>关闭</Button>}
    >
      <div className="space-y-3">
        {candidates.length === 0 ? (
          <div className="text-sm text-ink-faint py-4 text-center">
            没有可启用的模块。
            <br />
            <span className="text-xs">
              请先到"模块"页装一个,或者所有装好的模块已经启用了。
            </span>
          </div>
        ) : (
          <>
            <div className="text-xs text-ink-muted">
              选一个模块启用到这个项目。启用后,模块的菜单项(如"上游分析")会出现。
            </div>
            {candidates.map((m) => (
              <button
                key={m.id}
                onClick={() => enableMutation.mutate(m.id)}
                disabled={enableMutation.isPending}
                className="w-full text-left p-3 border border-bg-muted rounded hover:bg-bg-muted/50"
              >
                <div className="flex items-center gap-2">
                  <Package size={14} className="text-accent" />
                  <div className="font-medium text-sm">{m.manifest?.name}</div>
                </div>
                <div className="text-xs text-ink-faint mt-1">
                  {m.id} v{m.version}
                </div>
                {m.manifest?.description && (
                  <div className="text-xs text-ink-muted mt-1">
                    {m.manifest.description}
                  </div>
                )}
              </button>
            ))}
          </>
        )}
        {error && <Banner variant="error">{error}</Banner>}
      </div>
    </Modal>
  );
}
