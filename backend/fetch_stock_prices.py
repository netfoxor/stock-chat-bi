"""
使用 Tushare 拉取股票日线行情，写入 MySQL。

数据库：从 **backend/.env** 读取 **DATABASE_URL**（与 FastAPI 一致，自动将 mysql+aiomysql 转为 pymysql 同步连接）。

运行（在 backend 目录下）::
  cd backend
  python fetch_stock_prices.py

逻辑：
1) 从 stock_code_list 按 update_time 升序（NULL 最先）依次取股票。
2) 单只股票的拉取窗口：start_date = stock_daily MAX(trade_date) 次日；无则 19900101；
   end_date = 今天（若本地时间 >= CUTOFF_HOUR）否则昨天。
3) 若 start_date >= end_date：跳过。
4) 否则 pro.daily，写入 stock_daily（ON DUPLICATE KEY UPDATE）。
5) 每次处理完更新 stock_code_list.update_time。

环境变量（可写在 backend/.env）：
  DATABASE_URL    必填（mysql+aiomysql://...）
  TUSHARE_TOKEN   必填（Tushare Pro token）
  （股票列表可先运行 backend/fetch_stock_codes.py）
  TUSHARE_REQUEST_INTERVAL    默认 1.5
  CUTOFF_HOUR                 默认 17
  LOG_LEVEL / LOG_DIR / LOG_PROGRESS_EVERY
  SAVE_STOCK_PRICES_XLSX      1/true 时导出 Excel 到本目录
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pandas as pd
import tushare as ts
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent

DEFAULT_START_DATE = "19900101"
REQUEST_INTERVAL_SEC = float(os.environ.get("TUSHARE_REQUEST_INTERVAL", "1.5"))
LOG_PROGRESS_EVERY = max(1, int(os.environ.get("LOG_PROGRESS_EVERY", "1")))
CUTOFF_HOUR = max(0, min(23, int(os.environ.get("CUTOFF_HOUR", "17"))))
OUTPUT_XLSX = ROOT / "stock_prices_history.xlsx"
SHEET_NAME = "日线行情"


class _Env(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    database_url: str = Field(alias="DATABASE_URL")
    tushare_token: str | None = Field(default=None, alias="TUSHARE_TOKEN")


def _database_url_to_sync(url: str) -> str:
    if "+aiomysql" in url:
        return url.replace("+aiomysql", "+pymysql", 1)
    if "+asyncmy" in url:
        return url.replace("+asyncmy", "+pymysql", 1)
    return url


def _mysql_engine(sync_url: str):
    return create_engine(sync_url, pool_pre_ping=True, future=True)


def _configure_logging() -> Path:
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    log_dir = Path(os.environ.get("LOG_DIR") or (ROOT / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    today_str = date.today().strftime("%Y-%m-%d")
    log_file = log_dir / f"fetch_stock_prices_{today_str}.log"

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(fmt)

    file_h = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    file_h.suffix = "%Y-%m-%d"
    file_h.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(console)
    root.addHandler(file_h)
    return log_file


def compute_end_date(now: datetime | None = None) -> str:
    now = now or datetime.now()
    base = now.date() if now.hour >= CUTOFF_HOUR else now.date() - timedelta(days=1)
    return base.strftime("%Y%m%d")


def compute_start_date(last_trade_date) -> str:
    if last_trade_date is None or pd.isna(last_trade_date):
        return DEFAULT_START_DATE
    d = pd.Timestamp(last_trade_date).normalize().date() + timedelta(days=1)
    return d.strftime("%Y%m%d")


def ensure_stock_code_list_update_time(engine) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE stock_code_list "
                    "ADD COLUMN update_time DATETIME NULL DEFAULT NULL",
                ),
            )
    except Exception:
        pass


def ensure_stock_daily_table(engine) -> None:
    ddl_table = """
    CREATE TABLE IF NOT EXISTS stock_daily (
        stock_name VARCHAR(128) NOT NULL,
        ts_code VARCHAR(20) NOT NULL,
        trade_date DATE NOT NULL,
        `open` DOUBLE,
        `high` DOUBLE,
        `low` DOUBLE,
        `close` DOUBLE,
        pre_close DOUBLE,
        change_val DOUBLE,
        pct_chg DOUBLE,
        vol DOUBLE,
        amount DOUBLE,
        PRIMARY KEY (ts_code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    ddl_index = (
        "CREATE INDEX idx_stock_daily_trade_date ON stock_daily (trade_date)"
    )
    with engine.begin() as conn:
        conn.execute(text(ddl_table))
        try:
            conn.execute(text(ddl_index))
        except Exception:
            pass


def load_stocks_ordered(engine) -> pd.DataFrame:
    return pd.read_sql(
        text(
            "SELECT ts_code, stock_name, update_time "
            "FROM stock_code_list "
            "ORDER BY update_time ASC, ts_code ASC",
        ),
        engine,
    )


def load_last_trade_date_by_code(engine) -> dict[str, object]:
    try:
        df = pd.read_sql(
            text(
                "SELECT ts_code, MAX(trade_date) AS mx "
                "FROM stock_daily GROUP BY ts_code",
            ),
            engine,
        )
    except Exception as e:
        logging.warning("读取 stock_daily 汇总失败（按无历史处理）: %s", e)
        return {}
    if df is None or df.empty:
        return {}
    return {str(r["ts_code"]).strip(): r["mx"] for _, r in df.iterrows()}


def upsert_stock_daily(
    engine, ts_code: str, stock_name: str, df: pd.DataFrame,
) -> int:
    if df is None or df.empty:
        return 0
    df_db = df.copy()
    df_db["trade_date"] = pd.to_datetime(df_db["trade_date"]).dt.strftime("%Y-%m-%d")
    if "change" in df_db.columns:
        df_db = df_db.rename(columns={"change": "change_val"})
    df_db["stock_name"] = stock_name

    want = [
        "stock_name", "ts_code", "trade_date",
        "open", "high", "low", "close",
        "pre_close", "change_val", "pct_chg", "vol", "amount",
    ]
    df_db = df_db[[c for c in want if c in df_db.columns]]

    sql = (
        "INSERT INTO stock_daily ("
        "stock_name, ts_code, trade_date, `open`, `high`, `low`, `close`, "
        "pre_close, change_val, pct_chg, vol, amount"
        ") VALUES ("
        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s"
        ") ON DUPLICATE KEY UPDATE "
        "stock_name=VALUES(stock_name), "
        "`open`=VALUES(`open`), `high`=VALUES(`high`), `low`=VALUES(`low`), "
        "`close`=VALUES(`close`), pre_close=VALUES(pre_close), "
        "change_val=VALUES(change_val), pct_chg=VALUES(pct_chg), "
        "vol=VALUES(vol), amount=VALUES(amount)"
    )

    def _clean(v):
        if v is None:
            return None
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(v, float) and (v != v):
            return None
        return v

    rows = [
        tuple(_clean(r.get(c)) for c in want)
        for _, r in df_db.iterrows()
    ]
    if not rows:
        return 0

    raw = engine.raw_connection()
    cur = None
    try:
        cur = raw.cursor()
        cur.executemany(sql, rows)
        raw.commit()
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        raw.close()
    return len(rows)


def touch_update_time(engine, ts_code: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE stock_code_list SET update_time = NOW() "
                "WHERE ts_code = :code",
            ),
            {"code": ts_code},
        )


def process_one(
    pro,
    engine,
    ts_code: str,
    stock_name: str,
    start_date: str,
    end_date: str,
) -> str:
    try:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logging.warning(
            "Tushare 请求失败 %s %s (%s~%s): %s",
            ts_code,
            stock_name,
            start_date,
            end_date,
            e,
        )
        return "fail"

    if df is None or df.empty:
        logging.warning(
            "无返回数据 %s %s (%s~%s)",
            ts_code,
            stock_name,
            start_date,
            end_date,
        )
        touch_update_time(engine, ts_code)
        return "empty"

    part = df.copy()
    part.insert(0, "stock_name", stock_name)
    n = upsert_stock_daily(engine, ts_code, stock_name, part)
    touch_update_time(engine, ts_code)
    logging.debug("写入 %s %s 共 %d 行", ts_code, stock_name, n)
    return "ok"


def main() -> None:
    log_file = _configure_logging()
    logging.info("启动 fetch_stock_prices，日志文件: %s", log_file)

    try:
        env = _Env()
    except Exception as e:
        logging.error(
            "读取 %s/.env 失败（需 DATABASE_URL）: %s",
            ROOT,
            e,
        )
        sys.exit(1)

    token = (
        (env.tushare_token or "").strip()
        or os.environ.get("TUSHARE_TOKEN", "").strip()
    )
    if not token:
        logging.error(
            "未配置 TUSHARE_TOKEN（请写入 %s/.env 或环境变量）",
            ROOT,
        )
        sys.exit(1)

    now = datetime.now()
    end_date = compute_end_date(now)
    logging.info(
        "当前 %s，截止小时=%d，本轮 end_date=%s",
        now.strftime("%Y-%m-%d %H:%M:%S"),
        CUTOFF_HOUR,
        end_date,
    )

    ts.set_token(token)
    pro = ts.pro_api()

    sync_url = _database_url_to_sync(env.database_url)
    engine = _mysql_engine(sync_url)
    uinfo = make_url(sync_url)
    host_disp = str(uinfo.host or "")
    db_disp = str(uinfo.database or "")

    logging.info(
        "MySQL %s / %s ，请求间隔 %.2fs，进度每 %d 只一条",
        host_disp,
        db_disp,
        REQUEST_INTERVAL_SEC,
        LOG_PROGRESS_EVERY,
    )

    ensure_stock_code_list_update_time(engine)
    ensure_stock_daily_table(engine)

    stocks_df = load_stocks_ordered(engine)
    if stocks_df.empty:
        logging.error(
            "stock_code_list 无数据，请先运行 python fetch_stock_codes.py（在 backend 目录）",
        )
        sys.exit(1)

    total = len(stocks_df)
    null_rows = int(stocks_df["update_time"].isna().sum())
    logging.info(
        "共 %d 只股票，其中 update_time 为空 %d 只（将最先处理）",
        total,
        null_rows,
    )

    last_by_code = load_last_trade_date_by_code(engine)
    logging.info("stock_daily 已有日线的标的数: %d", len(last_by_code))

    cnt = {"ok": 0, "empty": 0, "fail": 0, "skip": 0}
    requested = 0

    window_cutoff = datetime.strptime(end_date, "%Y%m%d").replace(
        hour=23, minute=59, second=59,
    )

    for idx, (_, row) in enumerate(stocks_df.iterrows()):
        ts_code = str(row["ts_code"]).strip()
        stock_name = str(row["stock_name"]).strip()
        last_mx = last_by_code.get(ts_code)
        start_date = compute_start_date(last_mx)
        row_ut = row.get("update_time") if hasattr(row, "get") else row["update_time"]

        skip_reason = ""
        if start_date >= end_date:
            skip_reason = "start>=end"
        elif (
            row_ut is not None
            and not pd.isna(row_ut)
            and pd.Timestamp(row_ut).to_pydatetime() > window_cutoff
        ):
            skip_reason = f"本窗口期已处理 ut={pd.Timestamp(row_ut)}"

        if idx % LOG_PROGRESS_EVERY == 0:
            logging.info(
                "进度 %d/%d %s %s start=%s end=%s last=%s%s",
                idx + 1,
                total,
                ts_code,
                stock_name,
                start_date,
                end_date,
                "-" if last_mx is None or pd.isna(last_mx) else str(last_mx),
                f"  [跳过: {skip_reason}]" if skip_reason else "",
            )

        if skip_reason:
            cnt["skip"] += 1
            continue

        if requested > 0:
            time.sleep(REQUEST_INTERVAL_SEC)
        requested += 1

        try:
            status = process_one(
                pro, engine, ts_code, stock_name, start_date, end_date,
            )
        except Exception as e:
            status = "fail"
            logging.exception(
                "处理 %s %s 异常（已跳过该只，不中断任务）: %s",
                ts_code,
                stock_name,
                e,
            )
        cnt[status] = cnt.get(status, 0) + 1

    logging.info(
        "全部结束: ok=%d empty=%d fail=%d skip=%d（MySQL %s/%s，end_date=%s）",
        cnt["ok"],
        cnt["empty"],
        cnt["fail"],
        cnt["skip"],
        host_disp,
        db_disp,
        end_date,
    )

    if os.environ.get("SAVE_STOCK_PRICES_XLSX", "").lower() in ("1", "true", "yes"):
        logging.info("SAVE_STOCK_PRICES_XLSX 已开启，导出 Excel…")
        df_all = pd.read_sql(
            text("SELECT * FROM stock_daily ORDER BY ts_code, trade_date"),
            engine,
        )
        if not df_all.empty:
            df_all.to_excel(
                OUTPUT_XLSX,
                sheet_name=SHEET_NAME,
                index=False,
            )
            logging.info(
                "已保存 %s（%s 共 %d 行）",
                OUTPUT_XLSX,
                SHEET_NAME,
                len(df_all),
            )


if __name__ == "__main__":
    main()
