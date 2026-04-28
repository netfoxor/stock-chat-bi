// Chainlit 自定义元素：ECharts 渲染器
//
// 使用：
//   cl.CustomElement(
//       name="EChart",
//       props={"option": <echarts option dict>, "height": 520, "title": "..."}
//   )
//
// 注意：Chainlit 把 props 作为**全局变量**注入到组件，**不能**作为函数参数解构
//   （官方文档：https://docs.chainlit.io/api-reference/elements/custom ）
//
// echarts 通过 CDN 懒加载（首次渲染时注入 <script>，后续复用）。

import { useEffect, useRef, useState } from "react";

const ECHARTS_CDN =
  "https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js";

let echartsPromise = null;

function loadECharts() {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.echarts) return Promise.resolve(window.echarts);
  if (echartsPromise) return echartsPromise;
  echartsPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-echarts="1"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(window.echarts));
      existing.addEventListener("error", reject);
      return;
    }
    const s = document.createElement("script");
    s.src = ECHARTS_CDN;
    s.async = true;
    s.dataset.echarts = "1";
    s.onload = () => resolve(window.echarts);
    s.onerror = (e) => {
      echartsPromise = null;
      reject(e);
    };
    document.head.appendChild(s);
  });
  return echartsPromise;
}

// 检测 Chainlit 当前是否为深色主题。
// Chainlit 切换主题时会在 <html> 上增减 class="dark"，并且同步更新
// data-theme / color-scheme；这里三重兜底，只要任一命中就算 dark。
function detectDark() {
  if (typeof document === "undefined") return false;
  const html = document.documentElement;
  if (html.classList.contains("dark")) return true;
  const dataTheme = html.getAttribute("data-theme");
  if (dataTheme && dataTheme.toLowerCase() === "dark") return true;
  const cs = html.style && html.style.colorScheme;
  if (cs && cs.toLowerCase().includes("dark")) return true;
  return false;
}

export default function EChart() {
  // Chainlit 在组件作用域内注入全局 props，这里直接取
  const option = props?.option;
  const height =
    typeof props?.height === "number" && props.height > 0 ? props.height : 520;
  const title = props?.title || "";

  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const [isDark, setIsDark] = useState(detectDark);

  // 监听 <html> 上的 class / data-theme 变化，与 Chainlit 主题联动
  useEffect(() => {
    if (typeof document === "undefined") return;
    const html = document.documentElement;
    const sync = () => setIsDark(detectDark());
    const mo = new MutationObserver(sync);
    mo.observe(html, {
      attributes: true,
      attributeFilter: ["class", "data-theme", "style"],
    });
    return () => mo.disconnect();
  }, []);

  // 用 JSON 字符串作为 option 的稳定指纹，避免每次 render 重新初始化
  const optionKey = option ? JSON.stringify(option) : "";

  useEffect(() => {
    if (!option) {
      if (containerRef.current) {
        containerRef.current.innerText = "(空图表：未传入 option)";
      }
      return;
    }

    let disposed = false;
    let resizeObs = null;

    const onResize = () => {
      if (chartRef.current) chartRef.current.resize();
    };

    loadECharts()
      .then((echarts) => {
        if (disposed || !containerRef.current) return;
        // echarts 5 自带 'dark' 内置主题；浅色传 null 即用默认
        const chart = echarts.init(
          containerRef.current,
          isDark ? "dark" : null,
          { renderer: "canvas" }
        );
        chartRef.current = chart;
        // 深色主题下把画布背景设为透明，避免盖住 Chainlit 的消息气泡底色
        if (isDark) {
          chart.setOption({ backgroundColor: "transparent", ...option }, true);
        } else {
          chart.setOption(option, true);
        }

        window.addEventListener("resize", onResize);
        if (typeof ResizeObserver !== "undefined") {
          resizeObs = new ResizeObserver(() => chart.resize());
          resizeObs.observe(containerRef.current);
        }
      })
      .catch((err) => {
        if (containerRef.current) {
          containerRef.current.innerText =
            "ECharts 加载失败：" +
            (err && err.message ? err.message : String(err));
        }
      });

    return () => {
      disposed = true;
      window.removeEventListener("resize", onResize);
      if (resizeObs) resizeObs.disconnect();
      if (chartRef.current) {
        chartRef.current.dispose();
        chartRef.current = null;
      }
    };
    // isDark 变化时需要销毁旧实例再用新主题重建（echarts 不支持动态切 theme）
  }, [optionKey, height, isDark]);

  return (
    <div style={{ width: "100%" }}>
      {title ? (
        <div
          style={{
            fontSize: 13,
            color: "var(--muted-foreground, #888)",
            marginBottom: 4,
          }}
        >
          {title}
        </div>
      ) : null}
      <div
        ref={containerRef}
        style={{
          width: "100%",
          height: `${height}px`,
          minHeight: "200px",
        }}
      />
    </div>
  );
}
