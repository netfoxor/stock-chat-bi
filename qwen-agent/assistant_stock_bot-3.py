"""
股票查询助手 v3：在 assistant_stock_bot-2 基础上增加 ARIMA 收盘价预测工具 arima_stock。

- 从本地 SQLite 读取指定 ts_code、截止今天之前约一年的日线收盘价
- ARIMA(5,1,5) 建模，预测未来 n 个交易日收盘价
- 依赖：statsmodels（见 requirements.txt）

环境变量与 v2 相同：DASHSCOPE_API_KEY、TAVILY_API_KEY（可选）等。
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

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY", "")
dashscope.timeout = 30

system_prompt = """你是股票查询助手，可同时使用以下能力（按问题选用）：

1) **本地日线数据**：用 exc_sql 只读查询 SQLite 表 stock_daily。
2) **联网检索**：用 MCP 提供的 Tavily 工具获取新闻、政策等（非库内信息）。
3) **ARIMA 预测**：用 **arima_stock** 工具，根据近一年历史收盘价对未来 n 个交易日做 ARIMA(5,1,5) 预测（仅供学习参考，不构成投资建议）。

**Tavily**：若 `<tool_response>` 中非空，须基于正文回答，禁止否认联网能力。

**arima_stock / exc_sql**：若工具返回含 markdown 表格与图片，须**原样输出**全部内容（含图片 markdown）。

## 表 stock_daily（exc_sql 仅 SELECT/WITH）
字段含 stock_name, ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount。

## 股票代码示例
- 贵州茅台 600519.SH；五粮液 000858.SZ；广发证券 000776.SZ；中芯国际 688981.SH

## SQL 要求
只读 SELECT 或 WITH SELECT；trade_date 为 YYYY-MM-DD 文本可比较。

## 联网（Tavily）
新闻、舆情、公告、政策等用 Tavily；日线统计用 exc_sql；价格走势预测用 arima_stock。
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
    items: list = ["exc_sql", "arima_stock"]
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
    print("已注册工具: exc_sql, arima_stock" + (", Tavily MCP" if tavily_mcp else ""))
    bot = Assistant(
        llm=llm_cfg,
        name="股票查询助手",
        description="SQLite 日线查询、ARIMA 预测、联网检索（Tavily MCP 可选）",
        system_message=system_prompt,
        function_list=fn_list,
        files=["faq.txt"],
    )
    print("股票查询助手 v3 初始化成功！")
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
            "用 ARIMA 预测贵州茅台未来 10 个交易日的收盘价",
            "查询2025年全年五粮液的收盘价走势",
            "搜索最近贵州茅台相关新闻",
        ],
    }
    print("Web 界面准备就绪，正在启动服务...")
    WebUI(bot, chatbot_config=chatbot_config).run()


if __name__ == "__main__":
    app_gui()
