import { Collapse, Space, Tag, Timeline, Typography } from "antd";
import { useMemo } from "react";

function mergeTraceRow(prev: any, next: any) {
  const meta = { ...(prev.meta ?? {}), ...(next.meta ?? {}) };
  const endAt = next.ended_at ?? prev.ended_at;
  if (endAt != null && String(endAt).length > 0) meta.phase = "end";
  return {
    ...prev,
    ...next,
    started_at: next.started_at ?? prev.started_at,
    ended_at: next.ended_at ?? prev.ended_at,
    input: next.input !== undefined ? next.input : prev.input,
    output: next.output !== undefined ? next.output : prev.output,
    meta,
    ts: next.ts ?? prev.ts,
  };
}

/** 按 trace_key 合并重复行后，仅按 started_at/ts 时间先后排序（与原数组顺序仅在时间并列时用作稳定次序）。 */
function normalizeTraceForTimeline(raw: any[]): any[] {
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const byKey = new Map<string, any>();
  const firstIdx = new Map<string, number>();
  const singles: Array<{ row: any; idx: number }> = [];

  for (let idx = 0; idx < raw.length; idx++) {
    const row = raw[idx];
    const tk = row?.meta?.trace_key;
    if (typeof tk === "string" && tk.length > 0) {
      if (!firstIdx.has(tk)) firstIdx.set(tk, idx);
      const prev = byKey.get(tk);
      byKey.set(tk, prev ? mergeTraceRow(prev, row) : { ...row });
    } else {
      singles.push({ row, idx });
    }
  }

  const keyed: Array<{ row: any; sortTime: number; tie: number }> = [];
  for (const [tk, row] of byKey) {
    keyed.push({
      row,
      sortTime: +new Date(row.started_at || row.ts || 0).getTime(),
      tie: firstIdx.get(tk)!,
    });
  }
  const unkeyed = singles.map(({ row, idx }) => ({
    row,
    sortTime: +new Date(row.started_at || row.ts || 0).getTime(),
    tie: idx,
  }));

  return [...keyed, ...unkeyed]
    .sort((a, b) => (a.sortTime !== b.sortTime ? a.sortTime - b.sortTime : a.tie - b.tie))
    .map((x) => x.row);
}

function kindDisplayLabel(kind: string): string {
  if (kind === "tool") return "工具";
  if (kind === "skill") return "技能";
  if (kind === "llm") return "大模型";
  return kind;
}

export function TraceTimeline(props: { trace: any[] }) {
  const normalized = useMemo(() => normalizeTraceForTimeline(Array.isArray(props.trace) ? props.trace : []), [props.trace]);
  const items = normalized.map((ev: any, idx: number) => {
    const name = String(ev?.name ?? "unknown");
    const kind = String(ev?.kind ?? "log");
    const startedAt = ev?.started_at ? String(ev.started_at) : "";
    const endedAt = ev?.ended_at ? String(ev.ended_at) : "";
    const ts = ev?.ts ? String(ev.ts) : "";
    const meta = ev?.meta ?? {};
    const input = ev?.input;
    const output = ev?.output;

    const status = (() => {
      if (meta.status === "error") return "error";
      if (meta.status === "ok" || meta.phase === "end" || !!endedAt) return "ok";
      return "running";
    })();

    const tag =
      status === "running" ? (
        <Tag color="processing">调用中</Tag>
      ) : status === "ok" ? (
        <Tag color="success">OK</Tag>
      ) : (
        <Tag color="error">失败</Tag>
      );

    // LLM：只展示语义化摘要，不展示原始 request/response JSON
    if (kind === "llm") {
      const u = (output?.usage ?? {}) as Record<string, number | undefined>;
      const round = output?.round ?? (meta.iteration_no != null ? Number(meta.iteration_no) + 1 : "—");
      const tools = Array.isArray(output?.requested_tools) ? output.requested_tools.map((t: any) => t?.name).filter(Boolean) : [];
      const total = u.total_tokens ?? u.total;
      const pt = u.prompt_tokens ?? u.prompt;
      const ct = u.completion_tokens ?? u.completion;
      const dur =
        startedAt && endedAt ? `${Math.max(0, new Date(endedAt).getTime() - new Date(startedAt).getTime())}` : "";

      return {
        key: String(meta.trace_key ?? meta.span_id ?? idx),
        children: (
          <div>
            <Space size={8} wrap>
              <Typography.Text strong>[大模型] {name}</Typography.Text>
              {tag}
            </Space>
            <div style={{ marginTop: 6, fontSize: 12, color: "#666", lineHeight: 1.7 }}>
              <div>
                第 <strong>{String(round)}</strong> 轮 · Token 总计 <strong>{total ?? "—"}</strong>
                {pt != null || ct != null ? (
                  <>
                    （提示 <strong>{pt ?? "—"}</strong> / 补全 <strong>{ct ?? "—"}</strong>）
                  </>
                ) : null}
              </div>
              {tools.length > 0 ? <div>本轮将调用的工具：{tools.join("、")}</div> : null}
              {startedAt && endedAt && fmtCN(startedAt) !== fmtCN(endedAt) ? (
                <div>
                  起止：{fmtCN(startedAt)} ～ {fmtCN(endedAt)}
                  {dur ? ` · 耗时约 ${dur} ms` : ""}
                </div>
              ) : startedAt ? (
                <div>记录时间：{fmtCN(startedAt)}</div>
              ) : ts ? (
                <div>记录时间：{fmtCN(ts)}</div>
              ) : null}
              {!!output?.error && <div style={{ color: "#cf1322" }}>错误：{String(output.error)}</div>}
            </div>
          </div>
        ),
      };
    }

    const safeJson = (v: any) => {
      try {
        if (v == null) return "";
        if (typeof v === "string") return v;
        return JSON.stringify(v, null, 2);
      } catch {
        return String(v);
      }
    };

    const inputText = safeJson(input);
    const outputText = safeJson(output);

    const details =
      inputText || outputText ? (
        <Collapse
          size="small"
          style={{ marginTop: 8 }}
          items={[
            ...(inputText
              ? [
                  {
                    key: "in",
                    label: "入参",
                    children: (
                      <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{inputText}</pre>
                    ),
                  },
                ]
              : []),
            ...(outputText
              ? [
                  {
                    key: "out",
                    label: "结果",
                    children: (
                      <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{outputText}</pre>
                    ),
                  },
                ]
              : []),
          ]}
        />
      ) : null;

    const startShow = startedAt || ts;
    const durMs =
      startedAt && endedAt ? Math.max(0, new Date(endedAt).getTime() - new Date(startedAt).getTime()) : null;

    return {
      key: String(meta.trace_key ?? meta.span_id ?? idx),
      children: (
        <div>
          <Space size={8} wrap>
            <Typography.Text strong>
              [{kindDisplayLabel(kind)}] {name}
            </Typography.Text>
            {tag}
          </Space>
          {startedAt && endedAt ? (
            fmtCN(startedAt) !== fmtCN(endedAt) ? (
              <div style={{ marginTop: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  起止：{fmtCN(startedAt)} ～ {fmtCN(endedAt)}
                  {durMs != null ? ` · 耗时约 ${durMs} ms` : ""}
                </Typography.Text>
              </div>
            ) : (
              <div style={{ marginTop: 4 }}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  记录时间：{fmtCN(startedAt)}
                  {durMs != null ? ` · 耗时约 ${durMs} ms` : ""}
                </Typography.Text>
              </div>
            )
          ) : startShow ? (
            <div style={{ marginTop: 4 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                时间：{fmtCN(startShow)}
              </Typography.Text>
            </div>
          ) : null}
          {details}
        </div>
      ),
    };
  });

  return <Timeline items={items} />;
}

/** 中国区划时间展示（与后端语义一致时使用上海时区，不在文案中写「东八区」）。 */
export function fmtCN(input: string) {
  try {
    const d = new Date(input);
    if (Number.isNaN(d.getTime())) return input;
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).formatToParts(d);
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
    return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")}:${get("second")}`;
  } catch {
    return input;
  }
}
