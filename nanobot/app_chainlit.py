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
from self_heal_hook import SelfHealHook
from stock_bot import WORKSPACE, build_bot

# 匹配工具产出的两种图表引用：
#   * ECharts 自定义元素：![alt](chart:charts/xxx.json)
#   * 传统静态图片    ：![alt](image_show/xxx.png)（遗留兼容）
CHART_MD_RE = re.compile(r"!\[([^\]]*)\]\(chart:([^)]+)\)")
IMAGE_MD_RE = re.compile(r"!\[([^\]]*)\]\(((?!chart:)[^)]+)\)")


def _resolve_path(ref: str) -> Path | None:
    """把工具返回的文件引用解析成磁盘绝对路径。"""
    p = Path(ref)
    if p.is_absolute() and p.exists():
        return p
    candidate = (WORKSPACE / ref).resolve()
    if candidate.exists():
        return candidate
    return None


def _load_echart_option(ref: str) -> dict | None:
    path = _resolve_path(ref)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _split_markdown_and_elements(md: str) -> tuple[str, list]:
    """
    从 markdown 抽出所有图表/图片引用为 Chainlit elements，并从文本中删除对应标记。

    识别顺序：先 ECharts（chart: 前缀），再普通图片。
    """
    elements: list = []
    seen: set[str] = set()

    # 1) ECharts 自定义元素
    for m in CHART_MD_RE.finditer(md):
        alt, ref = m.group(1), m.group(2)
        key = f"chart::{ref}"
        if key in seen:
            continue
        seen.add(key)
        option = _load_echart_option(ref)
        if option is None:
            continue
        elements.append(
            cl.CustomElement(
                name="EChart",
                props={
                    "option": option,
                    "height": 560,
                    "title": alt or "",
                },
                display="inline",
            )
        )

    # 2) 普通图片兜底
    for m in IMAGE_MD_RE.finditer(md):
        alt, ref = m.group(1), m.group(2)
        if ref in seen:
            continue
        seen.add(ref)
        path = _resolve_path(ref)
        if path is None:
            continue
        elements.append(
            cl.Image(
                path=str(path),
                name=alt or path.name,
                display="inline",
                size="large",
            )
        )

    cleaned = CHART_MD_RE.sub("", md)
    cleaned = IMAGE_MD_RE.sub("", cleaned).strip()
    return cleaned, elements


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


# 这些工具的返回里含业务可视化（表格、K 线图、ARIMA 预测图等），可以 emit 到主聊天流
TOOLS_WITH_VISUAL_OUTPUT = {"exc_sql", "exec"}

# 拼 side 附件时的原始输出长度上限，防止把 SKILL.md 全文塞进来
_MAX_TRACE_CHARS = 6000


def _build_trace_element(name: str, args_str: str, result_str: str) -> "cl.CustomElement":
    """
    把「参数 + 原始输出」做成一个 inline 折叠块（自定义元素 ToolTrace）。

    浏览器原生 `<details>/<summary>` 实现：默认收起，点击展开，再点击收起，
    完全在消息原地，不弹侧栏、不跳 page、不会引起页面滚动跳跃。

    原始输出里的图表/图片引用（`chart:` 自定义协议 + 普通 image）会被清除：
    这些内容已经在主流通过 CustomElement / cl.Image 渲染过，trace 里
    再留 `![...](chart:...)` 只会被浏览器当成失败的 <img> 显示出破图占位符。
    """
    # 清掉图表/图片引用，避免在 trace 里渲染出破图
    cleaned = CHART_MD_RE.sub("", result_str)
    cleaned = IMAGE_MD_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    if len(cleaned) > _MAX_TRACE_CHARS:
        output = (
            cleaned[:_MAX_TRACE_CHARS]
            + f"\n\n...(已截断，原始共 {len(cleaned)} 字符)..."
        )
    else:
        output = cleaned
    return cl.CustomElement(
        name="ToolTrace",
        props={
            "toolName": name,
            "args": args_str,
            "output": output,
        },
        display="inline",
    )


class ChainlitHook(AgentHook):
    """
    呈现策略：按真实请求顺序在时间线上流式出现；每个工具调用 = 一条消息。

    * **`exc_sql` / `exec`**（有业务可视化产出）：
        消息 = "🔧 使用 `xx`" 小标题 + 可视化内容（表格/图表）+ 可点击跳转到 page 的"参数/原文"详情
    * **其他系统工具**（`read_file` / `list_dir` 等，给 LLM 自己读的）：
        消息 = "🔧 使用 `xx`" + 详情链接；**不把原文灌到主流**（避免 SKILL.md 刷屏）

    LLM 的最终文字总结由 `on_message` 在 `bot.run` 之后追加。
    """

    def __init__(self) -> None:
        super().__init__(reraise=True)
        self._pending_calls: list[tuple[str, str, str]] = []

    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        self._pending_calls = [
            (tc.id, tc.name, json.dumps(tc.arguments, ensure_ascii=False, indent=2))
            for tc in ctx.tool_calls
        ]

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        if not self._pending_calls or not ctx.tool_results:
            self._pending_calls = []
            return

        # tool_calls 与 tool_results 严格同序（runner.py 的 zip 实现）
        # 呈现顺序：先发 trace 折叠块（"我用了 xxx"），再发内容（表格/图表）
        #          这样符合真实的请求-响应心智模型
        for (_tc_id, name, args_str), result in zip(
            self._pending_calls, ctx.tool_results
        ):
            result_str = _stringify_tool_result(result)
            trace_elem = _build_trace_element(name, args_str, result_str)

            # 第一条：trace 折叠块（每次工具调用都有）
            await cl.Message(
                content="",
                author=name,
                elements=[trace_elem],
            ).send()

            # 第二条：只承载图表。表格等文本数据已经在 trace 折叠块的"原始输出"里，
            # 想看的点开 trace 即可；主流保持干净——只展示图表 + 最后的 LLM 总结
            if name in TOOLS_WITH_VISUAL_OUTPUT:
                _body, visual_elements = _split_markdown_and_elements(result_str)
                if visual_elements:
                    await cl.Message(
                        content="",
                        author=name,
                        elements=visual_elements,
                    ).send()

        self._pending_calls = []


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
            "- 查询贵州茅台 2025 年全年日线\n"
            "- 统计2025年4月广发证券的日均成交量\n"
            "- 对比2025年中芯国际和贵州茅台的涨跌幅\n"
            "- 用 ARIMA 预测五粮液未来 10 个交易日的收盘价\n"
            "- 检测广发证券 2025-01-01 到 2025-12-31 的超买超卖"
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
    healer = SelfHealHook()
    result = await bot.run(
        message.content,
        session_key=session_key,
        hooks=[hook, healer],
    )

    # LLM 最后的文字总结：同样尝试解析是否带图表（以防万一 LLM 真的复述了）
    final_text, final_elements = _split_markdown_and_elements(result.content or "")
    if final_text or final_elements:
        await cl.Message(
            content=final_text or "(空结果)",
            elements=final_elements,
        ).send()
