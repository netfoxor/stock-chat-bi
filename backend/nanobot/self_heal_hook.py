# -*- coding: utf-8 -*-
"""
SelfHealHook —— 给股票助手的 skill 脚本调用加"程序级自愈"。

问题背景：
  LLM 用 `exec` 工具调 skill 里的 Python 脚本时，在 Windows 上容易犯同一种错：
    * 给 `working_dir` 传一个以 `\` 结尾的绝对路径，触发 shell `\"` 转义 bug
    * 给路径加引号但路径里并无空格
  这些错误会让 `python` 报 `can't open file '...\"..."': [Errno 22] Invalid argument`，
  LLM 靠自己多试几次也能"撞对"，但**下次遇到同类问题还会重犯**，体验糟糕。

本 Hook 的机制：
  1. 每轮 iteration 结束时扫 `ctx.tool_calls + ctx.tool_results`
  2. 识别"exec 调 skill 脚本失败"的签名（详见 `_EXEC_ERROR_SIGNATURES`）
  3. 在 messages 末尾追加一条 `role=user` 的 `[Self-Heal Hint]` 消息
     （与 nanobot runtime context 风格一致，LLM 下一轮一定能看到）
  4. 每个会话每类错误只注入一次，避免刷屏；重试成功后自动复位

这层自愈是**写在代码里的硬规则**，不依赖 LLM 记住 SKILL.md，
比 SKILL.md 里的文字提示更稳。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from nanobot.agent.hook import AgentHook, AgentHookContext


# --------------------------------------------------------------------------- #
# 错误签名库（症状 → 处方）
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class HealPattern:
    """一条自愈规则：key 是去重 id；match 返回是否命中；hint 是给 LLM 的补救提示。"""

    key: str
    patterns: tuple[re.Pattern, ...]
    hint: str


_FIX_EXAMPLE = (
    "✅ 正确调用示例（本工作区根目录就是 `nanobot/`，脚本用相对路径、无引号、不设 working_dir。"
    "**Linux/Docker 无 `python` 时请用 `python3`**）：\n"
    "```\n"
    "python3 skills/arima-forecast/scripts/forecast.py --ts-code 600519.SH --n 10\n"
    "python3 skills/bollinger/scripts/detect.py --ts-code 600519.SH --start 2024-01-01 --end 2024-12-31\n"
    "```\n"
)

_EXEC_ERROR_SIGNATURES: tuple[HealPattern, ...] = (
    HealPattern(
        key="win-quote-trailing-backslash",
        patterns=(
            re.compile(r"can't open file .*scripts\\[\"'].*forecast\.py", re.I),
            re.compile(r"can't open file .*scripts\\[\"'].*detect\.py", re.I),
            re.compile(r"can't open file '.*\\\".*\\\"'", re.I),
            re.compile(r"\[Errno 22\] Invalid argument", re.I),
        ),
        hint=(
            "❌ 你刚才的 `exec` 调用遇到了 **Windows 下 shell 引号解析错乱**。\n"
            "根因：同时做了这两件事之一就会触发：\n"
            "  1. 把 `working_dir` 设成以 `\\` 结尾的路径（`\\\"` 被当转义符）\n"
            "  2. 给不含空格的路径加了引号\n\n"
            "🛠 自愈步骤：**重新调用 `exec`，严格遵守以下三条**：\n"
            "  - **不要传 `working_dir` 参数**（默认 cwd 就是 nanobot/，够用）\n"
            "  - **脚本路径用相对路径**：`skills\\arima-forecast\\scripts\\forecast.py` 或 `skills\\bollinger\\scripts\\detect.py`\n"
            "  - **命令里不加任何引号**（路径不含空格）\n\n"
            + _FIX_EXAMPLE
        ),
    ),
    HealPattern(
        key="python-interpreter-command-not-found",
        patterns=(
            re.compile(r"python:\s*command\s+not\s+found", re.I),
            re.compile(
                r"bash:\s+(?:line\s+\d+:\s+)?python:\s*(?:command\s+not\s+found|not\s+found)",
                re.I,
            ),
        ),
        hint=(
            "❌ Shell 里找不到 **`python`** 命令（常见于 **Linux/Docker**：只安装了 **`python3`**）。\n"
            "🛠 自愈步骤：把 **`python`** 全部改成 **`python3`**，路径与参数不变；不要改 working_dir。\n\n"
            + _FIX_EXAMPLE
        ),
    ),
    HealPattern(
        key="python-file-not-found",
        patterns=(
            re.compile(r"python: can't open file", re.I),
            re.compile(r"python3: can't open file", re.I),
            re.compile(r"No such file or directory.*\.py", re.I),
        ),
        hint=(
            "❌ `exec` 里 python 找不到脚本文件。\n"
            "🛠 自愈步骤：\n"
            "  - 确认路径没写错：`skills\\arima-forecast\\scripts\\forecast.py` 或 `skills\\bollinger\\scripts\\detect.py`\n"
            "  - 用**相对路径**（默认 cwd = nanobot/，相对路径最稳）\n"
            "  - 不要设置 `working_dir`，不要加引号\n\n"
            + _FIX_EXAMPLE
        ),
    ),
    HealPattern(
        key="module-not-found",
        patterns=(
            re.compile(r"ModuleNotFoundError: No module named", re.I),
        ),
        hint=(
            "❌ 脚本缺依赖。本环境里 `pandas / sqlalchemy / statsmodels / numpy` 都是已装的。\n"
            "🛠 自愈步骤：\n"
            "  - 确认 `python` 指向的是装了这些包的解释器（不是系统自带 python）\n"
            "  - 不要自己加 `pip install`（环境已 ready）；如果仍失败，把原始错误原样返回给用户并停止\n"
        ),
    ),
    HealPattern(
        key="script-error-prefix",
        patterns=(
            re.compile(r"Exit code: [1-9]\d*", re.I),
        ),
        hint=(
            "❌ skill 脚本返回了非零 exit code。\n"
            "🛠 自愈步骤：\n"
            "  1. 看 stdout/stderr 里以 `错误：` 开头的那行或 traceback 末行，那才是根因\n"
            "  2. 参数类错误（股票代码不存在、日期越界、预测天数 >60 等）→ **改参数重试**\n"
            "  3. 环境类错误（找不到数据库、找不到脚本）→ **如实告诉用户**，别反复重试\n"
            "  4. 如果连续 2 次都失败，直接把错误原文告诉用户并建议修改问题，**不要无限重试**\n"
        ),
    ),
    HealPattern(
        key="exc_sql-bad-date-format",
        patterns=(
            re.compile(r"日期格式不对", re.I),
            re.compile(r"Tushare 旧格式", re.I),
            re.compile(r"yyyymmdd", re.I),
        ),
        hint=(
            "❌ 你写的 SQL 里日期用了 `yyyymmdd` 无分隔符格式。\n"
            "🛠 `trade_date` 是 `YYYY-MM-DD` 带连字符字符串，严格按下面模板改：\n"
            "  ✅ 正确：`WHERE trade_date >= '2025-01-01' AND trade_date <= '2025-12-31'`\n"
            "  ❌ 错误：`WHERE trade_date BETWEEN '20250101' AND '20251231'`\n"
            "  ❌ 错误：`WHERE trade_date >= 20250101`（无引号无连字符）\n"
            "照模板改完**立即重新调用 `exc_sql`**。"
        ),
    ),
    HealPattern(
        key="exc_sql-empty-result",
        patterns=(
            re.compile(r"查询结果为空（0 行）。可能原因[:：]", re.I),
        ),
        hint=(
            "❌ 上一条 SQL 返回 0 行。tool 已列出常见原因（日期格式、未来日期、ts_code 格式），"
            "请**按提示修改 SQL 后重试一次**。\n"
            "如果修完仍 0 行，说明数据库里确实没有对应数据：**不要无限改 SQL**，直接告诉用户。"
        ),
    ),
)


# --------------------------------------------------------------------------- #
# Hook 实现
# --------------------------------------------------------------------------- #

_WATCH_TOOLS = {"exec", "exc_sql"}  # 目前保护 exec + exc_sql


class SelfHealHook(AgentHook):
    """检测 exec 工具失败并向 messages 注入修复提示。"""

    def __init__(self) -> None:
        super().__init__()
        # 每条错误指纹只注入一次 hint（不管具体命中哪条规则）
        self._handled_fps: set[str] = set()
        self._injected_rule_keys: set[str] = set()  # 便于外部观测/测试
        self._consecutive_failures: int = 0

    async def after_iteration(self, ctx: AgentHookContext) -> None:
        tool_calls = ctx.tool_calls or []
        tool_results = ctx.tool_results or []
        if not tool_calls:
            return

        failures: list[tuple[str, str]] = []  # (call_preview, result_text)
        successes_in_watch = 0
        for tc, tr in zip(tool_calls, tool_results):
            if tc.name not in _WATCH_TOOLS:
                continue
            result_text = _result_to_text(tr)
            if _looks_like_failure(result_text):
                failures.append((_call_preview(tc), result_text))
            else:
                successes_in_watch += 1

        if successes_in_watch and not failures:
            self._consecutive_failures = 0
            return

        if not failures:
            return

        self._consecutive_failures += len(failures)

        patterns_to_inject: list[HealPattern] = []
        for _call_preview_text, result_text in failures:
            fp = _fingerprint(result_text)
            if fp in self._handled_fps:
                continue  # 同一错误文本已贴过，不再贴
            for pat in _EXEC_ERROR_SIGNATURES:
                if any(p.search(result_text) for p in pat.patterns):
                    patterns_to_inject.append(pat)
                    self._handled_fps.add(fp)
                    self._injected_rule_keys.add(pat.key)
                    break  # 每条失败最多贴一个 hint，优先最具体的规则

        if not patterns_to_inject:
            if self._consecutive_failures >= 3:
                fp_last = _fingerprint(failures[-1][1])
                if fp_last not in self._handled_fps:
                    self._handled_fps.add(fp_last)
                    self._injected_rule_keys.add("fallback-stop-retry")
                    patterns_to_inject.append(HealPattern(
                        key="fallback-stop-retry",
                        patterns=(),
                        hint=(
                            "❌ 你已连续多次调用 `exec` 失败，但错误类型未识别。\n"
                            "🛠 停止重试，把最近一次的原始错误输出（整段 stderr / exit code）"
                            "告诉用户，并说明可能原因（参数越界？数据缺失？环境问题？），"
                            "让用户决定下一步。"
                        ),
                    ))
                else:
                    return
            else:
                return

        hint_body = "\n\n---\n\n".join(p.hint for p in patterns_to_inject)

        # 附带一小段"最近错误摘要"，让 LLM 对症下药
        last_call, last_result = failures[-1]
        diag_tail = _tail(last_result, 600)
        injection = (
            "[Self-Heal Hint — 系统自愈提示，不是用户输入]\n\n"
            f"{hint_body}\n\n"
            "📎 最近一次失败摘要：\n"
            f"```\n{last_call}\n```\n"
            "```\n"
            f"{diag_tail}\n"
            "```\n"
            "请在下一个 `exec` 调用里立刻采用上面的修复方案，不要重复刚才的错误命令。"
        )

        ctx.messages.append({"role": "user", "content": injection})


# --------------------------------------------------------------------------- #
# 辅助函数
# --------------------------------------------------------------------------- #

def _fingerprint(text: str) -> str:
    """生成一个稳定的 id 用于"同一错误"去重。取中间精华段 + 长度指纹。"""
    import hashlib

    normalized = re.sub(r"\s+", " ", text.strip())[:400]
    return hashlib.md5(normalized.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _result_to_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)


_FAILURE_MARKERS = (
    "Exit code: 1", "Exit code: 2", "Exit code: 3",
    "Exit code: 126", "Exit code: 127",
    "STDERR:", "Traceback",
    "can't open file", "Invalid argument",
    "ModuleNotFoundError", "FileNotFoundError",
    "错误：", "错误:",
)


def _looks_like_failure(text: str) -> bool:
    if not text:
        return False
    return any(marker in text for marker in _FAILURE_MARKERS)


def _call_preview(tc) -> str:
    try:
        args = json.dumps(tc.arguments, ensure_ascii=False)
    except Exception:
        args = str(tc.arguments)
    if len(args) > 400:
        args = args[:400] + "..."
    return f"{tc.name}({args})"


def _tail(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return "...(前略)...\n" + s[-n:]
