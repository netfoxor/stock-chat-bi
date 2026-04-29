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

### 离线脚本计划任务（生产 / 自建服务器）

`backend` 下有两条离线脚本：`fetch_stock_codes.py`（AkShare，股票代码增量入库）、`fetch_stock_prices.py`（Tushare，日线入库）。均需 **`backend/.env`** 配置 **`DATABASE_URL`**；日线脚本另需 **`TUSHARE_TOKEN`**。脚本已通过 `pydantic-settings` 读 `.env`，**工作目录设为 `backend` 即可**，无需再在 shell 里 `export`。

**调度建议**：股票代码建议在**夜里 03:00**跑一次（体量小、对齐交易所更新）；日线建议在 **17:00** 跑一次（与本仓库 `fetch_stock_prices.py` 中 **CUTOFF_HOUR=17**、Tushare 当日口径一致，收盘后数据源更就绪）。请将机器时区设为 **`Asia/Shanghai`**，否则 Cron 指向的本地时间会偏。

下边将项目根目录写成 **`<repo>`**，请换成你服务器上的克隆路径（例如 `/opt/stock-chat-bi`）；虚拟环境路径若在别处，按需替换。

#### Linux `crontab`（`*nix`）

```bash
# 首次：编辑当前用户的 crontab
crontab -e

# 追加两行（示例：venv 在项目 backend/.venv，Python 改用 .venv/bin/python）
0 3 * * * cd <repo>/backend && <repo>/backend/.venv/bin/python fetch_stock_codes.py >> <repo>/backend/logs/fetch_codes_cron.log 2>&1
0 17 * * * cd <repo>/backend && <repo>/backend/.venv/bin/python fetch_stock_prices.py >> <repo>/backend/logs/fetch_prices_cron.log 2>&1
```

说明：`cron` 环境变量极简，上边用 **`cd .../backend`** 再配合**绝对路径 Python**，可避免找不到依赖；首次若不存在 `logs/`，可先 `mkdir -p backend/logs`。**不要用全仓根目录当作 `cwd`**（除非你愿意改脚本里 `.env` 的路径），否则应保持 **`cd`** 到 `backend`。确认手工执行 `./.venv/bin/python fetch_stock_codes.py` 在服务器的同一用户在 `backend` 下能跑通再上定时任务。

如需用 **`systemd` timer`** 或其它调度器，把上述两行里的 **`cd`** + **`python ...`** 原样搬进 `ExecStart=` 或使用 `WorkingDirectory=` 指向 **`<repo>/backend`**、`ExecStart=` 指向 **`<repo>/backend/.venv/bin/python fetch_stock_prices.py`** 亦可。

#### 1Panel「计划任务」

1Panel 各版本菜单名称可能略有出入，一般有 **「计划任务」或「Cron 任务」**。
1. 进入 **计划任务 → 新建任务**，类型选择 **Shell 脚本**。
2. **股票代码**：任务名自定（如「股票代码增量」）；**执行周期** 选 **Cron 表达式**，填 **`0 3 * * *`**（每日 03:00）；脚本内容填入一行（按需改 `<repo>` 与 venv）：  
   `cd <repo>/backend && <repo>/backend/.venv/bin/python fetch_stock_codes.py >> <repo>/backend/logs/fetch_codes_cron.log 2>&1`
3. **日线行情**：同理再建一条；Cron  **`0 17 * * *`**（每日 **17:00**）；脚本：  
   `cd <repo>/backend && <repo>/backend/.venv/bin/python fetch_stock_prices.py >> <repo>/backend/logs/fetch_prices_cron.log 2>&1`
4. 保存后可点 **「执行」**做一次测试；在 **logs/** 查看输出与脚本内置日志（日线默认还有 `logs/fetch_stock_prices_YYYY-MM-DD.log`）。

若在 1Panel 里用 **`www`** 用户跑任务，请在「用户」一栏选对应用户或保证该用户对 **`<repo>`** 可读、对 **`backend/.env`** 与 **`backend/logs`** 可写。**Docker 单机部署**：若后端代码仅在容器内有环境，可把 `ExecStart` 换成 `docker compose exec -T backend ...`，先确认非交互、`DATABASE_URL` 在容器侧可用。

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
