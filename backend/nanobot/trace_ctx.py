from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any


_TRACE: ContextVar[list[dict[str, Any]] | None] = ContextVar("_TRACE", default=None)
_SINK: ContextVar[Any | None] = ContextVar("_SINK", default=None)  # callable(event_dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def start_trace(*, sink: Any | None = None) -> None:
    _TRACE.set([])
    _SINK.set(sink)


def get_trace() -> list[dict[str, Any]]:
    return list(_TRACE.get() or [])


def add_event(
    *,
    kind: str,
    name: str,
    input: Any | None = None,
    output: Any | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    trace = _TRACE.get()
    if trace is None:
        return
    ev = {
        "kind": kind,  # tool|skill|log
        "name": name,
        "input": input,
        "output": output,
        "started_at": started_at,
        "ended_at": ended_at,
        "meta": meta or {},
        "ts": _now_iso(),
    }
    trace.append(ev)

    sink = _SINK.get()
    if sink:
        try:
            sink(ev)
        except Exception:
            pass

