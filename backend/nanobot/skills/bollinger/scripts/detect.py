#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bollinger / scripts / detect.py

读区间日线 → MA20±2σ → stdout：**echarts** + **datatable** 两道围栏（与 ARIMA / exc_sql 契约一致），
便于会话内联图、表与「添加到大屏」。

用法：
    python detect.py --ts-code 600519.SH --start 2024-01-01 --end 2024-12-31
    python detect.py --ts-code 600519.SH                  # 近一年
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_WORKSPACE = Path(__file__).resolve().parents[3]
if str(_WORKSPACE) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE))

import stock_core as core  # noqa: E402


def _fail(msg: str, code: int = 1) -> int:
    print(f"错误：{msg}")
    return code


def main() -> int:
    core.setup_utf8_stdout()
    core.load_backend_dotenv_if_empty()
    parser = argparse.ArgumentParser(description="布林带超买/超卖检测")
    parser.add_argument("--ts-code", required=True, help="Tushare 代码")
    parser.add_argument("--start", default=None, help="起始日 YYYY-MM-DD（可选）")
    parser.add_argument("--end", default=None, help="结束日 YYYY-MM-DD（可选）")
    args = parser.parse_args()

    ts_code = args.ts_code.strip()
    if not core.has_stock_database_access():
        return _fail(
            "未配置 DATABASE_URL：请设置环境变量 DATABASE_URL（MySQL），"
            "与 backend/.env、exc_sql 同源，且库中存在 stock_daily。"
        )

    parsed = core.parse_boll_date_range(args.start, args.end)
    if isinstance(parsed, str):
        return _fail(parsed.removeprefix("错误：") if parsed.startswith("错误：") else parsed)
    start, end = parsed

    try:
        bundle = core.bollinger_series_for_viz(ts_code, start, end, table_max_rows=500)
    except ValueError as e:
        return _fail(str(e))

    option = bundle["option"]
    tab_payload = bundle["table_payload"]
    tab_truncated = bundle["table_truncated"]
    n_ob, n_os = bundle["n_overbought"], bundle["n_oversold"]

    core.write_echart_asset(option, prefix="boll")

    label = bundle["stock_name"]
    tc = bundle["ts_code"]

    caption = (
        f"_布林带 · {label}（{tc}）· {start} ~ {end} · "
        f"{bundle['trade_days']} 条日线 · 超买 {n_ob} 次 · 超卖 {n_os} 次 · "
        f"MA{core.BOLL_WINDOW} ± {core.BOLL_STD_MULT:g}σ · "
        f"会话内图表/表格可「添加到大屏」固定；不构成投资建议_"
    )
    print(caption)
    print()
    print(core.format_echarts_fence(option))
    print()
    if tab_truncated:
        print(core.format_datatable_fence(tab_payload, truncation_note_rows=500))
    else:
        print(core.format_datatable_fence(tab_payload))

    hint = "_提示：布林带是波动率指标，不保证反转；结合量能与趋势综合判断。仅供研究。**不构成投资建议**。"
    print(hint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
