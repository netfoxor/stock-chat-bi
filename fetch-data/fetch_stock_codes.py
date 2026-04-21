"""
使用 AkShare 获取沪深京 A 股全部代码与简称，写入 MySQL。
连接参数与 fetch_stock_prices.py 一致（环境变量 MYSQL_*，默认值相同）。

依赖：pip install akshare sqlalchemy pymysql
"""

from __future__ import annotations

import os
import sys

import akshare as ak
import pandas as pd
from sqlalchemy import text

from fetch_stock_prices import _mysql_engine


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


def save_stock_codes(df: pd.DataFrame) -> None:
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
    engine = _mysql_engine()
    with engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text("TRUNCATE TABLE stock_code_list"))

    df.to_sql(
        "stock_code_list",
        engine,
        if_exists="append",
        index=False,
        chunksize=2000,
        method="multi",
    )


def main() -> None:
    try:
        df = fetch_all_codes()
    except Exception as e:
        print(f"错误: 拉取股票列表失败: {e}", file=sys.stderr)
        sys.exit(1)

    if df.empty:
        print("错误: 未获取到任何股票代码", file=sys.stderr)
        sys.exit(1)

    save_stock_codes(df)
    db = os.environ.get("MYSQL_DATABASE", "stock")
    host = os.environ.get("MYSQL_HOST", "www.incredily.com")
    print(f"已写入 MySQL {host}/{db}.stock_code_list（共 {len(df)} 条）")


if __name__ == "__main__":
    main()
