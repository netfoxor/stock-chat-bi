# -*- coding: utf-8 -*-
"""
编排委托给 Nanobot.run 时的内侧打点。
与 nanobot.agent.loop 的日志并存，前缀统一 [workflow][agent]，便于在同一终端 grep。
"""

from __future__ import annotations

import json
from typing import Any

from nanobot.agent.hook import AgentHook, AgentHookContext


class WorkflowInnerAgentHook(AgentHook):
    """Orchestrator 专用：在每轮迭代、每次工具调用处写 loguru。"""

    def __init__(self, workflow_log: Any) -> None:
        super().__init__()
        self._log = workflow_log

    async def before_iteration(self, ctx: AgentHookContext) -> None:
        self._log.info("[workflow][agent] round={}/iter_index={}", ctx.iteration + 1, ctx.iteration)

    async def before_execute_tools(self, ctx: AgentHookContext) -> None:
        for tc in ctx.tool_calls:
            raw = json.dumps(tc.arguments, ensure_ascii=False)
            snippet = raw[:260] + ("..." if len(raw) > 260 else "")
            self._log.info("[workflow][agent] → tool={} {}", tc.name, snippet)

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        usage = dict(ctx.usage or {})
        self._log.info(
            "[workflow][agent] round={} done prompt_tokens={} completion_tokens={}",
            ctx.iteration + 1,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )
