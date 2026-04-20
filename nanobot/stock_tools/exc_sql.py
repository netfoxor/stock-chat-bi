# -*- coding: utf-8 -*-
"""exc_sql —— 在 SQLite 上执行只读 SQL，返回 markdown + ECharts 图表。"""

from __future__ import annotations

import asyncio
from typing import Any

from nanobot.agent.tools.base import Tool

import stock_core as core


class ExcSQLTool(Tool):
    """在本地 SQLite 上执行只读 SQL 并智能出图（K 线 / 折线 / 多子图）。"""

    @property
    def name(self) -> str:
        return "exc_sql"

    @property
    def description(self) -> str:
        return (
            "在本地 SQLite 的 stock_daily 表上执行只读 SQL 查询（仅 SELECT / WITH SELECT）；"
            "自动生成 markdown 表格、数值描述与交互式 ECharts 图表（K 线 / 折线 / 量价副图自动识别）。"
            "图表以 ![label](chart:charts/xxx.json) markdown 占位返回，由前端渲染。"
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
        if not core.DB_PATH.is_file():
            return f"错误：未找到数据库文件 {core.DB_PATH}。"
        if not core.is_read_only_sql(sql_input):
            return "错误：仅允许 SELECT 或 WITH ... SELECT 查询。"

        try:
            df = await asyncio.to_thread(core.run_query, sql_input)
        except Exception as e:
            return f"SQL 执行失败: {e}"

        if df.empty:
            return "查询结果为空（0 行）。"

        md = core.build_result_markdown(df)
        if df.shape[1] < 2:
            return md

        try:
            option, label = await asyncio.to_thread(core.build_stock_echart, df)
        except Exception as e:
            return f"{md}\n\n*（绘图失败：{e}）*"

        chart_md = core.save_echart_option(option, prefix="sql", label=label)
        return f"{md}\n\n{chart_md}"


def build_tool() -> Tool:
    return ExcSQLTool()
