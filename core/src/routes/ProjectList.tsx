import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus, FolderOpen, Package } from "lucide-react";
import { coreApi } from "../lib/api";
import {
  PageHeader,
  Button,
  Card,
  EmptyState,
  Pill,
  Loading,
  Banner,
} from "../components/ui";

export function ProjectList() {
  const navigate = useNavigate();

  const { data, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: coreApi.listProjects,
  });

  const { data: modulesData } = useQuery({
    queryKey: ["installed-modules"],
    queryFn: coreApi.listInstalledModules,
  });

  const projects = data?.projects ?? [];
  const installedModules = modulesData?.modules ?? [];
  const noModules = installedModules.length === 0;

  return (
    <div className="p-6 max-w-5xl">
      <PageHeader
        title="项目"
        subtitle={
          isLoading
            ? "加载中..."
            : `${projects.length} 个项目`
        }
        action={
          <Button onClick={() => navigate("/projects/new")}>
            <Plus size={14} />
            新建项目
          </Button>
        }
      />

      {noModules && projects.length === 0 && (
        <div className="mb-4">
          <Banner variant="info">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="font-medium mb-1">还没安装任何分析模块</div>
                <div className="text-xs">
                  你可以创建空项目并管理参考资源,但要做具体的组学分析(转录组、蛋白组等),
                  需要先安装相应模块。
                </div>
              </div>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => navigate("/modules")}
              >
                <Package size={12} />
                模块管理
              </Button>
            </div>
          </Banner>
        </div>
      )}

      {isLoading ? (
        <Loading />
      ) : projects.length === 0 ? (
        <EmptyState
          icon={<FolderOpen size={24} />}
          title="还没有项目"
          hint="点击右上角'新建项目'开始"
          action={
            <Button onClick={() => navigate("/projects/new")}>
              <Plus size={14} />
              新建项目
            </Button>
          }
        />
      ) : (
        <div className="space-y-2">
          {projects.map((p) => (
            <Card key={p.id} onClick={() => navigate(`/projects/${p.id}`)}>
              <div className="flex justify-between items-start">
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm">{p.name}</div>
                  {p.description && (
                    <div className="text-xs text-ink-muted mt-1 line-clamp-2">
                      {p.description}
                    </div>
                  )}
                  <div className="flex gap-1.5 mt-2 items-center flex-wrap">
                    {p.modules_used && p.modules_used.length > 0 ? (
                      p.modules_used.map((mid) => {
                        const mod = installedModules.find((m) => m.id === mid);
                        return (
                          <Pill key={mid} variant="info">
                            {mod?.manifest?.name || mid}
                          </Pill>
                        );
                      })
                    ) : (
                      <Pill variant="neutral">未关联模块</Pill>
                    )}
                    {p.workdir && (
                      <Pill variant="neutral">
                        <span className="font-mono text-xs">{p.workdir}</span>
                      </Pill>
                    )}
                  </div>
                </div>
                <div className="text-xs text-ink-faint shrink-0 ml-4">
                  {p.updated_at && (
                    <div title={new Date(p.updated_at).toLocaleString()}>
                      {formatRelative(p.updated_at)}
                    </div>
                  )}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

function formatRelative(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "刚刚";
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)} 天前`;
  return new Date(iso).toLocaleDateString();
}
