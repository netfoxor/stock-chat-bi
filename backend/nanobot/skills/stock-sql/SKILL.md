---

## name: stock-sql
description: A 股日线 MySQL 的表结构、查询范例与 SELECT 列最佳实践；任何涉及数据查询、筛选、回溯、画图的问题都先读这份。
metadata: {"nanobot":{"emoji":"🗃️","requires":{"bins":["python"]}}}

# 股票 SQL 业务知识 Skill

当用户问股票的**历史行情、涨跌幅、区间走势、排行榜、K 线、量价**等问题，**先读本 skill**，再用 `exc_sql` 工具执行查询。
查询路径：读本 skill → 组装 SQL → 调 `exc_sql(sql_input=...)` → 把返回的 markdown + `echarts/`datatable 代码块**原样转发**给用户。

## 数据库

- 方言：**MySQL 8**
- 主表：`stock_daily`（全员个股日线快照；与 `skills/arima-forecast`、`skills/bollinger` 脚本经 `stock_core` **同一环境变量**：**`DATABASE_URL`**）
- 辅助表：`stock_code_list`（代码 ↔ 中文名、`ak_code`）
- **只读**；禁止 INSERT / UPDATE / DELETE / DDL  
- DDL 及以下列说明为本仓库与各 exec 技能的**全局约定**（不必再抄到 AGENTS.md，避免两处漂移）。

### 表结构 DDL（权威）

```sql
CREATE TABLE `stock_daily` (
  `stock_name` varchar(128) NOT NULL,
  `ts_code` varchar(20) NOT NULL,
  `trade_date` date NOT NULL,
  `open` float DEFAULT NULL,
  `high` float DEFAULT NULL,
  `low` float DEFAULT NULL,
  `close` float DEFAULT NULL,
  `pre_close` float DEFAULT NULL,
  `change_val` float DEFAULT NULL,
  `pct_chg` float DEFAULT NULL,
  `vol` double DEFAULT NULL,
  `amount` double DEFAULT NULL,
  PRIMARY KEY (`ts_code`,`trade_date`),
  KEY `idx_trade_date_cover` (`trade_date`,`ts_code`,`close`,`pct_chg`,`vol`,`amount`),
  KEY `idx_stock_daily_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `stock_code_list` (
  `ts_code` varchar(20) NOT NULL,
  `ak_code` varchar(16) NOT NULL,
  `stock_name` varchar(128) NOT NULL,
  `update_time` datetime DEFAULT NULL,
  PRIMARY KEY (`ts_code`),
  KEY `idx_stock_code_list_ak` (`ak_code`),
  KEY `idx_stock_code_list_name` (`stock_name`(64))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
```

按名称解析代码：`SELECT ts_code FROM stock_code_list WHERE stock_name LIKE '%茅台%' LIMIT 5;`

## SQL 规范

- 只允许以 `SELECT` 或 `WITH ... SELECT` 开头；`exc_sql` 会拒绝其他语句
- **始终带 `ORDER BY trade_date`**（或需要的字段），避免结果乱序
- 排行榜记得用 `LIMIT N`

### ⚠️ 日期与函数（最常见的翻车点）

`trade_date` 列是 MySQL `DATE`。日期字面量仍建议用 `**'YYYY-MM-DD'**` 字符串写法（MySQL 会自动转换），避免把 Tushare 原始 `yyyymmdd`（如 `'20250101'`）写进 SQL。

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

### ✅ “今天 / 昨天 / 近一周 / 近一月”等相对时间（用于让数据随时间自动滚动）

当用户说“今天/昨天/近一周/近30天”等，**不要写死具体日期**，必须用 MySQL 的滚动日期函数：

```sql
-- 今天（注意：A 股交易日不一定每天都有数据，空结果请先确认 trade_date 覆盖范围）
WHERE trade_date = CURDATE()

-- 昨天
WHERE trade_date = DATE_SUB(CURDATE(), INTERVAL 1 DAY)

-- 近 7 天 / 近 30 天
WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)

-- 近 1 周（自然周：本周周一到今天）
WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL (WEEKDAY(CURDATE())) DAY)
```

如果需要按天分组（例如统计近 7 天每天的成交额），要用 `DATE(trade_date)`（虽然它本身是 DATE，但保持表达清晰）：

```sql
SELECT DATE(trade_date) AS d, SUM(amount) AS amt
FROM stock_daily
WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
GROUP BY d
ORDER BY d ASC;
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
2. 数值/文本描述统计
3. ````echarts` 代码块：标准 ECharts option JSON（前端内联渲染，并可“添加到大屏”）
4. ````datatable` 代码块：`{"columns":[...],"data":[...]}`（前端内联渲染，并可“添加到大屏”）

**必须原样把这整段转发给用户**，不得裁剪、重写或转成纯文字摘要。最后再用 1-3 句话点评。

若想做**预测**，跳转到 `arima-forecast` skill；若想做**布林带**信号检测，跳转到 `bollinger` skill。