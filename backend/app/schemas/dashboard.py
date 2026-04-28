from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WidgetCreateRequest(BaseModel):
    title: str | None = None
    type: str  # 'chart' | 'table'
    data: dict[str, Any]
    layout: dict[str, Any]


class WidgetUpdateRequest(BaseModel):
    title: str | None = None
    layout: dict[str, Any] | None = None


class WidgetItem(BaseModel):
    id: int
    user_id: int
    title: str
    type: str
    data: dict[str, Any]
    layout: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class LayoutUpdateRequest(BaseModel):
    layout: list[dict[str, Any]]

