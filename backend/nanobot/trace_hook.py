# -*- coding: utf-8 -*-
"""将 Agent 迭代中的 LLM 调用写入 trace_ctx（SSE 友好；只记轮次与 token，不记正文）。"""

from __future__ import annotations

import uuid
from typing import Any

from nanobot.agent.hook import AgentHook, AgentHookContext

import trace_ctx


def _now() -> str:
    return trace_ctx._now_iso()  # type: ignore[attr-defined]


class TraceHook(AgentHook):
    """每个 agent iteration：开始时间 + 结束时间与 usage（同 trace_key 在 trace_ctx 内合并为一条）。"""

    def __init__(self) -> None:
        super().__init__()
        self._llm_span_by_iter: dict[int, str] = {}

    async def before_iteration(self, context: AgentHookContext) -> None:
        sid = uuid.uuid4().hex
        self._llm_span_by_iter[context.iteration] = sid
        started_at = _now()
        trace_ctx.add_event(
            kind="llm",
            name="chat.completion",
            input=None,
            output=None,
            started_at=started_at,
            meta={
                "span_id": sid,
                "trace_key": f"llm:{sid}",
                "phase": "start",
                "iteration_no": context.iteration,
            },
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        sid = self._llm_span_by_iter.get(context.iteration)
        if not sid:
            return
        resp = context.response
        usage = dict(context.usage or {})

        summary: dict[str, Any] = {
            "round": context.iteration + 1,
            "iteration_index": context.iteration,
            "usage": usage,
            "stop_reason": getattr(resp, "finish_reason", None) if resp else None,
            "requested_tools": [{"name": tc.name} for tc in (context.tool_calls or [])]
            if context.tool_calls
            else [],
        }
        if context.error:
            summary["error"] = str(context.error)

        status = "error" if context.error else "ok"

        trace_ctx.add_event(
            kind="llm",
            name="chat.completion",
            input=None,
            output=summary,
            ended_at=_now(),
            meta={
                "span_id": sid,
                "trace_key": f"llm:{sid}",
                "phase": "end",
                "status": status,
                "iteration_no": context.iteration,
            },
        )
