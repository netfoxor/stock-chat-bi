from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.dashboard import Dashboard
from app.models.user import User
from app.models.widget import DashboardWidget
from app.core.config import settings
from app.schemas.dashboard import (
    DashboardCreateRequest,
    DashboardItem,
    DashboardUpdateRequest,
    LayoutUpdateRequest,
    SqlQueryRequest,
    SqlQueryResponse,
    WidgetCreateRequest,
    WidgetItem,
    WidgetUpdateRequest,
)
from app.services.dashboard_query import run_dashboard_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.post("/query", response_model=SqlQueryResponse)
async def dashboard_sql_query(
    payload: SqlQueryRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    登录用户执行只读 SELECT，返回表格数据；`include_echarts=true` 时附带 `stock_core.build_stock_echart`
    生成的 option（供图表组件刷新）。
    """
    sql = payload.sql.strip()
    if payload.widget_id is not None:
        res = await db.execute(
            select(DashboardWidget).where(
                DashboardWidget.id == payload.widget_id,
                DashboardWidget.user_id == user.id,
            )
        )
        w = res.scalar_one_or_none()
        if w is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
        if not sql:
            cfg = w.config or {}
            sql = str(cfg.get("sql") or "").strip()

    if not sql:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请填写 sql，或提供 widget_id 以使用组件内已保存的 SQL",
        )

    try:
        return await asyncio.to_thread(
            run_dashboard_query,
            sql=sql,
            limit=payload.limit,
            include_echarts=payload.include_echarts,
            database_url=settings.database_url,
        )
    except ValueError as e:
        logger.warning(
            "POST /dashboard/query 参数/校验失败 user_id=%s widget_id=%s detail=%s sql_preview=%r",
            user.id,
            payload.widget_id,
            e,
            (sql[:400] + "…") if len(sql) > 400 else sql,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception:
        logger.exception(
            "POST /dashboard/query 未处理异常 user_id=%s widget_id=%s include_echarts=%s sql_preview=%r",
            user.id,
            payload.widget_id,
            payload.include_echarts,
            (sql[:400] + "…") if len(sql) > 400 else sql,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="查询失败，请稍后重试或服务端日志中查看详情",
        ) from None


@router.get("/dashboards", response_model=list[DashboardItem])
async def list_dashboards(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Dashboard).where(Dashboard.user_id == user.id).order_by(Dashboard.updated_at.desc()))
    items = res.scalars().all()
    return [
        DashboardItem(
            id=d.id,
            user_id=d.user_id,
            name=d.name,
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in items
    ]


@router.post("/dashboards", response_model=DashboardItem, status_code=201)
async def create_dashboard(
    payload: DashboardCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    d = Dashboard(user_id=user.id, name=payload.name or "我的大屏")
    db.add(d)
    await db.commit()
    await db.refresh(d)
    return DashboardItem(id=d.id, user_id=d.user_id, name=d.name, created_at=d.created_at, updated_at=d.updated_at)


@router.put("/dashboards/{dashboard_id}", response_model=DashboardItem)
async def rename_dashboard(
    dashboard_id: int,
    payload: DashboardUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.user_id == user.id))
    d = res.scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")
    d.name = payload.name
    await db.commit()
    await db.refresh(d)
    return DashboardItem(id=d.id, user_id=d.user_id, name=d.name, created_at=d.created_at, updated_at=d.updated_at)


@router.delete("/dashboards/{dashboard_id}", status_code=204)
async def delete_dashboard(dashboard_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Dashboard).where(Dashboard.id == dashboard_id, Dashboard.user_id == user.id))
    d = res.scalar_one_or_none()
    if d is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not found")
    await db.delete(d)
    await db.commit()
    return None


@router.get("/widgets", response_model=list[WidgetItem])
async def list_widgets(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    dashboard_id: int | None = Query(default=None),
):
    q = select(DashboardWidget).where(DashboardWidget.user_id == user.id)
    if dashboard_id is not None:
        q = q.where(DashboardWidget.dashboard_id == dashboard_id)
    res = await db.execute(q.order_by(DashboardWidget.id.asc()))
    items = res.scalars().all()
    return [
        WidgetItem(
            id=w.id,
            user_id=w.user_id,
            dashboard_id=w.dashboard_id,
            title=w.title,
            type=w.type,
            data=w.data,
            layout=w.layout,
            config=w.config,
            created_at=w.created_at,
            updated_at=w.updated_at,
        )
        for w in items
    ]


@router.post("/widgets", response_model=WidgetItem, status_code=201)
async def create_widget(
    payload: WidgetCreateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    w = DashboardWidget(
        user_id=user.id,
        dashboard_id=payload.dashboard_id,
        title=payload.title or ("图表" if payload.type == "chart" else "表格"),
        type=payload.type,
        data=payload.data,
        layout=payload.layout,
        config=payload.config,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return WidgetItem(
        id=w.id,
        user_id=w.user_id,
        dashboard_id=w.dashboard_id,
        title=w.title,
        type=w.type,
        data=w.data,
        layout=w.layout,
        config=w.config,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


@router.put("/widgets/{widget_id}", response_model=WidgetItem)
async def update_widget(
    widget_id: int,
    payload: WidgetUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id, DashboardWidget.user_id == user.id))
    w = res.scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    if payload.title is not None:
        w.title = payload.title
    if payload.layout is not None:
        w.layout = payload.layout
    if payload.data is not None:
        w.data = payload.data
    if payload.config is not None:
        w.config = payload.config
    await db.commit()
    await db.refresh(w)
    return WidgetItem(
        id=w.id,
        user_id=w.user_id,
        dashboard_id=w.dashboard_id,
        title=w.title,
        type=w.type,
        data=w.data,
        layout=w.layout,
        config=w.config,
        created_at=w.created_at,
        updated_at=w.updated_at,
    )


@router.delete("/widgets/{widget_id}", status_code=204)
async def delete_widget(widget_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(DashboardWidget).where(DashboardWidget.id == widget_id, DashboardWidget.user_id == user.id))
    w = res.scalar_one_or_none()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Widget not found")
    await db.delete(w)
    await db.commit()
    return None


@router.put("/layout", status_code=204)
async def update_layout(
    payload: LayoutUpdateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    传入 react-grid-layout 的 layout 数组（每项需包含 i / x / y / w / h）。
    这里按 id 匹配：i 应为 widget_id 字符串。
    """
    # 取出用户所有 widget，做一次映射
    res = await db.execute(select(DashboardWidget).where(DashboardWidget.user_id == user.id))
    widgets = {str(w.id): w for w in res.scalars().all()}

    for item in payload.layout:
        wid = str(item.get("i", ""))
        if wid in widgets:
            widgets[wid].layout = item

    await db.commit()
    return None

