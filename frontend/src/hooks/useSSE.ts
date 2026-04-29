import { useCallback, useRef, useState } from "react";
import { api } from "../api/client";
import { useAuthStore } from "../store/authStore";

type SSEEvent =
  | { type: "status"; message: string }
  | { type: "delta"; content: string }
  | { type: "trace"; event: any }
  | { type: "done"; trace?: any[] };

export function useSSE() {
  const abortRef = useRef<AbortController | null>(null);
  const [running, setRunning] = useState(false);

  const start = useCallback(async (url: string, body: any, onEvent: (e: SSEEvent) => void) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setRunning(true);

    const token = useAuthStore.getState().token;
    const res = await fetch(`${api.defaults.baseURL}${url}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal: ac.signal,
    });

    if (!res.ok) {
      setRunning(false);
      let msg = `请求失败 (${res.status})`;
      try {
        const ct = res.headers.get("content-type") ?? "";
        if (ct.includes("application/json")) {
          const j = (await res.json()) as { detail?: unknown };
          if (typeof j?.detail === "string" && j.detail.trim()) {
            msg = j.detail;
          } else if (Array.isArray(j?.detail)) {
            const first = j.detail[0] as { msg?: unknown } | undefined;
            if (typeof first?.msg === "string") msg = first.msg;
          }
        } else {
          const t = await res.text();
          if (t.trim()) msg = t.slice(0, 300);
        }
      } catch {
        // ignore
      }
      throw new Error(msg);
    }
    if (!res.body) {
      setRunning(false);
      throw new Error("服务端未返回可读流（可能为代理断开）");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buf = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        let idx: number;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = raw
            .split("\n")
            .map((l) => l.trimEnd())
            .find((l) => l.startsWith("data:"));
          if (!line) continue;
          const jsonStr = line.replace(/^data:\s*/, "");
          try {
            const evt = JSON.parse(jsonStr);
            onEvent(evt);
          } catch {
            // ignore parse errors
          }
        }
      }
    } finally {
      setRunning(false);
    }
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setRunning(false);
  }, []);

  return { start, stop, running };
}

