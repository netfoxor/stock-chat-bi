#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chainlit 聊天历史持久化（SQLite 版）

Chainlit 的侧栏历史列表、历史搜索、删除单条历史、按反馈筛选等 UI 能力都由
官方 Data Layer 驱动。原生 SQLAlchemyDataLayer 默认面向 Postgres，但只要喂
一个 `sqlite+aiosqlite://...` 连接串 + 我们自己建好对应的表结构，SQLite
也能跑，好处是零依赖、单文件、迁移方便。

本模块只做两件事：
  1. 启动时幂等创建 users / threads / steps / elements / feedbacks 五张表
  2. 提供 `build_data_layer()` 给 app_chainlit.py 注册给 @cl.data_layer

数据库文件默认落在 memory/chainlit.db（与 nanobot 会话记忆同目录，便于备份）。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from chainlit.data.sql_alchemy import SQLAlchemyDataLayer

# 表结构。字段与 Chainlit `SQLAlchemyDataLayer` 里 INSERT/SELECT 的列名严格一致：
#   - UUID / 时间戳 → TEXT（Chainlit 上层已序列化为字符串）
#   - JSONB / TEXT[] → TEXT（Chainlit 用 json.dumps 存，读的时候自己 json.loads）
#   - BOOL → INTEGER（SQLite 没有 bool 类型，0/1 即可）
# 双引号括住列名，避免 SQLite 保留字 "end" / "start" 报错。
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    "id" TEXT PRIMARY KEY,
    "identifier" TEXT NOT NULL UNIQUE,
    "metadata" TEXT NOT NULL,
    "createdAt" TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    "id" TEXT PRIMARY KEY,
    "createdAt" TEXT,
    "name" TEXT,
    "userId" TEXT,
    "userIdentifier" TEXT,
    "tags" TEXT,
    "metadata" TEXT,
    FOREIGN KEY ("userId") REFERENCES users ("id") ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS steps (
    "id" TEXT PRIMARY KEY,
    "name" TEXT NOT NULL,
    "type" TEXT NOT NULL,
    "threadId" TEXT NOT NULL,
    "parentId" TEXT,
    "streaming" INTEGER NOT NULL,
    "waitForAnswer" INTEGER,
    "isError" INTEGER,
    "metadata" TEXT,
    "tags" TEXT,
    "input" TEXT,
    "output" TEXT,
    "createdAt" TEXT,
    "command" TEXT,
    "start" TEXT,
    "end" TEXT,
    "generation" TEXT,
    "showInput" TEXT,
    "language" TEXT,
    "indent" INTEGER,
    "defaultOpen" INTEGER,
    FOREIGN KEY ("threadId") REFERENCES threads ("id") ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS elements (
    "id" TEXT PRIMARY KEY,
    "threadId" TEXT,
    "type" TEXT,
    "url" TEXT,
    "chainlitKey" TEXT,
    "name" TEXT NOT NULL,
    "display" TEXT,
    "objectKey" TEXT,
    "size" TEXT,
    "page" INTEGER,
    "language" TEXT,
    "forId" TEXT,
    "mime" TEXT,
    "props" TEXT,
    FOREIGN KEY ("threadId") REFERENCES threads ("id") ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS feedbacks (
    "id" TEXT PRIMARY KEY,
    "forId" TEXT NOT NULL,
    "threadId" TEXT NOT NULL,
    "value" INTEGER NOT NULL,
    "comment" TEXT,
    FOREIGN KEY ("threadId") REFERENCES threads ("id") ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_threads_user ON threads ("userId");
CREATE INDEX IF NOT EXISTS idx_steps_thread ON steps ("threadId");
CREATE INDEX IF NOT EXISTS idx_elements_thread ON elements ("threadId");
CREATE INDEX IF NOT EXISTS idx_feedbacks_thread ON feedbacks ("threadId");
"""


def _default_db_path() -> Path:
    """允许用 CHAINLIT_DB_PATH 覆盖；默认 nanobot/memory/chainlit.db。"""
    override = os.environ.get("CHAINLIT_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve().parent
    return here / "memory" / "chainlit.db"


def init_schema(db_path: Path) -> None:
    """幂等建表。用同步 sqlite3 就够了，纯启动期一次性操作，不必走异步。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()


def build_data_layer() -> SQLAlchemyDataLayer:
    """
    构造 Chainlit Data Layer。

    统一改为 MySQL：
      - 优先读取 CHAINLIT_DATABASE_URL
      - 其次复用 DATABASE_URL

    兼容迁移期：若未配置 MySQL 连接串，则回退到旧的 SQLite（memory/chainlit.db）。
    """
    mysql_url = (os.environ.get("CHAINLIT_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()
    if mysql_url:
        return SQLAlchemyDataLayer(conninfo=mysql_url)

    db_path = _default_db_path()
    init_schema(db_path)
    conninfo = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    return SQLAlchemyDataLayer(conninfo=conninfo)
