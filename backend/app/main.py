from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.errors import ServerErrorMiddleware

from app.core.config import settings
from app.core.logging import setup_app_logging
from app.routers import auth, chat, conversations, dashboard, health


def create_app() -> FastAPI:
    setup_app_logging()

    app = FastAPI(title="stock-chat-bi", version="0.1.0")

    # 确保即使发生 500，也能带上 CORS 头（否则浏览器会把真实错误“伪装成 CORS”）
    # 必须放在 CORS 之前（中间件越早添加，越靠外层）。
    app.add_middleware(ServerErrorMiddleware)

    allow_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
    allow_credentials = True
    if "*" in allow_origins:
        # Starlette CORS doesn't allow wildcard origins with credentials.
        allow_credentials = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins or ["*"],
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(conversations.router, prefix="/api")
    app.include_router(chat.router, prefix="/api")
    app.include_router(dashboard.router, prefix="/api")

    return app


app = create_app()

