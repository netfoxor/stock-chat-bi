#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chainlit 前端 - 股票查询助手

启动：
  chainlit run app_chainlit.py -w

功能：
  * 多轮会话（按 Chainlit session 隔离）
  * 自动把工具调用渲染为 Step，折叠展示
  * 把 markdown 中的 ![](image_show/xxx.png) 自动解析为 cl.Image
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import chainlit as cl

from nanobot.agent.hook import AgentHook, AgentHookContext
from stock_bot import IMAGE_DIR, WORKSPACE, build_bot

IMAGE_MD_RE = re.compile(r"!\[([^\]]*)\]\((image_show/[^)]+)\)")


class ChainlitHook(AgentHook):
    """把每次工具调用作为 Step 显示到 UI 上。"""

    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        for tc in ctx.tool_calls:
            args_preview = str(tc.arguments)[:500]
            step = cl.Step(name=f"tool: {tc.name}", type="tool")
            step.input = args_preview
            await step.send()


def _split_markdown_and_images(md: str) -> tuple[str, list[cl.Image]]:
    """从 markdown 中提取 image_show 图片为 Chainlit 附件。"""
    images: list[cl.Image] = []
    seen: set[str] = set()

    def _abs_path(rel: str) -> Path:
        return (WORKSPACE / rel).resolve()

    for m in IMAGE_MD_RE.finditer(md):
        name, rel_path = m.group(1), m.group(2)
        if rel_path in seen:
            continue
        seen.add(rel_path)
        abs_p = _abs_path(rel_path)
        if not abs_p.exists():
            continue
        images.append(
            cl.Image(path=str(abs_p), name=name or abs_p.name, display="inline")
        )

    # 让文案中仍保留 markdown 表格/摘要，但把图片引用行删除（Chainlit 会用附件显示）
    cleaned = IMAGE_MD_RE.sub("", md).rstrip() + "\n"
    return cleaned, images


@cl.on_chat_start
async def on_chat_start() -> None:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        await cl.Message(
            content="⚠️ 未设置 `DASHSCOPE_API_KEY` 环境变量，请在启动前导出后再刷新页面。"
        ).send()
        return

    bot = build_bot()
    cl.user_session.set("bot", bot)
    cl.user_session.set("session_key", f"stock:chainlit:{int(time.time())}")

    await cl.Message(
        content=(
            "**股票查询助手已就绪（nanobot）** 🐈\n\n"
            "可以尝试：\n"
            "- 查询贵州茅台 2024 年全年日线\n"
            "- 用 ARIMA 预测五粮液未来 10 个交易日的收盘价\n"
            "- 检测广发证券 2025-01-01 到 2025-06-30 的超买超卖"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    bot = cl.user_session.get("bot")
    session_key = cl.user_session.get("session_key") or "stock:chainlit:default"
    if bot is None:
        await cl.Message(content="Bot 未初始化，请刷新页面。").send()
        return

    async with cl.Step(name="nanobot.run", type="llm") as thinking:
        thinking.input = message.content
        result = await bot.run(
            message.content, session_key=session_key, hooks=[ChainlitHook()]
        )
        thinking.output = "done"

    text, images = _split_markdown_and_images(result.content or "")
    await cl.Message(content=text or "(空结果)", elements=images).send()
