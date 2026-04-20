# -*- coding: utf-8 -*-
"""
stock_tools —— 常驻 in-process 工具集合

设计目标：
  * 每个工具一个文件（`<name>.py`），独立 import、独立失败
  * `load_all()` 逐个 try-import，某个坏了只是缺席，主程序仍能起
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.agent.tools.base import Tool

logger = logging.getLogger(__name__)

# 要加载的工具模块名（同目录下的 *.py，去掉后缀）。每个模块需暴露 `build_tool() -> Tool`
TOOL_MODULES: tuple[str, ...] = (
    "exc_sql",
)


def load_all() -> list["Tool"]:
    """逐个 try-import，返回实例化好的 Tool 列表；失败的写一条 warning 并跳过。"""
    tools: list["Tool"] = []
    for mod_name in TOOL_MODULES:
        try:
            mod = importlib.import_module(f".{mod_name}", __name__)
            builder = getattr(mod, "build_tool", None)
            if builder is None:
                logger.warning("stock_tools.%s 缺少 build_tool()，跳过", mod_name)
                continue
            tools.append(builder())
        except Exception as e:  # noqa: BLE001 - 目标就是兜底任何异常
            logger.warning("stock_tools.%s 加载失败，已跳过：%s", mod_name, e)
    return tools
