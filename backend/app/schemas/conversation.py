from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ConversationCreateRequest(BaseModel):
    title: str | None = None


class ConversationItem(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class MessageItem(BaseModel):
    id: int
    role: str
    content: str
    content_type: str
    extra: dict | None
    created_at: datetime

