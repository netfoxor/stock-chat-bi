from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, JSON, Text as SAText, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[str] = mapped_column(Enum("user", "assistant", name="message_role"), nullable=False)
    # MySQL TEXT 仅 ~64KB，ARIMA/indented ECharts 易截断致前端 JSON.parse 失败；生产库需 LONGTEXT：
    #   ALTER TABLE messages MODIFY COLUMN content LONGTEXT NOT NULL CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
    content: Mapped[str] = mapped_column(
        SAText().with_variant(LONGTEXT(), "mysql"),
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        Enum("text", "chart", "table", name="message_content_type"), nullable=False, server_default="text"
    )
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    conversation = relationship("Conversation", back_populates="messages")

