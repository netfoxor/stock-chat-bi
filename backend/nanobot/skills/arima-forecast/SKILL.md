---
name: arima-forecast
description: 用 ARIMA(5,1,5) 对指定 A 股股票预测未来 1~60 个交易日的收盘价，stdout 输出 datatable（Ant Design Table JSON）与 echarts 围栏（含 95% 置信带）。
metadata: {"nanobot":{"emoji":"📈","requires":{"bins":["python"]}}}
---

# ARIMA 收盘价预测 Skill

当用户问"**未来 n 个交易日收盘价**"、"**股价预测**"、"**趋势外推**"等问题时使用本 skill。
脚本经 `stock_core` 读库：与 `exc_sql` / stock-sql 共用 **`DATABASE_URL`**（连 MySQL `stock_daily`）。

## 运行（严格按此格式，否则会踩 shell 引号陷阱）

用 `exec` 工具执行：

```
python3 skills/arima-forecast/scripts/forecast.py --ts-code 600519.SH --n 10
```

**三条硬规定**（违反必挂）：

1. **不传 `working_dir`** —— `exec` 默认 cwd 就是 `nanobot/`，相对路径可直达
2. **用正斜杠相对路径** `skills/arima-forecast/scripts/forecast.py`（Windows / Linux / Docker 通吃），**不要**写绝对路径，也**不要**用反斜杠
3. **整个 command 里不加任何引号**（路径不含空格，加引号反而触发 `\"` 解析错乱）

参数：

- `--ts-code`（必填）：Tushare 代码，如 `600519.SH`、`000858.SZ`、`688981.SH`
- `--n`（必填）：预测 **交易日** 数，1~60。**经编排路由时**会从自然语言推算 `n`：如「一月 / 两个月 / 3个月」按约 **每月 22 个交易日**换算（封顶 60）；「N 个交易日」「N 天」「N 周」按字面（周≈每周 5 个交易日）。避免出现只有数字、不带「天 / 周 / 月」等上下文的情况，否则会与「一个月」里的 「1」产生歧义。

输出约定（非常重要）：

- **stdout**：一行简述 + **` ```echarts` 块在前**、` ```datatable` 在后（若有 MySQL `TEXT` 长度截断，优先保住图表围栏）；结构与 `exc_sql` 同源。
- 脚本仍向 `charts/` 落盘一份 option JSON（不参与聊天渲染）。
- **必须把 stdout 原样转发给用户**；勿删减 fenced 块。
- 非零 exit code / stderr 提示错误：把错误原因解释给用户，并建议修正（换代码、缩小 n、检查数据库等）。

## 出错自愈清单（Self-Heal）

| 症状                                                   | 处方                                                                 |
| ------------------------------------------------------ | -------------------------------------------------------------------- |
| `can't open file '...scripts\"...\"'` / `Errno 22`      | 触犯了引号规则。去掉 `working_dir` 参数 + 去掉所有引号，重试一次      |
| `No such file or directory ... forecast.py`             | 路径写错，按模板 `skills/arima-forecast/scripts/forecast.py` 再试     |
| `错误：未配置 DATABASE_URL` / MySQL 连接失败 | 与后端共用 **`DATABASE_URL`**；确认进程能读到 `.env`，且库中有 `stock_daily` |
| `错误：近一年数据仅 N 条，不足 80 条`                    | 样本不够，换成交投活跃的大盘股（如 `600519.SH`）再试                 |
| `错误：预测天数超出范围`                                | `--n` 必须 1~60，修正后重试                                           |
| `ARIMA 拟合失败`                                        | 换一只股票或缩短样本，还不行就告诉用户并停止                         |

> ⚠️ 如果同一个错误你已经试过 2 次，**立刻停止**，把原始错误返回给用户。

## 解读要点

- **前几天预测较可信**，越往后不确定性越大（置信带越宽）
- **95% 置信区间**覆盖 ±2σ，代表统计上 95% 的概率落入此范围
- ARIMA 擅长**趋势延续**，不擅长预测**拐点**——遇到重大事件驱动的行情反转，预测会严重滞后
- 样本不足 80 条会直接报错；预测步长上限 60

## 免责

所有预测仅供技术分析学习，**不构成任何投资建议**。请在最终回复里带上这句话。
