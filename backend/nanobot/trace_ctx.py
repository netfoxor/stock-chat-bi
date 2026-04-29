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


def _merge_trace_row(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """同一 trace_key 的多段事件合并为一条，避免出现「已经结束但还剩一条永远在调用中」。"""
    old_meta = dict(old.get("meta") or {})
    new_meta = dict(new.get("meta") or {})
    merged_meta = {**old_meta, **new_meta}
    row: dict[str, Any] = {
        "kind": new.get("kind", old.get("kind")),
        "name": new.get("name", old.get("name")),
        "meta": merged_meta,
        "ts": new.get("ts") or old.get("ts"),
    }
    sa = old.get("started_at")
    sb = new.get("started_at")
    row["started_at"] = sb if sb is not None else sa
    ea = old.get("ended_at")
    eb = new.get("ended_at")
    row["ended_at"] = eb if eb is not None else ea
    if "input" in new:
        row["input"] = new["input"]
    elif "input" in old:
        row["input"] = old["input"]
    if "output" in new:
        row["output"] = new["output"]
    elif "output" in old:
        row["output"] = old["output"]
    # 有结束时间即视为该 span 已结束，避免 meta.phase 仍停在 start 导致前端误判「调用中」
    if row.get("ended_at") is not None:
        merged_meta["phase"] = "end"
    return row


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
    m = meta or {}
    tk = str(m.get("trace_key") or "")

    ev: dict[str, Any] = {
        "kind": kind,
        "name": name,
        "meta": m,
        "ts": _now_iso(),
    }
    if input is not None:
        ev["input"] = input
    if output is not None:
        ev["output"] = output
    if started_at is not None:
        ev["started_at"] = started_at
    if ended_at is not None:
        ev["ended_at"] = ended_at

    if tk:
        for i, row in enumerate(trace):
            if str((row.get("meta") or {}).get("trace_key") or "") == tk:
                merged = _merge_trace_row(row, ev)
                trace[i] = merged
                sink = _SINK.get()
                if sink:
                    try:
                        sink(merged)
                    except Exception:
                        pass
                return

    trace.append(ev)

    sink = _SINK.get()
    if sink:
        try:
            sink(ev)
        except Exception:
            pass

