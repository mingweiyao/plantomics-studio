import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import {
  Package,
  CheckCircle,
  AlertCircle,
  Download,
  Loader2,
  Upload,
  Trash2,
} from "lucide-react";
import { coreApi } from "../lib/api";
import {
  PageHeader,
  Card,
  Pill,
  Loading,
  EmptyState,
  Banner,
  Button,
  Modal,
  ConfirmDialog,
} from "../components/ui";
import { PasswordDialog } from "../components/PasswordDialog";
import { extractError } from "../lib/errorMessage";

/**
 * 解析模块安装/卸载的错误。
 * 后端返回的 detail 是 {error, message, log} 对象,前端要正确展示。
 */
function parseInstallError(e: unknown): { message: string; log: string } {
  const raw = extractError(e);
  try {
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object") {
      return {
        message: parsed.message || parsed.error || raw,
        log: parsed.log || raw,
      };
    }
  } catch {
    // 不是 JSON 就当字符串
  }
  return { message: raw, log: raw };
}

export function Modules() {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"installed" | "catalog">("installed");
  
  // 待安装的 deb 路径 + 密码框状态
  const [pendingInstall, setPendingInstall] = useState<{
    debPath: string;
    errorMessage?: string | null;
  } | null>(null);
  
  // 待卸载的 module + 密码框状态
  const [pendingUninstall, setPendingUninstall] = useState<{
    moduleId: string;
    confirmed?: boolean;
    errorMessage?: string | null;
  } | null>(null);
  
  // 操作完成日志 Modal
  const [resultModal, setResultModal] = useState<{
    title: string;
    log: string;
    needRestart?: boolean;
  } | null>(null);

  const [uninstallPwd, setUninstallPwd] = useState("");

  const { data: installedData, isLoading: loadingInstalled } = useQuery({
    queryKey: ["installed-modules"],
    queryFn: coreApi.listInstalledModules,
  });

  const { data: catalogData, isLoading: loadingCatalog } = useQuery({
    queryKey: ["catalog"],
    queryFn: coreApi.getCatalog,
  });

  // 安装 mutation - 现在接受 password
  const installLocalMutation = useMutation({
    mutationFn: (args: { debPath: string; password: string }) =>
      coreApi.installLocalDeb(args.debPath, args.password),
    onSuccess: (res) => {
      setPendingInstall(null);
      setResultModal({
        title: "✅ 模块安装成功",
        log: res.message + "\n\n" + res.log,
        needRestart: true,
      });
      qc.invalidateQueries({ queryKey: ["installed-modules"] });
    },
    onError: (e) => {
      const parsed = parseInstallError(e);
      // 把错误回到密码框,让用户能重试
      setPendingInstall((prev) =>
        prev ? { ...prev, errorMessage: parsed.message } : null
      );
    },
  });

  // 卸载 mutation
  const uninstallMutation = useMutation({
    mutationFn: (args: { moduleId: string; password: string }) =>
      coreApi.uninstallModule(args.moduleId, args.password),
    onSuccess: (res) => {
      setPendingUninstall(null);
      setUninstallPwd("");
      setResultModal({
        title: "✅ 模块已卸载",
        log: res.message + "\n\n" + res.log,
        needRestart: true,
      });
      qc.invalidateQueries({ queryKey: ["installed-modules"] });
    },
    onError: (e) => {
      const parsed = parseInstallError(e);
      setPendingUninstall((prev) =>
        prev ? { ...prev, errorMessage: parsed.message } : null
      );
    },
  });

  // 点"从本地 .deb 安装" → 选文件 → 弹密码框
  async function pickAndInstallDeb() {
    try {
      const f = await openDialog({
        multiple: false,
        title: "选择模块 .deb 文件",
        filters: [{ name: "Debian package", extensions: ["deb"] }],
      });
      if (typeof f === "string") {
        setPendingInstall({ debPath: f, errorMessage: null });
      }
    } catch (e: any) {
      setResultModal({
        title: "❌ 选择文件失败",
        log: extractError(e),
      });
    }
  }

  const installed = installedData?.modules ?? [];
  const catalog = catalogData?.catalog ?? [];

  return (
    <div className="p-6 max-w-5xl">
      <PageHeader
        title="模块"
        subtitle="装上模块后,主程序就具备了对应的分析能力"
        action={
          <Button
            variant="secondary"
            onClick={pickAndInstallDeb}
            disabled={installLocalMutation.isPending}
          >
            <Upload size={14} />
            {installLocalMutation.isPending ? "安装中..." : "从本地 .deb 安装"}
          </Button>
        }
      />

      {/* Tabs */}
      <div className="flex gap-1 border-b border-bg-muted mb-4">
        <TabButton
          active={tab === "installed"}
          onClick={() => setTab("installed")}
          count={installed.length}
        >
          已安装
        </TabButton>
        <TabButton
          active={tab === "catalog"}
          onClick={() => setTab("catalog")}
          count={catalog.length}
        >
          模块清单
        </TabButton>
      </div>

      {tab === "installed" && (
        <InstalledTab
          modules={installed}
          loading={loadingInstalled}
          onUninstall={(id) =>
            setPendingUninstall({ moduleId: id, errorMessage: null })
          }
        />
      )}
      {tab === "catalog" && (
        <CatalogTab items={catalog} loading={loadingCatalog} />
      )}

      {/* 安装密码框 */}
      <PasswordDialog
        open={!!pendingInstall}
        onClose={() => setPendingInstall(null)}
        onSubmit={(password) => {
          if (pendingInstall) {
            installLocalMutation.mutate({
              debPath: pendingInstall.debPath,
              password,
            });
          }
        }}
        title="安装模块"
        description={
          <>
            将通过 sudo 安装{" "}
            <code className="text-xs bg-bg-muted px-1 rounded">
              {pendingInstall?.debPath.split("/").pop()}
            </code>
            。请输入你的登录密码。
          </>
        }
        busy={installLocalMutation.isPending}
        errorMessage={pendingInstall?.errorMessage}
      />

      {/* 卸载:确认 + 密码 合成一个对话框,避免多对话框状态机出问题 */}
      <Modal
        open={!!pendingUninstall}
        onClose={() => {
          setPendingUninstall(null);
          setUninstallPwd("");
        }}
        title="卸载模块"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setPendingUninstall(null);
                setUninstallPwd("");
              }}
            >
              取消
            </Button>
            <Button
              variant="danger"
              disabled={!uninstallPwd || uninstallMutation.isPending}
              onClick={() =>
                pendingUninstall &&
                uninstallMutation.mutate({
                  moduleId: pendingUninstall.moduleId,
                  password: uninstallPwd,
                })
              }
            >
              {uninstallMutation.isPending ? "卸载中…" : "卸载"}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-ink-muted">
          <div>
            确定卸载 <strong className="text-ink">{pendingUninstall?.moduleId}</strong> 吗?会调用{" "}
            <code className="rounded bg-bg-muted px-1 text-xs">apt remove</code>{" "}
            删除该模块的 deb 包;用这个模块建过的项目数据不受影响。卸载后请重启应用。
          </div>
          <div>
            <label className="mb-1 block text-xs text-ink-faint">管理员密码(sudo)</label>
            <input
              type="password"
              value={uninstallPwd}
              onChange={(e) => setUninstallPwd(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  uninstallPwd &&
                  pendingUninstall &&
                  !uninstallMutation.isPending
                ) {
                  uninstallMutation.mutate({
                    moduleId: pendingUninstall.moduleId,
                    password: uninstallPwd,
                  });
                }
              }}
              className="w-full rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-ink outline-none focus:border-accent"
              placeholder="登录密码"
              autoFocus
            />
          </div>
          {pendingUninstall?.errorMessage && (
            <Banner variant="error">{pendingUninstall.errorMessage}</Banner>
          )}
        </div>
      </Modal>

      {/* 结果日志 */}
      <Modal
        open={!!resultModal}
        onClose={() => setResultModal(null)}
        title={resultModal?.title || ""}
        size="lg"
        footer={
          <Button onClick={() => setResultModal(null)}>知道了</Button>
        }
      >
        <div className="space-y-3">
          {resultModal?.needRestart && (
            <Banner variant="warning">
              <strong>重要:</strong> 请关闭并重新启动 PlantOmics Studio 以加载/移除模块。
            </Banner>
          )}
          <div>
            <div className="text-xs text-ink-faint mb-1">操作日志:</div>
            <pre className="text-xs bg-bg-muted p-3 rounded overflow-x-auto max-h-80 whitespace-pre-wrap font-mono">
              {resultModal?.log}
            </pre>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function TabButton({
  children,
  active,
  onClick,
  count,
}: {
  children: React.ReactNode;
  active: boolean;
  onClick: () => void;
  count?: number;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm border-b-2 transition -mb-[1px] ${
        active
          ? "border-accent text-ink"
          : "border-transparent text-ink-muted hover:text-ink"
      }`}
    >
      {children}
      {count !== undefined && (
        <span className="ml-1.5 text-xs text-ink-faint">{count}</span>
      )}
    </button>
  );
}

function InstalledTab({
  modules,
  loading,
  onUninstall,
}: {
  modules: any[];
  loading: boolean;
  onUninstall: (id: string) => void;
}) {
  if (loading) return <Loading />;
  if (modules.length === 0) {
    return (
      <EmptyState
        icon={<Package size={24} />}
        title="还没装任何模块"
        hint="点击右上角'从本地 .deb 安装'选模块的 deb 文件。会弹出应用内的密码输入框。"
      />
    );
  }

  return (
    <div className="space-y-2">
      {modules.map((m) => (
        <ModuleCard key={m.id} mod={m} onUninstall={onUninstall} />
      ))}
    </div>
  );
}

function ModuleCard({
  mod,
  onUninstall,
}: {
  mod: any;
  onUninstall: (id: string) => void;
}) {
  const m = mod.manifest || {};
  const status = mod.status || "loading";

  const statusBadge = {
    loading: { variant: "neutral" as const, icon: Loader2, label: "加载中" },
    ready: { variant: "success" as const, icon: CheckCircle, label: "已就绪" },
    error: { variant: "error" as const, icon: AlertCircle, label: "错误" },
    disabled: {
      variant: "warning" as const,
      icon: AlertCircle,
      label: "已禁用",
    },
  }[status as "loading" | "ready" | "error" | "disabled"] || {
    variant: "neutral" as const,
    icon: Package,
    label: status,
  };
  const Icon = statusBadge.icon;

  return (
    <Card>
      <div className="flex justify-between items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <div className="font-medium text-sm">{m.name || mod.id}</div>
            <Pill variant={statusBadge.variant}>
              <span className="inline-flex items-center gap-1">
                <Icon
                  size={10}
                  className={status === "loading" ? "animate-spin" : ""}
                />
                {statusBadge.label}
              </span>
            </Pill>
          </div>
          <div className="text-xs text-ink-faint mt-0.5">
            {mod.id} · v{mod.version}
          </div>
          {m.description && (
            <div className="text-xs text-ink-muted mt-2 whitespace-pre-line">
              {m.description}
            </div>
          )}
          {typeof mod.error === "string" && mod.error && (
            <div className="mt-2">
              <Banner variant="error">
                <div className="text-xs font-mono">{mod.error}</div>
              </Banner>
            </div>
          )}
          {(mod.py_port || mod.r_port) && (
            <div className="text-xs text-ink-faint mt-2 space-x-3">
              {mod.py_port && <span>Py 端口 {mod.py_port}</span>}
              {mod.r_port && <span>R 端口 {mod.r_port}</span>}
            </div>
          )}
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onUninstall(mod.id)}
          className="text-red-500 hover:bg-red-50 dark:hover:bg-red-950/30"
        >
          <Trash2 size={12} />
          卸载
        </Button>
      </div>
    </Card>
  );
}

function CatalogTab({ items, loading }: { items: any[]; loading: boolean }) {
  if (loading) return <Loading />;
  if (items.length === 0) {
    return (
      <div>
        <Banner variant="info">
          <div className="text-xs">
            <strong>当前没有可下载的模块清单。</strong>{" "}
            主程序内置的 modules.json 是空的。当前只能通过"从本地 .deb 安装"按钮装模块。
          </div>
        </Banner>

        <div className="mt-6">
          <EmptyState
            icon={<Download size={24} />}
            title="清单为空"
            hint="未来会在主程序更新时带上模块清单"
          />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {items.map((it) => (
        <Card key={it.id}>
          <div className="flex justify-between items-start gap-4">
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <div className="font-medium text-sm">{it.name}</div>
                {it.installed && <Pill variant="success">已安装</Pill>}
              </div>
              <div className="text-xs text-ink-faint mt-0.5">
                {it.id} · v{it.version}
                {it.deb_size_mb && ` · ${it.deb_size_mb} MB`}
              </div>
              {it.description && (
                <div className="text-xs text-ink-muted mt-2">
                  {it.description}
                </div>
              )}
            </div>
            <Button size="sm" disabled>
              {it.installed ? "已装" : "安装"}
            </Button>
          </div>
        </Card>
      ))}
    </div>
  );
}
