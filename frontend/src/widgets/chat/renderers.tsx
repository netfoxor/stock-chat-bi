import React from "react";
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

