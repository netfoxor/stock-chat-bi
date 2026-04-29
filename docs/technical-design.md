# 股票数据分析系统 · 技术方案书

> 版本：v1.0  
> 日期：2026-04-28

---

## 一、整体架构

```
┌─────────────────────────────────────────┐
│              浏览器（React）              │
│  ┌─────────────────┐  ┌───────────────┐ │
│  │  大屏 Dashboard  │  │  AI 聊天面板  │ │
│  │ react-grid-layout│  │  SSE 流式输出 │ │
│  └────────┬────────┘  └──────┬────────┘ │
└───────────┼──────────────────┼──────────┘
            │ REST API          │ SSE / REST
┌───────────┼──────────────────┼──────────┐
│           │    FastAPI 层     │          │
│  ┌────────┴────────┐  ┌──────┴────────┐ │
│  │   业务 API       │  │  SSE 代理转发 │ │
│  │ 认证/会话/大屏   │  │  nanobot 接入 │ │
│  └────────┬────────┘  └──────┬────────┘ │
└───────────┼──────────────────┼──────────┘
            │                  │
     ┌──────┴──────┐    ┌──────┴──────┐
     │    MySQL    │    │   nanobot   │
     │  业务数据库  │    │  SQL+ECharts│
     └─────────────┘    └─────────────┘
```

---

## 二、目录结构

```
stock-analysis/
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── api/                # 接口请求封装
│   │   ├── components/
│   │   │   ├── Chat/           # 聊天相关组件
│   │   │   │   ├── ChatPanel.tsx
│   │   │   │   ├── MessageList.tsx
│   │   │   │   ├── MessageItem.tsx
│   │   │   │   ├── ChatInput.tsx
│   │   │   │   └── StreamRenderer.tsx
│   │   │   ├── Dashboard/      # 大屏相关组件
│   │   │   │   ├── DashboardPage.tsx
│   │   │   │   ├── GridLayout.tsx
│   │   │   │   ├── WidgetWrapper.tsx
│   │   │   │   ├── ChartWidget.tsx
│   │   │   │   └── TableWidget.tsx
│   │   │   └── Auth/           # 登录注册
│   │   ├── store/              # Zustand 状态管理
│   │   │   ├── authStore.ts
│   │   │   ├── chatStore.ts
│   │   │   └── dashboardStore.ts
│   │   ├── hooks/              # 自定义 hooks
│   │   │   ├── useSSE.ts
│   │   │   └── useDashboard.ts
│   │   ├── types/              # TypeScript 类型定义
│   │   └── utils/              # 工具函数
│   ├── package.json
│   └── vite.config.ts
│
├── backend/                    # Python 后端
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── routers/
│   │   │   ├── auth.py         # 登录注册接口
│   │   │   ├── chat.py         # 会话/消息接口
│   │   │   ├── dashboard.py    # 大屏接口
│   │   │   └── proxy.py        # nanobot SSE 代理
│   │   ├── models/             # SQLAlchemy 模型
│   │   │   ├── user.py
│   │   │   ├── conversation.py
│   │   │   ├── message.py
│   │   │   └── widget.py
│   │   ├── schemas/            # Pydantic 校验模型
│   │   ├── core/
│   │   │   ├── auth.py         # JWT 工具
│   │   │   ├── config.py       # 配置读取
│   │   │   └── database.py     # DB 连接
│   │   └── nanobot/            # nanobot 源码集成
│   ├── requirements.txt
│   └── .env.example
│
├── docker-compose.yml
└── README.md
```

---

## 三、技术选型

### 前端

| 分类 | 技术 | 说明 |
|------|------|------|
| 框架 | Vite + React 18 + TypeScript | 构建快，类型安全 |
| UI 组件库 | Ant Design 5 | 中后台首选，AI 生成质量高，配置驱动 |
| 图表 | ECharts（echarts-for-react） | 配置驱动，天然适合 LLM 动态生成 |
| 大屏布局 | react-grid-layout | 拖拽、缩放、布局序列化 |
| 状态管理 | Zustand | 轻量，无样板代码 |
| Markdown | react-markdown + remark-gfm + rehype-highlight | 代码高亮+表格支持 |
| 请求 | axios | REST 接口 |
| SSE | 原生 fetch + ReadableStream | 流式输出处理 |
| 路由 | React Router v6 | 页面路由 |

### 后端

| 分类 | 技术 | 说明 |
|------|------|------|
| 框架 | FastAPI | 异步，自动生成 API 文档 |
| 数据库 ORM | SQLAlchemy 2.0 + aiomysql | 异步 MySQL 操作 |
| 数据校验 | Pydantic v2 | 请求/响应模型 |
| 认证 | python-jose + passlib | JWT 签发，密码 bcrypt 加密 |
| SSE 代理 | httpx + FastAPI StreamingResponse | 转发 nanobot SSE 流 |
| 数据库 | MySQL 8.0 | 股票数据 + 业务数据共用 |
| 环境配置 | python-dotenv | .env 文件管理 |

---

## 四、数据库设计

```sql
-- 用户表
CREATE TABLE users (
  id          INT PRIMARY KEY AUTO_INCREMENT,
  username    VARCHAR(50) UNIQUE NOT NULL,
  password    VARCHAR(255) NOT NULL,  -- bcrypt hash
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 会话表
CREATE TABLE conversations (
  id          INT PRIMARY KEY AUTO_INCREMENT,
  user_id     INT NOT NULL,
  title       VARCHAR(200) DEFAULT '新会话',
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 消息表
CREATE TABLE messages (
  id              INT PRIMARY KEY AUTO_INCREMENT,
  conversation_id INT NOT NULL,
  role            ENUM('user', 'assistant') NOT NULL,
  content         TEXT NOT NULL,       -- 原始文本/Markdown
  extra           JSON,                -- ECharts option 或 Table 数据
  content_type    ENUM('text', 'chart', 'table') DEFAULT 'text',
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

-- 大屏组件表
CREATE TABLE dashboard_widgets (
  id          INT PRIMARY KEY AUTO_INCREMENT,
  user_id     INT NOT NULL,
  title       VARCHAR(200) DEFAULT '未命名',
  type        ENUM('chart', 'table') NOT NULL,
  data        JSON NOT NULL,    -- ECharts option 或表格数据
  layout      JSON NOT NULL,    -- {x, y, w, h}
  created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

-- 股票编号表
CREATE TABLE `stock_code_list` (
  `ts_code` varchar(20) NOT NULL,
  `ak_code` varchar(16) NOT NULL,
  `stock_name` varchar(128) NOT NULL,
  `update_time` datetime DEFAULT NULL,
  PRIMARY KEY (`ts_code`),
  KEY `idx_stock_code_list_ak` (`ak_code`),
  KEY `idx_stock_code_list_name` (`stock_name`(64))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 股票日行情
CREATE TABLE `stock_daily` (
  `stock_name` varchar(128) NOT NULL,
  `ts_code` varchar(20) NOT NULL,
  `trade_date` date NOT NULL,
  `open` float DEFAULT NULL,
  `high` float DEFAULT NULL,
  `low` float DEFAULT NULL,
  `close` float DEFAULT NULL,
  `pre_close` float DEFAULT NULL,
  `change_val` float DEFAULT NULL,
  `pct_chg` float DEFAULT NULL,
  `vol` double DEFAULT NULL,
  `amount` double DEFAULT NULL,
  PRIMARY KEY (`ts_code`,`trade_date`),
  KEY `idx_trade_date_cover` (`trade_date`,`ts_code`,`close`,`pct_chg`,`vol`,`amount`),
  KEY `idx_stock_daily_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
/*!50100 PARTITION BY RANGE (year(`trade_date`))
(PARTITION p1990 VALUES LESS THAN (1991) ENGINE = InnoDB,
 PARTITION p1991 VALUES LESS THAN (1992) ENGINE = InnoDB,
 PARTITION p1992 VALUES LESS THAN (1993) ENGINE = InnoDB,
 PARTITION p1993 VALUES LESS THAN (1994) ENGINE = InnoDB,
 PARTITION p1994 VALUES LESS THAN (1995) ENGINE = InnoDB,
 PARTITION p1995 VALUES LESS THAN (1996) ENGINE = InnoDB,
 PARTITION p1996 VALUES LESS THAN (1997) ENGINE = InnoDB,
 PARTITION p1997 VALUES LESS THAN (1998) ENGINE = InnoDB,
 PARTITION p1998 VALUES LESS THAN (1999) ENGINE = InnoDB,
 PARTITION p1999 VALUES LESS THAN (2000) ENGINE = InnoDB,
 PARTITION p2000 VALUES LESS THAN (2001) ENGINE = InnoDB,
 PARTITION p2001 VALUES LESS THAN (2002) ENGINE = InnoDB,
 PARTITION p2002 VALUES LESS THAN (2003) ENGINE = InnoDB,
 PARTITION p2003 VALUES LESS THAN (2004) ENGINE = InnoDB,
 PARTITION p2004 VALUES LESS THAN (2005) ENGINE = InnoDB,
 PARTITION p2005 VALUES LESS THAN (2006) ENGINE = InnoDB,
 PARTITION p2006 VALUES LESS THAN (2007) ENGINE = InnoDB,
 PARTITION p2007 VALUES LESS THAN (2008) ENGINE = InnoDB,
 PARTITION p2008 VALUES LESS THAN (2009) ENGINE = InnoDB,
 PARTITION p2009 VALUES LESS THAN (2010) ENGINE = InnoDB,
 PARTITION p2010 VALUES LESS THAN (2011) ENGINE = InnoDB,
 PARTITION p2011 VALUES LESS THAN (2012) ENGINE = InnoDB,
 PARTITION p2012 VALUES LESS THAN (2013) ENGINE = InnoDB,
 PARTITION p2013 VALUES LESS THAN (2014) ENGINE = InnoDB,
 PARTITION p2014 VALUES LESS THAN (2015) ENGINE = InnoDB,
 PARTITION p2015 VALUES LESS THAN (2016) ENGINE = InnoDB,
 PARTITION p2016 VALUES LESS THAN (2017) ENGINE = InnoDB,
 PARTITION p2017 VALUES LESS THAN (2018) ENGINE = InnoDB,
 PARTITION p2018 VALUES LESS THAN (2019) ENGINE = InnoDB,
 PARTITION p2019 VALUES LESS THAN (2020) ENGINE = InnoDB,
 PARTITION p2020 VALUES LESS THAN (2021) ENGINE = InnoDB,
 PARTITION p2021 VALUES LESS THAN (2022) ENGINE = InnoDB,
 PARTITION p2022 VALUES LESS THAN (2023) ENGINE = InnoDB,
 PARTITION p2023 VALUES LESS THAN (2024) ENGINE = InnoDB,
 PARTITION p2024 VALUES LESS THAN (2025) ENGINE = InnoDB,
 PARTITION p2025 VALUES LESS THAN (2026) ENGINE = InnoDB,
 PARTITION p2026 VALUES LESS THAN (2027) ENGINE = InnoDB,
 PARTITION pmax VALUES LESS THAN MAXVALUE ENGINE = InnoDB) */;
```

---

## 五、核心接口设计

### 认证

```
POST /api/auth/register     # 注册
POST /api/auth/login        # 登录，返回 JWT Token
```

### 会话管理

```
GET    /api/conversations           # 获取当前用户会话列表
POST   /api/conversations           # 新建会话
DELETE /api/conversations/{id}      # 删除会话
GET    /api/conversations/{id}/messages   # 获取会话消息历史
```

### AI 聊天（SSE 代理）

```
POST /api/chat/stream
```

请求体：
```json
{
  "conversation_id": 1,
  "message": "帮我查茅台最近一个月收盘价",
  "image": "base64..."   // 可选，多模态
}
```

FastAPI 接收后：
1. 将消息存入 `messages` 表（role=user）
2. 携带完整历史上下文，以 SSE 格式代理转发给 nanobot
3. 流式返回给前端
4. 流结束后，解析完整回复，识别内容类型（text/chart/table），存入 `messages` 表（role=assistant）

### 大屏

```
GET    /api/dashboard/widgets           # 获取当前用户所有 Widget
POST   /api/dashboard/widgets           # 新增 Widget（从聊天插入）
PUT    /api/dashboard/widgets/{id}      # 更新 Widget（标题/布局）
DELETE /api/dashboard/widgets/{id}      # 删除 Widget
PUT    /api/dashboard/layout            # 批量更新布局（拖拽后触发）
```

---

## 六、核心数据流

### 6.1 AI 返回图表并插入大屏

```
1. 用户在聊天框输入问题
2. 前端 POST /api/chat/stream，开启 SSE 连接
3. FastAPI 代理转发给 nanobot，nanobot 查 MySQL，生成 SQL+ECharts option
4. 流式返回，前端实时渲染打字效果
5. 流结束，前端解析消息中的 ECharts JSON 块，渲染内联图表
6. 消息底部显示"添加到大屏"按钮
7. 用户点击 → POST /api/dashboard/widgets，携带 ECharts option + 默认布局
8. 大屏自动刷新，新 Widget 出现在布局末尾
```

### 6.2 AI 回复内容识别规则

nanobot 返回内容约定格式（Prompt 中需要明确要求）：

````
普通回答直接输出 Markdown 文本。

返回图表时，输出：
```echarts
{ ECharts option JSON }
```

返回表格时，输出：
```datatable
{ "columns": [...], "data": [...] }
```
````

前端解析规则：
- 识别 ` ```echarts ` 代码块 → 渲染 ECharts + 显示"添加到大屏"
- 识别 ` ```datatable ` 代码块 → 渲染 Ant Design Table + 显示"添加到大屏"
- 其余内容 → 正常 Markdown 渲染

---

## 七、关键组件设计

### 7.1 聊天面板切换

```tsx
// 通过 Zustand 管理模式状态
const chatMode = useChatStore(s => s.mode) // 'sidebar' | 'float'

// 右上角切换按钮
<Button onClick={toggleMode}>
  {chatMode === 'sidebar' ? '切换浮动' : '切换侧边栏'}
</Button>

// sidebar 模式：主内容 flex 布局，右侧固定 380px
// float 模式：右下角固定定位气泡，点击展开
```

### 7.2 大屏 Widget 插入

```tsx
// 聊天消息底部按钮
<Button onClick={() => addToDashboard({ type: 'chart', data: echartsOption })}>
  添加到大屏
</Button>

// dashboardStore
const addWidget = async (widget) => {
  const res = await api.post('/dashboard/widgets', widget)
  setWidgets(prev => [...prev, res.data])
}
```

### 7.3 布局自动保存

```tsx
// react-grid-layout 布局变更回调，防抖 1s 后保存
const onLayoutChange = useDebouncedCallback(async (layout) => {
  await api.put('/dashboard/layout', { layout })
}, 1000)
```

---

## 八、Prompt 设计要点

在 FastAPI 代理层，系统 Prompt 中需要约定：

```
你是一个股票数据分析助手。
- 当需要展示图表时，使用 ```echarts 代码块输出标准 ECharts option JSON
- 当需要展示表格时，使用 ```datatable 代码块输出 {"columns":[{"title":"","dataIndex":""}], "data":[]} 格式
- ECharts option 必须是合法完整的 JSON，包含 xAxis、yAxis、series 等必要字段
- 不要在 JSON 中添加注释
- 数据分析结论用 Markdown 格式输出在图表前后
```

---

## 九、认证方案

```
登录成功 → 服务端签发 JWT（有效期 7 天）
         → 前端存入 localStorage
         → 后续请求 Header 携带 Authorization: Bearer <token>
         → FastAPI Depends(get_current_user) 统一鉴权
```

---

## 十、部署方案

### docker-compose.yml 结构

```yaml
services:
  frontend:
    build: ./frontend
    ports: ["3000:3000"]

  backend:
    build: ./backend
    ports: ["8000:8000"]
    depends_on: [mysql]
    env_file: ./backend/.env

  mysql:
    image: mysql:8.0
    environment:
      MYSQL_DATABASE: stock
    volumes:
      - mysql_data:/var/lib/mysql

volumes:
  mysql_data:
```

### 本地开发

```bash
# 后端
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend && npm install
npm run dev
```

---

## 十一、开发优先级

| 阶段 | 内容 | 优先级 |
|------|------|------|
| P0 | 用户注册/登录/JWT | 必须 |
| P0 | FastAPI SSE 代理转发 nanobot | 必须 |
| P0 | 聊天基础收发 + 流式渲染 | 必须 |
| P0 | ECharts / Table 内联渲染 | 必须 |
| P1 | 会话管理与历史持久化 | 重要 |
| P1 | 大屏 Dashboard + 拖拽布局 | 重要 |
| P1 | 一键插入大屏 | 重要 |
| P2 | 聊天面板侧边栏/浮动切换 | 次要 |
| P2 | 多模态图片上传 | 次要 |
| P2 | Docker 一键部署 | 次要 |
