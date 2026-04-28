from __future__ import annotations

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


router = APIRouter(prefix="/chat", tags=["chat"])

_FENCE_RE = re.compile(r"```(?P<lang>echarts|datatable)\n(?P<body>[\s\S]*?)\n```", re.IGNORECASE)


def _parse_assistant_content(text: str) -> tuple[str, str, dict[str, Any] | None]:
    """
    识别 nanobot 输出里的 ```echarts / ```datatable 代码块。
    - content: 原始 markdown（先不移除代码块，前端也可直接解析）
    - content_type: text|chart|table
    - extra: 解析出的 JSON
    """
    m = _FENCE_RE.search(text)
    if not m:
        return text, "text", None

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

        answer = await ask(payload.message, session_key=session_key)

        # 按块流式输出（非 token 级，但足够驱动打字机效果）
        chunk_size = 120
        for i in range(0, len(answer), chunk_size):
            chunk = answer[i : i + chunk_size]
            yield _sse(json.dumps({"type": "delta", "content": chunk}, ensure_ascii=False)).encode("utf-8")

        content, content_type, extra = _parse_assistant_content(answer)
        assistant_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=content or "",
            content_type=content_type,
            extra=extra,
        )
        db.add(assistant_msg)
        await db.commit()

        yield _sse(json.dumps({"type": "done"}, ensure_ascii=False)).encode("utf-8")

    return StreamingResponse(gen(), media_type="text/event-stream")

