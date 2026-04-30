# -*- coding: utf-8 -*-
"""
受限子编排：最多 3 步，每步只能从 AVAILABLE_SKILLS 中选一个并经 run_skill 或 SQL 兜底。
exec 类技能可链式多步；stock-sql 触发后走整条 Nanobot Agent 并结束。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from .registry import AVAILABLE_SKILLS, SkillCatalogEntry
from .skill_selector import llm_select_skill, match_candidates

RunSkillFn = Callable[[str, str], Awaitable[str]]
RunAgentFn = Callable[[str], Awaitable[str]]

MAX_SUBAGENT_STEPS = 3

_EXEC_SKILLS = frozenset({"arima-forecast", "bollinger"})
_SA_LOG = logger.bind(subsystem="orchestrator.subagent")


def _preview(text: str, limit: int = 120) -> str:
    t = text.replace("\n", " ").strip()
    if len(t) <= limit:
        return t
    return t[: limit - 3] + "..."


async def run_subagent(
    user_text: str,
    *,
    run_skill_fn: RunSkillFn,
    run_agent_fn: RunAgentFn | None,
    max_steps: int = MAX_SUBAGENT_STEPS,
) -> dict[str, Any]:
    """
    「工具来源 = available_skills」：每步在候选中用 llm_select_skill 选一个；
    exec 技能调 run_skill，可连续多步（最多 max_steps）；stock-sql 走 run_agent_fn 后结束。
    输出结构化 JSON dict（外层可 json.dumps）。
    """
    remaining = user_text.strip()
    steps_out: list[dict[str, Any]] = []
    used: set[str] = set()

    _SA_LOG.info(
        "[workflow][subagent] enter max_steps={} input={}",
        max_steps,
        _preview(user_text),
    )

    for step in range(max_steps):
        matched = [e for e in match_candidates(remaining) if e.get("name") not in used]
        pool: list[SkillCatalogEntry] = matched if matched else [
            e for e in AVAILABLE_SKILLS if e.get("name") not in used
        ]
        if not pool:
            _SA_LOG.info("[workflow][subagent] step={} stop_reason=no_pool_remaining", step + 1)
            break

        skill_name = llm_select_skill(pool, remaining)
        pool_names = [p.get("name") for p in pool]
        _SA_LOG.info(
            "[workflow][subagent] step={}/{} pool={} resolved_skill={}",
            step + 1,
            max_steps,
            pool_names,
            skill_name,
        )
        if not skill_name:
            _SA_LOG.info("[workflow][subagent] step={} stop_reason=no_skill_resolved", step + 1)
            break
        used.add(skill_name)

        if skill_name in _EXEC_SKILLS:
            _SA_LOG.info(
                "[workflow][subagent] step={} invoke=ExecTool skill={}",
                step + 1,
                skill_name,
            )
            raw = await run_skill_fn(skill_name, remaining)
            steps_out.append({"step": step + 1, "skill": skill_name, "channel": "exec", "output": raw})
            excerpt = raw[:2000] if len(raw) > 2000 else raw
            remaining = f"{user_text}\n\n[步骤 {step + 1} 工具输出节选]\n{excerpt}"
            continue

        if skill_name == "stock-sql" and run_agent_fn is not None:
            _SA_LOG.info(
                "[workflow][subagent] step={} invoke=Nanobot.run skill={}",
                step + 1,
                skill_name,
            )
            ans = await run_agent_fn(user_text)
            steps_out.append({"step": step + 1, "skill": "stock-sql", "channel": "nanobot_agent", "output": ans})
            break

        _SA_LOG.warning(
            "[workflow][subagent] step={} channel=noop skill={}",
            step + 1,
            skill_name,
        )
        steps_out.append(
            {
                "step": step + 1,
                "skill": skill_name,
                "channel": "noop",
                "output": "no backend for skill in subagent pipeline",
            },
        )
        break

    _SA_LOG.info(
        "[workflow][subagent] exit recorded_steps={}",
        len(steps_out),
    )

    return {
        "mode": "subagent",
        "max_steps": max_steps,
        "input": user_text,
        "steps": steps_out,
    }


def subagent_result_to_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
