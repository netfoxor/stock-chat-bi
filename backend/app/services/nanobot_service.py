from __future__ import annotations

import asyncio
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any


def _ensure_nanobot_on_path() -> None:
    """
    兼容当前仓库结构：nanobot 代码位于 `backend/nanobot/`，且内部以 `import stock_core`
    形式导入同目录模块。
    """
    here = Path(__file__).resolve()
    nanobot_dir = (here.parents[2] / "nanobot").resolve()  # backend/nanobot
    if nanobot_dir.is_dir() and str(nanobot_dir) not in sys.path:
        sys.path.insert(0, str(nanobot_dir))


def _load_env_files() -> None:
    """
    uvicorn/fastapi 不会自动读取 .env；但 nanobot 直接用 os.environ 取 DASHSCOPE_API_KEY。
    这里显式加载 backend/.env（以及可选的 backend/nanobot/.env），避免出现“明明写了 .env 但取不到”的情况。
    """
    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    here = Path(__file__).resolve()
    backend_dir = here.parents[2]
    load_dotenv(backend_dir / ".env", override=False)
    load_dotenv(backend_dir / "nanobot" / ".env", override=False)


@lru_cache(maxsize=1)
def _get_bot() -> Any:
    _ensure_nanobot_on_path()
    _load_env_files()

    # 确保 MySQL 连接串能被 stock_core 读取
    # 优先 STOCK_DATABASE_URL，其次 DATABASE_URL（两者由上层环境注入）
    if not os.environ.get("STOCK_DATABASE_URL") and os.environ.get("DATABASE_URL"):
        os.environ["STOCK_DATABASE_URL"] = os.environ["DATABASE_URL"]

    from stock_bot import build_bot  # type: ignore

    return build_bot()


async def ask(question: str, session_key: str, *, trace_sink: Any | None = None):
    bot = _get_bot()
    # 启用程序级自愈（主要约束 exec / exc_sql 常见失败模式）
    from self_heal_hook import SelfHealHook  # type: ignore
    import trace_ctx  # type: ignore

    trace_ctx.start_trace(sink=trace_sink)
    result = await bot.run(question, session_key=session_key, hooks=[SelfHealHook()])
    content = (result.content or "").strip()
    trace = trace_ctx.get_trace()
    return content, trace


async def ask_sync_bridge(question: str, session_key: str) -> str:
    """
    某些运行环境下路由可能在不同 loop 上工作，这里提供一个兜底桥接：
    - 若当前已有运行中的 event loop，直接 await ask()
    - 否则用 asyncio.run() 跑一次
    """
    try:
        asyncio.get_running_loop()
        return await ask(question, session_key)
    except RuntimeError:
        return asyncio.run(ask(question, session_key))

