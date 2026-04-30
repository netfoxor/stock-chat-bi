---
name: bollinger
description: 在指定区间用 MA20 ± 2σ 计算布林带并识别超买/超卖；stdout 依次输出语言标签为 echarts 与 datatable 的围栏（紧凑 JSON），与 exc_sql / 大屏契约一致。
metadata: {"nanobot":{"emoji":"📊","requires":{"bins":["python"]}}}
---

# 布林带信号检测 Skill

用户问「**布林带**」「**超买**」「**超卖**」「**上轨下轨**」「**布林通道**」等技术分析时使用本脚本。

脚本经 `stock_core` 读 **`stock_daily`**，与 `exc_sql` 共用 **`DATABASE_URL`**（MySQL）。

## 运行（严格按此格式，否则会踩 shell 引号陷阱）

用 `exec` 工具执行：

```
python3 skills/bollinger/scripts/detect.py --ts-code 600519.SH --start 2024-01-01 --end 2024-12-31
```

（区间可省略，规则见下文。）

**三条硬规定**（违反必挂）：

1. **不传 `working_dir`** —— `exec` 默认 cwd 就是 `nanobot/`。
2. **用正斜杠相对路径** `skills/bollinger/scripts/detect.py`，**不要**绝对路径或反斜杠。
3. **整个 command 里不加任何引号**（路径不含空格）。

参数：

- `--ts-code`（必填）：Tushare 代码
- `--start`（可选）：`YYYY-MM-DD`。**省略则与脚本内规则一致**：见下方区间表
- `--end`（可选）：`YYYY-MM-DD`。省略同上

区间规则：

| 参数组合                   | 实际区间                          |
| -------------------------- | --------------------------------- |
| 都不给                     | 近一年 → 今天                      |
| 仅 `--start`               | start → 今天                       |
| 仅 `--end`                 | (end − 1 年) → end                 |
| 都给                       | start → end（end 超过今天自动回退） |

输出约定（非常重要）：

- **stdout**：一行简述 + **` ```echarts` 在前、` ```datatable` 在后**。表格列为 `trade_date, close, mid_ma20, upper_2sigma, lower_2sigma, signal`（无触轨日 `signal` 为空）。
- **`datatable`** 至多 **500** 行，超出时在围栏前附有截断提示（与大屏同源逻辑）。
- 脚本仍写入 `charts/boll_*.json`（排查用，不参与聊天围栏）。
- **必须把 stdout 原样转发给用户**。
- **非零** exit code / stderr：**不要**捏造成功；把可读错误解释给用户。

## 大屏命名转换（与聊天无关，供你了解）

服务端支持 `transform_chart` / `transform_table` = **`bollinger_bands`**，参数 JSON：`ts_code`（必选），`start` / `end`（可选字符串，语义与脚本一致）。

## 指标规则（与脚本一致）

- **窗口** 20 日，`upper = MA20 + 2σ`、`lower = MA20 − 2σ`
- **超买**：收盘价 > upper
- **超卖**：收盘价 < lower
- 区间内少于 **25** 条日线会直接报错。

## 出错自愈清单（Self-Heal）

| 症状                                                   | 处方                                                             |
| ------------------------------------------------------ | ---------------------------------------------------------------- |
| `can't open file '...scripts\"...\"'` / `Errno 22`      | 去掉 `working_dir` + 去掉所有引号，重试一次                       |
| `No such file or directory ... detect.py`               | 路径写错，按模板再试                                               |
| `错误：数据库` / `DATABASE_URL`                        | 与后端共用环境变量；检查 `.env` 与 `stock_daily`                  |
| `错误：未找到 XXX 在 [...] 的日线数据`                 | 该区无行情，换代码或放宽区间                                       |
| `错误：仅有 N 条日线，不足 25 条`                      | 区间太短，`--start` 再往前拉长                                     |
| `错误：start_date / end_date 须为 YYYY-MM-DD`           | 日期格式改正后重试                                                 |

> ⚠️ 同一错误试 **2 次**未果则停手。

## 解读要点

- 频繁触轨≠必然反转；需结合量能、趋势与消息面。
- 带宽收窄后的突破常伴随波动率扩张。

## 免责

仅供技术分析学习，**不构成投资建议**。请在给用户说明时重申。
