"""
股票查询助手（增强可视化）：基于 SQLite 与 qwen-agent。
相对 assistant_stock_bot.py：行数较多时自动用折线图；横轴均匀抽样展示（默认最多 10 个点）。

LLM：Qwen3.6-Plus 等模型请走 DashScope **OpenAI 兼容**接口，避免原生 Generation 报
InvalidParameter「url error」。环境变量：
- DASHSCOPE_API_KEY：必填
- QWEN_AGENT_MODEL：默认 qwen3.6-plus，可改为 qwen3.6-plus-2026-04-02 等
- DASHSCOPE_OPENAI_BASE：默认 https://dashscope.aliyuncs.com/compatible-mode/v1
  国际站可设为 https://dashscope-intl.aliyuncs.com/compatible-mode/v1

默认已通过 API 关闭思考链（enable_thinking=false）；若需开启，可自行改 build_llm_cfg。

Tavily MCP（联网搜索）：需环境变量 TAVILY_API_KEY，且本机已安装 Node.js（npx 可用）。
未设置 TAVILY_API_KEY 时不会加载 MCP。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import dashscope
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from qwen_agent.agents import Assistant
from qwen_agent.gui import WebUI
from qwen_agent.tools.base import BaseTool, register_tool
from sqlalchemy import create_engine, text

plt.rcParams["font.sans-serif"] = [
    "SimHei",
    "Microsoft YaHei",
    "SimSun",
    "Arial Unicode MS",
]
plt.rcParams["axes.unicode_minus"] = False

ROOT_DIR = Path(__file__).resolve().parent
DB_PATH = ROOT_DIR / "stock_prices_history.db"

# 行数大于该阈值时用折线图；否则用柱状图（与原逻辑一致）
BAR_ROW_THRESHOLD = 20
# 折线图横轴最多展示的点数（均匀抽样，含首尾）
PLOT_X_SAMPLE_POINTS = 10

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY", "")
dashscope.timeout = 30

system_prompt = """你是股票查询助手，可同时使用两类能力回答用户（按问题选用，不要混用错误路径）：

1) **本地日线数据**：用 exc_sql 只读查询 SQLite 表 stock_daily（K 线、成交量、涨跌幅统计等）。
2) **联网检索**：用 MCP 提供的 Tavily 工具（如 tavily-search / tavily-extract）获取新闻、公告、政策、实时资讯等。

**重要**：若你已调用 Tavily 且 `<tool_response>` 里出现了检索正文（非空），你必须**基于该正文**向用户做摘要与引用，**禁止**再写「无法访问实时新闻」「不能直接联网」等否认能力的话；工具返回即代表已获取到可引用的检索结果。若工具返回报错或为空，再如实说明并给出建议（检查关键词或改查本地库）。

## 表结构（仅查询此表）
CREATE TABLE stock_daily (
    stock_name TEXT NOT NULL,   -- 股票中文简称
    ts_code    TEXT NOT NULL,   -- Tushare 代码
    trade_date TEXT NOT NULL,   -- 交易日，格式 YYYY-MM-DD
    open       REAL,            -- 开盘价
    high       REAL,            -- 最高价
    low        REAL,            -- 最低价
    close      REAL,            -- 收盘价
    pre_close  REAL,            -- 昨收
    change     REAL,            -- 涨跌额
    pct_chg    REAL,            -- 涨跌幅(%)
    vol        REAL,            -- 成交量
    amount     REAL,            -- 成交额
    PRIMARY KEY (ts_code, trade_date)
);

## 当前数据中的股票（可用 stock_name 或 ts_code 过滤）
- 贵州茅台  600519.SH
- 五粮液    000858.SZ
- 广发证券  000776.SZ
- 中芯国际  688981.SH

## SQL 要求
- 只生成 **SELECT** 或 **WITH ... SELECT**（只读），不要 INSERT/UPDATE/DELETE/DROP/PRAGMA 等。
- trade_date 为文本日期，可直接用字符串比较，如 `WHERE trade_date >= '2024-01-01'`。
- 需要按日期排序时：`ORDER BY trade_date` 或 `ORDER BY trade_date ASC`。

每当 exc_sql 工具返回 markdown 表格和图片时，你必须**原样输出**工具返回的全部内容（含图片 markdown），不要只文字总结，也不要省略图片。

## 联网（Tavily MCP）
当用户需要**新闻、舆情、公告、政策、宏观、行业动态**等不在 stock_daily 中的信息时，应调用 Tavily；**不要**用 exc_sql 硬答新闻类问题。日线与图表仍以 exc_sql 为准。
"""

functions_desc = [
    {
        "name": "exc_sql",
        "description": "对 stock_daily 执行只读 SQL 并返回结果与图表（行数多时为折线图）",
        "parameters": {
            "type": "object",
            "properties": {
                "sql_input": {
                    "type": "string",
                    "description": "仅包含 SELECT 或 WITH 的 SQL 语句",
                }
            },
            "required": ["sql_input"],
        },
    },
]

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
    """折线图横轴抽样：均匀取点，必含首尾。"""
    n = len(df)
    if n <= max_x_points:
        return df.copy()
    idx = np.linspace(0, n - 1, max_x_points, dtype=int)
    idx = np.unique(idx)
    return df.iloc[idx].reset_index(drop=True)


def _safe_label(s: str) -> str:
    return str(s).replace("%", "%%").replace("{", "{{").replace("}", "}}")


def _build_result_markdown(df: pd.DataFrame) -> str:
    """前 5 行 + 后 5 行（行数少时不重复展示）+ 描述统计，供模型综合理解结果。"""
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
    """Tavily 官方 MCP（stdio）：https://docs.tavily.com/documentation/mcp"""
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
    """exc_sql + 可选 Tavily MCP。"""
    items: list = ["exc_sql"]
    cfg = mcp_cfg if mcp_cfg is not None else build_tavily_mcp_config()
    if cfg is not None:
        items.append(cfg)
    return items


def build_llm_cfg() -> dict:
    """使用 compatible-mode/v1（OpenAI 协议），与 qwen-agent 的 oai 后端对接 DashScope。"""
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
            # DashScope OpenAI 兼容接口：关闭 Qwen3 系思考链，避免流式里先出 reasoning
            "extra_body": {"enable_thinking": False},
        },
    }


def init_agent_service() -> Assistant:
    llm_cfg = build_llm_cfg()
    tavily_mcp = build_tavily_mcp_config()
    fn_list = build_function_list(tavily_mcp)
    if tavily_mcp is None:
        print("提示: 未设置 TAVILY_API_KEY，已跳过 Tavily MCP（仅 exc_sql）。")
    else:
        print("已启用 Tavily MCP（需本机 npx 可运行 tavily-mcp）。")
    bot = Assistant(
        llm=llm_cfg,
        name="股票查询助手",
        description="基于 SQLite 日线数据的股票问答与可视化（大行数折线+横轴抽样）",
        system_message=system_prompt,
        function_list=fn_list,
        files=["faq.txt"],
    )
    print("股票查询助手初始化成功！")
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
            "查询2025年全年贵州茅台的收盘价走势",
            "统计2025年4月广发证券的日均成交量",
            "对比2025年中芯国际和贵州茅台的涨跌幅",
        ],
    }
    print("Web 界面准备就绪，正在启动服务...")
    WebUI(bot, chatbot_config=chatbot_config).run()


if __name__ == "__main__":
    app_gui()
