"""大屏：只读 SQL 查询（与 nanobot.stock_core 共用 MySQL），供表格/图表定时刷新。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _ensure_nanobot_path() -> Path:
    here = Path(__file__).resolve()
    nanobot_dir = here.parents[2] / "nanobot"
    if nanobot_dir.is_dir() and str(nanobot_dir) not in sys.path:
        sys.path.insert(0, str(nanobot_dir))
    return nanobot_dir


def _sync_mysql_url(aiomysql_url: str) -> str:
    u = aiomysql_url.strip()
    u = u.replace("mysql+aiomysql://", "mysql+pymysql://").replace(
        "mysql+asyncmy://", "mysql+pymysql://"
    )
    return u


def prepare_stock_env(sync_database_url: str) -> None:
    """在 worker 线程里调用前先设置环境变量。"""
    _ensure_nanobot_path()
    os.environ.setdefault("DATABASE_URL", _sync_mysql_url(sync_database_url))


def _normalize_sql_from_json_storage(sql: str) -> str:
    """
    大屏 config 经 JSON 存储后，多行 SQL 里的换行常被存成字面量「反斜杠 + n」（两个字符），
    MySQL 会报 syntax error near '\nFROM …'。此处还原为真实换行/制表。
    """
    s = (sql or "").strip()
    if not s:
        return s
    s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
    s = s.replace("\\t", "\t")
    return s.strip()


def run_dashboard_query(
    *,
    sql: str,
    limit: int,
    include_echarts: bool,
    database_url: str,
    transform_chart: str = "",
    transform_table: str = "",
    transform_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    执行只读查询，返回 Ant Design Table 结构；可选附带 stock_core 生成的 ECharts option。
    """
    prepare_stock_env(database_url)
    import stock_core as core  # noqa: PLC0415  # pylint: disable=import-error

    s = _normalize_sql_from_json_storage(sql)
    if not s:
        raise ValueError("sql 不能为空")
    if not core.is_read_only_sql(s):
        raise ValueError("仅允许 SELECT 或 WITH ... SELECT 查询")

    df = core.run_query(s)
    truncated = len(df) > limit
    if truncated:
        df = df.iloc[:limit].copy()

    cols = [{"title": str(c), "dataIndex": str(c)} for c in df.columns]
    rows_raw = df.to_json(orient="records", date_format="iso")
    rows = json.loads(rows_raw) if rows_raw else []

    table: dict[str, Any] = {
        "columns": cols,
        "data": rows,
        "meta": {"row_count": len(df), "truncated_to_limit": truncated},
    }

    result: dict[str, Any] = {
        "table": table,
        "echarts": None,
        "echarts_label": None,
    }
    if include_echarts:
        if df.empty:
            result["echarts"] = {
                "title": {"text": "无数据", "left": "center"},
                "series": [],
            }
            result["echarts_label"] = "空结果"
        else:
            max_rows_chart = min(500, limit)
            opt, label = core.build_stock_echart(df, max_rows=max_rows_chart)
            result["echarts"] = opt
            result["echarts_label"] = label

    from app.services.dashboard_transforms import apply_dashboard_named_transforms  # noqa: PLC0415

    result = apply_dashboard_named_transforms(
        result=result,
        transform_chart=transform_chart,
        transform_table=transform_table,
        transform_params=transform_params,
        include_echarts=include_echarts,
    )
    return result
