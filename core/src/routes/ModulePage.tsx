/**
 * 模块页面占位
 *
 * URL: /projects/:projectId/m/:moduleId/* (任意子路径)
 *
 * 这一版(3.2)只显示"占位 + 模块状态 + 菜单跳转",真实分析 UI 由模块前端
 * 提供 — 加载机制在 3.3 实装(动态 import 模块的 frontend.js + 注入 SDK)。
 *
 * 但模块后端已经就绪,可以通过 callModule(module_id, path) 直接调用,
 * 也可以 curl http://127.0.0.1:<core-port>/modules/<id>/<path> 验证。
 */
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Package } from "lucide-react";
import { coreApi } from "../lib/api";
import {
  PageHeader,
  Card,
  Loading,
  Banner,
  Pill,
} from "../components/ui";

export function ModulePage() {
  const { projectId, moduleId } = useParams<{
    projectId: string;
    moduleId: string;
  }>();
  const navigate = useNavigate();
  const location = useLocation();

  const { data: project, isLoading: loadingProject } = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => coreApi.getProject(projectId!),
    enabled: !!projectId,
  });

  const { data: modulesData } = useQuery({
    queryKey: ["installed-modules"],
    queryFn: coreApi.listInstalledModules,
  });

  if (loadingProject) return <Loading />;

  const mod = modulesData?.modules.find((m) => m.id === moduleId);
  if (!mod) {
    return (
      <div className="p-6">
        <Banner variant="error">
          找不到模块 <code>{moduleId}</code>。模块可能已被卸载。
        </Banner>
      </div>
    );
  }

  // 提取从 /projects/:projectId/m/:moduleId 之后的子路径
  const prefix = `/projects/${projectId}/m/${moduleId}`;
  const subPath = location.pathname.slice(prefix.length) || "/";
  const menuItems = mod.manifest?.extends?.menu_items ?? [];
  const currentMenuItem = menuItems.find(
    (item: any) => (item.route || `/${item.id}`) === subPath
  );

  // 通过模块 backend 调用一个 health check,验证模块仍然在跑
  const healthQuery = useQuery({
    queryKey: ["module-health", moduleId],
    queryFn: () => coreApi.callModule({
      module_id: moduleId!,
      path: "health",
      method: "GET",
    }),
    refetchInterval: 5000,
    retry: false,
  });

  return (
    <div className="p-6 max-w-4xl">
      <PageHeader
        title={currentMenuItem?.label || subPath || "模块"}
        subtitle={`${project?.name} · ${mod.manifest?.name || moduleId}`}
        back={
          <button
            onClick={() => navigate(`/projects/${projectId}`)}
            className="text-ink-faint hover:text-ink"
            aria-label="返回项目"
          >
            <ArrowLeft size={18} />
          </button>
        }
      />

      <div className="space-y-4">
        {/* 模块状态卡片 */}
        <Card>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Package size={14} className="text-ink-faint" />
              <div className="text-sm">
                <span className="font-medium">{mod.manifest?.name || moduleId}</span>
                <span className="text-ink-faint ml-2">v{mod.version}</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {healthQuery.isError ? (
                <Pill variant="error">后端不可达</Pill>
              ) : healthQuery.data ? (
                <Pill variant="success">已连通</Pill>
              ) : (
                <Pill variant="neutral">检查中...</Pill>
              )}
            </div>
          </div>
        </Card>

        {/* 占位内容 */}
        <Card className="text-center py-12">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-bg-muted text-ink-faint mb-4">
            <Package size={28} />
          </div>
          <h3 className="text-base font-medium mb-2">
            模块界面待实装
          </h3>
          <p className="text-sm text-ink-muted max-w-md mx-auto mb-4">
            <strong>{currentMenuItem?.label || subPath}</strong> 页面的 UI 由
            模块自身提供。模块前端 bundle 加载机制将在<strong>子批次 3.3</strong>实装。
          </p>
          <p className="text-xs text-ink-faint max-w-md mx-auto">
            模块的 backend 已经就绪并能通过主程序代理访问。
            目前可以通过 <code>curl /modules/{moduleId}/&lt;path&gt;</code>{" "}
            或主程序 API 直接调用模块功能。
          </p>
        </Card>

        {/* 可用菜单项快捷跳转 */}
        {menuItems.length > 0 && (
          <Card>
            <div className="text-sm font-medium mb-2">本模块的菜单</div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
              {menuItems.map((item: any) => {
                const route = item.route || `/${item.id}`;
                const active = route === subPath;
                return (
                  <button
                    key={item.id}
                    onClick={() => navigate(`${prefix}${route}`)}
                    className={`text-left p-2 rounded border transition ${
                      active
                        ? "border-accent bg-accent/10"
                        : "border-bg-muted hover:border-accent/50 hover:bg-bg-muted/30"
                    }`}
                  >
                    <div className="text-xs font-medium">{item.label}</div>
                    {item.description && (
                      <div className="text-xs text-ink-faint mt-0.5 line-clamp-2">
                        {item.description}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          </Card>
        )}

        {/* 调试信息(开发期间方便) */}
        <details className="text-xs">
          <summary className="cursor-pointer text-ink-faint hover:text-ink">
            调试信息
          </summary>
          <Card className="mt-2 text-xs font-mono">
            <div>项目 ID: {projectId}</div>
            <div>模块 ID: {moduleId}</div>
            <div>子路径: {subPath}</div>
            <div>模块状态: {mod.status}</div>
            <div>Py 端口: {mod.py_port || "(无)"}</div>
            <div>R 端口: {mod.r_port || "(无)"}</div>
            <div className="mt-2">健康检查响应:</div>
            <pre className="bg-bg-muted p-2 rounded mt-1 overflow-x-auto">
              {healthQuery.data
                ? JSON.stringify(healthQuery.data, null, 2)
                : healthQuery.isError
                ? `错误: ${String(healthQuery.error)}`
                : "(等待中)"}
            </pre>
          </Card>
        </details>
      </div>
    </div>
  );
}
