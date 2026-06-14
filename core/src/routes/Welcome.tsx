/**
 * 加载页:等主程序后端就绪后自动跳转到 /projects。
 * 用户看到的应该只是短暂的"启动中..." → 然后是项目列表。
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";

export function Welcome() {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);

  // 等后端就绪
  useEffect(() => {
    let cancelled = false;
    let attempts = 0;
    
    async function poll() {
      while (!cancelled) {
        attempts++;
        try {
          await invoke<number>("get_backend_port");
          if (!cancelled) {
            // 就绪!跳到项目列表
            navigate("/projects", { replace: true });
            return;
          }
        } catch {
          // 还没就绪
        }
        await new Promise((r) => setTimeout(r, 500));
        // 30 秒后还没就绪,显示错误
        if (attempts > 60) {
          setError("主程序后端启动超时(30 秒未就绪)。请重启应用,或查看 ~/.plantomics/logs/ 排查。");
          return;
        }
      }
    }
    
    poll();
    return () => { cancelled = true; };
  }, [navigate]);

  // 计时显示
  useEffect(() => {
    if (error) return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [error]);

  return (
    <div className="min-h-screen flex items-center justify-center p-8">
      <div className="max-w-md text-center">
        <div className="mb-6">
          <div className="inline-block w-12 h-12 rounded-full bg-accent/20 flex items-center justify-center text-2xl mb-4">
            🌱
          </div>
          <h1 className="text-2xl font-light mb-1">PlantOmics Studio</h1>
          <p className="text-sm text-ink-muted">v1.0.0</p>
        </div>

        {error ? (
          <div className="bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 rounded-lg p-4 text-sm text-red-700 dark:text-red-300 text-left">
            <div className="font-medium mb-1">启动失败</div>
            <div className="text-xs">{error}</div>
          </div>
        ) : (
          <div className="text-sm text-ink-muted">
            <div className="inline-flex items-center gap-2">
              <span className="inline-block w-2 h-2 bg-amber-500 rounded-full animate-pulse"></span>
              <span>正在启动后端服务...</span>
            </div>
            {elapsed > 5 && (
              <div className="text-xs text-ink-faint mt-2">
                已等待 {elapsed} 秒
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
