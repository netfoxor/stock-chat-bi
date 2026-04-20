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

import matplotlib
matplotlib.use("Agg")  # 后台渲染，避免与前端事件循环冲突
import matplotlib.pyplot as plt
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
# 图片保存到 nanobot workspace 下的 image_show/，方便前端服务静态文件
IMAGE_DIR = WORKSPACE / "image_show"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

plt.rcParams["font.sans-serif"] = [
    "SimHei", "Microsoft YaHei", "SimSun", "Arial Unicode MS",
]
plt.rcParams["axes.unicode_minus"] = False

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
# 智能绘图
#
# 设计思路：
#   - 识别日期列（trade_date/date/datetime）为 x 轴
#   - 剔除整列同值的 object 列（ts_code/stock_name 这种重复值，不参与绘图）
#   - 如果同时包含 OHLC 至少两列 → 金融模式：K 线 / 价格折线 + 成交量副图
#   - 否则按"量纲族"分组到多个子图，避免 amount(10⁶) 压扁 pct_chg(<10%)
# --------------------------------------------------------------------------- #

# 列语义识别（不区分大小写）
_PRICE_COLS = {"open", "high", "low", "close", "pre_close"}
_VOLUME_COLS = {"vol", "volume"}
_AMOUNT_COLS = {"amount", "turnover"}
_PCT_COLS = {"pct_chg", "pct_change", "change_pct", "pctchg"}
_CHANGE_COLS = {"change", "chg"}
_DATE_COLS = {"trade_date", "date", "datetime", "dt", "day"}


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


def _plot_candlestick(ax, x, o, h, low, c, *, width: float = 0.6) -> None:
    """朴素 K 线图（不依赖 mplfinance）：红涨绿跌。"""
    up = c >= o
    colors_body = np.where(up, "#d9534f", "#5cb85c")  # 红涨绿跌（A 股风格）
    colors_wick = colors_body
    # 影线
    ax.vlines(x, low, h, colors=colors_wick, linewidth=0.8, zorder=2)
    # 实体
    body_bottom = np.minimum(o, c)
    body_height = np.abs(c - o)
    # height 为 0 时用极小值避免不可见
    body_height = np.where(body_height < 1e-9, (h - low) * 0.02 + 1e-6, body_height)
    ax.bar(x, body_height, bottom=body_bottom, width=width,
           color=colors_body, edgecolor=colors_body, zorder=3, align="center")


def _plot_stock_auto(df_sql: pd.DataFrame, save_path: str, *, max_rows: int = 260) -> str:
    """
    智能绘图入口。返回用于 markdown 说明的"图表类型"文案。
    约定：对 DataFrame 做副本，不改动调用方传入的对象。
    """
    df = df_sql.copy()
    date_col = _detect_date_col(df)
    df = _drop_constant_object_cols(df, exclude={date_col} if date_col else set())

    # 确定 x 轴
    if date_col is not None:
        try:
            df[date_col] = pd.to_datetime(df[date_col])
            df = df.sort_values(date_col)
        except Exception:
            pass
        x_values = df[date_col]
        x_label = "日期"
    else:
        x_values = pd.Series(np.arange(len(df)))
        x_label = str(df.columns[0])

    # 数据量过大时降采样，保证图像清晰
    if len(df) > max_rows:
        idx = np.unique(np.linspace(0, len(df) - 1, max_rows, dtype=int))
        df = df.iloc[idx].reset_index(drop=True)
        x_values = x_values.iloc[idx].reset_index(drop=True) \
            if hasattr(x_values, "iloc") else x_values[idx]

    num_cols = [c for c in df.columns
                if c != date_col and pd.api.types.is_numeric_dtype(df[c])]
    if not num_cols:
        # 没有数值列可画，画一个空图占位
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, "无可绘数值列", ha="center", va="center")
        ax.set_axis_off()
        fig.savefig(save_path, bbox_inches="tight")
        plt.close(fig)
        return "占位图"

    groups = _group_numeric_cols(num_cols)

    # ------------------- 金融模式：OHLC + 量价 ------------------- #
    has_ohlc = {"open", "high", "low", "close"}.issubset(
        {str(c).lower() for c in groups.get("price", [])}
    )
    has_price_line = len(groups.get("price", [])) >= 1

    if date_col is not None and has_price_line:
        # 确定副图：volume / amount / pct / change（最多 3 个副图）
        sub_keys = [k for k in ("volume", "amount", "pct", "change") if k in groups][:3]
        n_axes = 1 + len(sub_keys)
        heights = [3.0] + [1.0] * len(sub_keys)
        fig, axes = plt.subplots(
            n_axes, 1, figsize=(12, 2.2 * sum(heights) / max(heights)),
            sharex=True, gridspec_kw={"height_ratios": heights},
        )
        if n_axes == 1:
            axes = [axes]
        ax_price = axes[0]

        # 价格主图：OHLC → K 线；否则画收盘 + 可选高低区间填充
        if has_ohlc:
            lowers = {str(c).lower(): c for c in groups["price"]}
            o = df[lowers["open"]].to_numpy(float)
            h = df[lowers["high"]].to_numpy(float)
            low_v = df[lowers["low"]].to_numpy(float)
            c_v = df[lowers["close"]].to_numpy(float)
            # x 轴：pandas DatetimeIndex 可直接传给 matplotlib
            x_arr = x_values.to_numpy() if hasattr(x_values, "to_numpy") else np.asarray(x_values)
            # 日级时 width 用天数为单位
            width = 0.7
            _plot_candlestick(ax_price, x_arr, o, h, low_v, c_v, width=width)
            ax_price.set_title("K 线图（红涨绿跌）")
        else:
            # 退化：只有 close 之类 → 折线
            price_cols = groups["price"]
            for col in price_cols:
                ax_price.plot(x_values, df[col], label=_safe_label(col), linewidth=1.2)
            if {"high", "low"}.issubset({str(c).lower() for c in price_cols}):
                lowers = {str(c).lower(): c for c in price_cols}
                ax_price.fill_between(x_values, df[lowers["low"]], df[lowers["high"]],
                                      color="C0", alpha=0.08, label="high-low 区间")
            ax_price.set_title("价格走势")
            ax_price.legend(loc="upper left", fontsize=8)
        ax_price.set_ylabel("价格")
        ax_price.grid(True, alpha=0.25)

        # 副图：成交量 / 成交额 / 涨跌幅 / 涨跌额 —— 柱状图，涨跌配色
        close_col = None
        pre_close_col = None
        for c in groups["price"]:
            lc = str(c).lower()
            if lc == "close":
                close_col = c
            elif lc == "pre_close":
                pre_close_col = c

        if close_col is not None:
            if pre_close_col is not None:
                up_mask = df[close_col].to_numpy() >= df[pre_close_col].to_numpy()
            else:
                # 用相邻收盘价判断涨跌
                diff = df[close_col].diff().fillna(0).to_numpy()
                up_mask = diff >= 0
            bar_colors = np.where(up_mask, "#d9534f", "#5cb85c")
        else:
            bar_colors = "#888"

        sub_titles = {
            "volume": "成交量", "amount": "成交额",
            "pct": "涨跌幅（%）", "change": "涨跌额",
        }
        for i, key in enumerate(sub_keys, start=1):
            ax = axes[i]
            col = groups[key][0]  # 取第一列即可（同族通常就一个）
            ax.bar(x_values, df[col].to_numpy(), color=bar_colors, width=0.7, align="center")
            ax.set_ylabel(sub_titles[key])
            ax.grid(True, alpha=0.25)

        axes[-1].set_xlabel(x_label)
        fig.autofmt_xdate()
        fig.suptitle(f"股票日线图（{len(df)} 个交易日）", y=0.995)
        fig.tight_layout()
        fig.savefig(save_path, dpi=110)
        plt.close(fig)

        base = "K 线图" if has_ohlc else "价格走势"
        sub_titles = {"volume": "成交量", "amount": "成交额",
                      "pct": "涨跌幅", "change": "涨跌额"}
        if sub_keys:
            return f"{base} + " + "/".join(sub_titles[k] for k in sub_keys)
        return base

    # ------------------- 通用模式：按量纲族分子图 ------------------- #
    # 每个分组一个子图，避免量纲混画
    keys = list(groups.keys())
    n_axes = len(keys)
    fig, axes = plt.subplots(n_axes, 1, figsize=(12, 2.6 * n_axes), sharex=True)
    if n_axes == 1:
        axes = [axes]

    labels_map = {"price": "价格", "volume": "成交量", "amount": "成交额",
                  "pct": "涨跌幅", "change": "涨跌额", "other": "数值"}
    for ax, key in zip(axes, keys):
        for col in groups[key]:
            if date_col is not None:
                ax.plot(x_values, df[col], label=_safe_label(col), linewidth=1.2, marker="o",
                        markersize=3)
            else:
                ax.bar(x_values, df[col], label=_safe_label(col))
        ax.set_ylabel(labels_map.get(key, key))
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, alpha=0.25)

    axes[-1].set_xlabel(x_label)
    if date_col is not None:
        fig.autofmt_xdate()
    fig.suptitle("查询结果（按量纲分组）", y=0.995)
    fig.tight_layout()
    fig.savefig(save_path, dpi=110)
    plt.close(fig)
    return "多子图折线"


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


def _plot_bollinger(dates, close, mid, upper, lower, ob_idx, os_idx, title, save_path) -> None:
    c = close.to_numpy()
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(dates, c, label="收盘", color="C0", linewidth=1.2)
    ax.plot(dates, mid, label=f"{BOLL_WINDOW} 日均线", color="gray", linewidth=0.9, alpha=0.8)
    ax.plot(dates, upper, label="上轨 (+2σ)", color="C3", linewidth=0.9, linestyle="--")
    ax.plot(dates, lower, label="下轨 (-2σ)", color="C2", linewidth=0.9, linestyle="--")
    ax.fill_between(dates, lower, upper, color="gray", alpha=0.08)
    if len(ob_idx):
        ax.scatter(dates[ob_idx], c[ob_idx], color="C3", s=36, zorder=5, label="超买")
    if len(os_idx):
        ax.scatter(dates[os_idx], c[os_idx], color="C2", s=36, zorder=5, label="超卖")
    ax.set_title(_safe_label(title))
    ax.set_xlabel("日期")
    ax.set_ylabel("价格")
    ax.legend(loc="upper left", fontsize=8)
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)


def _plot_arima_forecast(hist_dates, hist_close, fc_dates, fc_mean, fc_low, fc_high,
                         title, save_path) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(hist_dates, hist_close, label="历史收盘价", color="C0")
    ax.plot(fc_dates, fc_mean, label="ARIMA 预测", color="C1", marker="o", markersize=4)
    ax.fill_between(fc_dates, fc_low, fc_high, color="C1", alpha=0.2, label="95% 置信区间")
    ax.set_title(_safe_label(title))
    ax.set_xlabel("日期")
    ax.set_ylabel("价格")
    ax.legend()
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)


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
            "自动生成 markdown 表格、数值描述与柱状/折线图，返回图像以 markdown 嵌入。"
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

        filename = f"stock_chart_{int(time.time() * 1000)}.png"
        save_path = IMAGE_DIR / filename

        try:
            chart_name = await asyncio.to_thread(_plot_stock_auto, df, str(save_path))
        except Exception as e:
            # 绘图失败不影响数据返回
            return f"{md}\n\n*（绘图失败：{e}）*"

        img_md = f"![{chart_name}](image_show/{filename})"
        return f"{md}\n\n{img_md}"


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

        dt_index = pd.to_datetime(df["trade_date"])
        ob_idx = np.where(overbought.to_numpy())[0]
        os_idx = np.where(oversold.to_numpy())[0]

        filename = f"boll_{ts_code.replace('.', '_')}_{int(time.time() * 1000)}.png"
        save_path = IMAGE_DIR / filename
        await asyncio.to_thread(
            _plot_bollinger, dt_index, close, mid, upper, lower, ob_idx, os_idx,
            f"{stock_name} ({ts_code}) 布林带与超买超卖", str(save_path),
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
        img_md = f"![布林带检测](image_show/{filename})"
        return f"{summary}{tbl_md}\n\n{img_md}" if tbl_md else f"{summary}\n{img_md}"


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

        filename = f"arima_{ts_code.replace('.', '_')}_{int(time.time() * 1000)}.png"
        save_path = IMAGE_DIR / filename
        tail = min(120, len(df))
        await asyncio.to_thread(
            _plot_arima_forecast,
            pd.to_datetime(df["trade_date"].iloc[-tail:]),
            series.iloc[-tail:],
            future_dates, pred, low, high,
            f"{stock_name} ({ts_code}) 收盘价与 ARIMA 预测",
            str(save_path),
        )

        summary = (
            f"**ARIMA{ARIMA_ORDER} 预测概况**\n"
            f"- 股票：{stock_name}（{ts_code}）\n"
            f"- 训练样本：{len(series)} 个交易日（约一年内至 {df['trade_date'].iloc[-1]}）\n"
            f"- 预测：未来 **{n}** 个交易日\n"
            f"- *免责声明：预测仅供学习参考，不构成投资建议。*\n\n"
            f"{fc_df.to_markdown(index=False)}"
        )
        img_md = f"![ARIMA预测](image_show/{filename})"
        return f"{summary}\n\n{img_md}"


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
