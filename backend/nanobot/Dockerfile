# 股票查询助手 · nanobot + Chainlit
#
# 构建：
#   docker build -t nanobot-app:latest .
# 或：
#   docker compose build

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    PYTHONIOENCODING=UTF-8 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

WORKDIR /app

# 系统依赖：
#   tzdata / ca-certificates 基础
#   libgomp1 / libquadmath0 供 numpy/statsmodels 运行
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates \
        libgomp1 \
        libquadmath0 \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo "$TZ" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用 Docker layer 缓存）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目代码（含 data/stock_prices_history.db，按 .dockerignore 排除无关）
COPY . .

# 运行时目录（容器内自用，不对外挂载）
RUN mkdir -p /app/sessions /app/memory /app/charts /app/.files

EXPOSE 10001

CMD ["chainlit", "run", "app_chainlit.py", \
     "--host", "0.0.0.0", \
     "--port", "10001", \
     "--headless"]
