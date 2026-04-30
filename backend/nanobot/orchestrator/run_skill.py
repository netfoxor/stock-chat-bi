# -*- coding: utf-8 -*-
"""
通过 nanobot 内置 ExecTool 执行技能脚本。**不手写 subprocess / 不重实现 exec**。
命令行内容由本模块内静态映射 + 正则从用户 text 抽取参数拼装（非 skill loader）。
"""

from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

from loguru import logger
from nanobot.agent.tools.shell import ExecTool
from nanobot.config.loader import load_config
from nanobot.config.schema import ExecToolConfig


_EXEC_EXIT_TRAILER_RE = re.compile(r"\n+Exit code:\s*\d+\s*\Z", re.IGNORECASE)


class StockSkillExecTool(ExecTool):
    """nanobot 默认 ExecTool 将 stdout/stderr 截断在 10k 字符，ARIMA+ECharts 易从中截断破坏 ``` 围栏。"""

    _MAX_OUTPUT = 524_288


def _strip_exec_trailer(stdout: str) -> str:
    """ExecTool.execute 总会在末尾追加「Exit code: N」，混入会话正文会像多出一截 JSON。"""
    if not stdout:
        return stdout
    return _EXEC_EXIT_TRAILER_RE.sub("", stdout.rstrip())


def _build_exec(workspace: Path) -> ExecTool:
    ec = ExecToolConfig()
    try:
        cfg = load_config(workspace / "config.json")
        ec = cfg.tools.exec if cfg.tools else ec
    except Exception:
        pass
    return StockSkillExecTool(
        working_dir=str(workspace),
        timeout=ec.timeout,
        restrict_to_workspace=False,
        sandbox=ec.sandbox,
        path_append=ec.path_append,
        allowed_env_keys=list(ec.allowed_env_keys),
    )


_RE_TS = re.compile(r"\b(\d{6}\.(?:SH|SZ|BJ))\b", re.I)
_RE_DATES = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# 与 stock_core.MAX_FORECAST_DAYS 对齐；「月」→ 日历月按约 22 个交易日换算（cap 在本函数内）
_MAX_FORECAST_STEP = 60
_APPROX_TRADING_PER_CAL_MONTH = 22

_CN_MONTH_HEAD: dict[str, int] = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_RUN_SKILL_LOG = logger.bind(subsystem="orchestrator.run_skill")


def _python_cmd() -> str:
    """与宿主进程同源解释器，避免 Linux/Docker 无 `python` 仅有 python3。"""
    exe = (getattr(sys, "executable", None) or "").strip()
    if exe:
        return shlex.quote(exe)
    return "python3"


def _guess_ts_code(text: str, default: str = "600519.SH") -> str:
    m = _RE_TS.search(text)
    return m.group(1).upper() if m else default


def _clip_forecast_steps(n: int) -> int:
    return max(1, min(int(n), _MAX_FORECAST_STEP))


def _guess_n_forecast(text: str, default: int = 10) -> int:
    """
    推断 ARIMA 预测步数（交易日）。
    「天 / 个交易日」后缀必须写明；禁止把「一个月」里的「1」误解析成 n=1。
    """
    t = text.replace(" ", "").replace("\n", "").replace("\u3000", "").strip()

    if re.search(r"半\s*个?\s*月", t):
        return _clip_forecast_steps(_APPROX_TRADING_PER_CAL_MONTH // 2)

    m_cn = re.search(r"([一二三四五六七八九十两])\s*个?\s*月", t)
    if m_cn:
        mo = _CN_MONTH_HEAD.get(m_cn.group(1))
        if mo is not None:
            return _clip_forecast_steps(mo * _APPROX_TRADING_PER_CAL_MONTH)

    m_mo = re.search(r"(\d{1,2})\s*个?\s*月", t)
    if m_mo:
        return _clip_forecast_steps(int(m_mo.group(1)) * _APPROX_TRADING_PER_CAL_MONTH)

    m_trade = re.search(r"(\d{1,2})\s*个?\s*交易日", t)
    if m_trade:
        return _clip_forecast_steps(int(m_trade.group(1)))

    m_week = re.search(r"(\d{1,2})\s*个?\s*周", t)
    if m_week:
        return _clip_forecast_steps(int(m_week.group(1)) * 5)

    if re.search(r"一周|下个?周|接下来一周", t):
        return _clip_forecast_steps(5)

    m_day = re.search(r"(\d{1,2})\s*天", t)
    if m_day:
        return _clip_forecast_steps(int(m_day.group(1)))

    return default


def _guess_bollinger_range(text: str) -> tuple[str, str]:
    dates = _RE_DATES.findall(text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    return "2024-01-01", "2024-12-31"


def build_exec_command(skill_name: str, text: str) -> str | None:
    """相对路径脚本 + 宿主解释器（shlex.quote），与 AGENTS 手抄示例一致且不依赖 PATH 里的 python。"""
    py = _python_cmd()
    ts = _guess_ts_code(text)
    if skill_name == "arima-forecast":
        n = _guess_n_forecast(text)
        return f"{py} skills/arima-forecast/scripts/forecast.py --ts-code {ts} --n {n}"
    if skill_name == "bollinger":
        start, end = _guess_bollinger_range(text)
        return (
            f"{py} skills/bollinger/scripts/detect.py "
            f"--ts-code {ts} --start {start} --end {end}"
        )
    # stock-sql 无独立脚本入口，由 orchestrator 走 Nanobot.run
    return None


async def run_skill(skill_name: str, text: str, *, workspace: Path | None = None) -> str:
    """
    nanobot 侧统一技能调用入口。
    exec 类技能：经 ExecTool.execute；stock-sql / 未知名返回指引性错误字符串（由上层决定是否改走 bot.run）。
    """
    import stock_core as core  # noqa: PLC0415  # pylint: disable=import-error

    root = workspace or core.WORKSPACE
    cmd = build_exec_command(skill_name, text)
    if cmd is None:
        return (
            f"技能 `{skill_name}` 无 ExecTool 封装脚本；"
            "请由 Orchestrator 走默认 Nanobot Agent（read_file stock-sql / exc_sql）。"
        )
    if skill_name == "arima-forecast":
        n_guess = _guess_n_forecast(text)
        ts_guess = _guess_ts_code(text)
        _RUN_SKILL_LOG.info(
            "[run_skill] arima inferred ts_code={} n_steps={} (from query preview: {})",
            ts_guess,
            n_guess,
            text.replace("\n", " ")[:120],
        )
    elif skill_name == "bollinger":
        rng = _guess_bollinger_range(text)
        _RUN_SKILL_LOG.info(
            "[run_skill] bollinger inferred ts_code={} range={} preview={}",
            _guess_ts_code(text),
            rng,
            text.replace("\n", " ")[:120],
        )

    tool = _build_exec(Path(root))
    _RUN_SKILL_LOG.info("[run_skill] ExecTool command={}", cmd)
    raw = await tool.execute(command=cmd)
    return _strip_exec_trailer(raw)
