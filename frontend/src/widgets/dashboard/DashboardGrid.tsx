import { Button, Card, Input, Modal, Segmented, Select, Space, Typography, message } from "antd";
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

const FALLBACK_CHART_TRANSFORMS = [
  { id: "", label: "（默认）由 SQL 查询结果自动生成图" },
  { id: "arima_forecast", label: "ARIMA：近一年收盘 + N 日预测（需 ts_code、n）" },
  { id: "bollinger_bands", label: "布林带：MA20±2σ（需 ts_code；可选 start、end）" },
];
const FALLBACK_TABLE_TRANSFORMS = [
  { id: "", label: "（默认）使用 SQL 查询结果表格" },
  { id: "arima_forecast", label: "ARIMA：预测明细表（与图表转换数据源一致）" },
  { id: "bollinger_bands", label: "布林带：日线序列与信号列（与图表转换同源）" },
];

type CronPreset = "off" | "1m" | "5m" | "15m" | "1h" | "custom";
type ConfigModalTab = "sql" | "echarts" | "table" | "transform" | "poll" | "raw";

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
  const [configTab, setConfigTab] = useState<ConfigModalTab>("sql");
  const [sqlVal, setSqlVal] = useState("");
  const [cronPreset, setCronPreset] = useState<CronPreset>("off");
  const [cronVal, setCronVal] = useState("");
  const [echartsJson, setEchartsJson] = useState("{}");
  const [tableJson, setTableJson] = useState("{}");
  const [transformChart, setTransformChart] = useState("");
  const [transformTable, setTransformTable] = useState("");
  const [transformParamsJson, setTransformParamsJson] = useState("{}");
  const [transformCatalog, setTransformCatalog] = useState<{
    chart: { id: string; label: string }[];
    table: { id: string; label: string }[];
  } | null>(null);
  const [rawJson, setRawJson] = useState<string>("{}");

  useEffect(() => {
    if (!configOpen) return;
    api
      .get("/dashboard/transform-options")
      .then((res) => setTransformCatalog(res.data))
      .catch(() => setTransformCatalog(null));
  }, [configOpen]);

  const modalTabOptions = useMemo(() => {
    const wType = configWidget?.type;
    if (wType === "chart") {
      return [
        { label: "SQL", value: "sql" satisfies ConfigModalTab },
        { label: "图表转换", value: "transform" satisfies ConfigModalTab },
        { label: "图表模板", value: "echarts" satisfies ConfigModalTab },
        { label: "轮询", value: "poll" satisfies ConfigModalTab },
        { label: "原始JSON", value: "raw" satisfies ConfigModalTab },
      ];
    }
    if (wType === "table") {
      return [
        { label: "SQL", value: "sql" satisfies ConfigModalTab },
        { label: "表格转换", value: "transform" satisfies ConfigModalTab },
        { label: "表格选项", value: "table" satisfies ConfigModalTab },
        { label: "轮询", value: "poll" satisfies ConfigModalTab },
        { label: "原始JSON", value: "raw" satisfies ConfigModalTab },
      ];
    }
    return [
      { label: "SQL", value: "sql" satisfies ConfigModalTab },
      { label: "转换", value: "transform" satisfies ConfigModalTab },
      { label: "轮询", value: "poll" satisfies ConfigModalTab },
      { label: "原始JSON", value: "raw" satisfies ConfigModalTab },
    ];
  }, [configWidget?.type]);

  useEffect(() => {
    if (!configOpen || !configWidget || modalTabOptions.length === 0) return;
    const allowed = new Set(modalTabOptions.map((o) => o.value));
    if (!allowed.has(configTab)) setConfigTab("sql");
  }, [configOpen, configWidget, configTab, modalTabOptions]);

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
                      setTransformChart(String(cfg.transform_chart ?? ""));
                      setTransformTable(String(cfg.transform_table ?? ""));
                      setTransformParamsJson(
                        JSON.stringify(cfg.transform_params ?? { ts_code: "600519.SH", n: 10 }, null, 2),
                      );
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
        title={configWidget?.type === "chart" ? "图表组件配置" : configWidget?.type === "table" ? "表格组件配置" : "组件配置"}
        okText="保存"
        cancelText="取消"
        onCancel={() => setConfigOpen(false)}
        onOk={async () => {
          if (!configWidget) return;
          try {
            const echartsObj = JSON.parse(echartsJson || "{}");
            const tableObj = JSON.parse(tableJson || "{}");
            let tpObj: Record<string, unknown> = {};
            try {
              tpObj = JSON.parse(transformParamsJson || "{}") as Record<string, unknown>;
            } catch {
              throw new Error("转换参数必须是合法 JSON");
            }
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
              transform_chart: transformChart.trim(),
              transform_table: transformTable.trim(),
              transform_params: tpObj,
              poll: cronStr
                ? { ...(baseRaw.poll ?? {}), cron: cronStr, interval_sec }
                : { ...(baseRaw.poll ?? {}), cron: "", interval_sec: 0 },
            };

            await updateWidget(configWidget.id, { config: merged });
            setConfigOpen(false);
          } catch (e) {
            message.error(e instanceof Error ? e.message : "JSON 解析失败，请检查各标签页文本框内容");
          }
        }}
      >
        <Segmented
          block
          value={configTab}
          onChange={(v) => setConfigTab(v as ConfigModalTab)}
          options={modalTabOptions}
          style={{ marginBottom: 12 }}
        />

        {configTab === "sql" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              {configWidget?.type === "chart" ? (
                <>
                  填写只读 SELECT（MySQL）。轮询时请求「/dashboard/query」并由服务端生成<strong>图表</strong>所用数据；
                  「图表模板」仅作占位 / 预览。
                </>
              ) : (
                <>
                  填写只读 SELECT（MySQL）。轮询时请求「/dashboard/query」刷新<strong>表格</strong>行数据；
                  「表格选项」只覆盖样式与列配置。
                </>
              )}
              <br />
              若选用「ARIMA」「布林带」等转换而不依赖本条 SQL：可用占位语句，例如{" "}
              <code style={{ whiteSpace: "nowrap" }}>SELECT 1 AS dummy</code>。
            </Typography.Paragraph>
            <Input.TextArea rows={8} value={sqlVal} onChange={(e) => setSqlVal(e.target.value)} placeholder="SELECT ..." />
          </>
        )}

        {configTab === "transform" && configWidget?.type === "chart" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              在<strong>本条 SQL 查出结果之后</strong>，仅针对<strong>图表</strong>再走命名转换。
              「ARIMA」「布林带」按参数从行情库重算 option，不要求 SQL 里已有对应指标字段。
            </Typography.Paragraph>
            <Typography.Text strong>图表转换（config.transform_chart）</Typography.Text>
            <Select
              style={{ width: "100%", marginTop: 6, marginBottom: 12 }}
              value={transformChart || undefined}
              allowClear
              placeholder="默认：按查询结果智能出图"
              options={(transformCatalog?.chart?.length ? transformCatalog.chart : FALLBACK_CHART_TRANSFORMS).map(
                (o) => ({ value: o.id, label: o.label || o.id }),
              )}
              onChange={(v) => setTransformChart(v ?? "")}
            />
            <Typography.Text strong>转换参数（JSON）</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ marginTop: 6, marginBottom: 8 }}>
              示例 · ARIMA：{"{"} &quot;ts_code&quot;: &quot;600519.SH&quot;, &quot;n&quot;: 10 {"}"} · 布林带：
              {"{"} &quot;ts_code&quot;: &quot;600519.SH&quot;, &quot;start&quot;: &quot;2024-01-01&quot;,
              &quot;end&quot;: &quot;2025-04-01&quot; {"}"}（布林带 start/end 可省略）
            </Typography.Paragraph>
            <Input.TextArea rows={8} value={transformParamsJson} onChange={(e) => setTransformParamsJson(e.target.value)} />
          </>
        )}
        {configTab === "transform" && configWidget?.type === "table" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              在<strong>本条 SQL 查出结果之后</strong>，仅针对<strong>表格</strong>再走命名转换。
              「ARIMA」输出预测行；「布林带」输出日线+信号列；未选时使用 SQL 结果的列与数据。
            </Typography.Paragraph>
            <Typography.Text strong>表格转换（config.transform_table）</Typography.Text>
            <Select
              style={{ width: "100%", marginTop: 6, marginBottom: 12 }}
              value={transformTable || undefined}
              allowClear
              placeholder="默认：直接使用 SQL 结果表格"
              options={(transformCatalog?.table?.length ? transformCatalog.table : FALLBACK_TABLE_TRANSFORMS).map(
                (o) => ({ value: o.id, label: o.label || o.id }),
              )}
              onChange={(v) => setTransformTable(v ?? "")}
            />
            <Typography.Text strong>转换参数（JSON）</Typography.Text>
            <Typography.Paragraph type="secondary" style={{ marginTop: 6, marginBottom: 8 }}>
              示例 · ARIMA：{"{"} &quot;ts_code&quot;: &quot;600519.SH&quot;, &quot;n&quot;: 10 {"}"} · 布林带：
              {"{"} &quot;ts_code&quot;: &quot;600519.SH&quot;, &quot;start&quot;: &quot;2024-01-01&quot;,
              &quot;end&quot;: &quot;2025-04-01&quot; {"}"}（布林带 start/end 可省略）
            </Typography.Paragraph>
            <Input.TextArea rows={8} value={transformParamsJson} onChange={(e) => setTransformParamsJson(e.target.value)} />
          </>
        )}
        {configTab === "echarts" && configWidget?.type === "chart" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              初次展示或轮询开启前的静态 ECharts「壳」；轮询后以接口返回的 option 为主（可被「图表转换」覆盖）。
            </Typography.Paragraph>
            <Input.TextArea rows={10} value={echartsJson} onChange={(e) => setEchartsJson(e.target.value)} />
          </>
        )}

        {configTab === "table" && configWidget?.type === "table" && (
          <>
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              Ant Design Table 的列宽、滚动、分页等覆盖项（dataSource/columns 由 SQL 刷新与「表格转换」注入）。
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
