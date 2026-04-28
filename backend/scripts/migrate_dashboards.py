from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


async def _ensure_default_dashboards(session: AsyncSession) -> None:
    # create one dashboard per user if user has none
    await session.execute(
        text(
            """
            INSERT INTO dashboards (user_id, name)
            SELECT u.id, '我的大屏'
            FROM users u
            LEFT JOIN dashboards d ON d.user_id = u.id
            WHERE d.id IS NULL
            """
        )
    )


async def _backfill_widget_dashboard_id(session: AsyncSession) -> None:
    # backfill widgets.dashboard_id to user's newest dashboard if null
    await session.execute(
        text(
            """
            UPDATE dashboard_widgets w
            JOIN (
              SELECT user_id, MAX(id) AS dashboard_id
              FROM dashboards
              GROUP BY user_id
            ) d ON d.user_id = w.user_id
            SET w.dashboard_id = d.dashboard_id
            WHERE w.dashboard_id IS NULL
            """
        )
    )


async def main() -> None:
    engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_recycle=3600)
    SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as session:
        async with session.begin():
            await _ensure_default_dashboards(session)
            await _backfill_widget_dashboard_id(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

