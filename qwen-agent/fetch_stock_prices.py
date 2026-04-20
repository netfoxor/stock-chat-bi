"""
使用 Tushare 拉取指定股票日线行情，按交易日期升序写入 Excel 与 SQLite。
需设置环境变量 TUSHARE_TOKEN。
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import tushare as ts

ROOT = Path(__file__).resolve().parent
SCHEMA_SQL = ROOT / "schema.sql"
DB_PATH = ROOT / "stock_prices_history.db"

# (ts_code, 显示名称)
STOCKS: list[tuple[str, str]] = [
    ("600519.SH", "贵州茅台"),
    ("000858.SZ", "五粮液"),
    ("000776.SZ", "广发证券"),
    ("688981.SH", "中芯国际"),
]

START_DATE = "20200101"
OUTPUT_XLSX = ROOT / "stock_prices_history.xlsx"
SHEET_NAME = "日线行情"


def save_to_sqlite(df: pd.DataFrame) -> None:
    df_db = df.copy()
    df_db["trade_date"] = pd.to_datetime(df_db["trade_date"]).dt.strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    try:
        with SCHEMA_SQL.open("r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute("DELETE FROM stock_daily")
        df_db.to_sql("stock_daily", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()


def main() -> None:
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        print("错误: 未找到环境变量 TUSHARE_TOKEN", file=sys.stderr)
        sys.exit(1)

    ts.set_token(token)
    pro = ts.pro_api()

    end_date = date.today().strftime("%Y%m%d")
    frames: list[pd.DataFrame] = []

    for ts_code, name in STOCKS:
        df = pro.daily(ts_code=ts_code, start_date=START_DATE, end_date=end_date)
        if df is None or df.empty:
            print(f"警告: {name} ({ts_code}) 无返回数据")
            continue
        part = df.copy()
        part.insert(0, "stock_name", name)
        frames.append(part)

    if not frames:
        print("错误: 未获取到任何股票数据", file=sys.stderr)
        sys.exit(1)

    out = pd.concat(frames, ignore_index=True)
    out["trade_date"] = out["trade_date"].astype(str)
    out = out.sort_values(["trade_date", "ts_code"], ascending=[True, True])
    out = out.reset_index(drop=True)
    out["trade_date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d")

    # 列顺序：名称、代码、日期、其余行情字段
    front = ["stock_name", "ts_code", "trade_date"]
    rest = [c for c in out.columns if c not in front]
    out = out[front + rest]

    out.to_excel(OUTPUT_XLSX, sheet_name=SHEET_NAME, index=False)
    print(f"已保存: {OUTPUT_XLSX}（工作表「{SHEET_NAME}」共 {len(out)} 行）")

    save_to_sqlite(out)
    print(f"已保存: {DB_PATH}（表 stock_daily 共 {len(out)} 行）")


if __name__ == "__main__":
    main()
