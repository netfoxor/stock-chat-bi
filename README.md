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

### 部署后：AI 助理打不开 / 发消息无反应

按下面逐项对照（浏览器开发者工具 → **Network** → `/api/chat/stream`）：


| 现象 | 常见原因 |
|------|----------|
| `403`、控制台报 **CORS** / Cross-Origin | **`backend/.env`** 里的 **`CORS_ALLOW_ORIGINS`** 仍是默认 localhost。请加上实际访问前台地址（含 **`https://`** 与端口），与地址栏完全一致；保存后 **`docker compose up -d --force-recreate backend`**。 |
| `401`、Not authenticated | 登录失效或 **`JWT_SECRET`** 部署时被改过；可重新登录。 |
| `502`、`504` | 前端 Nginx **反代后端**不可用或超时：`docker compose ps`，确认 **`frontend`** → **`backend`** 服务名与 `default.conf` 里 **`proxy_pass http://backend:8000`** 一致。 |
| `500`、日志里 nanobot / Key | **`DASHSCOPE_API_KEY`** 或 **`OPENAI_API_KEY`** 未写入 **`backend/.env`** 或未注入容器。 |

生产构建勿把 **`VITE_API_BASE_URL`** 设为 `localhost:8000`，否则访客浏览器会连自己电脑；Compose 默认为 **`/api`**。排障： **`docker compose logs backend -f`** 看 traceback。

### 1Panel（或宿主 Nginx）在 Docker 前端之前再代理一层时的注意点

链路常见为：**浏览器 → 1Panel/HTTPS → 宿主机映射到 `:5173`（frontend 容器 80）→ 容器内再反代 `/api` → backend**。  
`/api/chat/stream` 使用 **SSE 流式**，若**外层** Nginx **`proxy_buffering` 仍为默认 on**，字节会被攒满整块再下发，看起来像「调用中卡住、结束时才突然出现」——**需在「最外层」关掉缓冲并拉长超时**（与 `frontend/nginx/default.conf` 里 `/api/` 语义一致）。

**推荐（与本仓库 `docker-compose` 一致）**：外层用两段 **`server {}`**：**80** 放行证书并已跳 **HTTPS**，**443** 下 **`location /`** 反向代理 **`http://127.0.0.1:5173`**。**无需你再拆 `/api`/改参数**，文件内已为 SSE（AI 流式）写好缓冲与时间。请直接打开仓库：**`docs/nginx-1panel-stock.incredily.com.example.conf`**，整段拷贝到站点配置后 **`nginx -t && nginx -s reload`**（若与同站点旧 `server {}` 冲突，先删掉旧块）。

若历史原因只能拆 **`location /api/`**（不推荐除非你改了前端 base），至少保证：**`proxy_buffering off`、`proxy_request_buffering off`、`proxy_cache off`、`gzip off`、超长 `proxy_read/send_timeout`**：

```nginx
# 示意：外层反代到 docker 映射端口；关键参数是 buffering / timeout
location /api/ {
    proxy_pass http://127.0.0.1:<前端容器映射端口>/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_request_buffering off;
    proxy_cache off;
    gzip off;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
}
```

或使用 1Panel 若提供「禁用代理缓冲 / 自定义高级配置」，开启与上述等价项。改完后重载外层 Nginx，并 **`docker compose up -d --build frontend`** 以带上仓库内 **`frontend/nginx/default.conf`** 的更新。

---

**关于 nanobot**：后端 **`Dockerfile`** 已将 **`backend/nanobot`** 拷贝进镜像，`nanobot_service` 的路径指向 **`/app/nanobot`**，与本地一致；若在仓库内 **`build_bot()` / LLM Key** 正常，一般不会因「只跑了 `app/`」而丢模块。卡在「大模型.chat.completion · 调用中」更常见是 **LLM HTTP 出站慢 / 超时** 或 **前级代理缓冲**。请对照 **`docker compose logs backend -f`** 里是否有 OSS/鉴权/OpenAI/Dashscope 报错或长时间无返回。
