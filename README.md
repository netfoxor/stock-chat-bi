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
export DATABASE_URL="mysql+aiomysql://root:root@localhost:3306/stock?charset=utf8mb4"
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

默认前端访问 `http://localhost:5173`，请求走 **`/api/**`**，由 Vite 代理到 `http://127.0.0.1:8000`（与生产 Nginx 同源路径一致）。

## Docker Compose（默认外置数据库）

假定 **MySQL 已由 1Panel 或宿主安装**，先在 **`backend/.env`** 配置 `DATABASE_URL`（可从 `backend/.env.example` 复制；容器连宿主库可参考其中的 `host.docker.internal`）。

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

包含服务：**`backend`**（`8000`）、**`frontend`**（`5173` → 容器内 Nginx 反代 `/api`）。

需要 **自带 MySQL 容器** 时（例如本地一键起库）：

```bash
docker compose -f docker-compose-with-mysql.yml up --build
```

初始化脚本仍见 `backend/sql/init.sql`（外置库需自行导入或执行一次）。

**构建加速**：Compose 已为镜像构建配置了国内 **PyPI（清华）**、**npm（npmmirror）**；若在境外构建，可自行修改根目录 **`docker-compose*.yml`** 里的 `PIP_INDEX_URL` / `NPM_REGISTRY`（或改为官方默认值）。
