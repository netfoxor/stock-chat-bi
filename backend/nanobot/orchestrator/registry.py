# -*- coding: utf-8 -*-
"""
调度层自带的技能清单（明文注册，**不扫描 skills/**）。
与 nanobot SkillsLoader 独立；Orchestrator 只做 keyword→name 选择与 run_skill。
"""

from __future__ import annotations

from typing import TypedDict


class SkillCatalogEntry(TypedDict, total=False):
    name: str
    desc: str
    keywords: list[str]


# keyword 为小写 substring 匹配（skill_selector 里对 user text 做小写化）
AVAILABLE_SKILLS: list[SkillCatalogEntry] = [
    {
        "name": "stock-sql",
        "desc": "A 股日线 MySQL：走势、排行榜、SQL、K 线相关",
        "keywords": ["sql", "mysql", "查询", "日线", "k线", "走势", "涨幅", "排名", "股票", "行情"],
    },
    {
        "name": "arima-forecast",
        "desc": "ARIMA 收盘价预测",
        "keywords": ["预测", "arima", "forecast", "未来", "外推", "趋势"],
    },
    {
        "name": "bollinger",
        "desc": "布林带超买超卖检测",
        "keywords": ["布林", "bollinger", "超买", "超卖", "上下轨"],
    },
]
