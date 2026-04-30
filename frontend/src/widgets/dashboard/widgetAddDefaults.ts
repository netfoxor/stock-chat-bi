export type WidgetSkillHint = "arima" | "bollinger" | null;

const YMD_TITLE_RE = /(\d{4}-\d{2}-\d{2})\s*[~～]\s*(\d{4}-\d{2}-\d{2})/;

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function isoDate(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

export function inferSkillFromDatatable(dt: unknown): WidgetSkillHint {
  const cols =
    dt != null &&
    typeof dt === "object" &&
    Array.isArray((dt as { columns?: unknown }).columns)
      ? (dt as { columns: unknown[] }).columns.map((c) =>
          String((c && typeof c === "object" && (c as { dataIndex?: unknown; title?: unknown }).dataIndex) ?? (c as { title?: unknown })?.title ?? ""),
        )
      : [];
  const set = new Set(cols);
  if (set.has("forecast_date")) return "arima";
  if (set.has("upper_2sigma") || set.has("mid_ma20") || set.has("lower_2sigma")) return "bollinger";
  return null;
}

/** 仅从 ECharts option 推断（先于全文关键词，避免正文里残留的「ARIMA」误伤布林带图表）。 */
export function inferSkillFromEcharts(echartsData: unknown): WidgetSkillHint {
  if (echartsData == null || typeof echartsData !== "object") return null;
  const o = echartsData as Record<string, unknown>;

  const titleText = echartsTitleText(echartsData);
  if (/布林带|布林(?:带)?|\bMA20\s*[±+]|\b±\s*2σ\b/i.test(titleText)) return "bollinger";
  if (/ARIMA|ARIMA\s*预测|置信区间|\bforecast\b/i.test(titleText)) return "arima";

  const leg = (o.legend && typeof o.legend === "object" ? o.legend : null) as { data?: unknown } | null;
  const lg = Array.isArray(leg?.data) ? (leg!.data as string[]).join(" ") : "";
  if (/超买|超卖|\b上轨\b|\b下轨\b|\b中轨\b|\b布林/.test(lg)) return "bollinger";
  if (/ARIMA|预测均值|置信|历史收盘/i.test(lg)) return "arima";

  const serRaw = o.series;
  const serList = Array.isArray(serRaw) ? serRaw : serRaw != null ? [serRaw] : [];
  const names = serList
    .map((s) => (s && typeof s === "object" && "name" in s ? String((s as { name?: unknown }).name ?? "") : ""))
    .join(" ");
  if (/超买|超卖|上轨|下轨|中轨|布林/.test(names)) return "bollinger";
  if (/ARIMA|置信|预测/.test(names)) return "arima";

  return null;
}

export function inferSkillFromMessage(content: string): WidgetSkillHint {
  const t = content ?? "";
  if (/布林带|布林(?:带)?(?:\s*分析)?|\b布林带\b/i.test(t)) return "bollinger";
  if (/ARIMA\s*预测|ARIMA预测|\bARIMA\b|时间序列\s*ARIMA/i.test(t)) return "arima";
  return null;
}

export function extractTsCodeFromText(...parts: string[]): string {
  for (const p of parts) {
    const m = /\b(\d{6}\.(?:SH|SZ|BJ))\b/i.exec(p || "");
    if (m) return m[1].toUpperCase();
  }
  return "600519.SH";
}

export function extractArimaN(content: string): number {
  const t = content ?? "";
  const m =
    /未来\s*(\d+)\s*个\s*交易日/.exec(t) ||
    /预测\s*(?:未来\s*)?(\d+)\s*日/.exec(t) ||
    /(\d+)\s*步/.exec(t);
  const n = m ? parseInt(m[1], 10) : 10;
  return Math.min(60, Math.max(1, Number.isFinite(n) ? n : 10));
}

export function extractBollRangeFromTitle(title: unknown): { start: string; end: string } | null {
  const s = typeof title === "string" ? title : "";
  const m = YMD_TITLE_RE.exec(s);
  if (m) return { start: m[1], end: m[2] };
  return null;
}

export function defaultBollDateRange(): { start: string; end: string } {
  const end = new Date();
  const start = new Date(end);
  start.setFullYear(end.getFullYear() - 1);
  return { start: isoDate(start), end: isoDate(end) };
}

export function buildBollSql(tsCode: string, start: string, end: string): string {
  const esc = tsCode.replace(/'/g, "''");
  return (
    `SELECT trade_date, ts_code, stock_name, open, high, low, close, vol\n` +
    `FROM stock_daily\n` +
    `WHERE ts_code = '${esc}'\n` +
    `  AND trade_date >= '${start}'\n` +
    `  AND trade_date <= '${end}'\n` +
    `ORDER BY trade_date`
  );
}

const DUMMY_SQL = "SELECT 1 AS dummy";

function echartsTitleText(echartsData: unknown): string {
  if (!echartsData || typeof echartsData !== "object") return "";
  const t = (echartsData as { title?: { text?: unknown } }).title?.text;
  return typeof t === "string" ? t : "";
}

export function buildChartWidgetConfig(opts: {
  sqlFence?: string;
  echartsData: unknown;
  datatableData?: unknown;
  messageContent: string;
}): Record<string, unknown> {
  const skill =
    inferSkillFromDatatable(opts.datatableData) ??
    inferSkillFromEcharts(opts.echartsData) ??
    inferSkillFromMessage(opts.messageContent);
  const sqlFence = String(opts.sqlFence ?? "").trim();
  const titleText = echartsTitleText(opts.echartsData);
  const ts = extractTsCodeFromText(opts.messageContent, titleText, JSON.stringify(opts.echartsData ?? ""));

  if (skill === "arima") {
    const n = extractArimaN(opts.messageContent);
    return {
      sql: sqlFence || DUMMY_SQL,
      echarts: opts.echartsData,
      transform_chart: "arima_forecast",
      transform_table: "",
      transform_params: { ts_code: ts, n },
    };
  }
  if (skill === "bollinger") {
    const rng = extractBollRangeFromTitle(titleText) ?? defaultBollDateRange();
    return {
      sql: buildBollSql(ts, rng.start, rng.end),
      echarts: opts.echartsData,
      transform_chart: "bollinger_bands",
      transform_table: "",
      transform_params: { ts_code: ts, start: rng.start, end: rng.end },
    };
  }
  return {
    sql: sqlFence,
    echarts: opts.echartsData,
    transform_chart: "",
    transform_table: "",
    transform_params: {},
  };
}

export function buildTableWidgetConfig(opts: {
  sqlFence?: string;
  datatableData: unknown;
  messageContent: string;
  echartsData?: unknown;
}): Record<string, unknown> {
  const skill =
    inferSkillFromDatatable(opts.datatableData) ??
    inferSkillFromEcharts(opts.echartsData) ??
    inferSkillFromMessage(opts.messageContent);
  const sqlFence = String(opts.sqlFence ?? "").trim();
  const titleText = echartsTitleText(opts.echartsData);
  const ts = extractTsCodeFromText(opts.messageContent, titleText);

  if (skill === "arima") {
    const n = extractArimaN(opts.messageContent);
    return {
      sql: sqlFence || DUMMY_SQL,
      table: opts.datatableData,
      transform_chart: "",
      transform_table: "arima_forecast",
      transform_params: { ts_code: ts, n },
    };
  }
  if (skill === "bollinger") {
    const rng = extractBollRangeFromTitle(titleText) ?? defaultBollDateRange();
    return {
      sql: buildBollSql(ts, rng.start, rng.end),
      table: opts.datatableData,
      transform_chart: "",
      transform_table: "bollinger_bands",
      transform_params: { ts_code: ts, start: rng.start, end: rng.end },
    };
  }
  return {
    sql: sqlFence,
    table: opts.datatableData,
    transform_chart: "",
    transform_table: "",
    transform_params: {},
  };
}
