# -*- coding: utf-8 -*-
"""
Orchestrator：固定工作流——复杂任务 → SubAgent(JSON)；否则 keyword 选 skill → ExecTool 或直接 Nanobot.run。
不替代 nanobot SkillLoader；仅用 registry.AVAILABLE_SKILLS 做静态路由。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import stock_core as core
from loguru import logger
from nanobot.agent.hook import AgentHook
from nanobot.nanobot import Nanobot

from .run_skill import build_exec_command, run_skill
from .skill_selector import match_candidates, pick_skill
from .subagent import run_subagent, subagent_result_to_json
from .workflow_inner_agent_hook import WorkflowInnerAgentHook

_WORKFLOW_LOG = logger.bind(subsystem="orchestrator.workflow")


def _preview(text: str, limit: int = 160) -> str:
    t = text.replace("\n", " ").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 3] + "..."


def is_complex(text: str) -> bool:
    if any(k in text for k in ("找出", "筛选", "排名")):
        return True
    has_analysis = any(k in text for k in ("分析", "统计", "对比", "算算"))
    has_predict = "预测" in text
    return bool(has_analysis and has_predict)


async def orchestrate_turn(
    question: str,
    *,
    bot: Nanobot,
    session_key: str = "orch",
    hooks: list[AgentHook] | None = None,
) -> str:
    ws = Path(core.WORKSPACE)

    _WORKFLOW_LOG.info("[workflow] 1.enter session_key={} query={}", session_key, _preview(question))

    async def rs(skill_name: str, user_text: str) -> str:
        return await run_skill(skill_name, text=user_text, workspace=ws)

    inner_trace = WorkflowInnerAgentHook(_WORKFLOW_LOG)

    async def via_agent(q: str) -> str:
        merged_hooks: list[AgentHook] = [inner_trace, *(hooks or [])]
        _WORKFLOW_LOG.info("[workflow] 4a.Nanobot.run start (nested logs: [workflow][agent])")
        try:
            result = await bot.run(q, session_key=session_key, hooks=merged_hooks)
            text = (result.content or "").strip()
            _WORKFLOW_LOG.info("[workflow] 4b.Nanobot.run end content_chars={}", len(text))
            return text
        except Exception:
            _WORKFLOW_LOG.exception("[workflow] 4b.Nanobot.run FAILED")
            raise

    complex_task = is_complex(question)
    _WORKFLOW_LOG.info("[workflow] 2.is_complex={}", complex_task)

    if complex_task:
        _WORKFLOW_LOG.info("[workflow] 3.branch=subagent max_steps=3")
        payload = await run_subagent(
            question,
            run_skill_fn=rs,
            run_agent_fn=via_agent,
            max_steps=3,
        )
        out = subagent_result_to_json(payload)
        _WORKFLOW_LOG.info("[workflow] z.done branch=subagent out_chars={}", len(out))
        return out

    matched_names = [e["name"] for e in match_candidates(question)]
    chosen = pick_skill(question)
    _WORKFLOW_LOG.info(
        "[workflow] 3.skill_selector keyword_hits={} resolved={}",
        matched_names or "<none>",
        chosen or "<none>",
    )

    if chosen in ("arima-forecast", "bollinger"):
        cmd_preview = build_exec_command(chosen, question)
        _WORKFLOW_LOG.info(
            "[workflow] 4.invoke=ExecTool(delegated) skill={} preview_cmd={}",
            chosen,
            cmd_preview,
        )
        out = await rs(chosen, question)
        _WORKFLOW_LOG.info("[workflow] z.done branch=exec out_chars={}", len(out))
        return out

    reason = "keyword_stock-sql" if chosen == "stock-sql" else "no_keyword_fallback_nanobot_agent"
    _WORKFLOW_LOG.info(
        "[workflow] 4.invoke=Nanobot.run reason={} (inner loop logs: nanobot.agent.loop)",
        reason,
    )
    out = await via_agent(question)
    _WORKFLOW_LOG.info("[workflow] z.done branch=nanobot_agent out_chars={}", len(out))
    return out
