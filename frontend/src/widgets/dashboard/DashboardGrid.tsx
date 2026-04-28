import { Button, Card, Input, Modal, Segmented, Space, Typography } from "antd";
import { SettingOutlined } from "@ant-design/icons";
import { useEffect, useMemo, useRef, useState } from "react";
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
  const [dragging, setDragging] = useState(false);

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
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [configOpen, setConfigOpen] = useState(false);
  const [configWidget, setConfigWidget] = useState<any>(null);
  const [configTab, setConfigTab] = useState<"sql" | "echarts" | "table" | "poll" | "raw">("sql");
  const [sqlVal, setSqlVal] = useState("");
  const [cronPreset, setCronPreset] = useState<"off" | "1m" | "5m" | "15m" | "1h" | "custom">("off");
  const [cronVal, setCronVal] = useState("");
  const [echartsJson, setEchartsJson] = useState("{}");
  const [tableJson, setTableJson] = useState("{}");
  const [rawJson, setRawJson] = useState<string>("{}");

  const onLayoutChange = (l: Layout[]) => {
    // 轻量：直接发到后端（后端会按 i=widget_id 写回 layout JSON）
    updateLayoutBatch(l).catch(() => void 0);
  };

  useEffect(() => {
    document.body.style.userSelect = dragging ? "none" : "";
    return () => {
      document.body.style.userSelect = "";
    };
  }, [dragging]);

  return (
    <div ref={wrapRef} style={{ height: "100%", background: "#fafafa", border: "1px solid #f0f0f0" }}>
      <AutoWidthGridLayout
        className="layout"
        layout={layout}
        cols={12}
        rowHeight={30}
        onLayoutChange={onLayoutChange}
        draggableHandle=".ant-card-head"
        draggableCancel="input,textarea,button,.ant-btn,.ant-input"
        onDragStart={() => setDragging(true)}
        onDragStop={() => setDragging(false)}
        onResizeStart={() => setDragging(true)}
        onResizeStop={() => setDragging(false)}
      >
        {widgets.map((w) => (
          <div key={String(w.id)}>
            <Card
              size="small"
              title={
                <div
                  onDoubleClick={() => {
                    setEditingId(w.id);
                    setTitle(w.title);
                    setTimeout(() => inputRef.current?.focus(), 0);
                  }}
                  style={{ cursor: "move" }}
                >
                  {editingId === w.id ? (
                    <Input
                      ref={(el) => {
                        // @ts-ignore
                        inputRef.current = el?.input ?? null;
                      }}
                      size="small"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      onBlur={() => {
                        updateWidget(w.id, { title }).catch(() => void 0);
                        setEditingId(null);
                      }}
                      onPressEnter={() => {
                        updateWidget(w.id, { title }).catch(() => void 0);
                        setEditingId(null);
                      }}
                      style={{ width: 240 }}
                    />
                  ) : (
                    <Typography.Text ellipsis style={{ maxWidth: 260, display: "inline-block" }}>
                      {w.title}
                    </Typography.Text>
                  )}
                </div>
              }
              extra={
                <Space>
                  <Button
                    size="small"
                    icon={<SettingOutlined />}
                    onClick={() => {
                      const cfg = w.config ?? {};
                      setConfigWidget(w);
                      setConfigTab("sql");
                      setSqlVal(String(cfg.sql ?? ""));
                      setCronVal(String(cfg?.poll?.cron ?? ""));
                      setCronPreset(cfg?.poll?.cron ? "custom" : "off");
                      setEchartsJson(JSON.stringify(cfg?.echarts ?? {}, null, 2));
                      setTableJson(JSON.stringify(cfg?.table ?? {}, null, 2));
                      setRawJson(JSON.stringify(cfg ?? {}, null, 2));
                      setConfigOpen(true);
                    }}
                  />
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

      <Modal
        open={configOpen}
        title="组件配置"
        okText="保存"
        cancelText="取消"
        onCancel={() => setConfigOpen(false)}
        onOk={async () => {
          if (!configWidget) return;
          try {
            const echartsObj = JSON.parse(echartsJson || "{}");
            const tableObj = JSON.parse(tableJson || "{}");
            const baseRaw = JSON.parse(rawJson || "{}");
            const cron =
              cronPreset === "off"
                ? ""
                : cronPreset === "1m"
                  ? "*/1 * * * *"
                  : cronPreset === "5m"
                    ? "*/5 * * * *"
                    : cronPreset === "15m"
                      ? "*/15 * * * *"
                      : cronPreset === "1h"
                        ? "0 * * * *"
                        : cronVal;

            const merged = {
              ...baseRaw,
              sql: sqlVal,
              echarts: echartsObj,
              table: tableObj,
              poll: cron ? { ...(baseRaw.poll ?? {}), cron } : { ...(baseRaw.poll ?? {}), cron: "" },
            };

            await updateWidget(configWidget.id, { config: merged });
            setConfigOpen(false);
          } catch {
            // ignore; 留给用户修 JSON
          }
        }}
      >
        <Segmented
          block
          value={configTab}
          onChange={(v) => setConfigTab(v as any)}
          options={[
            { label: "SQL", value: "sql" },
            { label: "ECharts", value: "echarts" },
            { label: "表格", value: "table" },
            { label: "轮询", value: "poll" },
            { label: "原始JSON", value: "raw" },
          ]}
          style={{ marginBottom: 12 }}
        />

        {configTab === "sql" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              写 SQL（MySQL 8）。这里先做配置保存，后续可接入后端执行与自动刷新。
            </Typography.Paragraph>
            <Input.TextArea rows={8} value={sqlVal} onChange={(e) => setSqlVal(e.target.value)} placeholder="SELECT ..." />
          </>
        )}

        {configTab === "echarts" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              填写 ECharts option 的“覆盖项”（JSON）。
            </Typography.Paragraph>
            <Input.TextArea rows={10} value={echartsJson} onChange={(e) => setEchartsJson(e.target.value)} />
          </>
        )}

        {configTab === "table" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              填写表格属性覆盖项（JSON），例如分页、列宽等（后续会逐步落地到渲染逻辑）。
            </Typography.Paragraph>
            <Input.TextArea rows={10} value={tableJson} onChange={(e) => setTableJson(e.target.value)} />
          </>
        )}

        {configTab === "poll" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              轮询 cron（分钟级）。先保存配置，执行逻辑后续接上。
            </Typography.Paragraph>
            <Space wrap>
              <Segmented
                value={cronPreset}
                onChange={(v) => setCronPreset(v as any)}
                options={[
                  { label: "关闭", value: "off" },
                  { label: "1分钟", value: "1m" },
                  { label: "5分钟", value: "5m" },
                  { label: "15分钟", value: "15m" },
                  { label: "1小时", value: "1h" },
                  { label: "自定义", value: "custom" },
                ]}
              />
            </Space>
            {cronPreset === "custom" && (
              <Input
                style={{ marginTop: 12 }}
                value={cronVal}
                onChange={(e) => setCronVal(e.target.value)}
                placeholder="例如：*/5 * * * *"
              />
            )}
          </>
        )}

        {configTab === "raw" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              完整 config JSON（兜底）。上面表单保存时会与这里合并。
            </Typography.Paragraph>
            <Input.TextArea rows={10} value={rawJson} onChange={(e) => setRawJson(e.target.value)} />
          </>
        )}
      </Modal>
    </div>
  );
}

function WidgetBody(props: { widget: any }) {
  const { ref, size } = useElementSize<HTMLDivElement>();
  const h = Math.max(0, size.height);

  return (
    <div ref={ref} style={{ flex: 1, minHeight: 0, height: "100%" }}>
      {props.widget.type === "chart" ? (
        <InlineECharts option={props.widget?.config?.echarts ?? props.widget.data} height={h || 260} />
      ) : (
        <InlineDataTable value={props.widget?.config?.table ?? props.widget.data} height={h || 260} showTitle={false} />
      )}
    </div>
  );
}

