/**
 * 应用内密码输入对话框
 * 
 * 用于管理员权限操作(安装/卸载模块)。
 * 用户在这里输入密码,前端通过 IPC 传给后端,后端 sudo -S 执行。
 * 密码不持久化、不进日志。
 */
import { useState, useEffect, ReactNode } from "react";
import { Lock, AlertCircle } from "lucide-react";
import { Modal, Button, Field, Input, Banner } from "./ui";

export function PasswordDialog({
  open,
  onClose,
  onSubmit,
  title = "需要管理员权限",
  description = "请输入你的登录密码以授权此操作",
  busy = false,
  errorMessage = null,
}: {
  open: boolean;
  onClose: () => void;
  onSubmit: (password: string) => void;
  title?: string;
  description?: ReactNode;
  busy?: boolean;
  errorMessage?: string | null;
}) {
  const [password, setPassword] = useState("");

  // 打开/关闭时清空
  useEffect(() => {
    if (open) setPassword("");
  }, [open]);

  function handleSubmit() {
    if (!password) return;
    onSubmit(password);
    // 注意:onSubmit 调完不能立刻清空 password,因为父组件可能还在异步调 sudo
    // 父组件会在完成后通过 onClose 关闭对话框,我们在 useEffect 里清空
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && password && !busy) {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onClose}
      title={title}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={busy || !password}
          >
            {busy ? "执行中..." : "确认"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="flex items-start gap-3">
          <div className="shrink-0 w-10 h-10 rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 flex items-center justify-center">
            <Lock size={18} />
          </div>
          <div className="flex-1 text-sm text-ink-muted">
            {description}
          </div>
        </div>

        {errorMessage && (
          <Banner variant="error">
            <div className="flex items-center gap-2 text-xs">
              <AlertCircle size={12} className="shrink-0" />
              <span>{errorMessage}</span>
            </div>
          </Banner>
        )}

        <Field label="密码">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={handleKeyDown}
            autoFocus
            disabled={busy}
            placeholder="你的登录密码"
            autoComplete="current-password"
          />
        </Field>

        <div className="text-xs text-ink-faint">
          密码用于 <code>sudo</code> 提权,只在内存里临时使用,不会写入磁盘或日志。
        </div>
      </div>
    </Modal>
  );
}
