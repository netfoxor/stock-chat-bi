"""
股票查询助手：基于本地 SQLite（stock_prices_history.db）与 qwen-agent。
参考 assistant_ticket_bot-3.py 结构。需环境变量 DASHSCOPE_API_KEY。
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

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY", "")
dashscope.timeout = 30

system_prompt = """你是股票查询助手，用户问题需通过 SQL 在本地 SQLite 表 stock_daily 中查询后回答。

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
"""

functions_desc = [
    {
        "name": "exc_sql",
        "description": "对 stock_daily 执行只读 SQL 并返回结果与图表",
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


@register_tool("exc_sql")
class ExcSQLTool(BaseTool):
    description = "执行只读 SQL 查询 stock_daily，并生成表格与柱状图"
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

        md = df.head(50).to_markdown(index=False)
        if df.shape[1] < 2:
            return md

        save_dir = ROOT_DIR / "image_show"
        save_dir.mkdir(parents=True, exist_ok=True)
        filename = f"stock_bar_{int(time.time() * 1000)}.png"
        save_path = save_dir / filename
        generate_chart_png(df, str(save_path))
        img_path = os.path.join("image_show", filename)
        img_md = f"![柱状图]({img_path})"
        return f"{md}\n\n{img_md}"


def generate_chart_png(df_sql: pd.DataFrame, save_path: str) -> None:
    columns = df_sql.columns
    x = np.arange(len(df_sql))
    object_columns = df_sql.select_dtypes(include="O").columns.tolist()
    if columns[0] in object_columns:
        object_columns.remove(columns[0])
    num_columns = df_sql.select_dtypes(exclude="O").columns.tolist()
    if len(object_columns) > 0:
        pivot_df = df_sql.pivot_table(
            index=columns[0], columns=object_columns, values=num_columns, fill_value=0
        )
        fig, ax = plt.subplots(figsize=(10, 6))
        bottoms = None
        for col in pivot_df.columns:
            label_str = str(col)
            safe_label = label_str.replace("%", "%%").replace("{", "{{").replace("}", "}}")
            ax.bar(pivot_df.index, pivot_df[col], bottom=bottoms, label=safe_label)
            if bottoms is None:
                bottoms = pivot_df[col].copy()
            else:
                bottoms += pivot_df[col]
    else:
        bottom = np.zeros(len(df_sql))
        for column in columns[1:]:
            label_str = str(column)
            safe_label = label_str.replace("%", "%%").replace("{", "{{").replace("}", "}}")
            plt.bar(x, df_sql[column], bottom=bottom, label=safe_label)
            bottom += df_sql[column]
        safe_xtick_labels = []
        for val in df_sql[columns[0]]:
            val_str = str(val)
            safe_val = val_str.replace("%", "%%").replace("{", "{{").replace("}", "}}")
            safe_xtick_labels.append(safe_val)
        plt.xticks(x, safe_xtick_labels)
    plt.legend()
    plt.title("股票查询结果")
    xlabel_str = str(columns[0])
    safe_xlabel = xlabel_str.replace("%", "%%").replace("{", "{{").replace("}", "}}")
    plt.xlabel(safe_xlabel)
    plt.ylabel("数值")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.savefig(save_path)
    plt.close()


def init_agent_service() -> Assistant:
    llm_cfg = {
        "model": "qwen-turbo",
        "timeout": 30,
        "retry_count": 3,
    }
    bot = Assistant(
        llm=llm_cfg,
        name="股票查询助手",
        description="基于 SQLite 日线数据的股票问答与简单可视化",
        system_message=system_prompt,
        function_list=["exc_sql", "code_interpreter"],
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
            "对比2025年中芯国际和贵州茅台的涨跌幅"
        ],
    }
    print("Web 界面准备就绪，正在启动服务...")
    WebUI(bot, chatbot_config=chatbot_config).run()


if __name__ == "__main__":
    app_gui()
