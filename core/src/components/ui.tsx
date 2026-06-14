import {
  ReactNode,
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  TextareaHTMLAttributes,
  SelectHTMLAttributes,
  useEffect,
  useState,
} from "react";
import { twMerge } from "tailwind-merge";
import { X } from "lucide-react";

// ─────────────────────────────────────────────────────
// PageHeader
// ─────────────────────────────────────────────────────
export function PageHeader({
  title,
  subtitle,
  action,
  back,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div className="flex items-center gap-3">
        {back}
        <div>
          <h1 className="text-xl font-semibold">{title}</h1>
          {subtitle && (
            <div className="text-sm text-ink-muted mt-1">{subtitle}</div>
          )}
        </div>
      </div>
      {action}
    </div>
  );
}

// ─────────────────────────────────────────────────────
// Card
// ─────────────────────────────────────────────────────
export function Card({
  children,
  className,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  onClick?: () => void;
}) {
  return (
    <div
      onClick={onClick}
      className={twMerge(
        "bg-bg-surface border border-bg-muted rounded-lg p-4",
        onClick && "cursor-pointer hover:border-accent/50 transition",
        className
      )}
    >
      {children}
    </div>
  );
}

// ─────────────────────────────────────────────────────
// Button
// ─────────────────────────────────────────────────────
export function Button({
  children,
  variant = "primary",
  size = "default",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "default" | "sm" | "lg";
}) {
  const variants = {
    primary: "bg-accent text-white hover:opacity-90",
    secondary: "bg-bg-muted text-ink hover:bg-bg-muted/70",
    ghost: "text-ink-muted hover:bg-bg-muted/50",
    danger: "bg-red-500 text-white hover:bg-red-600",
  };
  const sizes = {
    default: "px-3 py-1.5 text-sm",
    sm: "px-2 py-1 text-xs",
    lg: "px-4 py-2 text-sm",
  };
  return (
    <button
      className={twMerge(
        "rounded transition disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center justify-center gap-1.5",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    >
      {children}
    </button>
  );
}

// ─────────────────────────────────────────────────────
// Pill
// ─────────────────────────────────────────────────────
export function Pill({
  children,
  variant = "info",
}: {
  children: ReactNode;
  variant?: "info" | "success" | "warning" | "error" | "neutral";
}) {
  const colors = {
    info: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300",
    success:
      "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300",
    warning:
      "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300",
    error: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
    neutral: "bg-bg-muted text-ink-muted",
  };
  return (
    <span
      className={twMerge(
        "inline-block px-1.5 py-0.5 rounded text-xs font-medium",
        colors[variant]
      )}
    >
      {children}
    </span>
  );
}

// ─────────────────────────────────────────────────────
// EmptyState
// ─────────────────────────────────────────────────────
export function EmptyState({
  title,
  hint,
  action,
  icon,
}: {
  title: string;
  hint?: string;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="text-center py-16">
      {icon && (
        <div className="mb-3 inline-flex items-center justify-center w-12 h-12 rounded-full bg-bg-muted text-ink-faint">
          {icon}
        </div>
      )}
      <div className="text-base text-ink-muted mb-1">{title}</div>
      {hint && <div className="text-xs text-ink-faint max-w-md mx-auto">{hint}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// ─────────────────────────────────────────────────────
// Banner
// ─────────────────────────────────────────────────────
export function Banner({
  children,
  variant = "info",
  className,
}: {
  children: ReactNode;
  variant?: "info" | "warning" | "error" | "success";
  className?: string;
}) {
  const styles = {
    info: "bg-blue-50 dark:bg-blue-950/30 border-blue-200 dark:border-blue-900 text-blue-800 dark:text-blue-200",
    warning:
      "bg-amber-50 dark:bg-amber-950/30 border-amber-200 dark:border-amber-900 text-amber-800 dark:text-amber-200",
    error:
      "bg-red-50 dark:bg-red-950/30 border-red-200 dark:border-red-900 text-red-800 dark:text-red-200",
    success:
      "bg-emerald-50 dark:bg-emerald-950/30 border-emerald-200 dark:border-emerald-900 text-emerald-800 dark:text-emerald-200",
  };
  return (
    <div className={twMerge("px-4 py-3 border rounded text-sm", styles[variant], className)}>
      {children}
    </div>
  );
}

// ─────────────────────────────────────────────────────
// 表单组件
// ─────────────────────────────────────────────────────
export function Field({
  label,
  hint,
  error,
  required,
  children,
}: {
  label?: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div>
      {label && (
        <label className="block text-xs font-medium text-ink-muted mb-1">
          {label}
          {required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
      )}
      {children}
      {error && <div className="text-xs text-red-600 mt-1">{error}</div>}
      {hint && !error && (
        <div className="text-xs text-ink-faint mt-1">{hint}</div>
      )}
    </div>
  );
}

export function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={twMerge(
        "w-full px-3 py-2 text-sm rounded border border-bg-muted bg-bg-surface",
        "focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30",
        "placeholder:text-ink-faint",
        className
      )}
      {...props}
    />
  );
}

/**
 * 数字输入框 — 比 <Input type="number"> 更友好。
 * 用 type="text" + inputMode="numeric",允许中间状态(空、删字、回退)。
 * value 是数字,但内部维护 string state,用户输完(blur 或回车)才提交。
 */
export function NumberInput({
  value,
  onChange,
  min,
  max,
  className,
  ...props
}: {
  value: number;
  onChange: (n: number) => void;
  min?: number;
  max?: number;
  className?: string;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type">) {
  const [text, setText] = useState(String(value));
  
  // 外部 value 变了(比如自动预填)同步进来
  useEffect(() => {
    setText(String(value));
  }, [value]);
  
  function commit() {
    const n = parseInt(text, 10);
    if (isNaN(n)) {
      setText(String(value));  // 还原
      return;
    }
    let v = n;
    if (typeof min === "number" && v < min) v = min;
    if (typeof max === "number" && v > max) v = max;
    setText(String(v));
    if (v !== value) onChange(v);
  }
  
  return (
    <input
      type="text"
      inputMode="numeric"
      pattern="[0-9]*"
      value={text}
      onChange={(e) => {
        // 允许空字符串、纯数字
        const v = e.target.value;
        if (v === "" || /^[0-9]+$/.test(v)) {
          setText(v);
        }
      }}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
      }}
      className={twMerge(
        "w-full px-3 py-2 text-sm rounded border border-bg-muted bg-bg-surface",
        "focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30",
        "placeholder:text-ink-faint",
        className
      )}
      {...props}
    />
  );
}

export function Textarea({
  className,
  ...props
}: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={twMerge(
        "w-full px-3 py-2 text-sm rounded border border-bg-muted bg-bg-surface",
        "focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30",
        "placeholder:text-ink-faint",
        className
      )}
      {...props}
    />
  );
}

export function Select({
  className,
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={twMerge(
        "w-full px-3 py-2 text-sm rounded border border-bg-muted bg-bg-surface",
        "focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30",
        className
      )}
      {...props}
    >
      {children}
    </select>
  );
}

// ─────────────────────────────────────────────────────
// Modal
// ─────────────────────────────────────────────────────
export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  size = "default",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "default" | "lg";
}) {
  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  const widths = { default: "max-w-md", lg: "max-w-2xl" };

  return (
    <div
      className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={twMerge(
          "bg-bg-surface rounded-lg w-full shadow-2xl",
          widths[size]
        )}
      >
        <div className="flex items-center justify-between px-5 py-3 border-b border-bg-muted">
          <h2 className="text-base font-medium">{title}</h2>
          <button
            onClick={onClose}
            className="text-ink-faint hover:text-ink"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>
        <div className="p-5">{children}</div>
        {footer && (
          <div className="px-5 py-3 border-t border-bg-muted bg-bg-base/40 rounded-b-lg flex justify-end gap-2">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────
// 确认对话框
// ─────────────────────────────────────────────────────
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmLabel = "确定",
  cancelLabel = "取消",
  danger = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {cancelLabel}
          </Button>
          <Button
            variant={danger ? "danger" : "primary"}
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-sm text-ink-muted">{message}</div>
    </Modal>
  );
}

// ─────────────────────────────────────────────────────
// Spinner
// ─────────────────────────────────────────────────────
export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <span
      className="inline-block animate-spin rounded-full border-2 border-ink-faint border-t-transparent"
      style={{ width: size, height: size }}
    />
  );
}

// ─────────────────────────────────────────────────────
// Loading container
// ─────────────────────────────────────────────────────
export function Loading({ label = "加载中..." }: { label?: string }) {
  return (
    <div className="flex items-center justify-center py-12 gap-2 text-sm text-ink-faint">
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
