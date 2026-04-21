# 股票查询助手（nanobot 版）

基于 [nanobot-ai](https://pypi.org/project/nanobot-ai/) + [Chainlit](https://chainlit.io/) + 通义千问的 A 股自然语言查询 / 分析助手。

本地 SQLite 存 A 股日线数据，LLM 通过三类能力回答问题：

- **本地 SQL 查询** —— 工具 `exc_sql`，结果会自动渲染成表格 + ECharts 交互图表
- **ARIMA 收盘价预测** —— skill `arima-forecast`，用 `exec` 跑 `statsmodels`
- **布林带超买 / 超卖检测** —— skill `bollinger`，用 `exec` 跑脚本

---

## 🚀 启动（这就是你忘的命令）

```powershell
# 1. 激活虚拟环境（随意用你自己的）
# 2. 装依赖（第一次运行需要）
pip install -r requirements.txt

# 3. 配 API Key（Windows PowerShell）
$env:DASHSCOPE_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"

# 4. 启动 Chainlit 前端（热重载）
chainlit run app_chainlit.py -w
```

默认监听 **http://localhost:8000**。

> Linux / macOS 把第 3 步改成 `export DASHSCOPE_API_KEY=sk-...` 即可。
> 也可以在 `nanobot/` 下放一份 `.env`（复制 `.env.example`），Chainlit 会自动加载。

### 其他启动方式

```powershell
# CLI 单轮问答（不起前端，调试好用）
python stock_bot.py "用 ARIMA 预测贵州茅台未来 10 个交易日的收盘价"

# CLI 交互式 REPL
python stock_bot.py

# Docker / 1Panel 离线部署
# 详见 deploy/README.md
```

---

## 📂 目录结构

```
nanobot/
├── app_chainlit.py          # Chainlit 前端入口（工具 trace 折叠 / 图表 emit）
├── stock_bot.py             # nanobot AgentLoop 组装 + CLI 入口
├── stock_core.py            # 底层：DB 路径 / 画图 / SQL 执行工具函数
├── self_heal_hook.py        # 工具失败时的自动重试 / 修复钩子
├── stock_tools/             # 常驻 in-process 工具（目前只有 exc_sql）
│   └── exc_sql.py
├── skills/                  # LLM 按需读取的技能包（SKILL.md + scripts/）
│   ├── arima-forecast/
│   ├── bollinger/
│   └── stock-sql/           # 表结构 / SQL 最佳实践（SQL 前必读）
├── public/elements/         # Chainlit CustomElement（React/JSX）
│   ├── EChart.jsx           # ECharts 图表（已与 Chainlit 主题联动）
│   └── ToolTrace.jsx        # 工具调用 trace 折叠块
├── charts/                  # 运行时生成的 echarts option JSON
├── data/                    # SQLite 库（stock_prices_history.db）
├── memory/                  # 会话记忆持久化
├── sessions/                # Chainlit 会话数据
├── config.json              # 模型 / 上下文窗口 / 工具配置
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── AGENTS.md                # 给 LLM 读的系统规范（能力索引 + 输出纪律）
└── deploy/
    ├── README.md            # 1Panel 离线部署完整步骤
    └── build_and_save.ps1   # Windows 下构建并打包镜像 tar.gz
```

---

## 🔑 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | ✅ | 阿里云 DashScope（通义千问）API Key |
| `QWEN_AGENT_MODEL` | | 覆盖 `config.json` 里的模型名，默认 `qwen3.6-plus` |
| `STOCK_DB_PATH` | | 覆盖 SQLite 库路径，默认 `nanobot/data/stock_prices_history.db` |
| `HOST_PORT` | | 仅 docker-compose 用，映射到宿主机的端口（默认 `10001`） |

完整模板见 [`.env.example`](./.env.example)。

---

## 💬 示例问题

启动后直接问就行：

- 查询贵州茅台 2025 年全年日线
- 统计 2025 年 4 月广发证券的日均成交量
- 对比 2025 年中芯国际和贵州茅台的涨跌幅
- 用 ARIMA 预测五粮液未来 10 个交易日的收盘价
- 检测广发证券 2025-01-01 到 2025-12-31 的超买超卖

LLM 会按需调用 `exc_sql` 或 skill 脚本，**前端会把表格和 ECharts 图表直接渲染到消息流**，工具调用的原始输入输出在每条消息里以折叠块形式保留，便于 debug。

---

## 🧱 技术要点

- **nanobot AgentLoop**：自带工具调度、`skills/` 目录自动发现（`SkillsLoader`）、会话记忆。
- **Chainlit CustomElement**：`EChart.jsx` 通过 CDN 懒加载 `echarts@5.5.1`，并监听 `<html>` 上的 `class="dark"` / `data-theme` 变化，**自动跟随 Chainlit 主题切换**。
- **self-heal hook**：`exec` 失败时自动捕获 stderr、回传给 LLM 让它调整参数重试（最多 2 次，避免死循环）。
- **数据库**：SQLite 静态数据（~1 MB），`trade_date` 列是 `YYYY-MM-DD` 字符串（注意：**绝对不要写 `20250101` 这种无连字符格式**，详见 `AGENTS.md`）。

---

## 🐳 Docker 部署

本地开发不需要 Docker。若要部署到服务器（离线环境 / 1Panel 面板），见：

- [`deploy/README.md`](./deploy/README.md) —— 完整的 1Panel 离线部署步骤（构建、打包、导入、编排）

快速自测：

```powershell
docker compose up -d --build
# 浏览器访问 http://localhost:10001
```

---

## ⚠️ 免责声明

所有预测与技术指标仅供学习参考，**不构成投资建议**。
