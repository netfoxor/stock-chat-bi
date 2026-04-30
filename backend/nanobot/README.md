# 股票查询助手（nanobot 版）

基于 [nanobot-ai](https://pypi.org/project/nanobot-ai/) 与通义千问（或其它 OpenAI-compatible 网关）的 A 股自然语言查询 / 分析助手。

**Web 主站**走后端 FastAPI（`app/services/nanobot_service.py` → `stock_bot.py`），仓库内不再包含 Chainlit 独立前端。

能力概览：

- **SQL 查询** —— 工具 `exc_sql`，结果可渲染为表格与 ECharts（主站使用语言标签 `echarts` 的 fenced 块）
- **ARIMA 收盘价预测** —— skill `arima-forecast`，LLM 通过 `exec` 调用脚本
- **布林带超买 / 超卖检测** —— skill `bollinger`，同上

---

## 启动

```powershell
pip install -r requirements.txt
$env:DASHSCOPE_API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"
# 推荐：使用 backend/.env（复制 backend/.env.example），后端与 CLI 都会加载
```

```powershell
# 单轮问答
python stock_bot.py "用 ARIMA 预测贵州茅台未来 10 个交易日的收盘价"

# 交互式 REPL
python stock_bot.py

# 生产部署（含前端）
# 在仓库根目录：docker compose up -d --build
# 说明见 deploy/README.md
```

Linux / macOS：把 `$env:X = "..."` 换成 `export X=...`。

---

## 目录结构

```
nanobot/
├── trace_ctx.py             # 执行轨迹（FastAPI 流式 trace）
├── trace_hook.py
├── stock_bot.py             # AgentLoop 组装 + CLI 入口
├── stock_core.py            # SQL 守卫 / 图表 JSON / run_query（与后端大屏共用）
├── self_heal_hook.py
├── stock_tools/
│   ├── __init__.py
│   └── exc_sql.py
├── orchestrator/
├── skills/
│   ├── arima-forecast/
│   ├── bollinger/
│   └── stock-sql/
├── charts/                  # [运行时] 脚本可选落盘 *.json（见 .gitignore）
├── sessions/                # [运行时] 会话 *.jsonl
├── memory/                  # [运行时] 其它缓存
├── config.json
├── requirements.txt
├── Dockerfile               # 仅依赖校验/调试；生产用仓库根 compose 构建 backend
├── AGENTS.md
└── deploy/
    ├── README.md
    └── build_and_save.ps1   # 已弃用，会提示改用根目录 compose
```

> **\[运行时\]** 目录勿当业务源码长期提交；图表 JSON、jsonl 可随时删、会再生成。

---

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `DASHSCOPE_API_KEY` | 网关二选一 | 阿里云 DashScope（通义） |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` | 同上 | OpenAI 兼容网关 |
| `DATABASE_URL` | 是 | MySQL，`mysql+aiomysql://…`，与 FastAPI、`exc_sql`、skill 脚本同源 |
| `QWEN_AGENT_MODEL` | | 覆盖 `config.json` 模型名 |

完整模板见 `backend/.env.example`。

---

## 示例问题

- 查询贵州茅台 2025 年全年日线
- 统计 2025 年 4 月广发证券日均成交量
- 用 ARIMA 预测五粮液未来 10 个交易日收盘价
- 检测广发证券某一区间的超买超卖

---

## 技术要点

- **AgentLoop**：工具调度、`skills/` 自动发现、会话记忆；编排见 `orchestrator/`。
- **图表**：脚本输出 fenced JSON（围栏语言标签 `echarts`）；主站前端解析渲染。`charts/` 目录仅备份 option，不向用户输出旧式 `chart:` 行。
- **self-heal**：`exec` 失败时捕获 stderr 供 LLM 重试（有次数上限）。
- **数据库**：MySQL，`trade_date` 为 `YYYY-MM-DD`（见 `skills/stock-sql/SKILL.md`）。

---

## 免责声明

所有预测与技术指标仅供学习参考，**不构成投资建议**。
