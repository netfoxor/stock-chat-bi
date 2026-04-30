"""大屏：SQL 查询结果之后再套一层「命名转换」（入参数据集，出参表格/ECharts）。
内置 `arima_forecast`、`bollinger_bands`：读库重算，可与轮询共用；不要求 SQL 结果参与。"""
from __future__ import annotations

import json
import warnings
from typing import Any

import pandas as pd

TRANSFORM_CATALOG: dict[str, list[dict[str, str]]] = {
    "chart": [
        {"id": "", "label": "（默认）由 SQL 查询结果自动生成图"},
        {"id": "arima_forecast", "label": "ARIMA：近一年收盘 + N 日预测（需参数 ts_code、n）"},
        {"id": "bollinger_bands", "label": "布林带：MA20±2σ（需 ts_code；可选 start、end）"},
    ],
    "table": [
        {"id": "", "label": "（默认）使用 SQL 查询结果表格"},
        {"id": "arima_forecast", "label": "ARIMA：预测明细（与图表转换数据源一致）"},
        {"id": "bollinger_bands", "label": "布林带：日线序列与信号列（与图表转换同源）"},
    ],
}


def normalize_transform(tag: str | None) -> str:
    return (tag or "").strip().lower()


def _opt_param_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _antd_table(df: pd.DataFrame) -> dict[str, Any]:
    cols = [{"title": str(c), "dataIndex": str(c)} for c in df.columns.tolist()]
    raw = df.to_json(orient="records", date_format="iso")
    rows = json.loads(raw) if raw else []
    return {"columns": cols, "data": rows, "meta": {"transform": True}}


def build_arima_forecast_bundle(*, ts_code: str, n_steps: int) -> dict[str, Any]:
    """
    与 skills/arima-forecast 同源：近一年 + ARIMA + 置信带；
    返回 antd_table 结构与 ECharts option（已由 stock_core 紧凑序列化）。
    """
    import stock_core as core  # noqa: PLC0415

    ts_code = ts_code.strip().upper()
    if not ts_code:
        raise ValueError("ARIMA 转换需要 transform_params.ts_code")
    try:
        n_steps = int(n_steps)
    except (TypeError, ValueError) as e:
        raise ValueError("transform_params.n 必须为整数交易日数") from e
    if not (1 <= n_steps <= core.MAX_FORECAST_DAYS):
        raise ValueError(f"n 范围为 1~{core.MAX_FORECAST_DAYS}")
    if not core.has_stock_database_access():
        raise ValueError("未配置 DATABASE_URL，无法读取行情库")

    try:
        df = core.load_year_history(ts_code)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"读库失败：{e}") from e
    if df is None or len(df) < core.MIN_ARIMA_OBS:
        have = 0 if df is None else len(df)
        raise ValueError(f"{ts_code} 近一年数据 {have} 条，少于 ARIMA 所需 {core.MIN_ARIMA_OBS} 条")

    try:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"trade_date 解析失败：{e}") from e
    df = df.sort_values("trade_date").reset_index(drop=True)
    close = df["close"].astype(float)

    try:
        from statsmodels.tsa.arima.model import ARIMA

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = ARIMA(close, order=core.ARIMA_ORDER).fit()
            fc = res.get_forecast(steps=n_steps)
            mean = fc.predicted_mean
            ci = fc.conf_int(alpha=0.05)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"ARIMA 拟合失败：{e}") from e

    last_dt = pd.to_datetime(df["trade_date"].iloc[-1])
    future_dates = pd.bdate_range(last_dt + pd.Timedelta(days=1), periods=n_steps)

    stock_name = str(df["stock_name"].iloc[-1]) if "stock_name" in df.columns else ts_code

    out = pd.DataFrame(
        {
            "forecast_date": [d.strftime("%Y-%m-%d") for d in future_dates],
            "forecast_close": [round(float(v), 4) for v in mean.values],
            "ci_lower_95": [round(float(v), 4) for v in ci.iloc[:, 0].values],
            "ci_upper_95": [round(float(v), 4) for v in ci.iloc[:, 1].values],
        }
    )

    hist_dates = [d.strftime("%Y-%m-%d") for d in df["trade_date"]]
    hist_close = core.round_list(close)
    option = core.build_arima_echart(
        hist_dates,
        hist_close,
        out["forecast_date"].tolist(),
        out["forecast_close"].tolist(),
        out["ci_lower_95"].tolist(),
        out["ci_upper_95"].tolist(),
        title=f"{core.safe_label(stock_name)} ({ts_code}) · 近一年收盘 + ARIMA 预测 {n_steps} 日",
    )
    # 对齐聊天侧：compact 合法 JSON
    option_compact = json.loads(core.dumps_json_for_fence(option))

    table_payload = _antd_table(out)
    return {"table": table_payload, "echarts": option_compact, "echarts_label": "ARIMA 预测收盘价"}


def build_bollinger_bands_bundle(*, ts_code: str, start_date: str | None, end_date: str | None) -> dict[str, Any]:
    """与 skills/bollinger/scripts/detect.py 同源：布林带图 + 日线明细表。"""
    import stock_core as core  # noqa: PLC0415

    ts_code = ts_code.strip().upper()
    if not ts_code:
        raise ValueError("布林带转换需要 transform_params.ts_code")
    if not core.has_stock_database_access():
        raise ValueError("未配置 DATABASE_URL，无法读取行情库")

    parsed = core.parse_boll_date_range(start_date, end_date)
    if isinstance(parsed, str):
        msg = parsed.removeprefix("错误：") if parsed.startswith("错误：") else parsed
        raise ValueError(msg)
    start, end = parsed

    try:
        raw = core.bollinger_series_for_viz(ts_code, start, end, table_max_rows=500)
    except ValueError as e:
        raise ValueError(str(e)) from e

    option_compact = json.loads(core.dumps_json_for_fence(raw["option"]))
    meta = dict((raw["table_payload"].get("meta") or {}) | {"transform": "bollinger_bands"})
    table_payload = {**raw["table_payload"], "meta": meta}
    return {"table": table_payload, "echarts": option_compact, "echarts_label": "布林带收盘价"}


def apply_dashboard_named_transforms(
    *,
    result: dict[str, Any],
    transform_chart: str,
    transform_table: str,
    transform_params: dict[str, Any] | None,
    include_echarts: bool,
) -> dict[str, Any]:
    tc = normalize_transform(transform_chart)
    tt = normalize_transform(transform_table)
    params = dict(transform_params or {})

    wants_arima = tc == "arima_forecast" or tt == "arima_forecast"
    wants_boll = tc == "bollinger_bands" or tt == "bollinger_bands"
    if not wants_arima and not wants_boll:
        return result

    if wants_arima:
        ts_arima = str(params.get("ts_code") or "").strip()
        if not ts_arima:
            ts_arima = "600519.SH"
        try:
            n_steps = int(params.get("n") or params.get("days") or 10)
        except (TypeError, ValueError) as e:
            raise ValueError("transform_params.n（或 days）须为整数") from e

        arima_bundle = build_arima_forecast_bundle(ts_code=ts_arima, n_steps=n_steps)

        if tt == "arima_forecast":
            merged_meta = dict((result.get("table") or {}).get("meta") or {})
            merged_meta.update({"sql_row_count_hint": merged_meta.get("row_count"), "transform": "arima_forecast"})
            bundle_table = arima_bundle["table"]
            bundle_table["meta"] = {**merged_meta, **(bundle_table.get("meta") or {})}
            result["table"] = bundle_table

        if tc == "arima_forecast" and include_echarts:
            result["echarts"] = arima_bundle["echarts"]
            result["echarts_label"] = arima_bundle.get("echarts_label")

    if wants_boll:
        ts_boll = _opt_param_str(params.get("ts_code")) or "600519.SH"
        start_boll = _opt_param_str(params.get("start"))
        end_boll = _opt_param_str(params.get("end"))
        boll_bundle = build_bollinger_bands_bundle(ts_code=ts_boll, start_date=start_boll, end_date=end_boll)

        if tt == "bollinger_bands":
            merged_meta = dict((result.get("table") or {}).get("meta") or {})
            merged_meta.update({"sql_row_count_hint": merged_meta.get("row_count"), "transform": "bollinger_bands"})
            bt = dict(boll_bundle["table"])
            bt["meta"] = {**merged_meta, **(bt.get("meta") or {})}
            result["table"] = bt

        if tc == "bollinger_bands" and include_echarts:
            result["echarts"] = boll_bundle["echarts"]
            result["echarts_label"] = boll_bundle.get("echarts_label")

    return result
