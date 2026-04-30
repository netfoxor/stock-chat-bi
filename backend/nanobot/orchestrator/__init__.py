# -*- coding: utf-8 -*-
"""Nanobot 调度层：关键字选路 + ExecTool(run_skill) + SubAgent(JSON)。"""

from __future__ import annotations

from .orchestrator import is_complex, orchestrate_turn
from .registry import AVAILABLE_SKILLS, SkillCatalogEntry
from .run_skill import build_exec_command, run_skill
from .skill_selector import llm_select_skill, match_candidates, pick_skill, resolve_conflict
from .subagent import MAX_SUBAGENT_STEPS, run_subagent, subagent_result_to_json

__all__ = [
    "AVAILABLE_SKILLS",
    "MAX_SUBAGENT_STEPS",
    "SkillCatalogEntry",
    "build_exec_command",
    "is_complex",
    "llm_select_skill",
    "match_candidates",
    "orchestrate_turn",
    "pick_skill",
    "resolve_conflict",
    "run_skill",
    "run_subagent",
    "subagent_result_to_json",
]
