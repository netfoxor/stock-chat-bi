import { Button, Card, Collapse, Space, Typography, message as antdMessage } from "antd";
import { useMemo } from "react";
import { useDashboardStore } from "../../store/dashboardStore";
import { extractSpecialBlocks, InlineDataTable, InlineECharts, MarkdownView } from "./renderers";
import { TraceTimeline, fmtCN } from "./MessageItemTrace";

function MessageItem(props: { message: any }) {
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

      {!!m?.extra?.trace?.length && (
        <div style={{ marginTop: 10 }}>
          <Collapse
            size="small"
            items={[
              {
                key: "trace",
                label: "执行轨迹",
                children: <TraceTimeline trace={m.extra.trace} />,
              },
            ]}
          />
          <Typography.Paragraph type="secondary" style={{ marginTop: 6, marginBottom: 0, fontSize: 12 }}>
            说明：不展示模型内部“思考”原文。大模型轨迹只显示轮次与 Token；工具/技能可展开查看入参与结果。
          </Typography.Paragraph>
        </div>
      )}

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
    </Card>
  );
}

export { MessageItem };
