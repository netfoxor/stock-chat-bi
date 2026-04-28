#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股票查询助手 - nanobot 组装层

业务能力已拆到三处，各司其职：
  * 常驻 in-process 工具   → `stock_tools/`（目前只有 `exc_sql`）
  * ARIMA 预测             → `skills/arima-forecast/`（LLM 用 exec 调子进程）
  * 布林带检测             → `skills/bollinger/`   （LLM 用 exec 调子进程）
  * 业务知识（SQL 规范等）  → `skills/stock-sql/`   （LLM 按需 read_file 查阅）

本文件仅做三件事：
  1. 读 config.json / 环境变量
  2. 构建 nanobot.AgentLoop
  3. 调用 `stock_tools.load_all()` 容错注册工具
  4. 提供一个 CLI 入口（供 `python stock_bot.py "..."` 直接问）

运行方式：
  CLI  :  python stock_bot.py "用 ARIMA 预测贵州茅台未来 10 个交易日的收盘价"
  交互 :  python stock_bot.py
  前端 :  chainlit run app_chainlit.py -w

环境变量：
  - 方案 A（默认）：DashScope / 通义千问
      DASHSCOPE_API_KEY  必填
      QWEN_AGENT_MODEL   可选，覆盖 config.json 的 model

  - 方案 B：OpenAI 兼容协议（用于接入阿里“Coding Plan”等 OpenAI-compatible 网关）
      OPENAI_API_KEY     必填
      OPENAI_BASE_URL    必填（例如 https://xxx/v1）
      OPENAI_MODEL       可选（例如 qwen-coder / 你的网关模型名）

  - 通用：
      NANOBOT_PROVIDER   可选：openai | dashscope（默认自动判断：有 OPENAI_API_KEY 则 openai，否则 dashscope）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# 尽早设置 UTF-8 stdout（复用 core 里的逻辑，顺带给脚本烟测用）
import stock_core as core

core.setup_utf8_stdout()

from nanobot.agent.hook import AgentHook, AgentHookContext  # noqa: E402
from nanobot.agent.loop import AgentLoop  # noqa: E402
from nanobot.bus.queue import MessageBus  # noqa: E402
from nanobot.config.loader import load_config  # noqa: E402
from nanobot.nanobot import Nanobot, _make_provider  # noqa: E402

from self_heal_hook import SelfHealHook  # noqa: E402
from stock_tools import load_all as load_stock_tools  # noqa: E402

# app_chainlit 依赖 WORKSPACE，这里显式 re-export 保持兼容
WORKSPACE = core.WORKSPACE


class PrintHook(AgentHook):
    """CLI 下打印每次工具调用的精简信息，方便观察 LLM 在干什么。"""

    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        for tc in ctx.tool_calls:
            args = json.dumps(tc.arguments, ensure_ascii=False)
            print(f"  >> {tc.name}: {args[:200]}")


def build_bot() -> Nanobot:
    config = load_config(WORKSPACE / "config.json")
    config.agents.defaults.workspace = str(WORKSPACE)

    provider_override = os.environ.get("NANOBOT_PROVIDER", "").strip().lower()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_base_url = os.environ.get("OPENAI_BASE_URL", "").strip()

    use_openai = provider_override == "openai" or (not provider_override and bool(openai_key))
    if use_openai:
        if not openai_key or not openai_base_url:
            print("[Error] 选择 OpenAI 兼容协议时，需要同时设置 OPENAI_API_KEY + OPENAI_BASE_URL")
            sys.exit(1)
        config.agents.defaults.provider = "openai"
        # nanobot-ai 若支持 openai provider，则这里会生效；否则会在 _make_provider 时报错，便于定位。
        config.providers.openai.api_key = openai_key
        config.providers.openai.base_url = openai_base_url
        if model_override := os.environ.get("OPENAI_MODEL", "").strip():
            config.agents.defaults.model = model_override
    else:
        dashscope_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not dashscope_key:
            print("[Error] 未设置 DASHSCOPE_API_KEY 环境变量（或改用 OPENAI_API_KEY/OPENAI_BASE_URL）")
            sys.exit(1)
        config.agents.defaults.provider = "dashscope"
        config.providers.dashscope.api_key = dashscope_key
        if model_override := os.environ.get("QWEN_AGENT_MODEL", "").strip():
            config.agents.defaults.model = model_override

    provider = _make_provider(config)
    defaults = config.agents.defaults

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=WORKSPACE,
        model=defaults.model,
        max_iterations=defaults.max_tool_iterations,
        context_window_tokens=defaults.context_window_tokens,
        max_tool_result_chars=defaults.max_tool_result_chars,
        web_config=config.tools.web,
        exec_config=config.tools.exec,
        restrict_to_workspace=False,
        timezone=defaults.timezone,
    )

    # 容错加载 stock_tools/ 下所有业务工具
    loaded_names: list[str] = []
    for tool in load_stock_tools():
        try:
            loop.tools.register(tool)
            loaded_names.append(tool.name)
        except Exception as e:  # noqa: BLE001
            print(f"[nanobot] 工具 {getattr(tool, 'name', '?')} 注册失败：{e}")

    # skills 会自动被 SkillsLoader(WORKSPACE) 扫到；此处仅记录名单便于排查
    skills_dir = WORKSPACE / "skills"
    skill_names: list[str] = []
    if skills_dir.is_dir():
        skill_names = sorted(
            p.name for p in skills_dir.iterdir()
            if p.is_dir() and (p / "SKILL.md").is_file()
        )

    print(f"[nanobot] provider={defaults.provider}")
    print(f"[nanobot] model={defaults.model}")
    print(f"[nanobot] DB={core.DB_PATH}")
    print(f"[nanobot] 已注册工具: {', '.join(loaded_names) or '（无）'}")
    print(f"[nanobot] 可用 skills: {', '.join(skill_names) or '（无）'}"
          f"（由 SkillsLoader 自动发现）")
    return Nanobot(loop)


async def _run_once(bot: Nanobot, question: str, session_key: str = "stock:cli") -> None:
    # SelfHealHook 放在 PrintHook 之后，保证 before_execute_tools 的打印先于 healer 行动
    result = await bot.run(
        question,
        session_key=session_key,
        hooks=[PrintHook(), SelfHealHook()],
    )
    print("\n" + "=" * 60)
    print(result.content)
    print("=" * 60)


async def main() -> None:
    bot = build_bot()

    if len(sys.argv) > 1:
        await _run_once(bot, " ".join(sys.argv[1:]))
        return

    print("\n股票查询助手（nanobot 版）- 输入 exit/quit 退出\n")
    session_key = f"stock:repl:{int(time.time())}"
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in ("exit", "quit", ":q"):
            break
        try:
            await _run_once(bot, q, session_key=session_key)
        except Exception as e:  # noqa: BLE001
            print(f"[Error] {e}")


if __name__ == "__main__":
    asyncio.run(main())
