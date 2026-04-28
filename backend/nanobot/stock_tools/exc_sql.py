# -*- coding: utf-8 -*-
"""exc_sql —— 在 MySQL 上执行只读 SQL，返回 markdown + ECharts 图表。"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from nanobot.agent.tools.base import Tool

import stock_core as core


# 识别"挨着 trade_date 出现的 yyyymmdd 无分隔符日期字面量"（Tushare 旧格式，本库不吃）
# 只要 trade_date 所在的 ~80 字符窗口里出现形如 '20250101' / "20250101" 的 8 位数字串，就报错
_BAD_DATE_WINDOW_RE = re.compile(
    r"trade_date[^;\n]{0,80}['\"](\d{8})['\"]",
    re.IGNORECASE,
)
# 任何 SQL 字面里的 8 位纯数字日期（兜底，比窗口规则更宽）
_ANY_YYYYMMDD_LITERAL_RE = re.compile(r"['\"](20\d{6}|19\d{6})['\"]")
_SQLITE_DATE_NOW_RE = re.compile(r"date\s*\(\s*['\"]now['\"]\s*,", re.IGNORECASE)


class ExcSQLTool(Tool):
    """在 MySQL 上执行只读 SQL 并智能出图（K 线 / 折线 / 多子图）。"""

    @property
    def name(self) -> str:
        return "exc_sql"

    @property
    def description(self) -> str:
        return (
            "在 MySQL 的 stock_daily 表上执行只读 SQL 查询（仅 SELECT / WITH SELECT）；"
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
        if not core.is_read_only_sql(sql_input):
            return "错误：仅允许 SELECT 或 WITH ... SELECT 查询。"

        # 预检：LLM 常犯错——把 trade_date 当成 Tushare 的 yyyymmdd 格式
        if issue := _check_sql_pitfalls(sql_input):
            return issue

        try:
            df = await asyncio.to_thread(core.run_query, sql_input)
        except Exception as e:
            return f"SQL 执行失败: {e}"

        if df.empty:
            # 空结果不是"成功"，自动做一次常见原因诊断
            return _empty_result_diagnosis(sql_input)

        md = core.build_result_markdown(df)
        if df.shape[1] < 2:
            return md

        try:
            option, label = await asyncio.to_thread(core.build_stock_echart, df)
        except Exception as e:
            return f"{md}\n\n*（绘图失败：{e}）*"

        chart_md = core.save_echart_option(option, prefix="sql", label=label)
        return f"{md}\n\n{chart_md}"


def _check_sql_pitfalls(sql: str) -> str | None:
    """执行前做一次 SQL 质量检查，命中坑点立即返回详细错误字符串。"""
    # 坑 1：trade_date 旁边有 yyyymmdd 字面量
    m = _BAD_DATE_WINDOW_RE.search(sql)
    if m:
        bad = m.group(1)
        fixed = f"{bad[:4]}-{bad[4:6]}-{bad[6:]}"
        return (
            f"错误：SQL 里的日期格式不对。本库 `trade_date` 列是 **YYYY-MM-DD 带连字符** "
            f"的文本（例如 `'2025-01-01'`），你写成了 `'{bad}'`（Tushare 旧格式），"
            f"字符串比较匹配不到任何行。\n\n"
            f"🛠 修复：把 SQL 里每一处 `'{bad}'` 改成 `'{fixed}'`（以及其他同类日期常量）后重试。\n"
            f"✅ 正确示例：`WHERE trade_date >= '2025-01-01' AND trade_date <= '2025-12-31'`"
        )

    # 坑 2：把 SQLite 的 date('now', '-N days') 写进 MySQL
    if _SQLITE_DATE_NOW_RE.search(sql):
        return (
            "错误：检测到 SQLite 方言的 `date('now', ...)` 写法。当前数据库是 **MySQL**，"
            "请改用 MySQL 的日期函数。\n\n"
            "🛠 修复示例：\n"
            "- 近 30 天：`trade_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)`\n"
            "- 近 90 天：`trade_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)`\n"
        )
    return None


def _empty_result_diagnosis(sql: str) -> str:
    """结果为 0 行时，给 LLM 一个诊断清单，而不是冷冰冰的 '0 行'。"""
    hints: list[str] = []

    # 再做一次宽松的 yyyymmdd 兜底检查（可能不挨着 trade_date，但 8 位纯数字日期一般就是错）
    if _ANY_YYYYMMDD_LITERAL_RE.search(sql):
        hints.append(
            "- 看起来 SQL 里出现了 **yyyymmdd 无分隔符日期字面量**（如 `'20250101'`）。"
            "本库 `trade_date` 是 `YYYY-MM-DD` 带连字符字符串，请改成 `'2025-01-01'` 格式。"
        )

    # 未来日期：数据最多到今天附近
    future_years = re.findall(r"['\"]((?:20[3-9]\d|2[1-9]\d{2}))-\d{2}-\d{2}['\"]", sql)
    if future_years:
        hints.append(
            f"- SQL 里出现疑似未来年份：{sorted(set(future_years))}。本库只有历史行情，"
            f"查询未来日期必然是 0 行。"
        )

    # ts_code 大小写
    low_ts = re.findall(r"ts_code\s*=\s*['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
    for code in low_ts:
        if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
            hints.append(
                f"- `ts_code` 值 `'{code}'` 格式可疑，标准写法是 `6 位数字.SH/SZ/BJ`，"
                f"例如 `'600519.SH'`、`'000858.SZ'`。"
            )

    if not hints:
        hints.append(
            "- 数据库里这段条件下确实没有行。可以放宽条件（扩大日期区间 / 换一只股票）"
            "或先跑一条探测 SQL：`SELECT MIN(trade_date), MAX(trade_date) FROM stock_daily WHERE ts_code='...'` 确认数据覆盖范围。"
        )

    return "查询结果为空（0 行）。可能原因：\n" + "\n".join(hints)


def build_tool() -> Tool:
    return ExcSQLTool()
