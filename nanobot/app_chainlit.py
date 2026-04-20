#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chainlit 前端 - 股票查询助手

启动：
  chainlit run app_chainlit.py -w

设计要点：
  * 工具调用用 cl.Step 折叠显示（参数 + 原始 JSON 输出，便于 debug）
  * 工具返回的 markdown（表格 + 图表）立刻作为独立消息 emit 到聊天流，
    这样即使 LLM 没按 prompt "原样输出"，图表也一定会显示
  * LLM 的最终文字总结作为最后一条消息
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import chainlit as cl

from nanobot.agent.hook import AgentHook, AgentHookContext
from stock_bot import IMAGE_DIR, WORKSPACE, build_bot

# 匹配 ![alt](image_show/xxx.png) 或绝对路径的图片 markdown
IMAGE_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _resolve_image(ref: str) -> Path | None:
    """把工具返回的图片引用解析成磁盘绝对路径。"""
    p = Path(ref)
    if p.is_absolute() and p.exists():
        return p
    candidate = (WORKSPACE / ref).resolve()
    if candidate.exists():
        return candidate
    # 兜底：尝试按文件名在 IMAGE_DIR 里找
    candidate = IMAGE_DIR / Path(ref).name
    return candidate if candidate.exists() else None


def _split_text_and_images(md: str) -> tuple[str, list[cl.Image]]:
    """从 markdown 抽出所有图片为 cl.Image 附件，并从文本中删除图片标记。"""
    images: list[cl.Image] = []
    seen: set[str] = set()

    for m in IMAGE_MD_RE.finditer(md):
        alt, ref = m.group(1), m.group(2)
        if ref in seen:
            continue
        seen.add(ref)
        path = _resolve_image(ref)
        if path is None:
            continue
        images.append(
            cl.Image(
                path=str(path),
                name=alt or path.name,
                display="inline",  # 在消息正文下方原尺寸展示
                size="large",
            )
        )

    cleaned = IMAGE_MD_RE.sub("", md).strip()
    return cleaned, images


def _stringify_tool_result(result) -> str:
    """工具返回通常是 str；列表/字典也容忍一下。"""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        # content blocks 形式：[{"type":"text","text":"..."}]
        parts = []
        for block in result:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(result)


class ChainlitHook(AgentHook):
    """
    - before_execute_tools: 记录每个工具调用的 args，准备一个 Step
    - after_iteration:      用工具结果填充 Step，并把表格+图片 emit 到主聊天流
    """

    def __init__(self) -> None:
        super().__init__(reraise=True)
        # 本轮迭代里 (tool_call_id, step, name, args_str)
        self._pending_steps: list[tuple[str, cl.Step, str, str]] = []

    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        self._pending_steps.clear()
        for tc in ctx.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False, indent=2)
            step = cl.Step(name=f"🔧 {tc.name}", type="tool")
            step.input = args_str
            await step.send()
            self._pending_steps.append((tc.id, step, tc.name, args_str))

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        if not self._pending_steps or not ctx.tool_results:
            return

        # tool_calls 与 tool_results 严格同序（见 runner.py 的 zip 实现）
        for (tc_id, step, name, args_str), result in zip(
            self._pending_steps, ctx.tool_results
        ):
            result_str = _stringify_tool_result(result)

            # 1) Step 内：完整原始输出（折叠）
            step.output = result_str
            await step.update()

            # 2) 主聊天流：表格+说明 + 图片附件，永远可见
            body, images = _split_text_and_images(result_str)
            if body or images:
                await cl.Message(
                    content=body or f"*（{name} 已生成图表）*",
                    author=name,
                    elements=images,
                ).send()

        self._pending_steps.clear()


# --------------------------------------------------------------------------- #
# Chainlit 生命周期
# --------------------------------------------------------------------------- #

@cl.on_chat_start
async def on_chat_start() -> None:
    if not os.environ.get("DASHSCOPE_API_KEY"):
        await cl.Message(
            content="⚠️ 未设置 `DASHSCOPE_API_KEY` 环境变量，请在启动前导出后重启。"
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

    hook = ChainlitHook()
    result = await bot.run(message.content, session_key=session_key, hooks=[hook])

    # LLM 最后的文字总结：同样尝试解析是否带图片（以防万一 LLM 真的复述了）
    final_text, final_images = _split_text_and_images(result.content or "")
    if final_text or final_images:
        await cl.Message(
            content=final_text or "(空结果)",
            elements=final_images,
        ).send()
