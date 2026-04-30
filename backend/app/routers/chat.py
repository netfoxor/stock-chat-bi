from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import ChatStreamRequest
from app.services.nanobot_service import ask

# nanobot ExecTool 会在 stdout 末尾追加「Exit code: N」，污染 Markdown 并像多出一截正文
_EXEC_EXIT_TAIL_RE = re.compile(r"\n+Exit code:\s*\d+\s*\Z", re.IGNORECASE)


def strip_exec_stdout_trailer(text: str) -> str:
    if not text:
        return text
    return _EXEC_EXIT_TAIL_RE.sub("", text.rstrip())


router = APIRouter(prefix="/chat", tags=["chat"])

_SQL_FENCE_DETECT = re.compile(r"```sql\s*\n", re.IGNORECASE)
_VIZ_FENCE_START = re.compile(r"```(?:echarts|datatable)\b", re.IGNORECASE)

# 与前端 extractSpecialBlocks 对齐：围栏可不在行首；闭合行为「换行 + ```」
_VIZ_FENCE_RE = re.compile(
    r"(?:^|\r?\n)```\s*(?P<lang>echarts|datatable)\s*\r?\n(?P<body>[\s\S]*?)\r?\n\s*```(?:\r?\n|$)",
    re.IGNORECASE,
)
_MD_TABLE_RE = re.compile(
    r"(?P<table>(?:^\|.*\|\s*$\n){2,}(?:^\|.*\|\s*$\n?)*)",
    re.MULTILINE,
)


def _extract_viz_blocks(text: str) -> dict[str, Any]:
    """提取所有 echarts / datatable 围栏；供 extra.viz，避免正文截断或前端解析失败。"""
    out: dict[str, Any] = {}
    for m in _VIZ_FENCE_RE.finditer(text or ""):
        lang = (m.group("lang") or "").lower()
        body = (m.group("body") or "").strip()
        try:
            data = json.loads(body)
        except Exception:
            continue
        if lang == "echarts":
            out["echarts"] = data
        elif lang == "datatable":
            out["datatable"] = data
    return out


def _parse_assistant_content(text: str) -> tuple[str, str, dict[str, Any] | None]:
    """
    识别 nanobot 输出里的 ```echarts / ```datatable 代码块。
    - content: 原始 markdown（先不移除代码块，前端也可直接解析）
    - content_type: text|chart|table
    - extra: 解析出的 JSON
    """
    m = _VIZ_FENCE_RE.search(text)
    if not m:
        # 兜底：nanobot 可能输出纯 Markdown 表格/描述（没有 ```datatable/```echarts）
        patched = _augment_markdown_with_blocks(text)
        m2 = _VIZ_FENCE_RE.search(patched)
        if not m2:
            return patched, "text", None
        # 用增强后的内容继续解析
        text = patched
        m = m2

    lang = m.group("lang").lower()
    body = m.group("body").strip()
    try:
        data = json.loads(body)
    except Exception:
        # JSON 不合法时仍按 text 保存，避免接口 500
        return text, "text", None

    if lang == "echarts":
        return text, "chart", data
    return text, "table", data


def _augment_markdown_with_blocks(text: str) -> str:
    """
    若未包含 ```echarts/```datatable，则尝试从 Markdown pipe table 推断 datatable，
    并在可行时从表格推断一个简单的 ECharts option（折线或 K 线）。
    """
    if _VIZ_FENCE_RE.search(text):
        return text

    # 1) Markdown pipe table
    m = _MD_TABLE_RE.search(text + "\n")
    if m:
        table_md = m.group("table").strip()
        dt = _md_table_to_datatable(table_md)
        if dt:
            blocks: list[str] = []
            echarts = _datatable_to_echarts(dt)
            if echarts:
                blocks.append("```echarts\n" + json.dumps(echarts, ensure_ascii=False) + "\n```")
            blocks.append("```datatable\n" + json.dumps(dt, ensure_ascii=False) + "\n```")
            return text.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"

    # 2) 尝试从文本中提取 JSON（常见：LLM 直接吐 list[dict] / dict 序列）
    if dt := _try_extract_json_datatable(text):
        blocks: list[str] = []
        echarts = _datatable_to_echarts(dt)
        if echarts:
            blocks.append("```echarts\n" + json.dumps(echarts, ensure_ascii=False) + "\n```")
        blocks.append("```datatable\n" + json.dumps(dt, ensure_ascii=False) + "\n```")
        return text.rstrip() + "\n\n" + "\n\n".join(blocks) + "\n"

    return text


# 一轮里出现多条不同 SQL 时，不回填 ```sql，仅追加说明（大屏需与单条查询一一对应）
_MULTI_EXC_SQL_HINT = (
    "\n\n---\n\n"
    "**提示**：本轮执行了**多条不同的 SQL**。添加到大屏时，图表/表格只与**一条**查询绑定，"
    "暂不支持同一轮里多次查询。请**新开对话**，每轮只问一个数据问题；或把多个指标合并为**一条** SQL（子查询、JOIN、WITH 等）。\n"
)


def _exc_sql_chronological_inputs(trace: list[Any] | None) -> list[str]:
    """工具 exc_sql 的 input.sql_input，按 trace 出现顺序（含重试导致的重复）。"""
    out: list[str] = []
    for row in trace or []:
        if not isinstance(row, dict) or row.get("name") != "exc_sql":
            continue
        inp = row.get("input")
        if not isinstance(inp, dict):
            continue
        sql = inp.get("sql_input")
        if not isinstance(sql, str) or not sql.strip():
            continue
        out.append(sql.strip())
    return out


def merge_sql_from_exc_sql_trace(text: str, trace: list[Any] | None) -> str:
    """
    大屏「添加」依赖 ```sql 块；正文可能不含 fence 时从 trace 回填。
    仅当本轮仅有一条语义上的 SQL（trace 中去重后为 1）时回填，且使用**最后一次** exc_sql 的原文。
    若存在多条不同 SQL，不回填并追加说明，避免图表与查询错配。
    """
    body = text or ""
    if _SQL_FENCE_DETECT.search(body):
        return body
    chronological = _exc_sql_chronological_inputs(trace)
    if not chronological:
        return body
    if len(set(chronological)) > 1:
        trimmed = body.rstrip()
        hint = _MULTI_EXC_SQL_HINT
        return (trimmed + hint) if trimmed else hint.lstrip()

    sql = chronological[-1]
    blocks = f"```sql\n{sql}\n```\n\n"
    m = _VIZ_FENCE_START.search(body)
    if m:
        idx = m.start()
        return body[:idx] + blocks + body[idx:]
    trimmed = body.rstrip()
    if trimmed:
        return trimmed + "\n\n" + blocks.rstrip() + "\n"
    return blocks.rstrip() + "\n"


def _md_table_to_datatable(table_md: str) -> dict[str, Any] | None:
    lines = [ln.strip() for ln in table_md.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    # 第二行是分隔符：| --- | --- |
    sep = lines[1]
    if "-" not in sep:
        return None

    columns = [{"title": h, "dataIndex": h} for h in header]
    data: list[dict[str, Any]] = []
    for ln in lines[2:]:
        if not ln.startswith("|") or not ln.endswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) != len(header):
            continue
        row: dict[str, Any] = {}
        for h, v in zip(header, cells, strict=False):
            row[h] = _coerce_cell(v)
        data.append(row)
    return {"columns": columns, "data": data}


def _coerce_cell(v: str) -> Any:
    s = v.replace(",", "").strip()
    if s in ("", "-", "—"):
        return None
    try:
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        return v.strip()


def _datatable_to_echarts(dt: dict[str, Any]) -> dict[str, Any] | None:
    cols = [c.get("dataIndex") for c in dt.get("columns", []) if isinstance(c, dict)]
    rows = dt.get("data", [])
    if not isinstance(rows, list) or not rows:
        return None

    # 识别日期列
    date_key = None
    for cand in ("日期", "trade_date", "date"):
        if cand in cols:
            date_key = cand
            break
    if not date_key:
        return None

    # candlestick: open/high/low/close
    open_k = next((c for c in cols if c in ("open", "开盘价")), None)
    high_k = next((c for c in cols if c in ("high", "最高价")), None)
    low_k = next((c for c in cols if c in ("low", "最低价")), None)
    close_k = next((c for c in cols if c in ("close", "收盘价")), None)

    x = [r.get(date_key) for r in rows]
    if open_k and high_k and low_k and close_k:
        series_data = [[r.get(open_k), r.get(close_k), r.get(low_k), r.get(high_k)] for r in rows]
        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": x},
            "yAxis": {"type": "value", "scale": True},
            "series": [{"type": "candlestick", "name": "K线", "data": series_data}],
        }

    # line: close
    if close_k:
        y = [r.get(close_k) for r in rows]
        return {
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": x},
            "yAxis": {"type": "value", "scale": True},
            "series": [{"type": "line", "name": "收盘价", "data": y, "smooth": True}],
        }

    return None


def _try_extract_json_datatable(text: str) -> dict[str, Any] | None:
    """
    兜底：识别文本里的 JSON 数组（或多个对象拼接），转成 datatable 结构。
    用于修复“AI 回了大段 JSON 文本但没包 ```datatable” 的情况。
    """
    s = text.strip()
    if not s:
        return None

    candidates: list[str] = []
    # JSON array
    m = re.search(r"\[\s*\{[\s\S]*?\}\s*\]", s)
    if m:
        candidates.append(m.group(0))
    # concatenated objects: {...},{...},{...}
    m2 = re.search(r"(\{[\s\S]*?\})(\s*,\s*\{[\s\S]*?\}){1,}", s)
    if m2:
        candidates.append("[" + m2.group(0) + "]")

    for cand in candidates:
        try:
            obj = json.loads(cand)
        except Exception:
            continue
        if isinstance(obj, dict):
            rows = [obj]
        elif isinstance(obj, list):
            rows = [r for r in obj if isinstance(r, dict)]
        else:
            continue
        if not rows:
            continue
        # columns from union keys (keep stable order by first row then extras)
        keys: list[str] = list(rows[0].keys())
        for r in rows[1:]:
            for k in r.keys():
                if k not in keys:
                    keys.append(k)
        columns = [{"title": k, "dataIndex": k} for k in keys]
        return {"columns": columns, "data": rows}

    return None


def _sse(data: str, event: str | None = None) -> str:
    # 只发 data，保持前端解析简单；需要事件名时再扩展
    if event:
        return f"event: {event}\ndata: {data}\n\n"
    return f"data: {data}\n\n"


@router.post("/stream")
async def chat_stream(
    payload: ChatStreamRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # conversation ownership check
    res = await db.execute(
        select(Conversation).where(Conversation.id == payload.conversation_id, Conversation.user_id == user.id)
    )
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # persist user message
    user_msg = Message(conversation_id=conv.id, role="user", content=payload.message, content_type="text", extra=None)
    db.add(user_msg)
    await db.commit()

    session_key = f"user:{user.id}:conv:{conv.id}"

    async def gen() -> AsyncGenerator[bytes, None]:
        # 立即回一个小片段，提升“首字符 <2s”体感（即便 LLM 还没回来）
        yield _sse(json.dumps({"type": "status", "message": "thinking"}, ensure_ascii=False)).encode("utf-8")

        trace_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def _sink(ev: dict[str, Any]) -> None:
            try:
                trace_queue.put_nowait(ev)
            except Exception:
                pass

        ask_task = asyncio.create_task(ask(payload.message, session_key=session_key, trace_sink=_sink))

        # 在 LLM 运行期间，把工具/skill 事件实时推给前端
        while not ask_task.done() or not trace_queue.empty():
            try:
                ev = await asyncio.wait_for(trace_queue.get(), timeout=0.25)
                yield _sse(json.dumps({"type": "trace", "event": ev}, ensure_ascii=False)).encode("utf-8")
            except asyncio.TimeoutError:
                pass

        answer, trace = await ask_task
        answer = merge_sql_from_exc_sql_trace(answer, trace)
        answer = strip_exec_stdout_trailer(answer)

        # 按块流式输出（非 token 级，但足够驱动打字机效果）
        chunk_size = 120
        for i in range(0, len(answer), chunk_size):
            chunk = answer[i : i + chunk_size]
            yield _sse(json.dumps({"type": "delta", "content": chunk}, ensure_ascii=False)).encode("utf-8")

        content, content_type, extra = _parse_assistant_content(answer)
        merged_extra: dict[str, Any] = {}
        viz = _extract_viz_blocks(answer)
        if trace:
            merged_extra["trace"] = trace
        if extra is not None:
            merged_extra["parsed"] = extra
        if viz:
            merged_extra["viz"] = viz
        assistant_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=content or "",
            content_type=content_type,
            extra=merged_extra or None,
        )
        db.add(assistant_msg)
        await db.commit()

        # 流式期间可能漏合并某条 trace SSE，done 时附带完整 trace 校正前端 Timeline
        yield _sse(json.dumps({"type": "done", "trace": trace}, ensure_ascii=False)).encode("utf-8")

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

