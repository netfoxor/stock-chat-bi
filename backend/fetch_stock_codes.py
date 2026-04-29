"""
使用 AkShare 获取沪深京 A 股全部代码与简称，写入 MySQL 表 stock_code_list。

数据库：从 **backend/.env** 读取 **DATABASE_URL**（与 FastAPI、fetch_stock_prices.py 一致）。

运行（在 backend 目录下）::

  cd backend
  python fetch_stock_codes.py

增量逻辑（可重复执行）：
- 拉取 AkShare 全市场列表后与库中已有 **ts_code** 比对；
- **仅 INSERT 新增**的股票，不会对整表 TRUNCATE，已存在的代码不会重复写入；
- 首次运行等同于全量导入。

依赖：pip install akshare sqlalchemy pymysql pandas pydantic-settings
"""

from __future__ import annotations

import sys
from pathlib import Path

import akshare as ak
import pandas as pd
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent


class _Env(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(alias="DATABASE_URL")


def _database_url_to_sync(url: str) -> str:
    if "+aiomysql" in url:
        return url.replace("+aiomysql", "+pymysql", 1)
    if "+asyncmy" in url:
        return url.replace("+asyncmy", "+pymysql", 1)
    return url


def _mysql_engine(sync_url: str):
    return create_engine(sync_url, pool_pre_ping=True, future=True)


def to_ts_code(code: str) -> str:
    """将 AkShare 的 6 位 code 规范为与 Tushare 类似的 ts_code（.SH/.SZ/.BJ）。"""
    s = str(code).strip().upper()
    if "." in s:
        return s
    d = "".join(ch for ch in s if ch.isdigit())
    if len(d) < 6:
        d = d.zfill(6)
    elif len(d) > 6:
        d = d[-6:]
    p3 = d[:3]
    if p3 in ("600", "601", "603", "605") or p3 in ("688", "689"):
        return f"{d}.SH"
    if d.startswith("900"):
        return f"{d}.SH"
    if p3 in ("000", "001", "002", "003", "300", "301") or d.startswith("200"):
        return f"{d}.SZ"
    if d.startswith("920") or d.startswith("43") or d.startswith("83"):
        return f"{d}.BJ"
    if d.startswith("87") or d.startswith("88") or d.startswith("92"):
        return f"{d}.BJ"
    if d[0] in ("4", "8"):
        return f"{d}.BJ"
    if d[0] == "6":
        return f"{d}.SH"
    if d[0] in ("0", "1", "2", "3"):
        return f"{d}.SZ"
    return f"{d}.SZ"


def fetch_all_codes() -> pd.DataFrame:
    df = ak.stock_info_a_code_name()
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.rename(columns={"code": "ak_code", "name": "stock_name"}).copy()
    out["ak_code"] = out["ak_code"].astype(str).str.strip()
    out["ts_code"] = out["ak_code"].map(to_ts_code)
    out = out.drop_duplicates(subset=["ts_code"], keep="first")
    return out[["ts_code", "ak_code", "stock_name"]]


def ensure_stock_code_list_table(engine) -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS stock_code_list (
        ts_code VARCHAR(20) NOT NULL,
        ak_code VARCHAR(16) NOT NULL,
        stock_name VARCHAR(128) NOT NULL,
        update_time DATETIME NULL DEFAULT NULL,
        PRIMARY KEY (ts_code),
        KEY idx_stock_code_list_ak (ak_code),
        KEY idx_stock_code_list_name (stock_name(64))
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))


def save_stock_codes_incremental(df: pd.DataFrame, engine) -> tuple[int, int]:
    """返回 (本次新增条数, 已存在跳过条数)。"""
    ensure_stock_code_list_table(engine)
    with engine.connect() as conn:
        raw = pd.read_sql(text("SELECT ts_code FROM stock_code_list"), conn)
    existing = set(raw["ts_code"].astype(str).str.strip()) if not raw.empty else set()
    new_df = df[~df["ts_code"].astype(str).isin(existing)].copy()
    n_skip = len(df) - len(new_df)

    if new_df.empty:
        return 0, n_skip

    new_df.to_sql(
        "stock_code_list",
        engine,
        if_exists="append",
        index=False,
        chunksize=2000,
        method="multi",
    )
    return len(new_df), n_skip


def main() -> None:
    try:
        env = _Env()
    except Exception as e:
        print(f"读取 {ROOT}/.env 失败（需 DATABASE_URL）: {e}", file=sys.stderr)
        sys.exit(1)

    sync_url = _database_url_to_sync(env.database_url)
    engine = _mysql_engine(sync_url)

    try:
        df = fetch_all_codes()
    except Exception as e:
        print(f"错误: 拉取股票列表失败: {e}", file=sys.stderr)
        sys.exit(1)

    if df.empty:
        print("错误: 未获取到任何股票代码", file=sys.stderr)
        sys.exit(1)

    n_new, n_skip = save_stock_codes_incremental(df, engine)

    uinfo = make_url(sync_url)
    host_disp = str(uinfo.host or "")
    db_disp = str(uinfo.database or "")

    with engine.connect() as conn:
        total_in_db = int(
            pd.read_sql(text("SELECT COUNT(*) AS c FROM stock_code_list"), conn).iloc[0]["c"],
        )

    print(
        f"MySQL {host_disp}/{db_disp}.stock_code_list：AkShare 本轮 {len(df)} 支，"
        f"新增 {n_new} 支，跳过已存在 {n_skip} 支；表中合计 {total_in_db} 支。"
    )


if __name__ == "__main__":
    main()
