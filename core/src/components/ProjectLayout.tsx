/**
 * 项目页布局
 * 
 * 包含项目相关的所有子页(详情、上游分析等)。
 * 左侧:Outlet 渲染子路由(主内容)
 * 右侧:固定的任务进度面板(实时显示这个项目的所有任务)
 */
import { useState } from "react";
import { useParams, Outlet } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ListChecks,
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
  PauseCircle,
  X,
  FileText,
  ChevronRight,
  ChevronLeft,
} from "lucide-react";
import { coreApi } from "../lib/api";
import {
  rnaseqApi,
  Job,
  JobStatus,
  JOB_KIND_LABELS,
  isTerminal,
} from "../lib/rnaseqApi";
import { Modal, Button, Loading } from "./ui";

const STATUS_META: Record<
  JobStatus,
  { color: string; icon: any; label: string }
> = {
  pending: { color: "text-ink-faint", icon: Clock, label: "排队" },
  running: { color: "text-accent", icon: Loader2, label: "运行" },
  completed: { color: "text-green-600", icon: CheckCircle, label: "完成" },
  failed: { color: "text-red-500", icon: XCircle, label: "失败" },
  cancelled: { color: "text-ink-faint", icon: X, label: "取消" },
  interrupted: { color: "text-amber-500", icon: PauseCircle, label: "中断" },
};


export function ProjectLayout() {
  const { id } = useParams<{ id: string }>();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex h-full">
      <div className="flex-1 overflow-auto">
        <Outlet />
      </div>
      {!collapsed ? (
        <div className="w-80 border-l border-bg-muted bg-bg-card flex flex-col">
          <div className="p-3 border-b border-bg-muted flex items-center justify-between">
            <div className="text-xs font-medium flex items-center gap-1.5">
              <ListChecks size={12} />
              任务进度
            </div>
            <button
              onClick={() => setCollapsed(true)}
              className="text-ink-faint hover:text-ink"
              title="收起"
            >
              <ChevronRight size={14} />
            </button>
          </div>
          <ProjectTasksPanel projectId={id!} />
        </div>
      ) : (
        <button
          onClick={() => setCollapsed(false)}
          className="border-l border-bg-muted bg-bg-card hover:bg-bg-muted text-ink-faint hover:text-ink px-1 flex items-center"
          title="展开任务面板"
        >
          <ChevronLeft size={14} />
        </button>
      )}
    </div>
  );
}


export function ProjectTasksPanel({ projectId }: { projectId: string }) {
  const [logModalJob, setLogModalJob] = useState<Job | null>(null);

  // 检查模块状态
  const { data: modulesData } = useQuery({
    queryKey: ["installed-modules"],
    queryFn: coreApi.listInstalledModules,
  });
  const rnaseqModule = modulesData?.modules?.find(
    (m) => m.id === "omics-rnaseq-bulk"
  );
  const moduleReady = rnaseqModule?.status === "ready";

  const { data: jobsData, isLoading } = useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => rnaseqApi.listJobs(projectId),
    enabled: moduleReady,
    refetchInterval: 2000,
  });

  const jobs = jobsData?.jobs ?? [];
  const running = jobs.filter(
    (j) => j.status === "running" || j.status === "pending"
  );
  const finished = jobs.filter(
    (j) => j.status !== "running" && j.status !== "pending"
  );

  if (!moduleReady) {
    return (
      <div className="p-4 text-xs text-ink-faint">
        模块未就绪,无法跑任务
      </div>
    );
  }

  if (isLoading) {
    return <Loading />;
  }

  return (
    <>
      <div className="flex-1 overflow-y-auto p-3 space-y-3 text-xs">
        {jobs.length === 0 ? (
          <div className="text-ink-faint text-center py-6">
            还没有任务
          </div>
        ) : (
          <>
            {running.length > 0 && (
              <div>
                <div className="text-[10px] uppercase text-ink-faint mb-1.5 tracking-wide">
                  进行中 · {running.length}
                </div>
                <div className="space-y-1.5">
                  {running.map((j) => (
                    <CompactJobRow
                      key={j.id}
                      job={j}
                      onClick={() => setLogModalJob(j)}
                    />
                  ))}
                </div>
              </div>
            )}
            {finished.length > 0 && (
              <div>
                <div className="text-[10px] uppercase text-ink-faint mb-1.5 tracking-wide">
                  已结束 · {finished.length}
                </div>
                <div className="space-y-1.5">
                  {finished.slice(0, 20).map((j) => (
                    <CompactJobRow
                      key={j.id}
                      job={j}
                      onClick={() => setLogModalJob(j)}
                    />
                  ))}
                  {finished.length > 20 && (
                    <div className="text-ink-faint text-[10px] text-center pt-1">
                      …还有 {finished.length - 20} 条历史
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </div>

      <JobLogModal
        job={logModalJob}
        onClose={() => setLogModalJob(null)}
      />
    </>
  );
}


function CompactJobRow({
  job,
  onClick,
}: {
  job: Job;
  onClick: () => void;
}) {
  const qc = useQueryClient();
  const meta = STATUS_META[job.status];
  const Icon = meta.icon;
  const kindLabel = JOB_KIND_LABELS[job.kind] || job.kind;
  const isRunning = job.status === "running" || job.status === "pending";

  const cancelMutation = useMutation({
    mutationFn: () => rnaseqApi.cancelJob(job.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  return (
    <div
      className="w-full p-2 bg-bg rounded border border-bg-muted hover:border-ink-faint text-left transition"
    >
      <button
        onClick={onClick}
        className="w-full text-left"
      >
        <div className="flex items-center gap-1.5 mb-0.5">
          <Icon
            size={11}
            className={`${meta.color} ${
              isRunning ? "animate-spin" : ""
            } shrink-0`}
          />
          <span className="font-medium text-[11px] truncate flex-1">
            {kindLabel}
          </span>
          <span className={`${meta.color} text-[10px]`}>{meta.label}</span>
        </div>
        {isRunning && (
          <div className="mt-1">
            <div className="relative h-0.5 bg-bg-muted rounded overflow-hidden">
              {job.progress.indeterminate ? (
                // 长步骤:来回滑动的高亮带,证明任务在跑(不卡死)
                <div className="progress-indeterminate" />
              ) : (
                <div
                  className="h-full bg-accent transition-all"
                  style={{ width: `${job.progress.pct}%` }}
                />
              )}
            </div>
            <div className="text-[10px] text-ink-faint mt-0.5 truncate">
              {job.progress.stage}
              {job.progress.indeterminate ? " · 处理中…" : ` · ${job.progress.pct}%`}
            </div>
          </div>
        )}
        {typeof job.error === "string" && job.error && !isRunning && (
          <div className="text-[10px] text-red-500 mt-0.5 truncate">
            {job.error}
          </div>
        )}
      </button>
      {isRunning && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            cancelMutation.mutate();
          }}
          disabled={cancelMutation.isPending}
          className="mt-1 w-full text-[10px] text-ink-faint hover:text-red-500 border border-bg-muted hover:border-red-300 rounded px-1.5 py-0.5"
          title="取消任务"
        >
          {cancelMutation.isPending ? "取消中…" : "✕ 取消"}
        </button>
      )}
    </div>
  );
}


function JobLogModal({ job, onClose }: { job: Job | null; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: log } = useQuery({
    queryKey: ["job-log", job?.id],
    queryFn: () => rnaseqApi.getJobLog(job!.id),
    enabled: !!job,
    refetchInterval: job && !isTerminal(job.status) ? 2000 : false,
  });
  
  const cancelMutation = useMutation({
    mutationFn: () => rnaseqApi.cancelJob(job!.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  
  const isRunning = !!job && (job.status === "running" || job.status === "pending");

  return (
    <Modal
      open={!!job}
      onClose={onClose}
      title={job ? `${JOB_KIND_LABELS[job.kind] || job.kind}` : ""}
      size="lg"
      footer={
        <div className="flex gap-2">
          {isRunning && (
            <Button
              variant="secondary"
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
            >
              {cancelMutation.isPending ? "取消中…" : "取消任务"}
            </Button>
          )}
          <Button onClick={onClose}>关闭</Button>
        </div>
      }
    >
      {job && (
        <div>
          <div className="text-xs text-ink-faint mb-2 space-y-0.5">
            <div>
              <strong>ID:</strong> <code>{job.id}</code> ·{" "}
              <strong>Status:</strong> <code>{job.status}</code>
            </div>
            {job.output_subdir && (
              <div>
                <strong>输出:</strong> <code>{job.output_subdir}</code>
              </div>
            )}
            {Object.keys(job.params || {}).length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer hover:text-ink">参数</summary>
                <pre className="mt-1 bg-bg-muted p-2 rounded text-xs overflow-auto">
                  {JSON.stringify(job.params, null, 2)}
                </pre>
              </details>
            )}
          </div>
          <pre className="text-xs bg-bg-muted p-3 rounded overflow-auto max-h-96 whitespace-pre-wrap font-mono">
            {log || "(暂无日志)"}
          </pre>
        </div>
      )}
    </Modal>
  );
}
