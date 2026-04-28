from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.conversation import ConversationCreateRequest, ConversationItem, ConversationUpdateRequest, MessageItem


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationItem])
async def list_conversations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()))
    items = res.scalars().all()
    return [
        ConversationItem(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in items
    ]


@router.post("", response_model=ConversationItem, status_code=201)
async def create_conversation(
    payload: ConversationCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    title = payload.title
    if not title:
        # 自动命名：新会话1/2/3...
        res = await db.execute(select(Conversation.title).where(Conversation.user_id == user.id))
        titles = [t for (t,) in res.all()]
        max_n = 0
        for t in titles:
            m = re.fullmatch(r"新会话(\d+)", str(t or "").strip())
            if m:
                try:
                    max_n = max(max_n, int(m.group(1)))
                except Exception:
                    pass
        title = f"新会话{max_n + 1}"

    conv = Conversation(user_id=user.id, title=title)
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ConversationItem(id=conv.id, title=conv.title, created_at=conv.created_at, updated_at=conv.updated_at)


@router.put("/{conversation_id}", response_model=ConversationItem)
async def update_conversation(
    conversation_id: int,
    payload: ConversationUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id))
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    conv.title = payload.title
    await db.commit()
    await db.refresh(conv)
    return ConversationItem(id=conv.id, title=conv.title, created_at=conv.created_at, updated_at=conv.updated_at)


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id))
    conv = res.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()
    return None


@router.get("/{conversation_id}/messages", response_model=list[MessageItem])
async def list_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id))
    if res.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    msg_res = await db.execute(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc()))
    msgs = msg_res.scalars().all()
    return [
        MessageItem(
            id=m.id,
            role=m.role,
            content=m.content,
            content_type=m.content_type,
            extra=m.extra,
            created_at=m.created_at,
        )
        for m in msgs
    ]

