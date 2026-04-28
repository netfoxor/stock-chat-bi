import { Button, Card, Space, Typography, message as antdMessage } from "antd";
import { useMemo } from "react";
import { useDashboardStore } from "../../store/dashboardStore";
import { extractSpecialBlocks, InlineDataTable, InlineECharts, MarkdownView } from "./renderers";

export function MessageItem(props: { message: any }) {
  const m = props.message;
  const addWidget = useDashboardStore((s) => s.addWidget);

  const { cleanMarkdown, blocks } = useMemo(() => extractSpecialBlocks(m.content ?? ""), [m.content]);
  const echarts = blocks.find((b) => b.kind === "echarts");
  const datatable = blocks.find((b) => b.kind === "datatable");

  const onCopy = async () => {
    await navigator.clipboard.writeText(m.content ?? "");
    antdMessage.success("已复制");
  };

  const onAddChart = async () => {
    if (!echarts) return;
    const layout = { i: "new", x: 0, y: Infinity, w: 6, h: 8 };
    await addWidget({
      type: "chart",
      data: echarts.data,
      layout,
    });
    antdMessage.success("图表已添加到大屏");
  };

  const onAddTable = async () => {
    if (!datatable) return;
    const layout = { i: "new", x: 0, y: Infinity, w: 6, h: 8 };
    await addWidget({
      type: "table",
      data: datatable.data,
      layout,
    });
    antdMessage.success("表格已添加到大屏");
  };

  return (
    <Card size="small" style={{ marginTop: 8 }} bodyStyle={{ padding: 12 }}>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Text type="secondary">{m.role === "user" ? "你" : "AI"}</Typography.Text>
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
    </Card>
  );
}

