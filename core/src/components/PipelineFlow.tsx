/**
 * 数据处理流程时间线 —— 组学模块上游页的视觉核心。
 *
 * 纵向时间线:每个步骤一行,左侧状态节点 + 连接线串成顺序,右侧是可点击的步骤卡。
 * 步骤分两组:核心流程(默认勾选)与高级分析(可选)。
 * 每步 5 种状态:未运行 / 已配置 / 运行中(脉冲)/ 已完成 / 失败。
 *   - 点步骤卡   → onSelectStep(打开该步参数抽屉)
 *   - 顶部「一键运行」→ onRunAll(按勾选顺序执行核心流程)
 *   - 步骤卡上的勾选框 / 子选项 → onToggleSelect(决定一键运行跑哪些)
 *
 * 纯展示组件,状态由父组件传入(由 upstream_params + 任务状态推导)。
 */
import type { LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";
import { Play, Check, X, Loader2, ChevronRight } from "lucide-react";

export type StepStatus = "pending" | "configured" | "running" | "done" | "failed";

export interface FlowSubOption {
  id: string; // 与 selected 集合里的键一致
  label: string;
}

export interface FlowStep {
  id: string; // 节点唯一 id(也用于 activeId / 打开抽屉)
  label: string;
  sublabel?: string;
  icon: LucideIcon;
  status: StepStatus;
  group?: "core" | "advanced";
  selectKey?: string; // 勾选框对应 selected 里的键(默认 = id)
  selectable?: boolean; // false = 自动步骤,不显示勾选框(如建索引随比对自动跑)
  subOptions?: FlowSubOption[]; // 卡内的子开关(如数据量统计 / 比对统计)
  note?: string; // 卡下方的小提示
}

interface PipelineFlowProps {
  steps: FlowStep[];
  activeId?: string;
  onSelectStep: (id: string) => void;
  onRunAll: () => void;
  running?: boolean;
  canRun?: boolean;
  selected?: Set<string>;
  onToggleSelect?: (key: string) => void;
}

const STATUS_LABEL: Record<StepStatus, string> = {
  pending: "未运行",
  configured: "已配置",
  running: "运行中",
  done: "已完成",
  failed: "失败",
};

const GROUP_LABEL: Record<string, string> = {
  core: "核心流程",
  advanced: "高级分析 · 可选",
};

function statusColor(s: StepStatus): string {
  return `rgb(var(--state-${s}))`;
}

function StatusDot({ status }: { status: StepStatus }) {
  const c = statusColor(status);
  const base = "relative z-10 flex h-6 w-6 items-center justify-center rounded-full";
  if (status === "running")
    return (
      <span className={`${base} flow-node-running text-white`} style={{ backgroundColor: c }}>
        <Loader2 size={13} className="animate-spin" />
      </span>
    );
  if (status === "done")
    return (
      <span className={`${base} text-white`} style={{ backgroundColor: c }}>
        <Check size={13} strokeWidth={3} />
      </span>
    );
  if (status === "failed")
    return (
      <span className={`${base} text-white`} style={{ backgroundColor: c }}>
        <X size={13} strokeWidth={3} />
      </span>
    );
  if (status === "configured")
    return (
      <span
        className={base}
        style={{
          backgroundColor: `color-mix(in srgb, ${c} 22%, rgb(var(--bg-surface)))`,
          border: `2px solid ${c}`,
        }}
      />
    );
  return (
    <span className={`${base} bg-bg-surface`} style={{ border: "2px solid rgb(var(--border))" }} />
  );
}

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
  const core = steps.filter((s) => (s.group ?? "core") === "core");
  const total = core.length;
  const doneCount = core.filter((s) => s.status === "done").length;
  const frac = total > 0 ? doneCount / total : 0;

  return (
    <div className="mx-auto w-full max-w-[680px] select-none">
      {/* 顶部:标题 + 一键运行 + 进度 */}
      <div className="rounded-2xl border border-border bg-bg-surface p-4 shadow-card sm:p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
              数据处理流程
            </div>
            <div className="mt-1 text-[13px] text-ink-muted">
              点步骤可单独配置 / 运行;勾选后「一键运行」按顺序执行
            </div>
          </div>
          <button
            onClick={onRunAll}
            disabled={!canRun || running}
            title={canRun ? "按勾选顺序执行核心流程" : "请先在项目里配置参考基因组(GTF)并至少勾选一步"}
            className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-white shadow-pop transition hover:brightness-105 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-50"
          >
            {running ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} className="ml-0.5" />}
            {running ? "运行中…" : "一键运行"}
          </button>
        </div>
        <div className="mt-3.5 flex items-center gap-3">
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-bg-muted">
            <div
              className="h-full rounded-full bg-accent transition-[width] duration-500"
              style={{ width: `${Math.round(frac * 100)}%` }}
            />
          </div>
          <div className="shrink-0 text-xs font-medium tabular-nums text-ink-muted">
            {doneCount}/{total} 步完成
          </div>
        </div>
      </div>

      {/* 时间线 */}
      <div className="mt-3">
        {steps.map((step, i) => {
          const prev = steps[i - 1];
          const next = steps[i + 1];
          const showGroup = (step.group ?? "core") !== (prev?.group ?? (i === 0 ? null : "core"));
          const isLastInGroup = !next || (next.group ?? "core") !== (step.group ?? "core");
          const c = statusColor(step.status);
          const active = step.id === activeId;
          const Icon = step.icon;
          const key = step.selectKey ?? step.id;
          const sel = !selected || selected.has(key);
          const lineDone = step.status === "done";
          const canSelect = step.selectable !== false && !!onToggleSelect;

          return (
            <div key={step.id}>
              {showGroup && (
                <div
                  className={`mb-1.5 ml-10 text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-faint ${
                    i === 0 ? "" : "mt-4"
                  }`}
                >
                  {GROUP_LABEL[step.group ?? "core"]}
                </div>
              )}
              <div className="relative flex gap-3 pb-3 last:pb-0">
                {/* 左侧轨道:连接线 + 状态节点 */}
                <div className="relative w-7 shrink-0">
                  {!isLastInGroup && (
                    <span
                      className="absolute left-1/2 top-3 -bottom-3 w-[2px] -translate-x-1/2 rounded"
                      style={{
                        background: lineDone ? "rgb(var(--accent))" : "rgb(var(--border))",
                      }}
                    />
                  )}
                  <StatusDot status={step.status} />
                </div>

                {/* 步骤卡 */}
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelectStep(step.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelectStep(step.id);
                    }
                  }}
                  className={`-mt-0.5 flex-1 cursor-pointer rounded-xl border bg-bg-surface px-3.5 py-3 shadow-card outline-none transition focus-visible:ring-2 ${
                    active ? "ring-2" : "border-border hover:shadow-pop"
                  }`}
                  style={
                    active
                      ? ({ borderColor: c, ["--tw-ring-color" as any]: c } as CSSProperties)
                      : ({ ["--tw-ring-color" as any]: "rgb(var(--accent))" } as CSSProperties)
                  }
                >
                  <div className="flex items-center gap-3">
                    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-bg-muted text-[12px] font-semibold tabular-nums text-ink-muted">
                      {i + 1}
                    </span>
                    <span
                      className="grid h-9 w-9 shrink-0 place-items-center rounded-lg"
                      style={{
                        color: c,
                        backgroundColor: `color-mix(in srgb, ${c} 12%, rgb(var(--bg-surface)))`,
                      }}
                    >
                      <Icon size={18} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold text-ink">{step.label}</div>
                      {step.sublabel && (
                        <div className="truncate text-[11px] text-ink-faint">{step.sublabel}</div>
                      )}
                    </div>
                    <span
                      className="hidden shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium sm:inline-block"
                      style={{ color: c, backgroundColor: `color-mix(in srgb, ${c} 12%, transparent)` }}
                    >
                      {STATUS_LABEL[step.status]}
                    </span>
                    {canSelect ? (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleSelect!(key);
                        }}
                        aria-pressed={sel}
                        title={sel ? "已选入一键运行(点击取消)" : "未选入一键运行(点击加入)"}
                        className="grid h-5 w-5 shrink-0 place-items-center rounded-md border text-white transition"
                        style={{
                          borderColor: sel ? c : "rgb(var(--border))",
                          backgroundColor: sel ? c : "transparent",
                        }}
                      >
                        {sel && <Check size={12} strokeWidth={3} />}
                      </button>
                    ) : (
                      <span
                        className="shrink-0 rounded-md bg-bg-muted px-1.5 py-0.5 text-[10px] font-medium text-ink-faint"
                        title="随比对自动执行,已存在则跳过"
                      >
                        自动
                      </span>
                    )}
                    <ChevronRight size={16} className="shrink-0 text-ink-faint" />
                  </div>

                  {/* 子选项(如 数据量统计 / 比对统计) */}
                  {step.subOptions && step.subOptions.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5 pl-[68px]">
                      {step.subOptions.map((opt) => {
                        const on = !selected || selected.has(opt.id);
                        const ac = "rgb(var(--accent))";
                        return (
                          <button
                            key={opt.id}
                            onClick={(e) => {
                              e.stopPropagation();
                              onToggleSelect && onToggleSelect(opt.id);
                            }}
                            aria-pressed={on}
                            className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] transition"
                            style={{
                              borderColor: on ? ac : "rgb(var(--border))",
                              color: on ? ac : "rgb(var(--ink-muted))",
                              backgroundColor: on ? `color-mix(in srgb, ${ac} 10%, transparent)` : "transparent",
                            }}
                          >
                            <span
                              className="grid h-3 w-3 place-items-center rounded-[3px]"
                              style={{
                                backgroundColor: on ? ac : "transparent",
                                border: on ? "none" : "1px solid rgb(var(--border))",
                              }}
                            >
                              {on && <Check size={9} strokeWidth={4} className="text-white" />}
                            </span>
                            {opt.label}
                          </button>
                        );
                      })}
                    </div>
                  )}

                  {step.note && (
                    <div className="mt-1.5 pl-[68px] text-[11px] text-ink-faint">{step.note}</div>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
