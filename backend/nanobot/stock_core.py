#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_core —— 股票助手共享底层

同时服务两类调用者：
  1. `stock_tools/exc_sql.py`           —— 常驻 in-process tool
  2. `skills/*/scripts/*.py`            —— exec tool 调起的一次性脚本

提供：
  * DB 路径 / CHARTS_DIR / 业务常量
  * SQL 守卫、markdown 构建
  * 智能 ECharts option builders（K 线 / 折线 / ARIMA / 布林带）
  * 数据加载（日线区间、近一年）
  * 布林带指标计算
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# 路径与常量
# --------------------------------------------------------------------------- #

# 不管从哪里 import 进来，WORKSPACE 永远指向 nanobot/
WORKSPACE = Path(__file__).resolve().parent

# 数据库连接：
# 旧版使用 SQLite 文件（STOCK_DB_PATH），现统一改为 MySQL（STOCK_DATABASE_URL / DATABASE_URL）。
# 为兼容迁移期，可仍接受 STOCK_DB_PATH，但优先使用 MySQL 连接串。
_DEFAULT_SQLITE_DB = WORKSPACE / "data" / "stock_prices_history.db"
DB_PATH = Path(os.environ.get("STOCK_DB_PATH", str(_DEFAULT_SQLITE_DB))).resolve()
STOCK_DATABASE_URL = (
    os.environ.get("STOCK_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or ""
).strip()
CHARTS_DIR = WORKSPACE / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

ARIMA_ORDER = (5, 1, 5)
MIN_ARIMA_OBS = 80
MAX_FORECAST_DAYS = 60
BOLL_WINDOW = 20
BOLL_STD_MULT = 2.0
MIN_BOLL_ROWS = 25


# --------------------------------------------------------------------------- #
# SQL 守卫 & markdown 构建
# --------------------------------------------------------------------------- #

def is_read_only_sql(sql: str) -> bool:
    s = sql.strip().lstrip("(").strip().upper()
    return s.startswith("SELECT") or s.startswith("WITH")


def safe_label(s: Any) -> str:
    return str(s).replace("%", "%%").replace("{", "{{").replace("}", "}}")


def build_result_markdown(df: pd.DataFrame) -> str:
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
# 数据加载
# --------------------------------------------------------------------------- #

def _engine():
    # 延迟导入：skill script 冷启动时能省一点时间，且便于按需使用
    from sqlalchemy import create_engine
    if STOCK_DATABASE_URL:
        # 将异步驱动转换为同步驱动（pd.read_sql 仅支持同步 engine）
        url = STOCK_DATABASE_URL
        url = url.replace("mysql+aiomysql://", "mysql+pymysql://")
        url = url.replace("mysql+asyncmy://", "mysql+pymysql://")
        return create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    # 兼容：未配置 MySQL 时回退到 SQLite（主要用于本地旧数据排查）
    return create_engine(f"sqlite:///{DB_PATH.as_posix()}", connect_args={"check_same_thread": False})


def load_stock_daily_range(ts_code: str, start: str, end: str) -> pd.DataFrame | None:
    """读 ts_code 在 [start, end] 的 trade_date / close / stock_name。"""
    from sqlalchemy import text
    engine = _engine()
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


def load_year_history(ts_code: str) -> pd.DataFrame | None:
    today = date.today()
    start = (today - timedelta(days=365)).isoformat()
    end = today.isoformat()
    return load_stock_daily_range(ts_code, start, end)


def run_query(sql: str) -> pd.DataFrame:
    """执行任意只读 SQL，失败会抛 SQLAlchemy 异常。"""
    from sqlalchemy import text
    engine = _engine()
    try:
        return pd.read_sql(text(sql), engine)
    finally:
        engine.dispose()


# --------------------------------------------------------------------------- #
# 日期参数解析（布林带）
# --------------------------------------------------------------------------- #

def _opt_date_str(val) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() == "null":
        return None
    return s


def parse_boll_date_range(start_date: str | None, end_date: str | None
                          ) -> tuple[str, str] | str:
    """返回 (start, end) 或错误字符串。"""
    today = date.today()
    start_s = _opt_date_str(start_date)
    end_s = _opt_date_str(end_date)

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


def compute_bollinger(close: pd.Series
                      ) -> tuple[pd.Series, pd.Series, pd.Series]:
    w = BOLL_WINDOW
    mid = close.rolling(window=w, min_periods=w).mean()
    std = close.rolling(window=w, min_periods=w).std(ddof=0)
    return mid, mid + BOLL_STD_MULT * std, mid - BOLL_STD_MULT * std


# --------------------------------------------------------------------------- #
# ECharts 主题与共享工具
# --------------------------------------------------------------------------- #

_PRICE_COLS = {"open", "high", "low", "close", "pre_close"}
_VOLUME_COLS = {"vol", "volume"}
_AMOUNT_COLS = {"amount", "turnover"}
_PCT_COLS = {"pct_chg", "pct_change", "change_pct", "pctchg"}
_CHANGE_COLS = {"change", "chg"}
_DATE_COLS = {"trade_date", "date", "datetime", "dt", "day"}

COLOR_UP = "#ef4444"
COLOR_DOWN = "#22c55e"
COLOR_MA = ("#f59e0b", "#8b5cf6", "#0ea5e9")
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
    first = df.columns[0]
    if df[first].dtype == "O":
        try:
            pd.to_datetime(df[first].head(5), errors="raise")
            return first
        except Exception:
            return None
    return None


def _drop_constant_object_cols(df: pd.DataFrame, exclude: set[str]) -> pd.DataFrame:
    drop_cols = []
    for c in df.columns:
        if c in exclude:
            continue
        if df[c].dtype == "O" and df[c].nunique(dropna=True) <= 1:
            drop_cols.append(c)
    return df.drop(columns=drop_cols) if drop_cols else df


def _group_numeric_cols(cols: list[str]) -> dict[str, list[str]]:
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


def round_list(vals, ndigits: int = 4) -> list:
    """Series/ndarray → list，NaN → None，JSON 安全。"""
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


def _moving_average(values: list, window: int) -> list:
    out: list = []
    buf: list[float] = []
    acc = 0.0
    for v in values:
        x = None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)
        if x is None:
            buf.append(0.0)
            out.append(None)
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


def save_echart_option(option: dict, prefix: str, *, label: str = "图表") -> str:
    """落盘 JSON 到 charts/，返回 markdown 引用（chart: 前缀）。"""
    filename = f"{prefix}_{int(time.time() * 1000)}.json"
    path = CHARTS_DIR / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(option, f, ensure_ascii=False, separators=(",", ":"))
    return f"![{label}](chart:charts/{filename})"


# --------------------------------------------------------------------------- #
# ECharts option builders
# --------------------------------------------------------------------------- #

def _build_kline_option(dates, ohlc, volumes, title):
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
    grids = [{
        "left": "7%", "right": "4%", "top": 56,
        "height": "58%" if has_vol else "78%",
    }]
    x_axes = [{
        "type": "category", "data": dates,
        "scale": True, "boundaryGap": False,
        "axisLine": {"onZero": False},
        "splitLine": {"show": False},
        "axisTick": {"show": False},
        "min": "dataMin", "max": "dataMax",
    }]
    y_axes = [{"scale": True, "splitArea": {"show": True}}]
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
        up_mask = [row[1] >= row[0] for row in ohlc]
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


def _simple_grid_option(dates, *, title, panels):
    n_panels = len(panels)
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
            "type": "category",
            "gridIndex": i,
            "data": dates,
            "boundaryGap": (panel.get("type") == "bar"),
            "axisLabel": {"show": i == n_panels - 1},
            "axisLine": {"onZero": False},
            "axisTick": {"show": i == n_panels - 1},
            "splitLine": {"show": False},
        })
        y_axes.append({
            "gridIndex": i, "scale": True,
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
            for k in ("lineStyle", "itemStyle", "areaStyle", "stack"):
                if s.get(k):
                    entry[k] = s[k]
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


def build_stock_echart(df_sql: pd.DataFrame, *, max_rows: int = 500
                       ) -> tuple[dict, str]:
    """exc_sql 的智能绘图入口：OHLC → K 线；否则按量纲分 panel。"""
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

    if len(df) > max_rows:
        idx = np.unique(np.linspace(0, len(df) - 1, max_rows, dtype=int))
        df = df.iloc[idx].reset_index(drop=True)
        dates = [dates[i] for i in idx]

    num_cols = [c for c in df.columns
                if c != date_col and pd.api.types.is_numeric_dtype(df[c])]
    if not num_cols:
        return {
            "title": {"text": "无可绘数值列", "left": "center"},
            "xAxis": {"type": "category", "data": dates},
            "yAxis": {"type": "value"},
            "series": [],
        }, "占位图"

    groups = _group_numeric_cols(num_cols)
    has_ohlc = {"open", "high", "low", "close"}.issubset(
        {str(c).lower() for c in groups.get("price", [])}
    )
    if has_ohlc:
        lowers = {str(c).lower(): c for c in groups["price"]}
        o = round_list(df[lowers["open"]])
        h = round_list(df[lowers["high"]])
        lo = round_list(df[lowers["low"]])
        cl_ = round_list(df[lowers["close"]])
        ohlc = [[o[i], cl_[i], lo[i], h[i]] for i in range(len(df))]
        vol_col = None
        for c in groups.get("volume", []) + groups.get("amount", []):
            vol_col = c
            break
        volumes = round_list(df[vol_col]) if vol_col else None
        title = f"K 线图（{len(df)} 个交易日 · 红涨绿跌）"
        option = _build_kline_option(dates, ohlc, volumes, title)
        label = "K 线图 + MA" + (" + 成交量" if volumes else "")
        return option, label

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
                "name": safe_label(col),
                "type": panel_type,
                "data": round_list(df[col]),
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


def build_arima_echart(hist_dates, hist_close, fc_dates, fc_mean,
                       fc_low, fc_high, title):
    """历史收盘 + 预测均值 + 95% 置信带（stack 技巧）。"""
    all_dates = list(hist_dates) + list(fc_dates)
    n_hist, n_fc = len(hist_dates), len(fc_dates)

    hist_series = list(hist_close) + [None] * n_fc
    fc_series = [None] * (n_hist - 1) + [hist_close[-1]] + list(fc_mean)
    ci_low = [None] * n_hist + list(fc_low)
    ci_diff = [None] * n_hist + [h - l for h, l in zip(fc_high, fc_low)]

    series = [
        {"name": "95% 置信区间", "type": "line", "data": ci_low, "stack": "ci",
         "lineStyle": {"opacity": 0}, "showSymbol": False,
         "itemStyle": {"color": "transparent"}, "tooltip": {"show": False}},
        {"name": "95% 置信区间", "type": "line", "data": ci_diff, "stack": "ci",
         "lineStyle": {"opacity": 0}, "showSymbol": False,
         "areaStyle": {"color": COLOR_CI},
         "itemStyle": {"color": COLOR_CI}},
        {"name": "历史收盘", "type": "line", "data": hist_series,
         "showSymbol": False, "smooth": True,
         "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
         "itemStyle": {"color": COLOR_CLOSE}, "connectNulls": False},
        {"name": "ARIMA 预测", "type": "line", "data": fc_series,
         "symbol": "circle", "symbolSize": 6, "showSymbol": True, "smooth": False,
         "lineStyle": {"width": 1.8, "color": COLOR_FORECAST, "type": "dashed"},
         "itemStyle": {"color": COLOR_FORECAST}, "connectNulls": False},
    ]

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {"data": ["历史收盘", "ARIMA 预测", "95% 置信区间"],
                   "top": 30, "textStyle": {"fontSize": 12}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "grid": {"left": "7%", "right": "4%", "top": 64, "bottom": 64},
        "xAxis": {"type": "category", "data": all_dates,
                  "boundaryGap": False, "scale": True,
                  "axisLine": {"onZero": False}},
        "yAxis": {"type": "value", "scale": True,
                  "splitLine": {"lineStyle": {"opacity": 0.4}}},
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {"show": True, "type": "slider", "bottom": 8, "height": 18,
             "start": 0, "end": 100},
        ],
        "series": series,
    }


def build_boll_echart(dates, close, mid, upper, lower, ob_idx, os_idx, title):
    """收盘 + MA20 + ±2σ 上下轨 + 带区 + 超买/超卖散点。"""
    diff = [
        None if (u is None or l is None) else round(u - l, 4)
        for u, l in zip(upper, lower)
    ]
    ob_points = [[dates[i], close[i]] for i in ob_idx]
    os_points = [[dates[i], close[i]] for i in os_idx]

    series = [
        {"name": "布林下轨", "type": "line", "data": lower, "stack": "boll",
         "lineStyle": {"opacity": 0}, "showSymbol": False,
         "tooltip": {"show": False},
         "itemStyle": {"color": "transparent"}},
        {"name": "布林带", "type": "line", "data": diff, "stack": "boll",
         "lineStyle": {"opacity": 0}, "showSymbol": False,
         "areaStyle": {"color": COLOR_BOLL_BAND},
         "itemStyle": {"color": COLOR_BOLL_BAND},
         "tooltip": {"show": False}},
        {"name": "上轨 +2σ", "type": "line", "data": upper,
         "showSymbol": False,
         "lineStyle": {"width": 1, "color": COLOR_BOLL_UP, "type": "dashed"},
         "itemStyle": {"color": COLOR_BOLL_UP}},
        {"name": "中轨 MA20", "type": "line", "data": mid,
         "showSymbol": False,
         "lineStyle": {"width": 1, "color": COLOR_BOLL_MID},
         "itemStyle": {"color": COLOR_BOLL_MID}},
        {"name": "下轨 -2σ", "type": "line", "data": lower,
         "showSymbol": False,
         "lineStyle": {"width": 1, "color": COLOR_BOLL_LOW, "type": "dashed"},
         "itemStyle": {"color": COLOR_BOLL_LOW}},
        {"name": "收盘", "type": "line", "data": close,
         "showSymbol": False, "smooth": True,
         "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
         "itemStyle": {"color": COLOR_CLOSE}},
        {"name": "超买", "type": "scatter", "data": ob_points,
         "symbolSize": 10, "itemStyle": {"color": COLOR_UP}},
        {"name": "超卖", "type": "scatter", "data": os_points,
         "symbolSize": 10, "itemStyle": {"color": COLOR_DOWN}},
    ]
    legend = ["收盘", "中轨 MA20", "上轨 +2σ", "下轨 -2σ", "超买", "超卖"]

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {"data": legend, "top": 30, "textStyle": {"fontSize": 12}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "grid": {"left": "7%", "right": "4%", "top": 64, "bottom": 64},
        "xAxis": {"type": "category", "data": dates,
                  "boundaryGap": False, "scale": True,
                  "axisLine": {"onZero": False}},
        "yAxis": {"type": "value", "scale": True,
                  "splitLine": {"lineStyle": {"opacity": 0.4}}},
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {"show": True, "type": "slider", "bottom": 8, "height": 18,
             "start": 0, "end": 100},
        ],
        "series": series,
    }


# --------------------------------------------------------------------------- #
# Windows UTF-8 stdout（对 CLI 脚本友好）
# --------------------------------------------------------------------------- #

def setup_utf8_stdout() -> None:
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
