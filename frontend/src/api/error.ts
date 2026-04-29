import axios from "axios";

/**
 * FastAPI：`{ detail: string }` 或校验失败时的 `{ detail: [{ loc, msg, ... }] }`
 */
export function parseApiErrorMessage(err: unknown, fallback = "操作失败"): string {
  if (axios.isAxiosError(err) && err.response?.data !== undefined) {
    const data = err.response.data as Record<string, unknown>;
    const detail = data.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      const first = detail[0] as { msg?: unknown } | undefined;
      if (first?.msg !== undefined && typeof first.msg === "string") return first.msg;
    }
    const status = err.response.status;
    if (status === 409) return "该用户名已被占用";
    if (status === 401) return "用户名或密码错误";
  }
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}
