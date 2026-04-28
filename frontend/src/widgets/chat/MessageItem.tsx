import { Button, Card, Space, Typography, message as antdMessage } from "antd";
import { useMemo } from "react";
import { useDashboardStore } from "../../store/dashboardStore";
import { extractSpecialBlock, InlineDataTable, InlineECharts, MarkdownView } from "./renderers";

export function MessageItem(props: { message: any }) {
  const m = props.message;
  const addWidget = useDashboardStore((s) => s.addWidget);

  const special = useMemo(() => extractSpecialBlock(m.content ?? ""), [m.content]);

  const onCopy = async () => {
    await navigator.clipboard.writeText(m.content ?? "");
    antdMessage.success("已复制");
  };

  const onAdd = async () => {
    if (!special) return;
    const layout = { i: "new", x: 0, y: Infinity, w: 6, h: 8 };
    await addWidget({
      type: special.kind === "echarts" ? "chart" : "table",
      data: special.data,
      layout,
    });
    antdMessage.success("已添加到大屏");
  };

  return (
    <Card size="small" style={{ marginTop: 8 }} bodyStyle={{ padding: 12 }}>
      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        <Typography.Text type="secondary">{m.role === "user" ? "你" : "AI"}</Typography.Text>
        <Space>
          <Button size="small" onClick={() => void onCopy()}>
            复制
          </Button>
          {special && (
            <Button size="small" type="primary" onClick={() => void onAdd()}>
              添加到大屏
            </Button>
          )}
        </Space>
      </Space>

      <div style={{ marginTop: 8 }}>
        <MarkdownView content={m.content ?? ""} />
      </div>

      {special?.kind === "echarts" && (
        <div style={{ marginTop: 12 }}>
          <InlineECharts option={special.data} />
        </div>
      )}
      {special?.kind === "datatable" && (
        <div style={{ marginTop: 12 }}>
          <InlineDataTable value={special.data} />
        </div>
      )}
    </Card>
  );
}

