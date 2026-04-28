// Chainlit 自定义元素：工具调用的折叠详情
//
// 用法：
//   cl.CustomElement(
//       name="ToolTrace",
//       props={"toolName": "exc_sql", "args": "...", "output": "...markdown..."},
//       display="inline",
//   )
//
// 行为：
//   * 消息原地渲染成 <details>/<summary> 折叠块，默认收起
//   * 点击展开：
//       - 参数：JSON 代码块（等宽等宽）
//       - 原始输出：按 Markdown 渲染（表格、代码块、列表都正常显示）
//   * 再点击收起，完全不离当前位置，不滚动跳跃
//
// Markdown 渲染通过 CDN 懒加载 marked + DOMPurify（安全净化后 innerHTML 注入）。
// 注意：Chainlit 把 props 作为**全局变量**注入，不能作为函数参数解构。

import { useEffect, useRef, useState } from "react";

const MARKED_CDN = "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js";
const DOMPURIFY_CDN =
  "https://cdn.jsdelivr.net/npm/dompurify@3.1.7/dist/purify.min.js";

let mdLibsPromise = null;

function loadScriptOnce(src, flag) {
  return new Promise((resolve, reject) => {
    const selector = `script[data-tooltrace="${flag}"]`;
    const existing = document.querySelector(selector);
    if (existing) {
      if (existing.dataset.loaded === "1") return resolve();
      existing.addEventListener("load", () => resolve());
      existing.addEventListener("error", reject);
      return;
    }
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.dataset.tooltrace = flag;
    s.onload = () => {
      s.dataset.loaded = "1";
      resolve();
    };
    s.onerror = reject;
    document.head.appendChild(s);
  });
}

function loadMarkdownLibs() {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.marked && window.DOMPurify) return Promise.resolve();
  if (mdLibsPromise) return mdLibsPromise;
  mdLibsPromise = Promise.all([
    loadScriptOnce(MARKED_CDN, "marked"),
    loadScriptOnce(DOMPURIFY_CDN, "dompurify"),
  ]).then(() => {
    if (!window.marked || !window.DOMPurify) {
      throw new Error("markdown libs not ready on window");
    }
  });
  return mdLibsPromise;
}

function MarkdownPane({ source }) {
  const ref = useRef(null);
  const [fallback, setFallback] = useState("");

  useEffect(() => {
    let alive = true;
    if (!source) {
      if (ref.current) ref.current.innerHTML = "";
      return;
    }
    loadMarkdownLibs()
      .then(() => {
        if (!alive || !ref.current) return;
        const rawHtml = window.marked.parse(source, {
          gfm: true,
          breaks: false,
        });
        const clean = window.DOMPurify.sanitize(rawHtml);
        ref.current.innerHTML = clean;
      })
      .catch(() => {
        if (alive) setFallback(source);
      });
    return () => {
      alive = false;
    };
  }, [source]);

  if (fallback) {
    return (
      <pre
        style={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          fontSize: 12,
          margin: 0,
        }}
      >
        {fallback}
      </pre>
    );
  }
  return (
    <div
      ref={ref}
      className="tooltrace-md"
      style={{ fontSize: 13, lineHeight: 1.55 }}
    />
  );
}

export default function ToolTrace() {
  const toolName = props?.toolName || "tool";
  const args = props?.args || "";
  const output = props?.output || "";

  const summaryStyle = {
    cursor: "pointer",
    userSelect: "none",
    fontSize: 13,
    color: "var(--muted-foreground, #888)",
    padding: "4px 0",
    outline: "none",
  };

  const sectionTitleStyle = {
    fontSize: 12,
    fontWeight: 600,
    color: "var(--muted-foreground, #888)",
    margin: "10px 0 4px",
    textTransform: "uppercase",
    letterSpacing: "0.04em",
  };

  const preStyle = {
    background: "var(--muted, #f4f4f5)",
    color: "var(--foreground, #111)",
    border: "1px solid var(--border, #e5e7eb)",
    borderRadius: 6,
    padding: "10px 12px",
    fontSize: 12,
    lineHeight: 1.5,
    overflow: "auto",
    maxHeight: 260,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    margin: 0,
  };

  const mdBoxStyle = {
    background: "var(--muted, #f4f4f5)",
    border: "1px solid var(--border, #e5e7eb)",
    borderRadius: 6,
    padding: "10px 14px",
    overflow: "auto",
    maxHeight: 520,
  };

  // 给 Markdown 内的常见元素一点兜底样式（表格/代码/标题紧凑化）
  const scopedCss = `
    .tooltrace-md table { border-collapse: collapse; margin: 6px 0; font-size: 12px; }
    .tooltrace-md th, .tooltrace-md td { border: 1px solid var(--border, #e5e7eb); padding: 4px 8px; }
    .tooltrace-md th { background: rgba(0,0,0,0.04); font-weight: 600; }
    .tooltrace-md pre { background: rgba(0,0,0,0.04); padding: 8px 10px; border-radius: 4px; overflow:auto; font-size: 12px; }
    .tooltrace-md code { font-size: 12px; }
    .tooltrace-md h1, .tooltrace-md h2, .tooltrace-md h3,
    .tooltrace-md h4, .tooltrace-md h5, .tooltrace-md h6 {
      margin: 10px 0 6px; line-height: 1.3;
    }
    .tooltrace-md p { margin: 6px 0; }
    .tooltrace-md ul, .tooltrace-md ol { margin: 6px 0; padding-left: 1.4em; }
  `;

  return (
    <details
      style={{
        border: "1px solid var(--border, #e5e7eb)",
        borderRadius: 8,
        padding: "6px 12px",
        background: "var(--card, transparent)",
        marginTop: 4,
      }}
    >
      <summary style={summaryStyle}>
        🔧 使用 <code>{toolName}</code>（点击展开参数与原始输出）
      </summary>
      <div style={{ paddingTop: 4, paddingBottom: 8 }}>
        <style>{scopedCss}</style>
        <div style={sectionTitleStyle}>参数</div>
        <pre style={preStyle}>{args}</pre>
        <div style={sectionTitleStyle}>原始输出</div>
        <div style={mdBoxStyle}>
          <MarkdownPane source={output} />
        </div>
      </div>
    </details>
  );
}
