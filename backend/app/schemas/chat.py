from __future__ import annotations

from pydantic import BaseModel, Field


class ChatStreamRequest(BaseModel):
    conversation_id: int
    message: str = Field(min_length=1)
    image: str | None = None  # base64 (optional)

