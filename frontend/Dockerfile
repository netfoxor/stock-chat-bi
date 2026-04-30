# 生产镜像：静态资源 + Nginx，将 /api/* 反向代理至 backend 同源访问
FROM node:20-slim AS build

WORKDIR /app

# npm/pnpm registry（与国内 npmmirror；海外可自行 build-arg 换 registry.npmjs.org）
ARG NPM_REGISTRY=https://registry.npmmirror.com
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}

COPY package.json pnpm-lock.yaml ./

RUN corepack enable && corepack prepare pnpm@latest --activate
RUN pnpm config set registry "${NPM_REGISTRY}" && pnpm install --frozen-lockfile

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src

ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# 加入 CodeMirror 后 Rollup 打包内存峰值上升；slim 镜像默认堆偏小易 OOM 或大量 swap，表现为「rendering chunks」卡住
ENV NODE_OPTIONS="--max-old-space-size=4096"

RUN pnpm build

FROM nginx:1.26-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
