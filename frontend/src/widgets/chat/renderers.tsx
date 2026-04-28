import React from "react";
import ReactECharts from "echarts-for-react";
import { Table, Typography } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

const FENCE_RE = /```(echarts|datatable)\n([\s\S]*?)\n```/i;

export function extractSpecialBlock(markdown: string): { kind: "echarts" | "datatable"; data: any } | null {
  const m = markdown.match(FENCE_RE);
  if (!m) return null;
  const kind = m[1].toLowerCase() as "echarts" | "datatable";
  try {
    const data = JSON.parse(m[2].trim());
    return { kind, data };
  } catch {
    return null;
  }
}

export function MarkdownView(props: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>{props.content}</ReactMarkdown>;
}

export function InlineECharts(props: { option: any }) {
  return <ReactECharts option={props.option} style={{ height: 320 }} />;
}

export function InlineDataTable(props: { value: any }) {
  const cols = Array.isArray(props.value?.columns) ? props.value.columns : [];
  const data = Array.isArray(props.value?.data) ? props.value.data : [];
  const columns = cols.map((c: any) => ({
    title: c.title ?? c.dataIndex,
    dataIndex: c.dataIndex,
    key: c.dataIndex,
  }));
  return (
    <div>
      <Typography.Text type="secondary">数据表</Typography.Text>
      <Table
        size="small"
        columns={columns}
        dataSource={data.map((r: any, idx: number) => ({ key: idx, ...r }))}
        pagination={{ pageSize: 10 }}
        scroll={{ x: true }}
      />
    </div>
  );
}

