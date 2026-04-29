import { Button, Card, Input, Modal, Segmented, Space, Typography } from "antd";
import { SettingOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import GridLayout, { Layout, WidthProvider } from "react-grid-layout";
import { api } from "../../api/client";
import { useDashboardStore } from "../../store/dashboardStore";
import { InlineDataTable, InlineECharts } from "../chat/renderers";
import { useElementSize } from "../../hooks/useElementSize";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

const AutoWidthGridLayout = WidthProvider(GridLayout);

type CronPreset = "off" | "1m" | "5m" | "15m" | "1h" | "custom";

/** 解析「每 N 分钟 / 每小时」类 cron，得到秒数；不支持则返回 null */
function cronToIntervalSeconds(cron: string): number | null {
  const c = (cron ?? "").trim();
  if (!c) return null;
  const m1 = /^\*\/(\d+)\s+\*\s+\*\s+\*\s+\*$/.exec(c);
  if (m1) return parseInt(m1[1], 10) * 60;
  const m2 = /^0\s+\*\/(\d+)\s+\*\s+\*\s+\*$/.exec(c);
  if (m2) return parseInt(m2[1], 10) * 3600;
  return null;
}

function resolveIntervalSec(params: { cronPreset: CronPreset; cronVal: string; cronStr: string }): number {
  if (params.cronPreset === "off") return 0;
  const map: Record<string, number> = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600 };
  if (params.cronPreset !== "custom") return map[params.cronPreset] ?? 0;
  return cronToIntervalSeconds(params.cronVal || params.cronStr) ?? 300;
}

function inferCronUiFromConfig(cfg: any): { preset: CronPreset; val: string } {
  const cron = String(cfg?.poll?.cron ?? "").trim();
  const sec = Number(cfg?.poll?.interval_sec);
  if (!cron) {
    if (sec === 60) return { preset: "1m", val: "" };
    if (sec === 300) return { preset: "5m", val: "" };
    if (sec === 900) return { preset: "15m", val: "" };
    if (sec === 3600) return { preset: "1h", val: "" };
    if (sec > 0) return { preset: "custom", val: `*/${Math.max(1, Math.round(sec / 60))} * * * *` };
    return { preset: "off", val: "" };
  }
  if (cron === "*/1 * * * *") return { preset: "1m", val: "" };
  if (cron === "*/5 * * * *") return { preset: "5m", val: "" };
  if (cron === "*/15 * * * *") return { preset: "15m", val: "" };
  if (cron === "0 * * * *") return { preset: "1h", val: "" };
  return { preset: "custom", val: cron };
}

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
  const [cronPreset, setCronPreset] = useState<CronPreset>("off");
  const [cronVal, setCronVal] = useState("");
  const [echartsJson, setEchartsJson] = useState("{}");
  const [tableJson, setTableJson] = useState("{}");
  const [rawJson, setRawJson] = useState<string>("{}");

  const onLayoutChange = (l: Layout[]) => {
    updateLayoutBatch(l).catch(() => void 0);
  };

  useEffect(() => {
    document.body.style.userSelect = dragging ? "none" : "";
    return () => {
      document.body.style.userSelect = "";
    };
  }, [dragging]);

  return (
    <div ref={wrapRef} style={{ height: "100%", background: "#fafafa", border: "1px solid #f0f0f0", position: "relative" }}>
      {widgets.length === 0 && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
            color: "#d9d9d9",
            fontSize: 15,
            textAlign: "center",
            padding: 24,
            zIndex: 0,
          }}
        >
          和AI助理聊天，获取想到的数据，并添加到此大屏
        </div>
      )}
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
                      const ui = inferCronUiFromConfig(cfg);
                      setCronPreset(ui.preset);
                      setCronVal(ui.val);
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
              <WidgetBody key={`${w.id}-${String(w.updated_at ?? "")}`} widget={w} />
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
            const cronStr =
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

            const interval_sec = resolveIntervalSec({ cronPreset, cronVal, cronStr });

            const merged = {
              ...baseRaw,
              sql: sqlVal,
              echarts: echartsObj,
              table: tableObj,
              poll: cronStr
                ? { ...(baseRaw.poll ?? {}), cron: cronStr, interval_sec }
                : { ...(baseRaw.poll ?? {}), cron: "", interval_sec: 0 },
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
              填写只读 SELECT（MySQL）。保存后表格/图表会按轮询间隔调用「/dashboard/query」拉取最新数据；图表会由服务端按查询结果重算 ECharts。
            </Typography.Paragraph>
            <Input.TextArea rows={8} value={sqlVal} onChange={(e) => setSqlVal(e.target.value)} placeholder="SELECT ..." />
          </>
        )}

        {configTab === "echarts" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              初次展示或轮询开启前可用作静态模板；启用轮询后以接口返回的 option 为准（与聊天里添加大屏时的图表逻辑一致）。
            </Typography.Paragraph>
            <Input.TextArea rows={10} value={echartsJson} onChange={(e) => setEchartsJson(e.target.value)} />
          </>
        )}

        {configTab === "table" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              Ant Design Table 覆盖项（列宽、分页等）；数据来自 SQL 刷新结果。
            </Typography.Paragraph>
            <Input.TextArea rows={10} value={tableJson} onChange={(e) => setTableJson(e.target.value)} />
          </>
        )}

        {configTab === "poll" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              选择刷新间隔（内部保存为 cron 与 interval_sec）。关闭则仅在进入大屏时加载一次。
            </Typography.Paragraph>
            <Space wrap>
              <Segmented
                value={cronPreset}
                onChange={(v) => setCronPreset(v as CronPreset)}
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
                placeholder="例如：*/5 * * * *（每 5 分钟）"
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
  const cfg = props.widget.config ?? {};
  const sql = String(cfg.sql ?? "").trim();
  const intervalSec = Number(cfg?.poll?.interval_sec ?? 0) || 0;
  const includeEcharts = props.widget.type === "chart";

  const [tableVal, setTableVal] = useState<any>(() => cfg.table ?? props.widget.data ?? {});
  const [echartsVal, setEchartsVal] = useState<any>(() => cfg.echarts ?? props.widget.data ?? {});

  const refresh = useCallback(async () => {
    if (!sql) return;
    try {
      const { data } = await api.post("/dashboard/query", {
        sql,
        widget_id: props.widget.id,
        limit: 5000,
        include_echarts: includeEcharts,
      });
      if (data?.table != null && typeof data.table === "object") {
        setTableVal(data.table);
      }
      if (includeEcharts && data?.echarts && typeof data.echarts === "object") {
        setEchartsVal(data.echarts);
      }
    } catch {
      // 保留上一次成功数据
    }
  }, [sql, props.widget.id, includeEcharts]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (intervalSec <= 0 || !sql) return;
    const id = window.setInterval(() => void refresh(), intervalSec * 1000);
    return () => clearInterval(id);
  }, [intervalSec, sql, refresh]);

  const mergedTable = useMemo(
    () => ({ ...(typeof cfg.table === "object" ? cfg.table : {}), ...tableVal }),
    [cfg.table, tableVal],
  );

  return (
    <div ref={ref} style={{ flex: 1, minHeight: 0, height: "100%" }}>
      {props.widget.type === "chart" ? (
        <InlineECharts option={echartsVal} height={h || 260} />
      ) : (
        <InlineDataTable value={mergedTable} height={h || 260} showTitle={false} />
      )}
    </div>
  );
}
