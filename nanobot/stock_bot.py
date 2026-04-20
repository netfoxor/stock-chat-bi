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
BAR_ROW_THRESHOLD = 20
PLOT_X_SAMPLE_POINTS = 10
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


def _df_for_plot_line(df: pd.DataFrame, max_x_points: int) -> pd.DataFrame:
    n = len(df)
    if n <= max_x_points:
        return df.copy()
    idx = np.unique(np.linspace(0, n - 1, max_x_points, dtype=int))
    return df.iloc[idx].reset_index(drop=True)


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


def _generate_chart_png(df_sql: pd.DataFrame, save_path: str, *, use_line: bool) -> None:
    columns = df_sql.columns
    object_columns = df_sql.select_dtypes(include="O").columns.tolist()
    if columns[0] in object_columns:
        object_columns.remove(columns[0])
    num_columns = df_sql.select_dtypes(exclude="O").columns.tolist()

    fig, ax = plt.subplots(figsize=(10, 6))
    if len(object_columns) > 0:
        pivot_df = df_sql.pivot_table(
            index=columns[0], columns=object_columns, values=num_columns, fill_value=0
        )
        x_pos = np.arange(len(pivot_df))
        if use_line:
            for col in pivot_df.columns:
                ax.plot(x_pos, pivot_df[col].to_numpy(), marker="o", markersize=4,
                        label=_safe_label(col))
            ax.set_xticks(x_pos)
            ax.set_xticklabels([_safe_label(i) for i in pivot_df.index], rotation=45, ha="right")
        else:
            bottoms = None
            for col in pivot_df.columns:
                ax.bar(pivot_df.index, pivot_df[col], bottom=bottoms, label=_safe_label(col))
                bottoms = pivot_df[col].copy() if bottoms is None else bottoms + pivot_df[col]
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    else:
        x = np.arange(len(df_sql))
        if use_line:
            for column in columns[1:]:
                ax.plot(x, df_sql[column].to_numpy(), marker="o", markersize=4,
                        label=_safe_label(column))
            ax.set_xticks(x)
            ax.set_xticklabels([_safe_label(v) for v in df_sql[columns[0]]],
                               rotation=45, ha="right")
        else:
            bottom = np.zeros(len(df_sql))
            for column in columns[1:]:
                ax.bar(x, df_sql[column], bottom=bottom, label=_safe_label(column))
                bottom += df_sql[column]
            ax.set_xticks(x)
            ax.set_xticklabels([_safe_label(v) for v in df_sql[columns[0]]],
                               rotation=45, ha="right")

    ax.legend()
    ax.set_title("股票查询结果（折线图）" if use_line else "股票查询结果（柱状图）")
    ax.set_xlabel(_safe_label(columns[0]))
    ax.set_ylabel("数值")
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)


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

        n = len(df)
        use_line = n > BAR_ROW_THRESHOLD
        prefix = "stock_line" if use_line else "stock_bar"
        filename = f"{prefix}_{int(time.time() * 1000)}.png"
        save_path = IMAGE_DIR / filename

        df_plot = _df_for_plot_line(df, PLOT_X_SAMPLE_POINTS) if use_line else df
        await asyncio.to_thread(_generate_chart_png, df_plot, str(save_path), use_line=use_line)

        chart_name = "折线图" if use_line else "柱状图"
        note = ""
        if use_line and n > PLOT_X_SAMPLE_POINTS:
            note = f"\n\n*（折线图横轴从 {n} 条结果中均匀抽取 {len(df_plot)} 个点展示）*"
        img_md = f"![{chart_name}](image_show/{filename})"
        return f"{md}\n\n{img_md}{note}"


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
