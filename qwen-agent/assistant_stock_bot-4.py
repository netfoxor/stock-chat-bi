"""
股票查询助手 v4：在 v3 基础上增加布林带异常检测工具 boll_detection。

- 20 日移动均线 + 2 倍标准差上下轨；收盘价突破上轨记为超买，跌破下轨记为超卖
- 默认检测区间：截止今天的前约一年；可选 start_date / end_date（YYYY-MM-DD）
- 依赖：statsmodels（ARIMA）、pandas、matplotlib

环境变量：DASHSCOPE_API_KEY、TAVILY_API_KEY（可选）等。
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import dashscope
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from pandas.tseries.offsets import BDay
from qwen_agent.agents import Assistant
from qwen_agent.gui import WebUI
from qwen_agent.tools.base import BaseTool, register_tool
from sqlalchemy import create_engine, text
from statsmodels.tsa.arima.model import ARIMA

plt.rcParams["font.sans-serif"] = [
    "SimHei",
    "Microsoft YaHei",
    "SimSun",
    "Arial Unicode MS",
]
plt.rcParams["axes.unicode_minus"] = False

ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "stock_prices_history.db"

BAR_ROW_THRESHOLD = 20
PLOT_X_SAMPLE_POINTS = 10

# ARIMA 固定阶数（用户指定）
ARIMA_ORDER = (5, 1, 5)
# 至少需要约一年的交易日数据的一部分；阶数较高时样本过少易拟合失败
MIN_ARIMA_OBS = 80
MAX_FORECAST_DAYS = 60

# 布林带：N 日与 k 倍 σ（经典 20 + 2σ）
BOLL_WINDOW = 20
BOLL_STD_MULT = 2.0
MIN_BOLL_ROWS = 25  # 至少需略大于窗口才能产生有效轨线

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY", "")
dashscope.timeout = 30

system_prompt = """你是股票查询助手，可同时使用以下能力（按问题选用）：

1) **本地日线数据**：用 exc_sql 只读查询 SQLite 表 stock_daily。
2) **联网检索**：用 MCP 提供的 Tavily 工具获取新闻、政策等（非库内信息）。
3) **ARIMA 预测**：用 **arima_stock**，ARIMA(5,1,5) 预测未来 n 个交易日收盘（仅供学习，非投资建议）。
4) **布林带检测**：用 **boll_detection**，20 日 + 2σ 检测超买（收盘 > 上轨）、超卖（收盘 < 下轨）；默认近一年，可传 start_date、end_date。

**Tavily**：若 `<tool_response>` 中非空，须基于正文回答，禁止否认联网能力。

**boll_detection / arima_stock / exc_sql**：若工具返回含 markdown 表格与图片，须**原样输出**全部内容（含图片 markdown）。

## 表 stock_daily（exc_sql 仅 SELECT/WITH）
字段含 stock_name, ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount。

## 股票代码示例
- 贵州茅台 600519.SH；五粮液 000858.SZ；广发证券 000776.SZ；中芯国际 688981.SH

## SQL 要求
只读 SELECT 或 WITH SELECT；trade_date 为 YYYY-MM-DD 文本可比较。

## 联网（Tavily）
新闻、舆情、公告、政策等用 Tavily；日线统计用 exc_sql；预测用 arima_stock；超买超卖异常日用 boll_detection。
"""

_last_df_dict: dict[int, pd.DataFrame] = {}


def get_session_id(kwargs):
    messages = kwargs.get("messages")
    if messages is not None:
        return id(messages)
    return None


def _is_read_only_sql(sql: str) -> bool:
    s = sql.strip().lstrip("(").strip()
    u = s.upper()
    return u.startswith("SELECT") or u.startswith("WITH")


def _df_for_plot_line(df: pd.DataFrame, max_x_points: int) -> pd.DataFrame:
    n = len(df)
    if n <= max_x_points:
        return df.copy()
    idx = np.linspace(0, n - 1, max_x_points, dtype=int)
    idx = np.unique(idx)
    return df.iloc[idx].reset_index(drop=True)


def _safe_label(s: str) -> str:
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


@register_tool("exc_sql")
class ExcSQLTool(BaseTool):
    description = "执行只读 SQL 查询 stock_daily，并生成表格与图表（数据行数多时自动折线图）"
    parameters = [
        {
            "name": "sql_input",
            "type": "string",
            "description": "SQL 语句（仅 SELECT / WITH SELECT）",
            "required": True,
        }
    ]

    def call(self, params: str, **kwargs) -> str:
        session_id = get_session_id(kwargs)
        args = json.loads(params)
        sql_input = args["sql_input"]
        print("sql_input=", sql_input)

        if not DB_PATH.is_file():
            return f"错误：未找到数据库文件 {DB_PATH}，请先运行 fetch_stock_prices.py 或 import_xlsx_to_sqlite.py。"

        if not _is_read_only_sql(sql_input):
            return "错误：仅允许 SELECT 或 WITH ... SELECT 查询，请修改 SQL。"

        engine = create_engine(
            f"sqlite:///{DB_PATH.as_posix()}",
            connect_args={"check_same_thread": False},
        )
        try:
            df = pd.read_sql(text(sql_input), engine)
        except Exception as e:
            return f"SQL 执行失败: {e}"
        finally:
            engine.dispose()

        print("df=", df)

        if session_id is not None:
            _last_df_dict[session_id] = df

        if df.empty:
            return "查询结果为空（0 行）。"

        md = _build_result_markdown(df)
        if df.shape[1] < 2:
            return md

        n = len(df)
        use_line = n > BAR_ROW_THRESHOLD

        save_dir = ROOT_DIR / "image_show"
        save_dir.mkdir(parents=True, exist_ok=True)
        prefix = "stock_line" if use_line else "stock_bar"
        filename = f"{prefix}_{int(time.time() * 1000)}.png"
        save_path = save_dir / filename

        df_plot = _df_for_plot_line(df, PLOT_X_SAMPLE_POINTS) if use_line else df
        generate_chart_png(df_plot, str(save_path), use_line=use_line)

        img_path = os.path.join("image_show", filename)
        chart_name = "折线图" if use_line else "柱状图"
        note = ""
        if use_line and n > PLOT_X_SAMPLE_POINTS:
            note = f"\n\n*（折线图横轴从 {n} 条结果中均匀抽取 {len(df_plot)} 个点展示）*"
        img_md = f"![{chart_name}]({img_path})"
        return f"{md}\n\n{img_md}{note}"


def _load_year_history(ts_code: str) -> pd.DataFrame | None:
    """截止今天、向前约一年的日线，按 trade_date 升序。"""
    today = date.today()
    start = (today - timedelta(days=365)).isoformat()
    end = today.isoformat()
    engine = create_engine(
        f"sqlite:///{DB_PATH.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    try:
        q = text(
            """
            SELECT trade_date, close, stock_name
            FROM stock_daily
            WHERE ts_code = :code
              AND trade_date >= :start
              AND trade_date <= :end
            ORDER BY trade_date ASC
            """
        )
        df = pd.read_sql(q, engine, params={"code": ts_code, "start": start, "end": end})
    finally:
        engine.dispose()
    return df if not df.empty else None


def _load_stock_daily_range(ts_code: str, start: str, end: str) -> pd.DataFrame | None:
    """指定 [start, end] 区间日线，trade_date 为 YYYY-MM-DD 闭区间，升序。"""
    engine = create_engine(
        f"sqlite:///{DB_PATH.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    try:
        q = text(
            """
            SELECT trade_date, close, stock_name
            FROM stock_daily
            WHERE ts_code = :code
              AND trade_date >= :start
              AND trade_date <= :end
            ORDER BY trade_date ASC
            """
        )
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
    """解析 boll_detection 的日期范围；默认近一年到今天。"""
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
    upper = mid + BOLL_STD_MULT * std
    lower = mid - BOLL_STD_MULT * std
    return mid, upper, lower


def _plot_bollinger(
    dates: pd.DatetimeIndex,
    close: pd.Series,
    mid: pd.Series,
    upper: pd.Series,
    lower: pd.Series,
    ob_idx: np.ndarray,
    os_idx: np.ndarray,
    title: str,
    save_path: str,
) -> None:
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


@register_tool("boll_detection")
class BollDetectionTool(BaseTool):
    description = (
        f"基于本地 SQLite 日线，使用布林带（{BOLL_WINDOW} 日、{BOLL_STD_MULT}σ）"
        "检测超买（收盘>上轨）、超卖（收盘<下轨）。默认近一年；可传 start_date、end_date（YYYY-MM-DD）。"
    )
    parameters = [
        {
            "name": "ts_code",
            "type": "string",
            "description": "股票 Tushare 代码，如 600519.SH（必填）",
            "required": True,
        },
        {
            "name": "start_date",
            "type": "string",
            "description": "检测区间起始 YYYY-MM-DD，可选；缺省则与 end 搭配或默认近一年",
            "required": False,
        },
        {
            "name": "end_date",
            "type": "string",
            "description": "检测区间结束 YYYY-MM-DD，可选；缺省为今天；若仅填结束日则起点为结束日前一年",
            "required": False,
        },
    ]

    def call(self, params: str, **kwargs) -> str:
        session_id = get_session_id(kwargs)
        args = json.loads(params)
        ts_code = str(args.get("ts_code", "")).strip()
        if not ts_code:
            return "错误：ts_code 为必填。"

        dr = _parse_boll_date_range(args)
        if isinstance(dr, str):
            return dr
        start_iso, end_iso = dr

        if not DB_PATH.is_file():
            return f"错误：未找到数据库文件 {DB_PATH}。"

        df = _load_stock_daily_range(ts_code, start_iso, end_iso)
        if df is None:
            return (
                f"错误：在 {start_iso}～{end_iso} 内无 ts_code={ts_code} 的数据，请检查代码或区间。"
            )

        stock_name = str(df["stock_name"].iloc[-1])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) < MIN_BOLL_ROWS:
            return (
                f"错误：区间内有效样本仅 {len(df)} 条，布林带至少需要约 {MIN_BOLL_ROWS} 条。"
            )

        close = df["close"].astype(float)
        mid, upper, lower = _compute_bollinger(close)
        valid = upper.notna() & lower.notna()
        overbought = valid & (close > upper)
        oversold = valid & (close < lower)

        ob_df = df.loc[overbought, ["trade_date", "close"]].assign(
            signal="超买", boll_band=upper[overbought].values
        )
        os_df = df.loc[oversold, ["trade_date", "close"]].assign(
            signal="超卖", boll_band=lower[oversold].values
        )
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

        if session_id is not None:
            _last_df_dict[session_id] = out_df

        dt_index = pd.to_datetime(df["trade_date"])
        ob_idx = np.where(overbought.to_numpy())[0]
        os_idx = np.where(oversold.to_numpy())[0]

        summary = (
            f"**布林带检测**（{BOLL_WINDOW} 日 + {BOLL_STD_MULT}σ）\n"
            f"- 股票：{stock_name}（{ts_code}）\n"
            f"- 区间：**{start_iso}** ～ **{end_iso}**（共 {len(df)} 个交易日）\n"
            f"- **超买**（收盘 > 上轨）：**{int(overbought.sum())}** 日\n"
            f"- **超卖**（收盘 < 下轨）：**{int(oversold.sum())}** 日\n"
            f"- *仅供技术分析学习，不构成投资建议。*\n\n"
        )
        if out_df.empty:
            summary += "区间内未检测到超买/超卖触点。\n"
            tbl_md = ""
        else:
            summary += "**异常日明细：**\n"
            tbl_md = out_df.to_markdown(index=False)

        save_dir = ROOT_DIR / "image_show"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"boll_{ts_code.replace('.', '_')}_{int(time.time() * 1000)}.png"
        save_path = save_dir / filename
        _plot_bollinger(
            dt_index,
            close,
            mid,
            upper,
            lower,
            ob_idx,
            os_idx,
            f"{stock_name} ({ts_code}) 布林带与超买超卖",
            str(save_path),
        )
        img_md = f"![布林带检测]({os.path.join('image_show', filename)})"
        body = f"{summary}{tbl_md}\n\n{img_md}" if tbl_md else f"{summary}\n{img_md}"
        return body


def _plot_arima_forecast(
    hist_dates: pd.Series,
    hist_close: pd.Series,
    fc_dates: pd.DatetimeIndex,
    fc_mean: np.ndarray,
    fc_low: np.ndarray,
    fc_high: np.ndarray,
    title: str,
    save_path: str,
) -> None:
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(hist_dates, hist_close, label="历史收盘价", color="C0")
    ax.plot(fc_dates, fc_mean, label="ARIMA 预测", color="C1", marker="o", markersize=4)
    ax.fill_between(
        fc_dates, fc_low, fc_high, color="C1", alpha=0.2, label="95% 置信区间"
    )
    ax.set_title(_safe_label(title))
    ax.set_xlabel("日期")
    ax.set_ylabel("价格")
    ax.legend()
    ax.tick_params(axis="x", rotation=45)
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)


@register_tool("arima_stock")
class ArimaStockTool(BaseTool):
    description = (
        "对指定 ts_code 使用近一年历史收盘价拟合 ARIMA(5,1,5)，预测未来 n 个交易日收盘价；"
        "返回预测表与走势图（仅供学习，非投资建议）。"
    )
    parameters = [
        {
            "name": "ts_code",
            "type": "string",
            "description": "股票 Tushare 代码，如 600519.SH（必填）",
            "required": True,
        },
        {
            "name": "n",
            "type": "integer",
            "description": "向前预测的交易日数量（正整数，建议不超过 60）",
            "required": True,
        },
    ]

    def call(self, params: str, **kwargs) -> str:
        session_id = get_session_id(kwargs)
        args = json.loads(params)
        ts_code = str(args.get("ts_code", "")).strip()
        n_raw = args.get("n", 5)
        try:
            n = int(n_raw)
        except (TypeError, ValueError):
            return "错误：参数 n 必须为整数。"

        if not ts_code:
            return "错误：ts_code 为必填。"
        if n < 1 or n > MAX_FORECAST_DAYS:
            return f"错误：n 需在 1～{MAX_FORECAST_DAYS} 之间。"

        if not DB_PATH.is_file():
            return f"错误：未找到数据库文件 {DB_PATH}。"

        df = _load_year_history(ts_code)
        if df is None:
            return f"错误：未找到 ts_code={ts_code} 在近一年内的数据，请确认代码或先更新数据库。"

        stock_name = str(df["stock_name"].iloc[-1])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) < MIN_ARIMA_OBS:
            return (
                f"错误：有效收盘价样本仅 {len(df)} 条，ARIMA{ARIMA_ORDER} 至少需要约 {MIN_ARIMA_OBS} 条，"
                "请换更长区间数据或检查缺失。"
            )

        series = df["close"].astype(float)
        last_trade = pd.to_datetime(df["trade_date"].iloc[-1])
        future_dates = pd.bdate_range(start=last_trade + BDay(1), periods=n)

        try:
            model = ARIMA(series, order=ARIMA_ORDER)
            fitted = model.fit()
            fc = fitted.get_forecast(steps=n)
            pred = fc.predicted_mean.to_numpy()
            conf = fc.conf_int()
            low = conf.iloc[:, 0].to_numpy()
            high = conf.iloc[:, 1].to_numpy()
        except Exception as e:
            return f"ARIMA{ARIMA_ORDER} 拟合或预测失败（数据或数值问题）：{e}"

        fc_df = pd.DataFrame(
            {
                "forecast_date": future_dates.strftime("%Y-%m-%d"),
                "forecast_close": np.round(pred, 4),
                "ci_lower_95": np.round(low, 4),
                "ci_upper_95": np.round(high, 4),
            }
        )

        if session_id is not None:
            _last_df_dict[session_id] = fc_df

        summary = (
            f"**ARIMA{ARIMA_ORDER} 预测概况**\n"
            f"- 股票：{stock_name}（{ts_code}）\n"
            f"- 训练样本：{len(series)} 个交易日（约一年内至 {df['trade_date'].iloc[-1]}）\n"
            f"- 预测：未来 **{n}** 个交易日\n"
            f"- *免责声明：预测仅供学习参考，不构成投资建议。*\n\n"
            f"{fc_df.to_markdown(index=False)}"
        )

        save_dir = ROOT_DIR / "image_show"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"arima_{ts_code.replace('.', '_')}_{int(time.time() * 1000)}.png"
        save_path = save_dir / filename

        tail = min(120, len(df))
        _plot_arima_forecast(
            pd.to_datetime(df["trade_date"].iloc[-tail:]),
            series.iloc[-tail:],
            future_dates,
            pred,
            low,
            high,
            f"{stock_name} ({ts_code}) 收盘价与 ARIMA 预测",
            str(save_path),
        )
        img_md = f"![ARIMA预测]({os.path.join('image_show', filename)})"
        return f"{summary}\n\n{img_md}"


def generate_chart_png(df_sql: pd.DataFrame, save_path: str, *, use_line: bool) -> None:
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
                ax.plot(
                    x_pos,
                    pivot_df[col].to_numpy(),
                    marker="o",
                    markersize=4,
                    label=_safe_label(str(col)),
                )
            ax.set_xticks(x_pos)
            ax.set_xticklabels(
                [_safe_label(str(i)) for i in pivot_df.index], rotation=45, ha="right"
            )
        else:
            bottoms = None
            for col in pivot_df.columns:
                ax.bar(
                    pivot_df.index,
                    pivot_df[col],
                    bottom=bottoms,
                    label=_safe_label(str(col)),
                )
                if bottoms is None:
                    bottoms = pivot_df[col].copy()
                else:
                    bottoms += pivot_df[col]
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
    else:
        x = np.arange(len(df_sql))
        if use_line:
            for column in columns[1:]:
                ax.plot(
                    x,
                    df_sql[column].to_numpy(),
                    marker="o",
                    markersize=4,
                    label=_safe_label(str(column)),
                )
            ax.set_xticks(x)
            ax.set_xticklabels(
                [_safe_label(str(v)) for v in df_sql[columns[0]]], rotation=45, ha="right"
            )
        else:
            bottom = np.zeros(len(df_sql))
            for column in columns[1:]:
                ax.bar(x, df_sql[column], bottom=bottom, label=_safe_label(str(column)))
                bottom += df_sql[column]
            ax.set_xticks(x)
            ax.set_xticklabels(
                [_safe_label(str(v)) for v in df_sql[columns[0]]], rotation=45, ha="right"
            )

    ax.legend()
    ax.set_title("股票查询结果（折线图）" if use_line else "股票查询结果（柱状图）")
    ax.set_xlabel(_safe_label(str(columns[0])))
    ax.set_ylabel("数值")
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)


def build_tavily_mcp_config() -> dict | None:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return None
    return {
        "mcpServers": {
            "tavily": {
                "command": "npx",
                "args": ["-y", "tavily-mcp@latest"],
                "env": {"TAVILY_API_KEY": key},
            }
        }
    }


def build_function_list(mcp_cfg: dict | None = None) -> list:
    items: list = ["exc_sql", "arima_stock", "boll_detection"]
    cfg = mcp_cfg if mcp_cfg is not None else build_tavily_mcp_config()
    if cfg is not None:
        items.append(cfg)
    return items


def build_llm_cfg() -> dict:
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    base = os.getenv(
        "DASHSCOPE_OPENAI_BASE",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).strip()
    model = os.getenv("QWEN_AGENT_MODEL", "qwen3.6-plus").strip()
    return {
        "model": model,
        "model_server": base,
        "api_key": api_key or "EMPTY",
        "generate_cfg": {
            "max_retries": 3,
            "request_timeout": 120,
            "extra_body": {"enable_thinking": False},
        },
    }


def init_agent_service() -> Assistant:
    llm_cfg = build_llm_cfg()
    tavily_mcp = build_tavily_mcp_config()
    fn_list = build_function_list(tavily_mcp)
    if tavily_mcp is None:
        print("提示: 未设置 TAVILY_API_KEY，已跳过 Tavily MCP。")
    else:
        print("已启用 Tavily MCP（需本机 npx 可运行 tavily-mcp）。")
    print(
        "已注册工具: exc_sql, arima_stock, boll_detection"
        + (", Tavily MCP" if tavily_mcp else "")
    )
    bot = Assistant(
        llm=llm_cfg,
        name="股票查询助手",
        description="SQLite 查询、布林带检测、ARIMA 预测、Tavily 联网（可选）",
        system_message=system_prompt,
        function_list=fn_list,
        files=["faq.txt"],
    )
    print("股票查询助手 v4 初始化成功！")
    return bot


def app_tui() -> None:
    bot = init_agent_service()
    messages: list = []
    while True:
        try:
            query = input("user question: ")
            file = input("file url (press enter if no file): ").strip()
            if not query:
                print("user question cannot be empty！")
                continue
            if not file:
                messages.append({"role": "user", "content": query})
            else:
                messages.append(
                    {"role": "user", "content": [{"text": query}, {"file": file}]}
                )
            print("正在处理您的请求...")
            response: list = []
            for response in bot.run(messages):
                print("bot response:", response)
            messages.extend(response)
        except Exception as e:
            print(f"处理请求时出错: {e}")


def app_gui() -> None:
    print("正在启动 Web 界面...")
    bot = init_agent_service()
    chatbot_config = {
        "prompt.suggestions": [
            "检测五粮液近一年布林带超买超卖日期",
            "检测广发证券 2025-01-01 到 2025-06-30 的超买超卖",
            "用 ARIMA 预测贵州茅台未来 10 个交易日的收盘价",
        ],
    }
    print("Web 界面准备就绪，正在启动服务...")
    WebUI(bot, chatbot_config=chatbot_config).run()


if __name__ == "__main__":
    app_gui()
