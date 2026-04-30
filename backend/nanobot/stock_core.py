#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_core —— 股票助手共享底层

同时服务两类调用者：
  1. `stock_tools/exc_sql.py`           —— 常驻 in-process tool
  2. `skills/*/scripts/*.py`            —— exec tool 调起的一次性脚本

提供：
  * DB 连接（仅 MySQL：**环境变量 `DATABASE_URL`**，同步侧自动将 mysql+aiomysql 换成 pymysql）
  * SQL 守卫、markdown 构建
  * 智能 ECharts option builders（K 线 / 折线 / ARIMA / 布林带）
  * 数据加载（日线区间、近一年）
  * 布林带指标计算
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# 路径与常量
# --------------------------------------------------------------------------- #

# 不管从哪里 import 进来，WORKSPACE 永远指向 nanobot/
WORKSPACE = Path(__file__).resolve().parent

CHARTS_DIR = WORKSPACE / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)


def get_database_url() -> str:
    """当前进程下的数据库 URL；仅读取环境变量 **`DATABASE_URL`**。"""
    return (os.environ.get("DATABASE_URL") or "").strip()


def has_stock_database_access() -> bool:
    """是否已配置 DATABASE_URL（不做文件探测、不接库验证）。"""
    return bool(get_database_url())


def load_backend_dotenv_if_empty() -> None:
    """
    供 exec 拉的 skill 脚本在子进程起步时调用：`nanobot/..` → `backend/.env`。
    父进程已通过 allowed_env_keys 透传时使用不到；直接从命令行跑脚本时能补齐。
    """
    if get_database_url():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    p = WORKSPACE.parent / ".env"
    if p.is_file():
        load_dotenv(p, override=False)


def _normalize_sync_mysql_url(raw: str) -> str:
    u = raw.strip()
    if not u:
        return ""
    return u.replace("mysql+aiomysql://", "mysql+pymysql://").replace(
        "mysql+asyncmy://", "mysql+pymysql://"
    )

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

    url = _normalize_sync_mysql_url(get_database_url())
    if not url:
        raise RuntimeError(
            "DATABASE_URL 未设置：请在环境中配置 DATABASE_URL（MySQL），"
            "与 FastAPI backend/.env 一致。"
        )
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)


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


def bollinger_series_for_viz(
    ts_code: str,
    start: str,
    end: str,
    *,
    table_max_rows: int = 500,
) -> dict[str, Any]:
    """
    布林带：日线序列 + ``build_boll_echart`` + Ant Design 表格载荷。
    供 detect.py 与大屏命名转换共用；失败抛 ``ValueError``（中文）。
    """
    ts_code = (ts_code or "").strip().upper()
    if not ts_code:
        raise ValueError("缺少股票代码 ts_code")

    try:
        df = load_stock_daily_range(ts_code, start, end)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"数据库查询失败：{e}") from e
    if df is None:
        raise ValueError(f"未找到 {ts_code} 在 [{start}, {end}] 的日线数据。")
    if len(df) < MIN_BOLL_ROWS:
        raise ValueError(
            f"{ts_code} 在 [{start}, {end}] 仅有 {len(df)} 条日线，"
            f"不足 {MIN_BOLL_ROWS} 条，不足以计算布林带。"
        )

    try:
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"trade_date 解析失败：{e}") from e
    df = df.sort_values("trade_date").reset_index(drop=True)

    close = df["close"].astype(float)
    mid, upper, lower = compute_bollinger(close)

    valid = mid.notna()
    overbought_mask = valid & (close > upper)
    oversold_mask = valid & (close < lower)

    signals = pd.DataFrame({
        "trade_date": df["trade_date"].dt.strftime("%Y-%m-%d"),
        "close": close.round(4),
        "mid_ma20": mid.round(4),
        "upper_2sigma": upper.round(4),
        "lower_2sigma": lower.round(4),
        "signal": [
            "超买" if ob else ("超卖" if os_ else "")
            for ob, os_ in zip(overbought_mask, oversold_mask)
        ],
    })

    dates = [d.strftime("%Y-%m-%d") for d in df["trade_date"]]
    close_l = round_list(close)
    mid_l = round_list(mid)
    upper_l = round_list(upper)
    lower_l = round_list(lower)
    ob_idx = [i for i, x in enumerate(overbought_mask.tolist()) if x]
    os_idx = [i for i, x in enumerate(oversold_mask.tolist()) if x]

    stock_name = str(df["stock_name"].iloc[-1]) if "stock_name" in df.columns else ts_code
    option = build_boll_echart(
        dates,
        close_l,
        mid_l,
        upper_l,
        lower_l,
        ob_idx,
        os_idx,
        title=(
            f"{safe_label(stock_name)} ({ts_code}) · 布林带 MA{BOLL_WINDOW}±{BOLL_STD_MULT:g}σ · "
            f"{dates[0]} ~ {dates[-1]}"
        ),
    )

    tab_payload, tab_truncated = dataframe_to_antd_table_payload(signals, max_rows=table_max_rows)
    n_ob = int(overbought_mask.sum())
    n_os = int(oversold_mask.sum())

    return {
        "option": option,
        "table_payload": tab_payload,
        "table_truncated": tab_truncated,
        "n_overbought": n_ob,
        "n_oversold": n_os,
        "stock_name": safe_label(stock_name),
        "ts_code": ts_code,
        "start": start,
        "end": end,
        "trade_days": len(df),
        "signals_total": len(signals),
    }


# --------------------------------------------------------------------------- #
# ECharts 主题与共享工具
# --------------------------------------------------------------------------- #

_PRICE_COLS = {"open", "high", "low", "close", "pre_close"}
_VOLUME_COLS = {"vol", "volume"}
_AMOUNT_COLS = {"amount", "turnover"}
_PCT_COLS = {"pct_chg", "pct_change", "change_pct", "pctchg"}
_CHANGE_COLS = {"change", "chg", "change_val"}
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


def sanitize_for_json(obj: Any) -> Any:
    """NaN/Inf→null；numpy 标量→ Python 原生。供 ``json.dumps(..., allow_nan=False)`` / 浏览器 JSON.parse。"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(v) for v in obj]
    if isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        x = float(obj)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    if isinstance(obj, int):
        return obj
    return obj


def dumps_json_for_fence(obj: Any) -> str:
    """紧凑单行 JSON（避免 MySQL messages.content TEXT 64KB 截断）；禁止 NaN 字面量。"""
    return json.dumps(
        sanitize_for_json(obj),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def write_echart_asset(option: dict, prefix: str) -> str:
    """落盘 JSON 到 charts/，返回相对路径 ``charts/xxx.json``（供附件/排查，不写入聊天正文）。"""
    filename = f"{prefix}_{int(time.time() * 1000)}.json"
    path = CHARTS_DIR / filename
    with path.open("w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(option), f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return f"charts/{filename}"


def format_echarts_fence(option: dict) -> str:
    """Web 主站 MessageItem：语言标签 ``echarts`` 的 fenced 块；与 exc_sql 同为紧凑合法 JSON。"""
    body = dumps_json_for_fence(option)
    return f"```echarts\n{body}\n```"


def dataframe_to_antd_table_payload(
    df: pd.DataFrame,
    *,
    max_rows: int = 200,
) -> tuple[dict, bool]:
    """
    Ant Design Table JSON，与 ``exc_sql`` 的 ``datatable`` 围栏一致：
    ``{"columns":[{"title","dataIndex"}], "data":[dict,...]}``。
    """
    cols = [{"title": str(c), "dataIndex": str(c)} for c in df.columns.tolist()]
    data = df.to_dict(orient="records")
    for row in data:
        for k, v in row.items():
            if isinstance(v, (date, datetime)):
                row[k] = v.isoformat() if hasattr(v, "isoformat") else str(v)
    truncated = len(data) > max_rows
    if truncated:
        data = data[:max_rows]
    return {"columns": cols, "data": data}, truncated


def format_datatable_fence(payload: dict, *, truncation_note_rows: int | None = None) -> str:
    """Web 主站：语言标签 ``datatable`` 的 fenced 块 + JSON（与 exc_sql 同源）。"""
    body = dumps_json_for_fence(payload)
    block = f"```datatable\n{body}\n```"
    if truncation_note_rows is not None:
        block = f"*（表格仅展示前 {truncation_note_rows} 行，已截断）*\n\n{block}"
    return block


_YMD_FULL = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def dates_are_daily_strings(dates: list) -> bool:
    """X 轴为 ``YYYY-MM-DD`` 时使用 ECharts ``time`` 轴。"""
    if not dates:
        return False
    for x in dates:
        if not isinstance(x, str) or not _YMD_FULL.fullmatch(x):
            return False
    return True


def _format_y_tick_estimate(v: Any) -> int:
    """与 ECharts 默认数值刻度长度同阶的粗略字符数（用于估算 grid.left）。"""
    if v is None:
        return 1
    try:
        xf = float(v)
        if math.isnan(xf) or math.isinf(xf):
            return 1
        s = f"{xf:.6g}"
        return max(4, len(s))
    except (TypeError, ValueError):
        return max(4, len(str(v)))


def _grid_left_px_from_values(
    *collections: list[Any],
    floor: int = 48,
    ceiling: int = 240,
    extra_pad: int = 0,
) -> int:
    maxlen = 4
    for coll in collections:
        if not coll:
            continue
        for v in coll:
            maxlen = max(maxlen, _format_y_tick_estimate(v))
    char_w = 7
    margin = 28 + extra_pad
    return int(min(ceiling, max(floor, maxlen * char_w + margin)))


def _pairs_date_value(dates: list[str], values: list[Any]) -> list[list[Any]]:
    out: list[list[Any]] = []
    n = min(len(dates), len(values))
    for i in range(n):
        y = values[i]
        if y is None:
            continue
        try:
            if isinstance(y, float) and (math.isnan(y) or math.isinf(y)):
                continue
        except (TypeError, ValueError):
            pass
        out.append([str(dates[i]), float(y)])
    return out


# --------------------------------------------------------------------------- #
# ECharts option builders
# --------------------------------------------------------------------------- #

def _build_kline_option(dates, ohlc, volumes, title):
    closes = [row[1] for row in ohlc]
    use_time = dates_are_daily_strings(dates)

    nums_for_margin: list[Any] = [*closes]
    for row in ohlc:
        nums_for_margin.extend(row)
    if volumes is not None and len(volumes) == len(dates):
        nums_for_margin.extend([v for v in volumes if v is not None])
    left_px = _grid_left_px_from_values(nums_for_margin)

    if use_time:
        candle_data: list[list[Any]] = [
            [str(dates[i]), ohlc[i][0], ohlc[i][1], ohlc[i][2], ohlc[i][3]]
            for i in range(len(dates))
        ]
    else:
        candle_data = ohlc

    series: list[dict] = [{
        "name": "K线",
        "type": "candlestick",
        "data": candle_data,
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
            ma_vals = _moving_average(closes, period)
            if use_time:
                ma_pts = [[str(dates[j]), float(ma_vals[j])] for j in range(len(dates))
                          if ma_vals[j] is not None]
                ma_data = ma_pts
            else:
                ma_data = ma_vals
            series.append({
                "name": f"MA{period}",
                "type": "line",
                "data": ma_data,
                "smooth": True,
                "showSymbol": False,
                "lineStyle": {"width": 1.1, "opacity": 0.95, "color": color},
                "emphasis": {"focus": "series"},
            })
            legend_items.append(f"MA{period}")

    has_vol = volumes is not None and len(volumes) == len(dates)
    grids = [{
        "left": left_px, "right": 32, "top": 56,
        "height": "58%" if has_vol else "78%",
    }]

    if use_time:
        x_axes: list[dict] = [{
            "type": "time",
            "scale": True, "boundaryGap": False,
            "axisLine": {"onZero": False},
            "splitLine": {"show": False},
            "axisTick": {"show": False},
            "min": "dataMin", "max": "dataMax",
        }]
    else:
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
        grids.append({"left": left_px, "right": 32, "top": "74%", "height": "16%"})
        if use_time:
            x_axes.append({
                "type": "time",
                "gridIndex": 1,
                "scale": True,
                "boundaryGap": False,
                "axisLine": {"onZero": False},
                "axisLabel": {"show": False},
                "axisTick": {"show": False},
                "splitLine": {"show": False},
                "min": "dataMin", "max": "dataMax",
            })
        else:
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
        vol_data = []
        for i, v in enumerate(volumes or []):
            val = v if v is not None else 0
            col = COLOR_UP if up_mask[i] else COLOR_DOWN
            if use_time:
                vol_data.append({
                    "value": [str(dates[i]), val],
                    "itemStyle": {"color": col},
                })
            else:
                vol_data.append({"value": val, "itemStyle": {"color": col}})
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
             "bottom": 16, "height": 18,
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

    use_time = dates_are_daily_strings(dates)
    nums_for_margin: list[Any] = []
    for panel in panels:
        for s in panel.get("series", []):
            nums_for_margin.extend(s.get("data") or [])
    yname_extra = max((len(panel.get("yname") or "") for panel in panels), default=0) * 6
    left_px = _grid_left_px_from_values(nums_for_margin, extra_pad=min(72, yname_extra))

    grids: list[dict] = []
    x_axes: list[dict] = []
    y_axes: list[dict] = []
    series: list[dict] = []
    legend_items: list[str] = []
    dz_axes: list[int] = list(range(n_panels))

    for i, panel in enumerate(panels):
        top_pct = top_reserve + i * (each_h + gap_pct)
        grids.append({
            "left": left_px,
            "right": 32,
            "top": f"{top_pct:.2f}%",
            "height": f"{each_h:.2f}%",
        })

        boundary_gap_val = any(s.get("type") == "bar" for s in panel.get("series", []))
        if use_time:
            x_axes.append({
                "type": "time",
                "gridIndex": i,
                "scale": True,
                "boundaryGap": boundary_gap_val,
                "axisLabel": {"show": i == n_panels - 1},
                "axisLine": {"onZero": False},
                "axisTick": {"show": i == n_panels - 1},
                "splitLine": {"show": False},
                "min": "dataMin", "max": "dataMax",
            })
        else:
            x_axes.append({
                "type": "category",
                "gridIndex": i,
                "data": dates,
                "boundaryGap": boundary_gap_val,
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
            raw_data = s["data"]
            if use_time:
                stype = s.get("type", "line")
                pdata: list[list[Any]] = []
                for j in range(min(len(dates), len(raw_data))):
                    yj = raw_data[j]
                    if yj is None:
                        continue
                    pdata.append([str(dates[j]), yj])
                plotted = pdata
            else:
                plotted = raw_data

            entry: dict = {
                "name": s["name"],
                "type": s.get("type", "line"),
                "xAxisIndex": i,
                "yAxisIndex": i,
                "data": plotted,
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
             "bottom": 16, "height": 18, "start": 0, "end": 100},
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
    """历史收盘 + 预测均值 + 95% 置信带（stack 技巧）。日期为 ``YYYY-MM-DD`` 时使用 time 轴。"""
    left_px = _grid_left_px_from_values(hist_close, fc_mean, fc_low, fc_high)
    hd = [str(x) for x in hist_dates]
    fd = [str(x) for x in fc_dates]
    use_time = dates_are_daily_strings(hd) and dates_are_daily_strings(fd)

    if use_time:
        ci_low_pts = [[fd[i], float(fc_low[i])] for i in range(len(fd))]
        ci_diff_pts = [
            [fd[i], float(fc_high[i]) - float(fc_low[i])]
            for i in range(len(fd))
        ]
        hist_pts = [[hd[i], float(hist_close[i])] for i in range(len(hd))]
        fc_pts = [[hd[-1], float(hist_close[-1])]]
        fc_pts.extend([[fd[i], float(fc_mean[i])] for i in range(len(fd))])

        series = [
            {"name": "95% 置信区间", "type": "line", "data": ci_low_pts,
             "stack": "ci",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "itemStyle": {"color": "transparent"}, "tooltip": {"show": False}},
            {"name": "95% 置信区间", "type": "line", "data": ci_diff_pts,
             "stack": "ci",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "areaStyle": {"color": COLOR_CI},
             "itemStyle": {"color": COLOR_CI}},
            {"name": "历史收盘", "type": "line", "data": hist_pts,
             "showSymbol": False, "smooth": True,
             "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
             "itemStyle": {"color": COLOR_CLOSE}},
            {"name": "ARIMA 预测", "type": "line", "data": fc_pts,
             "symbol": "circle", "symbolSize": 6, "showSymbol": True,
             "smooth": False,
             "lineStyle": {"width": 1.8, "color": COLOR_FORECAST,
                           "type": "dashed"},
             "itemStyle": {"color": COLOR_FORECAST}},
        ]
        x_axis: dict[str, Any] = {
            "type": "time",
            "boundaryGap": False, "scale": True,
            "axisLine": {"onZero": False},
            "min": "dataMin", "max": "dataMax",
        }
    else:
        all_dates = hd + fd
        n_hist, n_fc = len(hd), len(fd)

        hist_series = list(hist_close) + [None] * n_fc
        fc_series = [None] * (n_hist - 1) + [hist_close[-1]] + list(fc_mean)
        ci_low_arr = [None] * n_hist + list(fc_low)
        ci_diff_arr = ([None] * n_hist +
                       [h - l for h, l in zip(fc_high, fc_low)])

        series = [
            {"name": "95% 置信区间", "type": "line", "data": ci_low_arr,
             "stack": "ci",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "itemStyle": {"color": "transparent"}, "tooltip": {"show": False}},
            {"name": "95% 置信区间", "type": "line", "data": ci_diff_arr,
             "stack": "ci",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "areaStyle": {"color": COLOR_CI},
             "itemStyle": {"color": COLOR_CI}},
            {"name": "历史收盘", "type": "line", "data": hist_series,
             "showSymbol": False, "smooth": True,
             "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
             "itemStyle": {"color": COLOR_CLOSE}, "connectNulls": False},
            {"name": "ARIMA 预测", "type": "line", "data": fc_series,
             "symbol": "circle", "symbolSize": 6, "showSymbol": True,
             "smooth": False,
             "lineStyle": {"width": 1.8, "color": COLOR_FORECAST,
                           "type": "dashed"},
             "itemStyle": {"color": COLOR_FORECAST}, "connectNulls": False},
        ]
        x_axis = {
            "type": "category", "data": all_dates,
            "boundaryGap": False, "scale": True,
            "axisLine": {"onZero": False},
        }

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {"data": ["历史收盘", "ARIMA 预测", "95% 置信区间"],
                   "top": 30, "textStyle": {"fontSize": 12}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "grid": {"left": left_px, "right": 32, "top": 64, "bottom": 64},
        "xAxis": x_axis,
        "yAxis": {"type": "value", "scale": True,
                  "splitLine": {"lineStyle": {"opacity": 0.4}}},
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {"show": True, "type": "slider", "bottom": 16, "height": 18,
             "start": 0, "end": 100},
        ],
        "series": series,
    }


def build_boll_echart(dates, close, mid, upper, lower, ob_idx, os_idx, title):
    """收盘 + MA20 + ±2σ 上下轨 + 带区 + 超买/超卖散点。"""
    ds = [str(x) for x in dates]
    left_px = _grid_left_px_from_values(close, mid, upper, lower)
    use_time = dates_are_daily_strings(ds)

    diff_arr = [
        None if (u is None or l is None) else round(u - l, 4)
        for u, l in zip(upper, lower)
    ]
    ob_points = [[ds[i], close[i]] for i in ob_idx]
    os_points = [[ds[i], close[i]] for i in os_idx]

    if use_time:
        band_lo: list[list[Any]] = []
        band_hi: list[list[Any]] = []
        for i in range(len(ds)):
            u_raw, l_raw = upper[i], lower[i]
            if u_raw is None or l_raw is None:
                continue
            d_str = ds[i]
            lf = float(l_raw)
            band_lo.append([d_str, lf])
            band_hi.append([d_str, float(u_raw) - lf])

        upper_p = _pairs_date_value(ds, upper)
        mid_p = _pairs_date_value(ds, mid)
        lower_vis_p = _pairs_date_value(ds, lower)
        close_p = _pairs_date_value(ds, close)

        series = [
            {"name": "布林下轨", "type": "line", "data": band_lo,
             "stack": "boll",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "tooltip": {"show": False},
             "itemStyle": {"color": "transparent"}},
            {"name": "布林带", "type": "line", "data": band_hi,
             "stack": "boll",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "areaStyle": {"color": COLOR_BOLL_BAND},
             "itemStyle": {"color": COLOR_BOLL_BAND},
             "tooltip": {"show": False}},
            {"name": "上轨 +2σ", "type": "line", "data": upper_p,
             "showSymbol": False,
             "lineStyle": {"width": 1, "color": COLOR_BOLL_UP,
                           "type": "dashed"},
             "itemStyle": {"color": COLOR_BOLL_UP}},
            {"name": "中轨 MA20", "type": "line", "data": mid_p,
             "showSymbol": False,
             "lineStyle": {"width": 1, "color": COLOR_BOLL_MID},
             "itemStyle": {"color": COLOR_BOLL_MID}},
            {"name": "下轨 -2σ", "type": "line", "data": lower_vis_p,
             "showSymbol": False,
             "lineStyle": {"width": 1, "color": COLOR_BOLL_LOW,
                           "type": "dashed"},
             "itemStyle": {"color": COLOR_BOLL_LOW}},
            {"name": "收盘", "type": "line", "data": close_p,
             "showSymbol": False, "smooth": True,
             "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
             "itemStyle": {"color": COLOR_CLOSE}},
            {"name": "超买", "type": "scatter", "data": ob_points,
             "symbolSize": 10, "itemStyle": {"color": COLOR_UP}},
            {"name": "超卖", "type": "scatter", "data": os_points,
             "symbolSize": 10, "itemStyle": {"color": COLOR_DOWN}},
        ]
        xa: dict[str, Any] = {
            "type": "time",
            "boundaryGap": False, "scale": True,
            "axisLine": {"onZero": False},
            "min": "dataMin", "max": "dataMax",
        }
    else:
        series = [
            {"name": "布林下轨", "type": "line", "data": lower,
             "stack": "boll",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "tooltip": {"show": False},
             "itemStyle": {"color": "transparent"}},
            {"name": "布林带", "type": "line", "data": diff_arr,
             "stack": "boll",
             "lineStyle": {"opacity": 0}, "showSymbol": False,
             "areaStyle": {"color": COLOR_BOLL_BAND},
             "itemStyle": {"color": COLOR_BOLL_BAND},
             "tooltip": {"show": False}},
            {"name": "上轨 +2σ", "type": "line", "data": upper,
             "showSymbol": False,
             "lineStyle": {"width": 1, "color": COLOR_BOLL_UP,
                           "type": "dashed"},
             "itemStyle": {"color": COLOR_BOLL_UP}},
            {"name": "中轨 MA20", "type": "line", "data": mid,
             "showSymbol": False,
             "lineStyle": {"width": 1, "color": COLOR_BOLL_MID},
             "itemStyle": {"color": COLOR_BOLL_MID}},
            {"name": "下轨 -2σ", "type": "line", "data": lower,
             "showSymbol": False,
             "lineStyle": {"width": 1, "color": COLOR_BOLL_LOW,
                           "type": "dashed"},
             "itemStyle": {"color": COLOR_BOLL_LOW}},
            {"name": "收盘", "type": "line", "data": close,
             "showSymbol": False, "smooth": True,
             "lineStyle": {"width": 1.6, "color": COLOR_CLOSE},
             "itemStyle": {"color": COLOR_CLOSE}},
            {"name": "超买", "type": "scatter",
             "data": [[dates[i], close[i]] for i in ob_idx],
             "symbolSize": 10, "itemStyle": {"color": COLOR_UP}},
            {"name": "超卖", "type": "scatter",
             "data": [[dates[i], close[i]] for i in os_idx],
             "symbolSize": 10, "itemStyle": {"color": COLOR_DOWN}},
        ]
        xa = {
            "type": "category", "data": dates,
            "boundaryGap": False, "scale": True,
            "axisLine": {"onZero": False},
        }

    legend = ["收盘", "中轨 MA20", "上轨 +2σ", "下轨 -2σ", "超买", "超卖"]

    return {
        "animation": False,
        "title": {"text": title, "left": "center", "top": 6,
                  "textStyle": {"fontSize": 14}},
        "legend": {"data": legend, "top": 30, "textStyle": {"fontSize": 12}},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "cross"}},
        "grid": {"left": left_px, "right": 32, "top": 64, "bottom": 64},
        "xAxis": xa,
        "yAxis": {"type": "value", "scale": True,
                  "splitLine": {"lineStyle": {"opacity": 0.4}}},
        "dataZoom": [
            {"type": "inside", "start": 0, "end": 100},
            {"show": True, "type": "slider", "bottom": 16, "height": 18,
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
