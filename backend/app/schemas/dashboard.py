from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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


class SqlQueryRequest(BaseModel):
    """执行大屏 SELECT；可直接传 sql，或仅靠 widget_id 使用组件配置中的 sql。"""

    sql: str = ""
    widget_id: int | None = None
    limit: int = Field(default=3000, ge=1, le=10000)
    include_echarts: bool = False


class SqlQueryResponse(BaseModel):
    table: dict[str, Any]
    echarts: dict[str, Any] | None = None
    echarts_label: str | None = None

