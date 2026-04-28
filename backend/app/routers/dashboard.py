from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.user import User
from app.models.widget import DashboardWidget
from app.schemas.dashboard import LayoutUpdateRequest, WidgetCreateRequest, WidgetItem, WidgetUpdateRequest


router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/widgets", response_model=list[WidgetItem])
async def list_widgets(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(DashboardWidget).where(DashboardWidget.user_id == user.id).order_by(DashboardWidget.id.asc()))
    items = res.scalars().all()
    return [
        WidgetItem(
            id=w.id,
            user_id=w.user_id,
            title=w.title,
            type=w.type,
            data=w.data,
            layout=w.layout,
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
        title=payload.title or ("图表" if payload.type == "chart" else "表格"),
        type=payload.type,
        data=payload.data,
        layout=payload.layout,
    )
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return WidgetItem(
        id=w.id,
        user_id=w.user_id,
        title=w.title,
        type=w.type,
        data=w.data,
        layout=w.layout,
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
    await db.commit()
    await db.refresh(w)
    return WidgetItem(
        id=w.id,
        user_id=w.user_id,
        title=w.title,
        type=w.type,
        data=w.data,
        layout=w.layout,
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

