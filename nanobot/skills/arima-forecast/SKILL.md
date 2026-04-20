---
name: arima-forecast
description: 用 ARIMA(5,1,5) 对指定 A 股股票预测未来 1~60 个交易日的收盘价，输出预测表与 ECharts 图表（含 95% 置信带）。
metadata: {"nanobot":{"emoji":"📈","requires":{"bins":["python"]}}}
---

# ARIMA 收盘价预测 Skill

当用户问"**未来 n 个交易日收盘价**"、"**股价预测**"、"**趋势外推**"等问题时使用本 skill。
实际拟合与绘图由独立 Python 脚本完成，一次调用 = 一个子进程，**脚本崩溃不会影响主程序**。

## 运行

用 `exec` 工具执行脚本：

```bash
python "{baseDir}/scripts/forecast.py" --ts-code 600519.SH --n 10
```

参数：

- `--ts-code`（必填）：Tushare 代码，如 `600519.SH`、`000858.SZ`、`688981.SH`
- `--n`（必填）：预测交易日数，1~60

输出约定（非常重要）：

- **stdout** 是完整 markdown：包含预测表（forecast_date / forecast_close / ci_lower_95 / ci_upper_95）以及图表占位 `![ARIMA 预测](chart:charts/arima_xxx.json)`。
- **必须把 stdout 原样转发给用户**，包括那条 `chart:` 图表 markdown，前端会自动渲染。
- 非零 exit code / stderr 提示错误：把错误原因解释给用户，并建议修正（换代码、缩小 n、检查数据库等）。

## 常见股票代码

- 贵州茅台 `600519.SH`
- 五粮液 `000858.SZ`
- 广发证券 `000776.SZ`
- 中芯国际 `688981.SH`

## 解读要点

- **前几天预测较可信**，越往后不确定性越大（置信带越宽）
- **95% 置信区间**覆盖 ±2σ，代表统计上 95% 的概率落入此范围
- ARIMA 擅长**趋势延续**，不擅长预测**拐点**——遇到重大事件驱动的行情反转，预测会严重滞后
- 样本不足 80 条会直接报错；预测步长上限 60

## 免责

所有预测仅供技术分析学习，**不构成任何投资建议**。请在最终回复里带上这句话。
