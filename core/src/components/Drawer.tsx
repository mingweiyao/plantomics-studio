/**
 * 右侧滑出抽屉 —— 点击流程图的某一步时,该步的参数从右侧滑入,
 * 不离开圆形概览(保留上下文)。
 */
import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  accent?: string; // 顶部强调色(用步骤状态色)
  children: React.ReactNode;
}

export function Drawer({ open, onClose, title, subtitle, icon, accent, children }: DrawerProps) {
  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return createPortal(
    <>
      {/* 遮罩 */}
      <div
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px] transition-opacity duration-200 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
      />
      {/* 抽屉面板 */}
      <aside
        className={`fixed right-0 top-0 z-50 flex h-full w-full max-w-[560px] flex-col bg-bg-surface shadow-pop transition-transform duration-300 ease-out ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {/* 顶部条 */}
        <div className="flex items-center gap-3 border-b border-border px-5 py-4">
          {accent && <span className="h-8 w-1 rounded-full" style={{ backgroundColor: accent }} />}
          {icon && (
            <span
              className="flex h-9 w-9 items-center justify-center rounded-lg"
              style={{
                color: accent || "rgb(var(--accent))",
                backgroundColor: `color-mix(in srgb, ${accent || "rgb(var(--accent))"} 12%, rgb(var(--bg-surface)))`,
              }}
            >
              {icon}
            </span>
          )}
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-ink">{title}</div>
            {subtitle && <div className="truncate text-xs text-ink-faint">{subtitle}</div>}
          </div>
          <button
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-faint transition hover:bg-bg-muted hover:text-ink"
            aria-label="关闭"
          >
            <X size={18} />
          </button>
        </div>
        {/* 内容(可滚动) */}
        <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
      </aside>
    </>,
    document.body
  );
}
