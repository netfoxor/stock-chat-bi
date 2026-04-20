"""
将 stock_prices_history.xlsx 中的「日线行情」表导入 SQLite（无需 Tushare）。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
SCHEMA_SQL = ROOT / "schema.sql"
XLSX_PATH = ROOT / "stock_prices_history.xlsx"
DB_PATH = ROOT / "stock_prices_history.db"
SHEET_NAME = "日线行情"


def load_xlsx_to_sqlite() -> None:
    if not XLSX_PATH.is_file():
        print(f"错误: 未找到 {XLSX_PATH}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_excel(XLSX_PATH, sheet_name=SHEET_NAME)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")

    expected = [
        "stock_name",
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ]
    missing = [c for c in expected if c not in df.columns]
    if missing:
        print(f"错误: Excel 缺少列: {missing}", file=sys.stderr)
        sys.exit(1)
    df = df[expected]

    conn = sqlite3.connect(DB_PATH)
    try:
        with SCHEMA_SQL.open("r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute("DELETE FROM stock_daily")
        df.to_sql("stock_daily", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()

    print(f"已写入: {DB_PATH}（stock_daily 共 {len(df)} 行）")


if __name__ == "__main__":
    load_xlsx_to_sqlite()
