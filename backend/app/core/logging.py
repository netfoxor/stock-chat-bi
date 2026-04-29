"""应用日志：保证业务代码的 ERROR/异常栈能输出到 stderr（不依赖 uvicorn 根 logger 级别）。"""
from __future__ import annotations

import logging
import sys

_APP_LOGGER_NAME = "app"


def setup_app_logging() -> None:
    """为 `app` 命名空间挂一个 StreamHandler，默认 INFO；异常栈用 logger.exception 即可见。"""
    log = logging.getLogger(_APP_LOGGER_NAME)
    if getattr(log, "_stock_chat_bi_configured", False):
        return
    log.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    log.addHandler(h)
    log.propagate = False
    setattr(log, "_stock_chat_bi_configured", True)


def get_logger(name: str) -> logging.Logger:
    """子模块名如 `app.routers.dashboard`，统一挂在 app 下。"""
    if name.startswith(_APP_LOGGER_NAME + "."):
        return logging.getLogger(name)
    if name == _APP_LOGGER_NAME:
        return logging.getLogger(name)
    return logging.getLogger(f"{_APP_LOGGER_NAME}.{name}")
