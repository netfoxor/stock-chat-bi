-- 日线行情表：字段与 Tushare pro.daily 一致，并增加 stock_name
-- trade_date 存为 TEXT，格式 YYYY-MM-DD

CREATE TABLE IF NOT EXISTS stock_daily (
    stock_name TEXT NOT NULL,
    ts_code    TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    pre_close  REAL,
    change     REAL,
    pct_chg    REAL,
    vol        REAL,
    amount     REAL,
    PRIMARY KEY (ts_code, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_stock_daily_trade_date ON stock_daily (trade_date);
