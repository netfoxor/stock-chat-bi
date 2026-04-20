#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票查询助手 - nanobot 版（对标 qwen-agent/assistant_stock_bot-4.py）

迁移的三个自定义工具：
  * exc_sql         -- 只读查询 SQLite stock_daily
  * arima_stock     -- ARIMA(5,1,5) 预测未来 n 个交易日收盘价
  * boll_detection  -- 20 日 + 2σ 布林带检测超买/超卖

运行方式：
  CLI  :  python stock_bot.py "用 ARIMA 预测贵州茅台未来 10 个交易日的收盘价"
  交互 :  python stock_bot.py            (进入 REPL)

环境变量：
  DASHSCOPE_API_KEY (必填)
  QWEN_AGENT_MODEL  (可选，默认读 config.json 的 qwen-plus)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay
from sqlalchemy import create_engine, text
from statsmodels.tsa.arima.model import ARIMA

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.base import Tool
from nanobot.bus.queue import MessageBus
from nanobot.config.loader import load_config
from nanobot.nanobot import Nanobot, _make_provider

# --------------------------------------------------------------------------- #
# 基础配置
# --------------------------------------------------------------------------- #

WORKSPACE = Path(__file__).resolve().parent
# 复用 qwen-agent 目录下已下载好的 stock_prices_history.db
DB_PATH = (WORKSPACE.parent / "qwen-agent" / "stock_prices_history.db").resolve()
# ECharts option 以 JSON 形式落盘到 charts/，由前端自定义元素加载渲染
CHARTS_DIR = WORKSPACE / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

# 业务常量
ARIMA_ORDER = (5, 1, 5)
MIN_ARIMA_OBS = 80
MAX_FORECAST_DAYS = 60
BOLL_WINDOW = 20
BOLL_STD_MULT = 2.0
MIN_BOLL_ROWS = 25


# --------------------------------------------------------------------------- #
# 共用工具函数（从原 qwen-agent 版本迁移，保持行为一致）
# --------------------------------------------------------------------------- #

def _is_read_only_sql(sql: str) -> bool:
    s = sql.strip().lstrip("(").strip().upper()
    return s.startswith("SELECT") or s.startswith("WITH")


def _safe_label(s: Any) -> str:
    return str(s).replace("%", "%%").replace("{", "{{").replace("}", "}}")


def _build_result_markdown(df: pd.DataFrame) -> str:
    n, p = len(df), df.shape[1]
    parts: list[str] = [f"**查询结果概况**：共 **{n}** 行、**{p}** 列。"]

    if n <= 10:
        parts.append("\n**数据预览（全部行）：**")
        parts.append(df.to_markdown(index=False))
    else:
        parts.append("\n**数据预览（前 5 行）：**")
        parts.append(df.head(5).to_markdown(index=False))
        parts.append("\n**数据预览（后 5 行）：**")
        parts.append(df.tail(5).to_markdown(index=False))

    num_desc = df.describe(include=np.number)
    if not num_desc.empty:
        parts.append("\n**数值列描述统计（describe）：**")
        parts.append(num_desc.to_markdown())

    obj_cols = df.select_dtypes(include=["object", "string"]).columns
    if len(obj_cols) > 0:
        obj_desc = df[obj_cols].describe()
        if not obj_desc.empty:
            parts.append("\n**文本/分类列描述（count、unique、top、freq 等）：**")
            parts.append(obj_desc.to_markdown())

    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# ECharts option 构造器
#
# 设计思路（对比 matplotlib PNG）：
#   - 所有图都是可缩放、带十字线 tooltip 的交互图
#   - option 为纯 JSON dict，落盘到 charts/xxx.json，由前端 CustomElement 渲染
#   - 列语义识别与分组规则保持不变，只是后端从 matplotlib 换成 echarts
#     * OHLC 齐全 → K 线 + MA5/10/20 + 成交量副图
#     * 否则按量纲族切多个 grid，避免量纲混画
#   - 红涨绿跌（A 股习惯）
# --------------------------------------------------------------------------- #

# 列语义识别（不区分大小写）
_PRICE_COLS = {"open", "high", "low", "close", "pre_close"}
_VOLUME_COLS = {"vol", "volume"}
_AMOUNT_COLS = {"amount", "turnover"}
_PCT_COLS = {"pct_chg", "pct_change", "change_pct", "pctchg"}
_CHANGE_COLS = {"change", "chg"}
_DATE_COLS = {"trade_date", "date", "datetime", "dt", "day"}

COLOR_UP = "#ef4444"      # 阳线 / 上涨
COLOR_DOWN = "#22c55e"    # 阴线 / 下跌
COLOR_MA = ("#f59e0b", "#8b5cf6", "#0ea5e9")  # MA5 / MA10 / MA20
COLOR_CLOSE = "#2563eb"
COLOR_BOLL_BAND = "rgba(156,163,175,0.18)"
COLOR_BOLL_MID = "#6b7280"
COLOR_BOLL_UP = "#f97316"
COLOR_BOLL_LOW = "#10b981"
COLOR_FORECAST = "#f97316"
COLOR_CI = "rgba(249,115,22,0.22)"


def _detect_date_col(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        if str(c).lower() in _DATE_COLS:
            return c
    # 兜底：第一列是 object 且能被解析为日期
    first = df.columns[0]
    if df[first].dtype == "O":
        try:
            pd.to_datetime(df[first].head(5), errors="raise")
            return first
        except Exception:
            return None
    return None


def _drop_constant_object_cols(df: pd.DataFrame, exclude: set[str]) -> pd.DataFrame:
    """丢弃整列只有一个唯一值的 object 列（如 ts_code、stock_name）。"""
    drop_cols = []
    for c in df.columns:
        if c in exclude:
            continue
        if df[c].dtype == "O" and df[c].nunique(dropna=True) <= 1:
            drop_cols.append(c)
    return df.drop(columns=drop_cols) if drop_cols else df


def _group_numeric_cols(cols: list[str]) -> dict[str, list[str]]:
    """把数值列按语义分组；未识别的归入 'other'，保留画图顺序。"""
    groups: dict[str, list[str]] = {
        "price": [], "volume": [], "amount": [],
        "pct": [], "change": [], "other": [],
    }
    for c in cols:
        lc = str(c).lower()
        if lc in _PRICE_COLS:
            groups["price"].append(c)
        elif lc in _VOLUME_COLS:
            groups["volume"].append(c)
        elif lc in _AMOUNT_COLS:
            groups["amount"].append(c)
        elif lc in _PCT_COLS:
            groups["pct"].append(c)
        elif lc in _CHANGE_COLS:
            groups["change"].append(c)
        else:
            groups["other"].append(c)
    return {k: v for k, v in groups.items() if v}


def _round_list(vals, ndigits: int = 4) -> list:
    """Series/ndarray → list，NaN 转 None，数字保留 ndigits 位，JSON 友好。"""
    out = []
    for v in vals:
        if v is None:
            out.append(None)
            continue
        try:
            if pd.isna(v):
                out.append(None)
                continue
        except (TypeError, ValueError):
            pass
        if isinstance(v, (int, np.integer)):
            out.append(int(v))
        elif isinstance(v, (float, np.floating)):
            out.append(round(float(v), ndigits))
        else:
            out.append(v)
    return out


def _moving_average(values: list[float | None], window: int) -> list[float | None]:
    """朴素 N 日移动均线；样本不足窗口返回 None，保留 NaN 语义。"""
    out: list[float | None] = []
    acc = 0.0
    count = 0
    buf: list[float] = []
    for v in values:
        x = None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
        if x is None:
            buf.append(0.0)  # 占位
            out.append(None)
            count = 0  # 遇到缺失重置（简化：对日线数据通常无缺失）
            acc = 0.0
            continue
        buf.append(x)
        acc += x
        if len(buf) > window:
            acc -= buf[-window - 1]
        if len(buf) >= window:
            out.append(round(acc / window, 4))
        else:
            out.append(None)
    return out


def _save_echart_option(option: dict, prefix: str, *, label: str = "图表") -> str:
    """把 option dict 落盘到 charts/，返回 markdown 引用（chart: 前缀）。"""
    filename = f"{prefix}_{int(time.time() * 1000)}.json"
    path = CHARTS_DIR / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(option, f, ensure_ascii=False, separators=(",", ":"))
    return f"![{label}](chart:charts/{filename})"


# ------------------------------- K 线 / 量能 ------------------------------- #

def _build_kline_option(dates: list[str], ohlc: list[list], volumes: list | None,
                        title: str) -> dict:
    """
    OHLC → K 线 + MA5/10/20 + 成交量（可选）。
    ohlc 元素格式：[open, close, low, high]（ECharts candlestick 约定）
    """
    closes = [row[1] for row in ohlc]

    series: list[dict] = [{
        "name": "K线",
        "type": "candlestick",
        "data": ohlc,
        "itemStyle": {
            "color": COLOR_UP,
            "color0": COLOR_DOWN,
            "borderColor": COLOR_UP,
            "borderColor0": COLOR_DOWN,
        },
        "emphasis": {"focus": "series"},
    }]
    legend_items = ["K线"]
    for period, color in zip((5, 10, 20), COLOR_MA):
        if len(closes) >= period:
            series.append({
                "name": f"MA{period}",
                "type": "line",
                "data": _moving_average(closes, period),
                "smooth": True,
                "showSymbol": False,
                "lineStyle": {"width": 1.1, "opacity": 0.95, "color": color},
                "emphasis": {"focus": "series"},
            })
            legend_items.append(f"MA{period}")

    has_vol = volumes is not None and len(volumes) == len(dates)
    grids: list[dict] = [{
        "left": "7%", "right": "4%", "top": 56,
        "height": "58%" if has_vol else "78%",
    }]
    x_axes: list[dict] = [{
        "type": "category",
        "data": dates,
        "scale": True,
        "boundaryGap": False,
        "axisLine": {"onZero": False},
        "splitLine": {"show": False},
        "axisTick": {"show": False},
        "min": "dataMin", "max": "dataMax",
    }]
    y_axes: list[dict] = [{"scale": True, "splitArea": {"show": True}}]
    dz_axes = [0]

    if has_vol:
        grids.append({"left": "7%", "right": "4%", "top": "74%", "height": "16%"})
        x_axes.append({
            "type": "category", "gridIndex": 1, "data": dates,
            "scale": True, "boundaryGap": False,
            "axisLine": {"onZero": False},
            "axisLabel": {"show": False},
            "axisTick": {"show": False},
            "splitLine": {"show": False},
            "min": "dataMin", "max": "dataMax",
        })
        y_axes.append({
            "gridIndex": 1, "scale": True, "splitNumber": 2,
            "axisLabel": {"show": False},
            "axisLine": {"show": False},
            "axisTick": {"show": False},
            "splitLine": {"show": False},
        })
        dz_axes.append(1)
        up_mask = [row[1] >= row[0] for row in ohlc]  # close >= open
        vol_data = [
            {"value": (v if v is not None else 0),
             "itemStyle": {"color": COLOR_UP if u else COLOR_DOWN}}
            for v, u in zip(volumes, up_mask)
        ]
        series.append({
            "name": "成交量",
            "type": "bar",
            "xAxisIndex": 1,
            "yAxisIndex": 1,
            "data": vol_data,
        })
        legend_items.append("成交量")

    # 默认窗口：超过 120 根时聚焦到最近 ~120 根
    n = len(dates)
    start_pct = max(0, 100 - int(120 / max(n, 1) * 100)) if n > 120 else 0

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {"data": legend_items, "top": 30, "textStyle": {"fontSize": 12}},
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
            "backgroundColor": "rgba(250,250,250,0.95)",
            "borderColor": "#ccc",
            "borderWidth": 1,
            "textStyle": {"color": "#111"},
        },
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "grid": grids,
        "xAxis": x_axes,
        "yAxis": y_axes,
        "dataZoom": [
            {"type": "inside", "xAxisIndex": dz_axes, "start": start_pct, "end": 100},
            {"show": True, "type": "slider", "xAxisIndex": dz_axes,
             "bottom": 8, "height": 18,
             "start": start_pct, "end": 100},
        ],
        "series": series,
    }


def _simple_grid_option(
    dates: list[str], *,
    title: str,
    panels: list[dict],
    date_axis: bool = True,
) -> dict:
    """
    多子图通用构造器。panels = [{"type": "line"|"bar", "name": ..., "data": [...]}, ...]
    每个 panel 独占一个 grid（上下排列）。
    """
    n_panels = len(panels)
    # 留给 title+legend 约 12%，底部 dataZoom 约 12%，中间给 panels
    top_reserve, bottom_reserve = 12, 14
    gap_pct = 4 if n_panels > 1 else 0
    usable = 100 - top_reserve - bottom_reserve - gap_pct * (n_panels - 1)
    each_h = usable / n_panels

    grids: list[dict] = []
    x_axes: list[dict] = []
    y_axes: list[dict] = []
    series: list[dict] = []
    legend_items: list[str] = []
    dz_axes: list[int] = list(range(n_panels))

    for i, panel in enumerate(panels):
        top_pct = top_reserve + i * (each_h + gap_pct)
        grids.append({
            "left": "7%", "right": "4%",
            "top": f"{top_pct:.2f}%",
            "height": f"{each_h:.2f}%",
        })

        x_axes.append({
            "type": "category" if date_axis else "category",
            "gridIndex": i,
            "data": dates,
            "boundaryGap": (panel.get("type") == "bar"),
            "axisLabel": {"show": i == n_panels - 1},
            "axisLine": {"onZero": False},
            "axisTick": {"show": i == n_panels - 1},
            "splitLine": {"show": False},
        })
        y_axes.append({
            "gridIndex": i,
            "scale": True,
            "name": panel.get("yname", ""),
            "nameTextStyle": {"fontSize": 11},
            "splitLine": {"lineStyle": {"opacity": 0.4}},
        })

        for s in panel.get("series", []):
            entry: dict = {
                "name": s["name"],
                "type": s.get("type", "line"),
                "xAxisIndex": i,
                "yAxisIndex": i,
                "data": s["data"],
                "showSymbol": s.get("showSymbol", False),
                "smooth": s.get("smooth", True),
            }
            if s.get("lineStyle"):
                entry["lineStyle"] = s["lineStyle"]
            if s.get("itemStyle"):
                entry["itemStyle"] = s["itemStyle"]
            if s.get("areaStyle"):
                entry["areaStyle"] = s["areaStyle"]
            if s.get("stack"):
                entry["stack"] = s["stack"]
            series.append(entry)
            if s["name"] not in legend_items and not s.get("noLegend"):
                legend_items.append(s["name"])

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {"data": legend_items, "top": 30, "textStyle": {"fontSize": 12}},
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "cross"},
            "backgroundColor": "rgba(250,250,250,0.95)",
            "borderColor": "#ccc",
            "borderWidth": 1,
            "textStyle": {"color": "#111"},
        },
        "axisPointer": {"link": [{"xAxisIndex": "all"}]},
        "grid": grids,
        "xAxis": x_axes,
        "yAxis": y_axes,
        "dataZoom": [
            {"type": "inside", "xAxisIndex": dz_axes, "start": 0, "end": 100},
            {"show": True, "type": "slider", "xAxisIndex": dz_axes,
             "bottom": 8, "height": 18, "start": 0, "end": 100},
        ],
        "series": series,
    }


def _build_stock_echart(df_sql: pd.DataFrame, *, max_rows: int = 500
                        ) -> tuple[dict, str]:
    """
    exc_sql 智能绘图入口。返回 (option_dict, label)。
    """
    df = df_sql.copy()
    date_col = _detect_date_col(df)
    df = _drop_constant_object_cols(df, exclude={date_col} if date_col else set())

    if date_col is not None:
        try:
            df[date_col] = pd.to_datetime(df[date_col])
            df = df.sort_values(date_col).reset_index(drop=True)
        except Exception:
            pass
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
                 for d in df[date_col]]
    else:
        dates = [str(v) for v in df[df.columns[0]].tolist()]

    # 过大时等距采样
    if len(df) > max_rows:
        idx = np.unique(np.linspace(0, len(df) - 1, max_rows, dtype=int))
        df = df.iloc[idx].reset_index(drop=True)
        dates = [dates[i] for i in idx]

    num_cols = [c for c in df.columns
                if c != date_col and pd.api.types.is_numeric_dtype(df[c])]
    if not num_cols:
        option = {
            "title": {"text": "无可绘数值列", "left": "center"},
            "xAxis": {"type": "category", "data": dates},
            "yAxis": {"type": "value"},
            "series": [],
        }
        return option, "占位图"

    groups = _group_numeric_cols(num_cols)

    # OHLC 齐全 → K 线图
    has_ohlc = {"open", "high", "low", "close"}.issubset(
        {str(c).lower() for c in groups.get("price", [])}
    )
    if has_ohlc:
        lowers = {str(c).lower(): c for c in groups["price"]}
        o = _round_list(df[lowers["open"]])
        h = _round_list(df[lowers["high"]])
        lo = _round_list(df[lowers["low"]])
        cl_ = _round_list(df[lowers["close"]])
        ohlc = [[o[i], cl_[i], lo[i], h[i]] for i in range(len(df))]
        vol_col = None
        for c in groups.get("volume", []) + groups.get("amount", []):
            vol_col = c
            break
        volumes = _round_list(df[vol_col]) if vol_col else None

        title = f"K 线图（{len(df)} 个交易日 · 红涨绿跌）"
        option = _build_kline_option(dates, ohlc, volumes, title)
        label = "K 线图 + MA" + (" + 成交量" if volumes else "")
        return option, label

    # 无 OHLC：按量纲族分 panel
    labels_map = {"price": "价格", "volume": "成交量", "amount": "成交额",
                  "pct": "涨跌幅(%)", "change": "涨跌额", "other": "数值"}
    panels: list[dict] = []
    color_cycle = ["#2563eb", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6",
                   "#0ea5e9", "#db2777", "#16a34a"]
    ci = 0
    for key, cols in groups.items():
        panel_series = []
        panel_type = "bar" if key in ("volume", "amount", "pct", "change") else "line"
        for col in cols:
            color = color_cycle[ci % len(color_cycle)]
            ci += 1
            panel_series.append({
                "name": _safe_label(col),
                "type": panel_type,
                "data": _round_list(df[col]),
                "smooth": panel_type == "line",
                "showSymbol": False,
                "lineStyle": {"width": 1.4, "color": color},
                "itemStyle": {"color": color},
            })
        panels.append({"name": labels_map.get(key, key),
                       "yname": labels_map.get(key, key),
                       "series": panel_series})

    title = f"查询结果（{len(df)} 行，按量纲分组）"
    option = _simple_grid_option(dates, title=title, panels=panels)
    label = "多子图折线" if len(panels) > 1 else "折线图"
    return option, label


# ------------------------------- ARIMA 预测 ------------------------------- #

def _build_arima_echart(
    hist_dates: list[str], hist_close: list[float],
    fc_dates: list[str], fc_mean: list[float],
    fc_low: list[float], fc_high: list[float],
    title: str,
) -> dict:
    """历史收盘 + 预测均值 + 95% 置信带（stack 技巧填充）。"""
    all_dates = list(hist_dates) + list(fc_dates)
    n_hist, n_fc = len(hist_dates), len(fc_dates)

    # 历史 close：预测段填 None
    hist_series = list(hist_close) + [None] * n_fc
    # 预测 mean：历史段最后 1 点接上，保证曲线连续
    fc_series = [None] * (n_hist - 1) + [hist_close[-1]] + list(fc_mean)
    # 置信带：下轨绝对值 + (上-下) 差值 stack，形成带区
    ci_low = [None] * n_hist + list(fc_low)
    ci_diff = [None] * n_hist + [h - l for h, l in zip(fc_high, fc_low)]

    series = [
        {
            "name": "95% 置信区间",
            "type": "line",
            "data": ci_low,
            "stack": "ci",
            "lineStyle": {"opacity": 0},
            "showSymbol": False,
            "itemStyle": {"color": "transparent"},
            "tooltip": {"show": False},
        },
        {
            "name": "95% 置信区间",
            "type": "line",
            "data": ci_diff,
            "stack": "ci",
            "lineStyle": {"opacity": 0},
            "showSymbol": False,
            "areaStyle": {"color": COLOR_CI},
            "itemStyle": {"color": COLOR_CI},
        },
        {
            "name": "历史收盘",
            "type": "line",
            "data": hist_series,
            "showSymbol": False,
            "smooth": True,
            "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
            "itemStyle": {"color": COLOR_CLOSE},
            "connectNulls": False,
        },
        {
            "name": "ARIMA 预测",
            "type": "line",
            "data": fc_series,
            "symbol": "circle",
            "symbolSize": 6,
            "showSymbol": True,
            "smooth": False,
            "lineStyle": {"width": 1.8, "color": COLOR_FORECAST, "type": "dashed"},
            "itemStyle": {"color": COLOR_FORECAST},
            "connectNulls": False,
        },
    ]

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {
            "data": ["历史收盘", "ARIMA 预测", "95% 置信区间"],
            "top": 30, "textStyle": {"fontSize": 12},
        },
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "grid": {"left": "7%", "right": "4%", "top": 64, "bottom": 64},
        "xAxis": {
            "type": "category", "data": all_dates,
            "boundaryGap": False, "scale": True,
            "axisLine": {"onZero": False},
        },
        "yAxis": {"type": "value", "scale": True,
                  "splitLine": {"lineStyle": {"opacity": 0.4}}},
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {"show": True, "type": "slider", "bottom": 8, "height": 18,
             "start": 0, "end": 100},
        ],
        "series": series,
    }


# ------------------------------- 布林带 ------------------------------- #

def _build_boll_echart(
    dates: list[str], close: list[float],
    mid: list[float], upper: list[float], lower: list[float],
    ob_idx: list[int], os_idx: list[int],
    title: str,
) -> dict:
    """收盘 + 20 日均线 + ±2σ 上下轨 + 带区填充 + 超买/超卖散点标注。"""
    # 带区（下轨绝对 + 上下差值 stack）
    diff = [
        None if (u is None or l is None) else round(u - l, 4)
        for u, l in zip(upper, lower)
    ]
    ob_points = [[dates[i], close[i]] for i in ob_idx]
    os_points = [[dates[i], close[i]] for i in os_idx]

    series = [
        {
            "name": "布林下轨",
            "type": "line",
            "data": lower,
            "stack": "boll",
            "lineStyle": {"opacity": 0},
            "showSymbol": False,
            "tooltip": {"show": False},
            "itemStyle": {"color": "transparent"},
            "noLegend": False,
        },
        {
            "name": "布林带",
            "type": "line",
            "data": diff,
            "stack": "boll",
            "lineStyle": {"opacity": 0},
            "showSymbol": False,
            "areaStyle": {"color": COLOR_BOLL_BAND},
            "itemStyle": {"color": COLOR_BOLL_BAND},
            "tooltip": {"show": False},
        },
        {
            "name": "上轨 +2σ",
            "type": "line",
            "data": upper,
            "showSymbol": False,
            "lineStyle": {"width": 1, "color": COLOR_BOLL_UP, "type": "dashed"},
            "itemStyle": {"color": COLOR_BOLL_UP},
        },
        {
            "name": "中轨 MA20",
            "type": "line",
            "data": mid,
            "showSymbol": False,
            "lineStyle": {"width": 1, "color": COLOR_BOLL_MID},
            "itemStyle": {"color": COLOR_BOLL_MID},
        },
        {
            "name": "下轨 -2σ",
            "type": "line",
            "data": lower,
            "showSymbol": False,
            "lineStyle": {"width": 1, "color": COLOR_BOLL_LOW, "type": "dashed"},
            "itemStyle": {"color": COLOR_BOLL_LOW},
        },
        {
            "name": "收盘",
            "type": "line",
            "data": close,
            "showSymbol": False,
            "smooth": True,
            "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
            "itemStyle": {"color": COLOR_CLOSE},
        },
        {
            "name": "超买",
            "type": "scatter",
            "data": ob_points,
            "symbolSize": 10,
            "itemStyle": {"color": COLOR_UP},
        },
        {
            "name": "超卖",
            "type": "scatter",
            "data": os_points,
            "symbolSize": 10,
            "itemStyle": {"color": COLOR_DOWN},
        },
    ]

    legend = ["收盘", "中轨 MA20", "上轨 +2σ", "下轨 -2σ", "超买", "超卖"]

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {"data": legend, "top": 30, "textStyle": {"fontSize": 12}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "grid": {"left": "7%", "right": "4%", "top": 64, "bottom": 64},
        "xAxis": {
            "type": "category", "data": dates,
            "boundaryGap": False, "scale": True,
            "axisLine": {"onZero": False},
        },
        "yAxis": {"type": "value", "scale": True,
                  "splitLine": {"lineStyle": {"opacity": 0.4}}},
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {"show": True, "type": "slider", "bottom": 8, "height": 18,
             "start": 0, "end": 100},
        ],
        "series": series,
    }


def _load_year_history(ts_code: str) -> pd.DataFrame | None:
    today = date.today()
    start = (today - timedelta(days=365)).isoformat()
    end = today.isoformat()
    return _load_stock_daily_range(ts_code, start, end)


def _load_stock_daily_range(ts_code: str, start: str, end: str) -> pd.DataFrame | None:
    engine = create_engine(
        f"sqlite:///{DB_PATH.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    try:
        q = text("""
            SELECT trade_date, close, stock_name
            FROM stock_daily
            WHERE ts_code = :code
              AND trade_date >= :start
              AND trade_date <= :end
            ORDER BY trade_date ASC
        """)
        df = pd.read_sql(q, engine, params={"code": ts_code, "start": start, "end": end})
    finally:
        engine.dispose()
    return df if not df.empty else None


def _opt_date_str(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() == "null":
        return None
    return s


def _parse_boll_date_range(args: dict) -> tuple[str, str] | str:
    today = date.today()
    start_s = _opt_date_str(args.get("start_date"))
    end_s = _opt_date_str(args.get("end_date"))

    if start_s is None and end_s is None:
        return (today - timedelta(days=365)).isoformat(), today.isoformat()
    if end_s is None:
        try:
            sd = date.fromisoformat(start_s)
        except ValueError:
            return "错误：start_date 须为 YYYY-MM-DD。"
        if sd > today:
            return "错误：start_date 不能晚于今天。"
        return sd.isoformat(), today.isoformat()
    if start_s is None:
        try:
            ed = date.fromisoformat(end_s)
        except ValueError:
            return "错误：end_date 须为 YYYY-MM-DD。"
        return (ed - timedelta(days=365)).isoformat(), ed.isoformat()
    try:
        sd = date.fromisoformat(start_s)
        ed = date.fromisoformat(end_s)
    except ValueError:
        return "错误：start_date / end_date 须为 YYYY-MM-DD。"
    if sd > ed:
        return "错误：start_date 不能晚于 end_date。"
    if ed > today:
        ed = today
    if sd > ed:
        return "错误：调整 end_date 至今天后，区间无效。"
    return sd.isoformat(), ed.isoformat()


def _compute_bollinger(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    w = BOLL_WINDOW
    mid = close.rolling(window=w, min_periods=w).mean()
    std = close.rolling(window=w, min_periods=w).std(ddof=0)
    return mid, mid + BOLL_STD_MULT * std, mid - BOLL_STD_MULT * std


# --------------------------------------------------------------------------- #
# 三个 nanobot Tool
# --------------------------------------------------------------------------- #

class ExcSQLTool(Tool):
    """执行只读 SQL，并自动生成 markdown 表格 + 柱状/折线图。"""

    @property
    def name(self) -> str:
        return "exc_sql"

    @property
    def description(self) -> str:
        return (
            "在本地 SQLite 的 stock_daily 表上执行只读 SQL 查询（仅 SELECT / WITH SELECT）；"
            "自动生成 markdown 表格、数值描述与交互式 ECharts 图表（K 线 / 折线 / 量价副图自动识别），"
            "图表以 ![xxx](chart:charts/xxx.json) markdown 占位返回，由前端渲染。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "sql_input": {
                    "type": "string",
                    "description": "SQL 语句（仅 SELECT / WITH SELECT）",
                }
            },
            "required": ["sql_input"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        sql_input = (kwargs.get("sql_input") or "").strip()
        if not sql_input:
            return "错误：sql_input 不能为空。"
        if not DB_PATH.is_file():
            return f"错误：未找到数据库文件 {DB_PATH}。请先运行 qwen-agent/fetch_stock_prices.py。"
        if not _is_read_only_sql(sql_input):
            return "错误：仅允许 SELECT 或 WITH ... SELECT 查询。"

        engine = create_engine(
            f"sqlite:///{DB_PATH.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        try:
            df = await asyncio.to_thread(pd.read_sql, text(sql_input), engine)
        except Exception as e:
            return f"SQL 执行失败: {e}"
        finally:
            engine.dispose()

        if df.empty:
            return "查询结果为空（0 行）。"

        md = _build_result_markdown(df)
        if df.shape[1] < 2:
            return md

        try:
            option, label = await asyncio.to_thread(_build_stock_echart, df)
        except Exception as e:
            return f"{md}\n\n*（绘图失败：{e}）*"

        chart_md = _save_echart_option(option, prefix="sql", label=label)
        return f"{md}\n\n{chart_md}"


class BollDetectionTool(Tool):
    """布林带（20 日 + 2σ）超买超卖检测。"""

    @property
    def name(self) -> str:
        return "boll_detection"

    @property
    def description(self) -> str:
        return (
            f"基于本地 SQLite 日线，使用布林带（{BOLL_WINDOW} 日、{BOLL_STD_MULT}σ）"
            "检测超买（收盘 > 上轨）、超卖（收盘 < 下轨）。默认检测近一年，"
            "可传 start_date、end_date（YYYY-MM-DD）。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ts_code": {
                    "type": "string",
                    "description": "股票 Tushare 代码，如 600519.SH（必填）",
                },
                "start_date": {
                    "type": "string",
                    "description": "检测区间起始 YYYY-MM-DD，可选；缺省则与 end_date 搭配或默认近一年",
                },
                "end_date": {
                    "type": "string",
                    "description": "检测区间结束 YYYY-MM-DD，可选；缺省为今天；仅填 end 则起点为其前一年",
                },
            },
            "required": ["ts_code"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        ts_code = str(kwargs.get("ts_code", "")).strip()
        if not ts_code:
            return "错误：ts_code 为必填。"

        dr = _parse_boll_date_range(kwargs)
        if isinstance(dr, str):
            return dr
        start_iso, end_iso = dr

        if not DB_PATH.is_file():
            return f"错误：未找到数据库文件 {DB_PATH}。"

        df = await asyncio.to_thread(_load_stock_daily_range, ts_code, start_iso, end_iso)
        if df is None:
            return f"错误：在 {start_iso}～{end_iso} 内无 ts_code={ts_code} 的数据，请检查代码或区间。"

        stock_name = str(df["stock_name"].iloc[-1])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) < MIN_BOLL_ROWS:
            return f"错误：区间内有效样本仅 {len(df)} 条，布林带至少需要约 {MIN_BOLL_ROWS} 条。"

        close = df["close"].astype(float)
        mid, upper, lower = _compute_bollinger(close)
        valid = upper.notna() & lower.notna()
        overbought = valid & (close > upper)
        oversold = valid & (close < lower)

        ob_df = df.loc[overbought, ["trade_date", "close"]].assign(
            signal="超买", boll_band=upper[overbought].values)
        os_df = df.loc[oversold, ["trade_date", "close"]].assign(
            signal="超卖", boll_band=lower[oversold].values)

        if ob_df.empty and os_df.empty:
            out_df = pd.DataFrame(columns=["trade_date", "close", "signal", "boll_band"])
        else:
            out_df = (
                pd.concat([ob_df, os_df], ignore_index=True)
                .sort_values("trade_date")
                .reset_index(drop=True)
            )
            out_df["close"] = out_df["close"].round(4)
            out_df["boll_band"] = out_df["boll_band"].round(4)

        dates_iso = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d").tolist()
        ob_idx = [int(i) for i in np.where(overbought.to_numpy())[0]]
        os_idx = [int(i) for i in np.where(oversold.to_numpy())[0]]

        option = _build_boll_echart(
            dates=dates_iso,
            close=_round_list(close),
            mid=_round_list(mid),
            upper=_round_list(upper),
            lower=_round_list(lower),
            ob_idx=ob_idx,
            os_idx=os_idx,
            title=f"{stock_name} ({ts_code}) 布林带与超买超卖",
        )
        chart_md = _save_echart_option(
            option,
            prefix=f"boll_{ts_code.replace('.', '_')}",
            label="布林带检测",
        )

        summary = (
            f"**布林带检测**（{BOLL_WINDOW} 日 + {BOLL_STD_MULT}σ）\n"
            f"- 股票：{stock_name}（{ts_code}）\n"
            f"- 区间：**{start_iso}** ～ **{end_iso}**（共 {len(df)} 个交易日）\n"
            f"- **超买**（收盘 > 上轨）：**{int(overbought.sum())}** 日\n"
            f"- **超卖**（收盘 < 下轨）：**{int(oversold.sum())}** 日\n"
            f"- *仅供技术分析学习，不构成投资建议。*\n\n"
        )
        tbl_md = out_df.to_markdown(index=False) if not out_df.empty else ""
        if out_df.empty:
            summary += "区间内未检测到超买/超卖触点。\n"
        else:
            summary += "**异常日明细：**\n"
        return f"{summary}{tbl_md}\n\n{chart_md}" if tbl_md else f"{summary}\n{chart_md}"


class ArimaStockTool(Tool):
    """ARIMA(5,1,5) 预测未来 n 个交易日收盘价。"""

    @property
    def name(self) -> str:
        return "arima_stock"

    @property
    def description(self) -> str:
        return (
            "对指定 ts_code 使用近一年历史收盘价拟合 ARIMA(5,1,5)，预测未来 n 个交易日收盘价；"
            "返回预测表与走势图（仅供学习，非投资建议）。"
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "ts_code": {
                    "type": "string",
                    "description": "股票 Tushare 代码，如 600519.SH（必填）",
                },
                "n": {
                    "type": "integer",
                    "description": f"向前预测的交易日数量，1 ~ {MAX_FORECAST_DAYS}",
                    "minimum": 1,
                    "maximum": MAX_FORECAST_DAYS,
                },
            },
            "required": ["ts_code", "n"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        ts_code = str(kwargs.get("ts_code", "")).strip()
        if not ts_code:
            return "错误：ts_code 为必填。"
        try:
            n = int(kwargs.get("n", 5))
        except (TypeError, ValueError):
            return "错误：参数 n 必须为整数。"
        if n < 1 or n > MAX_FORECAST_DAYS:
            return f"错误：n 需在 1～{MAX_FORECAST_DAYS} 之间。"

        if not DB_PATH.is_file():
            return f"错误：未找到数据库文件 {DB_PATH}。"

        df = await asyncio.to_thread(_load_year_history, ts_code)
        if df is None:
            return f"错误：未找到 ts_code={ts_code} 在近一年内的数据，请确认代码或先更新数据库。"

        stock_name = str(df["stock_name"].iloc[-1])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) < MIN_ARIMA_OBS:
            return (
                f"错误：有效收盘价样本仅 {len(df)} 条，ARIMA{ARIMA_ORDER} 至少需要约 {MIN_ARIMA_OBS} 条。"
            )

        series = df["close"].astype(float)
        last_trade = pd.to_datetime(df["trade_date"].iloc[-1])
        future_dates = pd.bdate_range(start=last_trade + BDay(1), periods=n)

        def _fit_and_forecast():
            model = ARIMA(series, order=ARIMA_ORDER)
            fitted = model.fit()
            fc = fitted.get_forecast(steps=n)
            pred = fc.predicted_mean.to_numpy()
            conf = fc.conf_int()
            return pred, conf.iloc[:, 0].to_numpy(), conf.iloc[:, 1].to_numpy()

        try:
            pred, low, high = await asyncio.to_thread(_fit_and_forecast)
        except Exception as e:
            return f"ARIMA{ARIMA_ORDER} 拟合或预测失败（数据或数值问题）：{e}"

        fc_df = pd.DataFrame({
            "forecast_date": future_dates.strftime("%Y-%m-%d"),
            "forecast_close": np.round(pred, 4),
            "ci_lower_95": np.round(low, 4),
            "ci_upper_95": np.round(high, 4),
        })

        tail = min(120, len(df))
        hist_dates = pd.to_datetime(
            df["trade_date"].iloc[-tail:]
        ).dt.strftime("%Y-%m-%d").tolist()
        hist_close = _round_list(series.iloc[-tail:])
        fc_dates = future_dates.strftime("%Y-%m-%d").tolist()

        option = _build_arima_echart(
            hist_dates=hist_dates,
            hist_close=hist_close,
            fc_dates=fc_dates,
            fc_mean=_round_list(pred),
            fc_low=_round_list(low),
            fc_high=_round_list(high),
            title=f"{stock_name} ({ts_code}) 收盘价与 ARIMA 预测",
        )
        chart_md = _save_echart_option(
            option,
            prefix=f"arima_{ts_code.replace('.', '_')}",
            label="ARIMA 预测",
        )

        summary = (
            f"**ARIMA{ARIMA_ORDER} 预测概况**\n"
            f"- 股票：{stock_name}（{ts_code}）\n"
            f"- 训练样本：{len(series)} 个交易日（约一年内至 {df['trade_date'].iloc[-1]}）\n"
            f"- 预测：未来 **{n}** 个交易日\n"
            f"- *免责声明：预测仅供学习参考，不构成投资建议。*\n\n"
            f"{fc_df.to_markdown(index=False)}"
        )
        return f"{summary}\n\n{chart_md}"


# --------------------------------------------------------------------------- #
# 日志 hook：把每次工具调用打印出来，方便排障
# --------------------------------------------------------------------------- #

class PrintHook(AgentHook):
    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        for tc in ctx.tool_calls:
            args = json.dumps(tc.arguments, ensure_ascii=False)
            print(f"  >> {tc.name}: {args[:200]}")


# --------------------------------------------------------------------------- #
# 构建 Nanobot
# --------------------------------------------------------------------------- #

def build_bot() -> Nanobot:
    dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not dashscope_key:
        print("[Error] 未设置 DASHSCOPE_API_KEY 环境变量")
        sys.exit(1)

    config = load_config(WORKSPACE / "config.json")
    config.providers.dashscope.api_key = dashscope_key
    config.agents.defaults.workspace = str(WORKSPACE)

    # 支持通过 QWEN_AGENT_MODEL 覆盖模型
    if model_override := os.environ.get("QWEN_AGENT_MODEL", "").strip():
        config.agents.defaults.model = model_override

    provider = _make_provider(config)
    defaults = config.agents.defaults

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=WORKSPACE,
        model=defaults.model,
        max_iterations=defaults.max_tool_iterations,
        context_window_tokens=defaults.context_window_tokens,
        max_tool_result_chars=defaults.max_tool_result_chars,
        web_config=config.tools.web,
        exec_config=config.tools.exec,
        restrict_to_workspace=False,
        timezone=defaults.timezone,
    )

    # 注册三个业务工具
    loop.tools.register(ExcSQLTool())
    loop.tools.register(BollDetectionTool())
    loop.tools.register(ArimaStockTool())

    print(f"[nanobot] model={defaults.model}, DB={DB_PATH}")
    print("[nanobot] 已注册工具: exc_sql, boll_detection, arima_stock")
    return Nanobot(loop)


# --------------------------------------------------------------------------- #
# CLI 入口
# --------------------------------------------------------------------------- #

async def _run_once(bot: Nanobot, question: str, session_key: str = "stock:cli") -> None:
    result = await bot.run(question, session_key=session_key, hooks=[PrintHook()])
    print("\n" + "=" * 60)
    print(result.content)
    print("=" * 60)


async def main() -> None:
    bot = build_bot()

    if len(sys.argv) > 1:
        await _run_once(bot, " ".join(sys.argv[1:]))
        return

    print("\n股票查询助手（nanobot 版）- 输入 exit/quit 退出\n")
    session_key = f"stock:repl:{int(time.time())}"
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", ":q"):
            break
        try:
            await _run_once(bot, q, session_key=session_key)
        except Exception as e:
            print(f"[Error] {e}")


if __name__ == "__main__":
    asyncio.run(main())
