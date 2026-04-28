# stock-chat-bi

一个“AI 对话驱动的数据查询 + 可视化 + 大屏持久化”的股票分析 demo。

## 本地开发（无 Docker）

### 后端（FastAPI）

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 配置环境变量（可复制 backend/.env.example）
export DATABASE_URL="mysql+aiomysql://root:root@localhost:3306/stock_analysis?charset=utf8mb4"
export JWT_SECRET="please-change-me"
export DASHSCOPE_API_KEY="your-key"

uvicorn app.main:app --reload --port 8000
```

### 前端（Vite）

```bash
cd frontend
pnpm install
pnpm dev
```

默认前端访问 `http://localhost:5173`，后端 API 为 `http://localhost:8000/api`。

## Docker Compose

> 需要本机有 Docker/Compose。

```bash
docker compose up --build
```

包含服务：
- `mysql`: 初始化脚本见 `backend/sql/init.sql`
- `backend`: FastAPI（`8000`）
- `frontend`: Vite dev server（`5173`）

