# -*- coding: utf-8 -*-
"""
仅用 AVAILABLE_SKILLS 做关键字命中与冲突消解；**不扫描目录、不使用 SkillsLoader**。
"""

from __future__ import annotations

from .registry import AVAILABLE_SKILLS, SkillCatalogEntry


def llm_select_skill(candidates: list[SkillCatalogEntry], text: str) -> str | None:
    """
    Mock：生产可换真实 LLM。
    规则——命中关键字条数最多的技能；平局取 candidates 列表中靠前的一项。
    """
    if not candidates:
        return None
    t = text.lower()
    best: SkillCatalogEntry | None = None
    best_score = -1
    for ent in candidates:
        kws = ent.get("keywords") or []
        score = sum(1 for kw in kws if kw.lower() in t)
        if score > best_score:
            best_score = score
            best = ent
        elif score == best_score and score >= 0 and best is not None:
            pass
    if best is None or best_score <= 0:
        # 平局或无人命中关键字：退化取第一个候选（仍由上层决定是否再走 full agent）
        return candidates[0]["name"]
    return best["name"]


def match_candidates(text: str) -> list[SkillCatalogEntry]:
    """返回所有命中至少一个 keyword 的技能（可多选）。"""
    t = text.lower()
    out: list[SkillCatalogEntry] = []
    for ent in AVAILABLE_SKILLS:
        kws = ent.get("keywords") or []
        if any(kw.lower() in t for kw in kws):
            out.append(ent)
    return out


def resolve_conflict(candidates: list[SkillCatalogEntry], text: str) -> str | None:
    """多技能冲突时统一走 llm_select_skill。"""
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]["name"]
    # 交集场景：ARIMA vs 布林 vs SQL —— 交由 mock LLM 规则
    return llm_select_skill(candidates, text)


def pick_skill(text: str) -> str | None:
    """对外主入口：无命中返回 None。"""
    cands = match_candidates(text)
    if not cands:
        return None
    return resolve_conflict(cands, text)
