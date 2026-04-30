#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arima-forecast / scripts / forecast.py

读近一年日线 → 拟合 ARIMA(5,1,5) → 预测 n 个交易日。
stdout：说明行 + **`echarts` + `datatable`** 两道围栏（与主站会话 / 大屏契约一致），
图表在前、明细表在后，便于渲染与「添加到大屏」；不写 pandas markdown 表。

用法：
    python forecast.py --ts-code 600519.SH --n 10
"""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

# 把 workspace 根加到 sys.path，让脚本能 import stock_core
_WORKSPACE = Path(__file__).resolve().parents[3]
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

import pandas as pd  # noqa: E402

import stock_core as core  # noqa: E402


def _fail(msg: str, code: int = 1) -> int:
    print(f"错误：{msg}")
    return code


def main() -> int:
    core.setup_utf8_stdout()
    core.load_backend_dotenv_if_empty()
    parser = argparse.ArgumentParser(description="ARIMA 收盘价预测")
    parser.add_argument("--ts-code", required=True, help="Tushare 代码，如 600519.SH")
    parser.add_argument("--n", type=int, required=True, help="预测交易日数 1~60")
    args = parser.parse_args()

    ts_code = args.ts_code.strip()
    n_raw = args.n
    try:
        n = int(n_raw)
    except (TypeError, ValueError):
        return _fail(f"预测天数必须是整数，收到：{n_raw}")
    if not (1 <= n <= core.MAX_FORECAST_DAYS):
        return _fail(f"预测天数超出范围，应为 1~{core.MAX_FORECAST_DAYS}，收到：{n}")

    if not core.has_stock_database_access():
        return _fail(
            "未配置 DATABASE_URL：请设置环境变量 DATABASE_URL（MySQL），"
            "与 backend/.env、exc_sql 同源，且库中存在 stock_daily。"
        )

    try:
        df = core.load_year_history(ts_code)
    except Exception as e:  # noqa: BLE001
        return _fail(f"数据库查询失败：{e}")
    if df is None or len(df) < core.MIN_ARIMA_OBS:
        have = 0 if df is None else len(df)
        return _fail(
            f"{ts_code} 近一年数据仅 {have} 条，不足 ARIMA 拟合所需的 "
            f"{core.MIN_ARIMA_OBS} 条，无法预测。"
        )

    try:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    except Exception as e:  # noqa: BLE001
        return _fail(f"trade_date 解析失败：{e}")
    df = df.sort_values("trade_date").reset_index(drop=True)
    close = df["close"].astype(float)

    try:
        from statsmodels.tsa.arima.model import ARIMA
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = ARIMA(close, order=core.ARIMA_ORDER).fit()
            fc = res.get_forecast(steps=n)
            mean = fc.predicted_mean
            ci = fc.conf_int(alpha=0.05)
    except Exception as e:  # noqa: BLE001
        return _fail(f"ARIMA 拟合失败：{e}")

    last_date = df["trade_date"].iloc[-1]
    future_dates = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=n)

    stock_name = str(df["stock_name"].iloc[-1]) if "stock_name" in df.columns else ts_code

    out = pd.DataFrame({
        "forecast_date": [d.strftime("%Y-%m-%d") for d in future_dates],
        "forecast_close": [round(float(v), 4) for v in mean.values],
        "ci_lower_95": [round(float(v), 4) for v in ci.iloc[:, 0].values],
        "ci_upper_95": [round(float(v), 4) for v in ci.iloc[:, 1].values],
    })

    hist_dates = [d.strftime("%Y-%m-%d") for d in df["trade_date"]]
    hist_close = core.round_list(close)
    fc_dates = out["forecast_date"].tolist()
    fc_mean = out["forecast_close"].tolist()
    fc_low = out["ci_lower_95"].tolist()
    fc_high = out["ci_upper_95"].tolist()
    option = core.build_arima_echart(
        hist_dates, hist_close, fc_dates, fc_mean, fc_low, fc_high,
        title=f"{core.safe_label(stock_name)} ({ts_code}) · 近一年收盘 + ARIMA 预测 {n} 日",
    )
    core.write_echart_asset(option, prefix="arima")

    caption = (
        f"_ARIMA 预测 · {core.safe_label(stock_name)}（{ts_code}）· 未来 {n} 个交易日 · "
        f"ARIMA{core.ARIMA_ORDER} · {len(df)} 条日线 · "
        f"会话内图表/表格可「添加到大屏」固定；不构成投资建议_"
    )
    print(caption)
    print()
    print(core.format_echarts_fence(option))
    print()
    tab_payload, tab_truncated = core.dataframe_to_antd_table_payload(out, max_rows=200)
    if tab_truncated:
        print(core.format_datatable_fence(tab_payload, truncation_note_rows=200))
    else:
        print(core.format_datatable_fence(tab_payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
