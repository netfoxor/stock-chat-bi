#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bollinger / scripts / detect.py

CLI 入口：读区间日线 → 20 日 MA ± 2σ → 标记超买/超卖 → markdown + ECharts。

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

import pandas as pd  # noqa: E402

import stock_core as core  # noqa: E402


def _fail(msg: str, code: int = 1) -> int:
    print(f"错误：{msg}")
    return code


def main() -> int:
    core.setup_utf8_stdout()
    parser = argparse.ArgumentParser(description="布林带超买/超卖检测")
    parser.add_argument("--ts-code", required=True, help="Tushare 代码")
    parser.add_argument("--start", default=None, help="起始日 YYYY-MM-DD（可选）")
    parser.add_argument("--end", default=None, help="结束日 YYYY-MM-DD（可选）")
    args = parser.parse_args()

    ts_code = args.ts_code.strip()
    if not core.DB_PATH.is_file():
        return _fail(f"未找到数据库文件 {core.DB_PATH}")

    parsed = core.parse_boll_date_range(args.start, args.end)
    if isinstance(parsed, str):
        return _fail(parsed.removeprefix("错误：") if parsed.startswith("错误：") else parsed)
    start, end = parsed

    try:
        df = core.load_stock_daily_range(ts_code, start, end)
    except Exception as e:  # noqa: BLE001
        return _fail(f"数据库查询失败：{e}")
    if df is None:
        return _fail(f"未找到 {ts_code} 在 [{start}, {end}] 的日线数据。")
    if len(df) < core.MIN_BOLL_ROWS:
        return _fail(
            f"{ts_code} 在 [{start}, {end}] 仅有 {len(df)} 条日线，"
            f"不足 {core.MIN_BOLL_ROWS} 条，不足以计算 20 日布林带。"
        )

    try:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    except Exception as e:  # noqa: BLE001
        return _fail(f"trade_date 解析失败：{e}")
    df = df.sort_values("trade_date").reset_index(drop=True)

    close = df["close"].astype(float)
    mid, upper, lower = core.compute_bollinger(close)

    valid = mid.notna()
    overbought_mask = valid & (close > upper)
    oversold_mask = valid & (close < lower)

    signals = pd.DataFrame({
        "trade_date": df["trade_date"].dt.strftime("%Y-%m-%d"),
        "close": close.round(4),
        "mid_ma20": mid.round(4),
        "upper_2sigma": upper.round(4),
        "lower_2sigma": lower.round(4),
        "signal": ["超买" if ob else ("超卖" if os_ else "")
                   for ob, os_ in zip(overbought_mask, oversold_mask)],
    })
    signals = signals[signals["signal"] != ""].reset_index(drop=True)

    stock_name = str(df["stock_name"].iloc[-1]) if "stock_name" in df.columns else ts_code

    # ECharts option
    dates = [d.strftime("%Y-%m-%d") for d in df["trade_date"]]
    close_l = core.round_list(close)
    mid_l = core.round_list(mid)
    upper_l = core.round_list(upper)
    lower_l = core.round_list(lower)
    ob_idx = [i for i, x in enumerate(overbought_mask.tolist()) if x]
    os_idx = [i for i, x in enumerate(oversold_mask.tolist()) if x]
    option = core.build_boll_echart(
        dates, close_l, mid_l, upper_l, lower_l, ob_idx, os_idx,
        title=(f"{core.safe_label(stock_name)} ({ts_code}) · 布林带 "
               f"MA{core.BOLL_WINDOW}±{core.BOLL_STD_MULT:g}σ · "
               f"{dates[0]} ~ {dates[-1]}"),
    )
    chart_md = core.save_echart_option(option, prefix="boll",
                                       label=f"{stock_name} 布林带")

    header = (
        f"### {core.safe_label(stock_name)}（{ts_code}）布林带检测"
        f"（{start} ~ {end}，共 {len(df)} 条日线）"
    )
    n_ob, n_os = int(overbought_mask.sum()), int(oversold_mask.sum())
    summary = (
        f"- 触及/超过 **上轨 +2σ（超买）**：**{n_ob}** 次\n"
        f"- 触及/低于 **下轨 -2σ（超卖）**：**{n_os}** 次\n"
        f"- 窗口：{core.BOLL_WINDOW} 日 MA ± {core.BOLL_STD_MULT:g}σ"
    )

    parts: list[str] = [header, "", summary, ""]
    if len(signals) == 0:
        parts.append("_区间内未检测到超买/超卖信号。_")
    else:
        parts.append("**信号明细：**")
        parts.append(signals.to_markdown(index=False))
    parts.append("")
    parts.append(chart_md)
    parts.append("")
    parts.append("_提示：布林带是波动率指标，不保证反转；结合量能、均线趋势综合判断。仅供研究，**不构成投资建议**。_")

    print("\n".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
