import { Button, Card, Collapse, Space, Tag, Timeline, Typography, message as antdMessage } from "antd";
import { useMemo } from "react";
import { useDashboardStore } from "../../store/dashboardStore";
import { extractSpecialBlocks, InlineDataTable, InlineECharts, MarkdownView } from "./renderers";

export function MessageItem(props: { message: any }) {
  const m = props.message;
  const addWidget = useDashboardStore((s) => s.addWidget);

  const { cleanMarkdown, blocks } = useMemo(() => extractSpecialBlocks(m.content ?? ""), [m.content]);
  const echarts = blocks.find((b) => b.kind === "echarts");
  const datatable = blocks.find((b) => b.kind === "datatable");
  const sql = blocks.find((b) => b.kind === "sql") as any;

  const onCopy = async () => {
    await navigator.clipboard.writeText(m.content ?? "");
    antdMessage.success("已复制");
  };

  const onAddChart = async () => {
    if (!echarts) return;
    const layout = { i: "new", x: 0, y: Infinity, w: 6, h: 8 };
    await addWidget({
      type: "chart",
      // 大屏持久化：以 sql + echarts/table 配置为核心，便于后续二次编辑
      data: {},
      config: { sql: sql?.data ?? "", echarts: echarts.data },
      layout,
    });
    antdMessage.success("图表已添加到大屏");
  };

  const onAddTable = async () => {
    if (!datatable) return;
    const layout = { i: "new", x: 0, y: Infinity, w: 6, h: 8 };
    await addWidget({
      type: "table",
      data: {},
      config: { sql: sql?.data ?? "", table: datatable.data },
      layout,
    });
    antdMessage.success("表格已添加到大屏");
  };

  return (
    <Card size="small" style={{ marginTop: 8 }} bodyStyle={{ padding: 12 }}>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Space size={8}>
          <Typography.Text type="secondary">{m.role === "user" ? "你" : "AI"}</Typography.Text>
          {!!m?.created_at && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {fmtCN(m.created_at)}
            </Typography.Text>
          )}
        </Space>
        <Space>
          <Button size="small" onClick={() => void onCopy()}>
            复制
          </Button>
        </Space>
      </Space>

      <div style={{ marginTop: 8 }}>
        <MarkdownView content={cleanMarkdown || m.content || ""} />
      </div>

      {echarts && (
        <div style={{ marginTop: 12, position: "relative" }}>
          <div style={{ position: "absolute", right: 0, top: 0, zIndex: 1 }}>
            <Button size="small" type="primary" onClick={() => void onAddChart()}>
              添加到大屏
            </Button>
          </div>
          <InlineECharts option={echarts.data} />
        </div>
      )}
      {datatable && (
        <div style={{ marginTop: 12, position: "relative" }}>
          <div style={{ position: "absolute", right: 0, top: 0, zIndex: 1 }}>
            <Button size="small" type="primary" onClick={() => void onAddTable()}>
              添加到大屏
            </Button>
          </div>
          <InlineDataTable value={datatable.data} />
        </div>
      )}

      {!!m?.extra?.trace?.length && (
        <div style={{ marginTop: 10 }}>
          <Collapse
            size="small"
            items={[
              {
                key: "trace",
                label: "执行轨迹（工具/Skill）",
                children: <TraceTimeline trace={m.extra.trace} />,
              },
            ]}
          />
          <Typography.Paragraph type="secondary" style={{ marginTop: 6, marginBottom: 0, fontSize: 12 }}>
            说明：系统不会展示“思考过程”，但会展示工具/Skill 的调用与结果。
          </Typography.Paragraph>
        </div>
      )}
    </Card>
  );
}

function TraceTimeline(props: { trace: any[] }) {
  const items = (Array.isArray(props.trace) ? props.trace : []).map((ev: any, idx: number) => {
    const name = String(ev?.name ?? "unknown");
    const kind = String(ev?.kind ?? "log");
    const startedAt = ev?.started_at ? String(ev.started_at) : "";
    const endedAt = ev?.ended_at ? String(ev.ended_at) : "";
    const ts = ev?.ts ? String(ev.ts) : "";
    const input = ev?.input;
    const output = ev?.output;

    const status = (() => {
      if (!endedAt) return "running";
      const outStr = typeof output === "string" ? output : "";
      if (outStr.startsWith("错误") || outStr.includes("失败") || outStr.toLowerCase().includes("error")) return "error";
      return "ok";
    })();

    const tag = status === "running" ? (
      <Tag color="processing">调用中</Tag>
    ) : status === "ok" ? (
      <Tag color="success">OK</Tag>
    ) : (
      <Tag color="error">失败</Tag>
    );

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

    return {
      key: String(idx),
      children: (
        <div>
          <Space size={8} wrap>
            <Typography.Text strong>
              [{kind}] {name}
            </Typography.Text>
            {tag}
          </Space>
          <div style={{ marginTop: 4 }}>
            {!!ts && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                时间：{fmtCN(ts)}
              </Typography.Text>
            )}
            {!!startedAt && (
              <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 10 }}>
                开始：{fmtCN(startedAt)}
              </Typography.Text>
            )}
            {!!endedAt && (
              <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 10 }}>
                结束：{fmtCN(endedAt)}
              </Typography.Text>
            )}
          </div>
          {details}
        </div>
      ),
    };
  });

  return <Timeline items={items} />;
}

function fmtCN(input: string) {
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

