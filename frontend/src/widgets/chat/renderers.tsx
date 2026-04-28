import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import ReactECharts from "echarts-for-react";
import { Table, Typography } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

const FENCE_RE_G = /```(echarts|datatable)\n([\s\S]*?)\n```/gi;

export type SpecialBlock = { kind: "echarts" | "datatable"; data: any };

export function extractSpecialBlocks(markdown: string): { cleanMarkdown: string; blocks: SpecialBlock[] } {
  const blocks: SpecialBlock[] = [];
  const cleanMarkdown = (markdown ?? "").replace(FENCE_RE_G, (_full, lang, body) => {
    const kind = String(lang).toLowerCase() as "echarts" | "datatable";
    try {
      const data = JSON.parse(String(body).trim());
      blocks.push({ kind, data });
    } catch {
      // ignore invalid JSON blocks, but remove fence to keep UI clean
    }
    return "";
  });

  return { cleanMarkdown: cleanMarkdown.trim(), blocks };
}

export function MarkdownView(props: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{props.content}</ReactMarkdown>;
}

export function InlineECharts(props: { option: any; height?: number | string }) {
  const chartRef = useRef<ReactECharts | null>(null);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [dim, setDim] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  const style = useMemo(() => ({ height: props.height ?? 320, width: "100%" }), [props.height]);

  const normalizedOption = useMemo(() => {
    return normalizeEChartsOption(props.option, dim.width, dim.height);
  }, [props.option, dim.width, dim.height]);

  const doResize = () => {
    const inst = chartRef.current?.getEchartsInstance?.();
    if (!inst) return;
    // 连续两帧 resize，避免 react-grid-layout resize/transform 过程中的“测量不准”
    requestAnimationFrame(() => {
      inst.resize();
      requestAnimationFrame(() => inst.resize());
    });
  };

  // option/高度变化后，在 layout 完成后触发一次
  useLayoutEffect(() => {
    doResize();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.option, style.height]);

  // 容器尺寸变化时触发 resize（核心：比单纯 setTimeout 更稳）
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => doResize());
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 记录容器尺寸（用于把 option 里的百分比边距转成像素）
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (!cr) return;
      setDim({ width: cr.width, height: cr.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={wrapRef} style={{ width: "100%", height: style.height as any, minHeight: 120 }}>
      <ReactECharts
        ref={chartRef}
        option={normalizedOption}
        style={{ width: "100%", height: "100%" }}
        notMerge
        lazyUpdate
      />
    </div>
  );
}

export function InlineDataTable(props: { value: any; height?: number; showTitle?: boolean }) {
  const cols = Array.isArray(props.value?.columns) ? props.value.columns : [];
  const data = Array.isArray(props.value?.data) ? props.value.data : [];
  const columns = cols.map((c: any) => ({
    title: c.title ?? c.dataIndex,
    dataIndex: c.dataIndex,
    key: c.dataIndex,
  }));

  const height = props.height ?? 0;
  // 估算：表头/工具栏/分页条会占高
  const showTitle = props.showTitle ?? true;
  const titleH = showTitle ? 22 /* typography */ + 4 /* margin */ : 0;
  const headerH = 38;
  const paginationH = 36; // size=small + margin
  const rowH = 36;
  const chromeBase = titleH + headerH;
  // 先按“有分页”的情况估算；若一页装得下就关闭分页并重新估算
  const chromeWithPager = chromeBase + paginationH;
  const usable1 = Math.max(0, height - chromeWithPager);
  const fitRows1 = Math.max(3, Math.min(20, Math.floor(usable1 / rowH)));
  const pageSizeWithPager = Math.min(fitRows1, data.length || fitRows1);
  const needsPager = data.length > pageSizeWithPager;

  const chrome = chromeBase + (needsPager ? paginationH : 0);
  const usable2 = Math.max(0, height - chrome);
  const fitRows2 = Math.max(3, Math.min(50, Math.floor(usable2 / rowH)));
  const pageSize = Math.min(fitRows2, data.length || fitRows2);
  const finalNeedsPager = data.length > pageSize;

  return (
    <div>
      {showTitle && (
        <Typography.Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
          数据表
        </Typography.Text>
      )}
      <Table
        size="small"
        columns={columns}
        dataSource={data.map((r: any, idx: number) => ({ key: idx, ...r }))}
        pagination={
          finalNeedsPager
            ? {
                pageSize,
                showSizeChanger: false,
                size: "small",
                style: { marginTop: 6, marginBottom: 0 },
              }
            : false
        }
        scroll={{ x: true, y: height ? Math.max(height - chrome, 120) : undefined }}
      />
    </div>
  );
}

function normalizeEChartsOption(option: any, width: number, height: number): any {
  if (!option || typeof option !== "object") return option;
  if (!width || !height) return option;

  // shallow clone to avoid mutating upstream
  const out = { ...option };
  const grid = out.grid;
  if (grid) {
    out.grid = Array.isArray(grid)
      ? grid.map((g) => normalizeGrid(g, width, height))
      : normalizeGrid(grid, width, height);
  }
  return out;
}

function normalizeGrid(grid: any, width: number, height: number): any {
  if (!grid || typeof grid !== "object") return grid;
  const g = { ...grid };
  g.left = pctToPx(g.left, width);
  g.right = pctToPx(g.right, width);
  g.top = pctToPx(g.top, height);
  g.bottom = pctToPx(g.bottom, height);
  return g;
}

function pctToPx(val: any, base: number): any {
  if (typeof val !== "string") return val;
  const s = val.trim();
  if (!s.endsWith("%")) return val;
  const n = Number.parseFloat(s.slice(0, -1));
  if (!Number.isFinite(n)) return val;
  return Math.round((n / 100) * base);
}

