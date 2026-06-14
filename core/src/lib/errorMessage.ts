/**
 * 把任何错误对象提取成可读字符串。
 * 
 * Tauri 的 invoke() 失败可能抛出:
 *   - 字符串(我们的 commands.rs 直接返回 String)
 *   - JSON 字符串(后端 HTTPException 序列化的 detail)
 *   - 对象 { detail: "..." } 或 { message: "..." }
 * 
 * 这个工具把它们都展平成单个字符串,避免界面显示 "[object Object]"。
 */
export function extractError(e: unknown): string {
  if (!e) return "未知错误";
  if (typeof e === "string") {
    // 尝试当 JSON 解析,可能是 HTTPException 序列化的
    try {
      const parsed = JSON.parse(e);
      return extractError(parsed);
    } catch {
      return e;
    }
  }
  if (typeof e === "object") {
    const obj = e as any;
    if (obj.detail) {
      if (typeof obj.detail === "string") return obj.detail;
      if (Array.isArray(obj.detail))
        return obj.detail.map((d: any) => extractError(d)).join("; ");
      if (typeof obj.detail === "object") {
        if (obj.detail.message) return obj.detail.message;
        return JSON.stringify(obj.detail);
      }
    }
    if (obj.message) return String(obj.message);
    if (obj.error) return String(obj.error);
    try {
      return JSON.stringify(obj);
    } catch {
      return "[无法序列化的错误]";
    }
  }
  return String(e);
}
