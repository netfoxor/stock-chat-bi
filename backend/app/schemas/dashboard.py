from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WidgetCreateRequest(BaseModel):
    title: str | None = None
    type: str  # 'chart' | 'table'
    data: dict[str, Any]
    layout: dict[str, Any]
    dashboard_id: int | None = None
    config: dict[str, Any] | None = None


class WidgetUpdateRequest(BaseModel):
    title: str | None = None
    layout: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


class WidgetItem(BaseModel):
    id: int
    user_id: int
    dashboard_id: int | None
    title: str
    type: str
    data: dict[str, Any]
    layout: dict[str, Any]
    config: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class DashboardCreateRequest(BaseModel):
    name: str | None = None


class DashboardUpdateRequest(BaseModel):
    name: str


class DashboardItem(BaseModel):
    id: int
    user_id: int
    name: str
    created_at: datetime
    updated_at: datetime


class LayoutUpdateRequest(BaseModel):
    layout: list[dict[str, Any]]

