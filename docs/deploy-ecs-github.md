# GitHub `master` 推送 → 阿里云 ECS（1Panel）自动部署

## 思路

```text
本地 git push origin master
    → GitHub Actions（ubuntu-latest）触发
    → 使用密钥 SSH 登录你的 ECS
    → 进入已克隆的仓库目录：git fetch + reset 到远端 master，保证与仓库一致
    → docker compose build / up（按你选的 compose 文件）
```

不要求在 ECS 上再装 Jenkins；1Panel 里可以继续用面板看容器，只是「发版命令」改成由 GitHub 远程触发。

---

## 你需要准备的东西

### 1. ECS 上一次性的目录与克隆

任选目录，例如 `/opt/stock-chat-bi`：

```bash
sudo mkdir -p /opt/stock-chat-bi && sudo chown "$USER:$USER" /opt/stock-chat-bi
cd /opt/stock-chat-bi
git clone git@github.com:<你的>/<仓库名>.git .
# 或 HTTPS + PAT；保证之后 Actions 在同一目录能 pull / reset
```

**建议**：为该目录单独放一个 [Deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys#deploy-keys)（只读即可），ECS 保留 **公钥**，GitHub 仓库 **Settings → Deploy keys** 添加。

### 2. SSH 登录方式（交给 GitHub Secrets 的那把钥匙）

任选其一：

- **A**：在 ECS 上使用专门用户（如 `deploy`），为该用户写入 **GitHub Actions 使用的公钥**，私钥整段粘贴到 Secret `DEPLOY_SSH_KEY`。  
- **B**：使用现有用户的密钥对，同样私钥放进 `DEPLOY_SSH_KEY`。

在 ECS `/etc/ssh/sshd_config` 里保证 `PubkeyAuthentication yes`，并确保安全组放行 **22**（或你改的端口），来源可先收紧为你的家用 IP，按需再放宽。

### 3. 生产环境与 Compose（重点：GitHub Actions 不负责带 `.env`）

**Secrets 不会在仓库里**。Workflow 只做 `git fetch` + `reset`，**不会删掉** ECS 上已经存在、`gitignore` 忽略的文件——因此推荐在服务器上保留「仅存在于机器上的配置」：

| 做法 | 说明 |
|------|------|
| **`backend/.env`（推荐与当前 compose 一致）** | 在 ECS 克隆目录执行一次：`cp backend/.env.example backend/.env`，按需填写 `DATABASE_URL`、`JWT_SECRET`、`CORS_ALLOW_ORIGINS`、LLM 相关变量。该文件不进 Git，`git reset` **不会清除**它。根目录 **`docker-compose.yml` 已对 `backend` 使用 `env_file: ./backend/.env`**，部署后后端直接读这台机器上的密钥。 |
| **`docker-compose.override.yml`（仅放在服务器）** | 不写进仓库；与 `docker-compose.yml` **同目录** 时 Compose 会自动合并。**默认不必为前端单独配域名**：容器内 **前端 Nginx 已将 `/api` 反向代理至 `backend`**，浏览器与 API 同源。override 常用于宿主机映射端口、与宿主机反代配合等。 |

**数据库**：默认 **`docker-compose.yml` 不含 MySQL 服务**；`DATABASE_URL` 指向 **1Panel / 宿主已有 MySQL**（可用 `host.docker.internal`，见 `backend/.env.example` 与 compose 中 `extra_hosts`）。需要随 compose 起内置库时用 **`docker-compose-with-mysql.yml`**，或在外置库上自行执行 `backend/sql/init.sql`。

**不推荐**把整份 `.env` 贴进 **GitHub Actions Secrets** 再在流水线里 `echo > backend/.env`：密钥会进入 Actions 日志/暴露面（除非团队已规范脱敏轮换）；若坚持该方式，请用单独的「写入 env」步骤并做好权限与审计。

**前端与 API 路径**：镜像构建时 `VITE_API_BASE_URL=/api`（见 `docker-compose.yml` 的 `build.args`），页面请求 **`/api/*`** 由 **前端容器内 Nginx** 转到 FastAPI。Compose 将 **宿主机 `5173`** 映射到容器 80，避免与整机/面板的 **80** 冲突；在 **1Panel** 里把站点反代到 **`http://127.0.0.1:5173`**（或内网 IP）即可。一般**无需**再为 Vite 填公网 `VITE_API_BASE_URL`（除非 API 与页面不同源，需另做路径或拆站）。

本 CI 仅负责「拉代码 + compose 再起」。生产 compose 默认不改时，可把 Secret **`DEPLOY_COMPOSE_FILES`** 留空（见下）。

### 4. GitHub Actions 使用的 Secrets

在 **Settings → Secrets and variables → Actions** 中可放在 **Repository secrets**，或放在 **Environments**（如 `prod`）下的 **Environment secrets**。

| Name | 必填 | 说明 |
|------|------|------|
| `DEPLOY_HOST` | ✅ | ECS 公网 IP 或可解析域名 |
| `DEPLOY_USER` | ✅ | SSH 用户名，如 `root` |
| `DEPLOY_SSH_KEY` | 与下二选一 | SSH **私钥**全文（推荐）；用密码登录时不要配置此项（或删除该 Secret） |
| `DEPLOY_PASSWORD` | 与上二选一 | **SSH 登录密码**；服务器 `sshd` 须允许 `PasswordAuthentication`（或等价配置） |
| `DEPLOY_PORT` | 否 | SSH 端口，不配则工作流里默认 `22` |
| `DEPLOY_APP_DIR` | ✅ | 服务器上克隆目录的绝对路径，如 `/opt/stock-chat-bi` |
| `DEPLOY_COMPOSE_FILES` | 否 | 若不用默认 `docker-compose.yml`，可填 `-f docker-compose.yml -f docker-compose.prod.yml`（含 `-f`，多个文件用空格） |
| `DEPLOY_SSH_KEY_PASSPHRASE` | 否 | 仅当 **`DEPLOY_SSH_KEY` 对应的私钥文件本身还设置了密码** 时填写；无密码则不要建此项 |

**认证方式**：**私钥**与**密码**二选一即可；同时配置时，以 `appleboy/ssh-action` 实际行为为准（一般优先密钥）。**只用密码**时，建议删除（或不要创建）`DEPLOY_SSH_KEY`，避免误传空/错内容导致仍去解析私钥。

**关于 `DEPLOY_SSH_KEY`（私钥）**：必须是 **OpenSSH 可解析的私钥**，整段粘贴，例如：

- 以 **`-----BEGIN OPENSSH PRIVATE KEY-----`** 或 **`-----BEGIN RSA PRIVATE KEY-----`** 开头，以对应的 **`-----END ... PRIVATE KEY-----`** 结尾；
- **一行都不可少**，含中间换行（在 GitHub Secret 编辑框里粘贴多行即可，**不要**写成一行无换行、也不要在前后加引号）；
- **不要**把 **`.pub` 公钥** 填进 `DEPLOY_SSH_KEY`；
- **PuTTY 的 `.ppk`** 需先转成 OpenSSH 格式再粘贴，例如：  
  `puttygen key.ppk -O private-openssh -o id_ed25519`，再把 `id_ed25519` 全文粘到 Secret。

若仍报 `ssh: no key found`，多为 Secret 内容为空、剪贴板缺了头尾行，或私钥与 ECS 上 **`authorized_keys`** 里对应的公钥不匹配（请确认配的是 **配对** 的一把私钥）。

SSH 校验主机指纹：若在 Actions 里出现 `REMOTE HOST IDENTIFICATION HAS CHANGED`，把 ECS 当前的 `ssh-keyscan -p端口 host` 的一行可考虑写入 **`DEPLOY_HOST_FINGERPRINT`**（见 Workflow 注释；也可先用手动跑一次 SSH 信任的流程）。

---

## 工作流文件位置

- `.github/workflows/deploy-ecs.yml`：推送到 `master` 时 SSH 部署。

可在 **Actions** 面板里点开每次运行查看日志。

---

## 与 1Panel 的关系

- **1Panel** 负责装 Docker / 看图形容器都行；自动化部署本质是 **ECS 主机上的 shell + docker compose**。
- 若你把应用完全交给「1Panel 应用商店」单机运行，则可能与本仓库自带的 `docker compose` 路径不一致，需要把你的实际启动命令改成与 `DEPLOY_APP_DIR` / compose 路径一致。
- 推荐：**项目在固定目录 git 管理**，1Panel **只用作监控**，发版只靠本 Workflow。

---

## 故障排查清单

| 现象 | 可能原因 |
|------|----------|
| SSH timeout | 安全组未放行、`sshd` 未监听、IP 变了 |
| Permission denied | 私钥与用户不匹配、`authorized_keys` 未配置；或**用密码**时密码错误 / 服务器禁止密码登录 |
| git pull / reset 失败 | 目录未 clone、`origin`、Deploy key |
| compose 报错 | 服务器 `.env` 缺失、端口冲突、磁盘满 |
| `missing server host` | Secrets 写在 **Environment** 但 workflow 未写对应 **`environment:`**；或未配置 `DEPLOY_HOST` |
| `ssh.ParsePrivateKey: no key found` | `DEPLOY_SSH_KEY` 不是合法私钥 PEM（空值、只粘了公钥、缺头尾行、PuTTY 未转换、或私钥有密码却未配 **`DEPLOY_SSH_KEY_PASSPHRASE`**） |
| `unable to authenticate` / `attempted methods [none]` | 私钥与服务端 `authorized_keys` 中**公钥不匹配**、或 `DEPLOY_USER` 不是该密钥所在用户、或服务器仅允许密码登录 |

如需「仅打标签才部署」或「手动 workflow_dispatch」，可在 workflow 里加 `workflow_dispatch` 或 `release` 条件。
