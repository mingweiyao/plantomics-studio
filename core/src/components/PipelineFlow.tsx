/**
 * 圆形数据处理流程图 —— 组学模块首页的视觉核心。
 *
 * 六个数据处理步骤围成一圈(SRA→质控→过滤→比对→定量→标准化),
 * 圆心是"一键运行"。每个步骤 5 种状态:
 *   未配置(灰) / 已配置(蓝) / 运行中(主色 + 呼吸光环) / 完成(绿✓) / 失败(红✗)。
 * 点击步骤 → onSelectStep(进入该步参数);圆心按钮 → onRunAll(一键跑到标准化)。
 *
 * 纯展示组件,状态由父组件传入(由 upstream_params + 任务状态推导)。
 */
import type { LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";
import { Play, Check, X, Loader2 } from "lucide-react";

export type StepStatus = "pending" | "configured" | "running" | "done" | "failed";

export interface FlowStep {
  id: string;
  label: string;
  sublabel?: string;
  icon: LucideIcon;
  status: StepStatus;
}

interface PipelineFlowProps {
  steps: FlowStep[];
  activeId?: string;
  onSelectStep: (id: string) => void;
  onRunAll: () => void;
  running?: boolean;
  canRun?: boolean;
  // 若提供 selected + onToggleSelect,则每个节点带勾选框,圆心只跑勾选的步骤
  selected?: Set<string>;
  onToggleSelect?: (id: string) => void;
}

const STATUS_LABEL: Record<StepStatus, string> = {
  pending: "未配置",
  configured: "已配置",
  running: "运行中",
  done: "已完成",
  failed: "失败",
};

// 每个状态的主色(CSS 变量,light/dark 自动切换)
function statusColor(s: StepStatus): string {
  return `rgb(var(--state-${s}))`;
}

const RADIUS_PCT = 39; // 节点到圆心的半径(占容器宽度百分比)

export function PipelineFlow({
  steps,
  activeId,
  onSelectStep,
  onRunAll,
  running = false,
  canRun = true,
  selected,
  onToggleSelect,
}: PipelineFlowProps) {
  const n = steps.length;
  const doneCount = steps.filter((s) => s.status === "done").length;
  const frac = n > 0 ? doneCount / n : 0;

  // 圆心进度环
  const R = 15.5;
  const C = 2 * Math.PI * R;

  return (
    <div className="relative mx-auto aspect-square w-full max-w-[540px] select-none">
      {/* 背景光晕 */}
      <div
        className="pointer-events-none absolute inset-[12%] rounded-full opacity-60"
        style={{
          background:
            "radial-gradient(circle at 50% 45%, rgb(var(--accent-soft)) 0%, transparent 70%)",
        }}
      />

      {/* 轨道环 + 圆心进度环 */}
      <svg viewBox="0 0 100 100" className="pointer-events-none absolute inset-0 h-full w-full">
        {/* 步骤轨道(虚线圆;运行时流动) */}
        <circle
          cx="50"
          cy="50"
          r={RADIUS_PCT}
          fill="none"
          stroke="rgb(var(--border))"
          strokeWidth="0.5"
          strokeDasharray="2 2.4"
          className={running ? "flow-orbit-active" : ""}
        />
        {/* 圆心进度底环 */}
        <circle cx="50" cy="50" r={R} fill="none" stroke="rgb(var(--bg-muted))" strokeWidth="2.4" />
        {/* 圆心进度值环 */}
        <circle
          cx="50"
          cy="50"
          r={R}
          fill="none"
          stroke="rgb(var(--accent))"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={C * (1 - frac)}
          transform="rotate(-90 50 50)"
          style={{ transition: "stroke-dashoffset .5s ease" }}
        />
      </svg>

      {/* 圆心:一键运行 */}
      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2">
        <button
          onClick={onRunAll}
          disabled={!canRun || running}
          title={canRun ? "一键跑到标准化" : "请先在项目里配置参考基因组(GTF)"}
          className="flow-node flex h-[112px] w-[112px] flex-col items-center justify-center rounded-full bg-bg-surface shadow-pop ring-1 ring-border disabled:cursor-not-allowed disabled:opacity-60"
        >
          <span
            className="mb-1 flex h-9 w-9 items-center justify-center rounded-full"
            style={{ backgroundColor: statusColor(running ? "running" : "done"), color: "white" }}
          >
            {running ? (
              <Loader2 size={18} className="animate-spin" />
            ) : (
              <Play size={18} className="ml-0.5" />
            )}
          </span>
          <span className="text-[13px] font-semibold text-ink">
            {running ? "运行中" : "一键运行"}
          </span>
          <span className="text-[10px] text-ink-faint">
            {doneCount}/{n} 步完成
          </span>
        </button>
      </div>

      {/* 六个步骤节点 */}
      {steps.map((step, i) => {
        const angle = (-90 + (360 / n) * i) * (Math.PI / 180);
        const x = 50 + RADIUS_PCT * Math.cos(angle);
        const y = 50 + RADIUS_PCT * Math.sin(angle);
        const color = statusColor(step.status);
        const active = step.id === activeId;
        const sel = !selected || selected.has(step.id);
        const Icon = step.icon;
        return (
          <div
            key={step.id}
            className="absolute"
            style={{ left: `${x}%`, top: `${y}%`, transform: "translate(-50%,-50%)" }}
          >
            <button
              onClick={() => onSelectStep(step.id)}
              className={`flow-node group relative flex w-[92px] flex-col items-center gap-1.5 rounded-2xl px-2 py-2.5 transition-opacity ${
                active ? "bg-bg-surface shadow-pop ring-2" : "bg-bg-surface/80 shadow-card ring-1 ring-border hover:ring-border"
              } ${selected && !sel ? "opacity-40" : ""}`}
              style={active ? ({ ["--tw-ring-color" as any]: color } as CSSProperties) : undefined}
            >
              {/* 步骤序号 */}
              <span className="absolute -left-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-bg-base text-[10px] font-semibold text-ink-faint ring-1 ring-border">
                {i + 1}
              </span>

              {/* 勾选框(选不选进一键运行) */}
              {onToggleSelect && (
                <span
                  role="checkbox"
                  aria-checked={sel}
                  onClick={(e) => {
                    e.stopPropagation();
                    onToggleSelect(step.id);
                  }}
                  className="absolute -right-1.5 -top-1.5 z-10 flex h-5 w-5 cursor-pointer items-center justify-center rounded-full bg-bg-base text-[11px] font-bold leading-none ring-1 transition hover:ring-accent"
                  style={{
                    color: sel ? color : "rgb(var(--ink-faint))",
                    ["--tw-ring-color" as any]: sel ? color : "rgb(var(--border))",
                  }}
                  title={sel ? "已选入一键运行(点击取消)" : "未选(点击加入一键运行)"}
                >
                  {sel ? "✓" : ""}
                </span>
              )}

              {/* 图标圆环(按状态着色) */}
              <span
                className={`relative flex h-12 w-12 items-center justify-center rounded-full ${
                  step.status === "running" ? "flow-node-running" : ""
                }`}
                style={{
                  color,
                  backgroundColor:
                    step.status === "pending"
                      ? "rgb(var(--bg-muted))"
                      : `color-mix(in srgb, ${color} 14%, rgb(var(--bg-surface)))`,
                  border: `1.5px ${step.status === "pending" ? "dashed" : "solid"} ${color}`,
                }}
              >
                <Icon size={20} />
                {/* 状态角标 */}
                {step.status === "done" && (
                  <span
                    className="absolute -bottom-1 -right-1 flex h-[18px] w-[18px] items-center justify-center rounded-full text-white"
                    style={{ backgroundColor: statusColor("done") }}
                  >
                    <Check size={11} strokeWidth={3} />
                  </span>
                )}
                {step.status === "failed" && (
                  <span
                    className="absolute -bottom-1 -right-1 flex h-[18px] w-[18px] items-center justify-center rounded-full text-white"
                    style={{ backgroundColor: statusColor("failed") }}
                  >
                    <X size={11} strokeWidth={3} />
                  </span>
                )}
                {step.status === "running" && (
                  <span
                    className="absolute -bottom-1 -right-1 flex h-[18px] w-[18px] items-center justify-center rounded-full text-white"
                    style={{ backgroundColor: statusColor("running") }}
                  >
                    <Loader2 size={11} className="animate-spin" />
                  </span>
                )}
              </span>

              {/* 文本 */}
              <span className="text-center leading-tight">
                <span className="block text-[12px] font-medium text-ink">{step.label}</span>
                {step.sublabel && (
                  <span className="block text-[10px] text-ink-faint">{step.sublabel}</span>
                )}
              </span>

              {/* 状态文字 */}
              <span className="text-[10px] font-medium" style={{ color }}>
                {STATUS_LABEL[step.status]}
              </span>
            </button>
          </div>
        );
      })}
    </div>
  );
}
