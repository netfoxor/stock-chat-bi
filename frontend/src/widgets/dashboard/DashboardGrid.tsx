import { Button, Card, Input, Space, Typography } from "antd";
import { useMemo, useState } from "react";
import GridLayout, { Layout, WidthProvider } from "react-grid-layout";
import { useDashboardStore } from "../../store/dashboardStore";
import { InlineDataTable, InlineECharts } from "../chat/renderers";
import { useElementSize } from "../../hooks/useElementSize";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

const AutoWidthGridLayout = WidthProvider(GridLayout);

export function DashboardGrid() {
  const widgets = useDashboardStore((s) => s.widgets);
  const updateWidget = useDashboardStore((s) => s.updateWidget);
  const deleteWidget = useDashboardStore((s) => s.deleteWidget);
  const updateLayoutBatch = useDashboardStore((s) => s.updateLayoutBatch);
  const { ref: wrapRef } = useElementSize<HTMLDivElement>();

  const layout: Layout[] = useMemo(
    () =>
      widgets.map((w) => ({
        i: String(w.id),
        x: w.layout?.x ?? 0,
        y: w.layout?.y ?? 0,
        w: w.layout?.w ?? 6,
        h: w.layout?.h ?? 8,
      })),
    [widgets],
  );

  const [editingId, setEditingId] = useState<number | null>(null);
  const [title, setTitle] = useState("");

  const onLayoutChange = (l: Layout[]) => {
    // 轻量：直接发到后端（后端会按 i=widget_id 写回 layout JSON）
    updateLayoutBatch(l).catch(() => void 0);
  };

  return (
    <div ref={wrapRef} style={{ height: "100%", background: "#fafafa", border: "1px solid #f0f0f0" }}>
      <AutoWidthGridLayout
        className="layout"
        layout={layout}
        cols={12}
        rowHeight={30}
        onLayoutChange={onLayoutChange}
        draggableHandle=".widget-drag"
      >
        {widgets.map((w) => (
          <div key={String(w.id)}>
            <Card
              size="small"
              title={
                <Space className="widget-drag" style={{ cursor: "move" }}>
                  <Typography.Text ellipsis style={{ maxWidth: 220 }}>
                    {w.title}
                  </Typography.Text>
                </Space>
              }
              extra={
                <Space>
                  {editingId === w.id ? (
                    <>
                      <Input
                        size="small"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        style={{ width: 160 }}
                      />
                      <Button
                        size="small"
                        type="primary"
                        onClick={() => {
                          updateWidget(w.id, { title }).catch(() => void 0);
                          setEditingId(null);
                        }}
                      >
                        保存
                      </Button>
                    </>
                  ) : (
                    <Button
                      size="small"
                      onClick={() => {
                        setEditingId(w.id);
                        setTitle(w.title);
                      }}
                    >
                      改标题
                    </Button>
                  )}
                  <Button size="small" danger onClick={() => deleteWidget(w.id).catch(() => void 0)}>
                    删除
                  </Button>
                </Space>
              }
              bodyStyle={{ flex: 1, minHeight: 0, padding: 8, display: "flex", flexDirection: "column" }}
              style={{ height: "100%", display: "flex", flexDirection: "column" }}
            >
              <WidgetBody widget={w} />
            </Card>
          </div>
        ))}
      </AutoWidthGridLayout>
    </div>
  );
}

function WidgetBody(props: { widget: any }) {
  const { ref, size } = useElementSize<HTMLDivElement>();
  const h = Math.max(0, size.height);

  return (
    <div ref={ref} style={{ flex: 1, minHeight: 0, height: "100%" }}>
      {props.widget.type === "chart" ? (
        <InlineECharts option={props.widget.data} height={h || 260} />
      ) : (
        <InlineDataTable value={props.widget.data} height={h || 260} showTitle={false} />
      )}
    </div>
  );
}

