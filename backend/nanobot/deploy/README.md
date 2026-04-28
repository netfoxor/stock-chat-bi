# nanobot 离线部署指引（1Panel）

不走 Docker Hub，把镜像打包成 `tar.gz` 离线文件，通过 1Panel 面板导入后直接用 Compose 编排启动。

---

## 0. 服务器前提

- Linux + 1Panel 已装好
- 1Panel 里已启用 **容器 → Docker** 模块（1Panel 会自动装 Docker + Compose v2）
- 防火墙 / 云安全组放行 **10001/TCP**（或你打算对外暴露的端口）

---

## 1. 开发机（Windows）：构建离线包

```powershell
cd nanobot
powershell -ExecutionPolicy Bypass -File deploy\build_and_save.ps1
```

产物（文件名带打包时间戳，便于同目录留历史版本）：

```
nanobot/deploy/dist/nanobot-image-YYYYMMDD-HHmmss.tar.gz     # 镜像（含 SQLite 数据库）
```

预计 gzip 后体积 ~300 MB。下文统一以 `nanobot-image-*.tar.gz` 指代这个文件。

> 如果这一步报 `python:3.11-slim-bookworm ... dial tcp 198.18.0.157:443 ...failed`，
> 是 Docker Desktop 的 registry mirror 跟本机代理冲突，见文末 **常见问题**。

---

## 2. 在开发机整理上传包

把下面这 3 个文件放一起（比如 `nanobot-deploy/` 临时目录），准备上传：

| 文件 | 来源 |
|---|---|
| `nanobot-image-*.tar.gz` | `deploy/dist/nanobot-image-YYYYMMDD-HHmmss.tar.gz`（最新那个） |
| `docker-compose.yml` | 仓库根 |
| `.env` | 复制仓库根的 `.env.example`，填入 `DASHSCOPE_API_KEY=sk-xxxx` |

---

## 3. 1Panel 面板操作

### 3.1 上传文件

**菜单**：`主机 → 文件`

1. 进入 `/opt/`，新建文件夹 `nanobot`，进入该目录
2. 点右上角 **上传**，把 `docker-compose.yml`、`.env`、`nanobot-image-*.tar.gz` 三个文件全部上传
3. 确认 `.env` 文件存在（有些浏览器会默认隐藏 `.` 开头文件，1Panel 通常会显示）

> 路径最终长这样（文件名里的时间戳以你打包当天为准）：
> ```
> /opt/nanobot/
> ├── docker-compose.yml
> ├── .env
> └── nanobot-image-20260421-175900.tar.gz
> ```

### 3.2 导入离线镜像

**菜单**：`容器 → 镜像`

1. 点右上角 **导入镜像**
2. 来源选 **服务器文件**，路径填 `/opt/nanobot/nanobot-image-<时间戳>.tar.gz`
3. 确认，等几秒钟，刷新镜像列表
4. 列表里应该能看到 `nanobot-app:latest`，体积约 800 MB

> 如果面板没有"服务器文件"选项，可以直接在 SSH 里跑：
> ```bash
> cd /opt/nanobot && gunzip -c nanobot-image-*.tar.gz | docker load
> ```
> 同样会把 `nanobot-app:latest` 导入到本机 Docker。
> 镜像 tag 固定是 `nanobot-app:latest`，所以不管哪个时间戳的包导入后都会覆盖更新；
> 服务器磁盘紧张的话可以 `rm` 掉旧的 `nanobot-image-*.tar.gz`。

### 3.3 创建 Compose 编排

**菜单**：`容器 → 编排`

1. 点右上角 **创建编排**
2. 填写：
   - **名称**：`nanobot`（或自定义）
   - **来源**：选 **本地目录**
   - **目录**：`/opt/nanobot`
3. 1Panel 会自动识别该目录下的 `docker-compose.yml` 和 `.env`
4. 点 **确定**

### 3.4 启动

回到 **编排** 列表：

1. 找到刚创建的 `nanobot` 行
2. 点 **启动**（或"应用"）
3. 稍等 10~30 秒，状态变成 `running`

### 3.5 验证

**菜单**：`容器 → 容器`

- 找到容器名 `nanobot`，点 **日志** 查看启动日志；出现 `Your app is available at http://0.0.0.0:10001` 代表 OK
- 浏览器访问 `http://<服务器 IP>:10001`
- 首页能看到"股票查询助手已就绪"即成功

---

## 4. 常用运维（1Panel UI）

| 操作 | 位置 |
|---|---|
| 看日志 | 容器 → 容器 → `nanobot` → **日志** |
| 重启 | 容器 → 编排 → `nanobot` → **重启** |
| 停止 | 容器 → 编排 → `nanobot` → **停止** |
| 改密钥 / 端口 | 主机 → 文件 → `/opt/nanobot/.env` → 编辑，再到编排里 **重启** |
| 更新镜像 | 传新的 `nanobot-image-*.tar.gz` → 镜像 → 导入 → 编排 → 重启 |

---

## 5. 常见自定义

改 `/opt/nanobot/.env`（改完到 1Panel 编排里点"重启"生效）：

```bash
# 必填
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx

# 改映射端口（默认 10001，如果冲突可改）
HOST_PORT=11001

# 改模型（默认 qwen-plus）
QWEN_AGENT_MODEL=qwen-max
```

改端口后记得在 1Panel 的"主机 → 防火墙"和云安全组里同步开放新端口。

---

## 6. 常见问题：`docker pull` 拉不到 base image

打包脚本第一步需要 `python:3.11-slim-bookworm`，没拉过时会自动 `docker pull`。这一步若失败，通常是 **Docker Desktop 的 registry mirror 与系统代理冲突**，典型错误：

```
dialing docker.1panel.live:443 ...
matches static system exclude
dial tcp 198.18.0.157:443: connectex: A connection attempt failed ...
```

解读：
- `198.18.0.0/15` 是 Clash / V2Ray 等代理工具用于劫持域名解析的 **Fake IP** 段
- `matches static system exclude` 说明 Docker Desktop 的 **Proxies → Bypass** 列表里把这个域名设成"绕过代理直连"
- 于是 Docker 用 Fake IP 去直连（不走代理）→ 连不到真的 mirror 服务器

### 三种修法（任选一个）

**A. 清空 Docker Desktop 的 Bypass 列表**（最稳）

Docker Desktop → Settings → Resources → **Proxies** → 「Bypass proxy settings for these hosts」
删掉涉及 `docker.*` / `*.1panel.live` / `*.m.daocloud.io` / `198.18.*` 之类的条目 → Apply & Restart。

**B. 换 registry mirror**

Docker Desktop → Settings → **Docker Engine**，改 `registry-mirrors`：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerpull.com"
  ]
}
```

保存 → Apply & Restart。

**C. 直连 Docker Hub**

如果代理本身能让 Docker Desktop 通到 `registry-1.docker.io`，就在 Docker Engine 配置里把 `registry-mirrors` 整行删掉。

### 修好之后

```powershell
docker pull python:3.11-slim-bookworm      # 能拉成功就 OK
powershell -ExecutionPolicy Bypass -File deploy\build_and_save.ps1
```

脚本第二次执行时会看到 base image 已在本地，跳过 pull，并用 `docker build --pull=never` 构建，**完全不再联 registry**，稳定出包。

---

## 7. 为什么 DB 打进镜像

本项目是 demo，`stock_prices_history.db` 只有 ~1 MB，静态数据、不热更新，打进镜像最省心：一次传输就完整跑起来，不用再单独管数据目录。

如果以后换成在线更新的大库：

1. 把 db 从镜像里剥离，放到宿主目录 `/opt/nanobot/data/stock_prices_history.db`
2. 在 `docker-compose.yml` 里加 volume：`- ./data:/app/data`
3. `.env` 里加：`STOCK_DB_PATH=/app/data/stock_prices_history.db`
4. 在 1Panel 编排里点 **重启** 生效

（`stock_core.py` 已经支持 `STOCK_DB_PATH` 环境变量，代码无需改动。）
