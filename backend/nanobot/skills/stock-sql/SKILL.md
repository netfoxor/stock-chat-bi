---
name: stock-sql
description: A 股日线 MySQL 的表结构、查询范例与 SELECT 列最佳实践；任何涉及数据查询、筛选、回溯、画图的问题都先读这份。
metadata: {"nanobot":{"emoji":"🗃️","requires":{"bins":["python"]}}}
---

# 股票 SQL 业务知识 Skill

当用户问股票的**历史行情、涨跌幅、区间走势、排行榜、K 线、量价**等问题，**先读本 skill**，再用 `exc_sql` 工具执行查询。
查询路径：读本 skill → 组装 SQL → 调 `exc_sql(sql_input=...)` → 把返回的 markdown + 图表占位**原样转发**给用户。

## 数据库

- 方言：MySQL 8
- 表：`stock_daily`（单表，已含所有个股的后复权日线）
- 只读；禁止 INSERT / UPDATE / DELETE / DDL

## 关键列

| 列名          | 类型    | 含义                                           |
| ------------- | ------- | ---------------------------------------------- |
| `ts_code`     | TEXT    | Tushare 代码，如 `600519.SH`、`000858.SZ`      |
| `trade_date`  | DATE    | 交易日，`YYYY-MM-DD`                           |
| `stock_name`  | TEXT    | 股票名称                                       |
| `open`        | REAL    | 开盘价                                         |
| `high`        | REAL    | 最高价                                         |
| `low`         | REAL    | 最低价                                         |
| `close`       | REAL    | 收盘价（后复权）                               |
| `pre_close`   | REAL    | 昨收                                           |
| `change`      | REAL    | 涨跌额                                         |
| `pct_chg`     | REAL    | 涨跌幅（百分比数值，例如 `1.23` 表示 +1.23%）  |
| `vol`         | REAL    | 成交量（手）                                   |
| `amount`      | REAL    | 成交额（千元）                                 |

常见公司代码：贵州茅台 `600519.SH`、五粮液 `000858.SZ`、广发证券 `000776.SZ`、中芯国际 `688981.SH`。

## SQL 规范

- 只允许以 `SELECT` 或 `WITH ... SELECT` 开头；`exc_sql` 会拒绝其他语句
- **始终带 `ORDER BY trade_date`**（或需要的字段），避免结果乱序
- 排行榜记得用 `LIMIT N`

### ⚠️ 日期与函数（最常见的翻车点）

`trade_date` 列是 MySQL `DATE`。日期字面量仍建议用 **`'YYYY-MM-DD'`** 字符串写法（MySQL 会自动转换），避免把 Tushare 原始 `yyyymmdd`（如 `'20250101'`）写进 SQL。

✅ 正确：

```sql
WHERE trade_date >= '2025-01-01' AND trade_date <= '2025-12-31'
-- 或
WHERE trade_date BETWEEN '2025-01-01' AND '2025-12-31'
```

❌ 错误（会返回 0 行，`exc_sql` 会直接拦截并报错）：

```sql
WHERE trade_date BETWEEN '20250101' AND '20251231'        -- 无连字符
WHERE trade_date >= 20250101                               -- 无引号 + 无连字符
```

### ⚠️ MySQL 近 N 天写法（不要用 SQLite 的 date('now', ...)）

✅ MySQL 正确：

```sql
WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
```

❌ SQLite 写法（MySQL 不支持，会导致工具执行失败，LLM 会开始“自救”反复调用工具直到触发 20 次上限）：

```sql
WHERE trade_date >= date('now', '-90 days')
```

## SELECT 列最佳实践（**直接影响图表质量**）

`exc_sql` 会根据返回列智能出图。请按场景选择：

### K 线 / 日线详情
推荐列：`trade_date, open, high, low, close, vol`

含有 `open/high/low/close` 四列时会自动渲染 **K 线图**；若再带 `vol` 或 `amount`，会叠加成交量副图。

### 单指标趋势（收盘、成交量、涨幅等）
推荐列：`trade_date` + 目标数值列

会渲染为**折线图**（成交量/涨跌幅等会渲染为柱状）。

### 多指标对比（多子图）
推荐列：`trade_date` + 不同量纲的多个列，如 `close, vol, pct_chg`

按量纲自动拆多 panel，避免大数压小数。

### 排行榜 / 聚合
只需要聚合后的列即可，通常无图。

## 示例 SQL

### 贵州茅台 2024 年全年日线（K 线 + 成交量）
```sql
SELECT trade_date, open, high, low, close, vol
FROM stock_daily
WHERE ts_code = '600519.SH'
  AND trade_date >= '2024-01-01'
  AND trade_date <= '2024-12-31'
ORDER BY trade_date ASC;
```

### 五粮液近 90 日收盘价
```sql
SELECT trade_date, close
FROM stock_daily
WHERE ts_code = '000858.SZ'
  AND trade_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
ORDER BY trade_date ASC;
```

### 某日涨幅前 20
```sql
SELECT ts_code, stock_name, close, pct_chg
FROM stock_daily
WHERE trade_date = '2024-12-31'
ORDER BY pct_chg DESC
LIMIT 20;
```

### 区间累计涨跌幅（CTE）
```sql
WITH base AS (
  SELECT ts_code, stock_name, trade_date, close
  FROM stock_daily
  WHERE ts_code = '600519.SH'
    AND trade_date BETWEEN '2024-01-01' AND '2024-12-31'
)
SELECT
  MIN(trade_date) AS start_date,
  MAX(trade_date) AS end_date,
  (SELECT close FROM base ORDER BY trade_date ASC  LIMIT 1) AS start_close,
  (SELECT close FROM base ORDER BY trade_date DESC LIMIT 1) AS end_close
FROM base;
```

## 输出纪律

`exc_sql` 返回的内容包含：
1. 概况文字
2. 数据预览 markdown 表
3. 数值/文本描述统计
4. 图表占位 `![...](chart:charts/xxx.json)`（前端自动渲染 ECharts）

**必须原样把这整段转发给用户**，不得裁剪、重写或转成纯文字摘要。最后再用 1-3 句话点评。

若想做**预测**，跳转到 `arima-forecast` skill；若想做**布林带**信号检测，跳转到 `bollinger` skill。
