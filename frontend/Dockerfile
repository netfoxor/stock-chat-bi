FROM node:20-slim

WORKDIR /app

COPY package.json /app/package.json

# 使用 pnpm（与本地一致）
RUN corepack enable && corepack prepare pnpm@latest --activate
RUN pnpm install

COPY index.html /app/index.html
COPY tsconfig.json /app/tsconfig.json
COPY vite.config.ts /app/vite.config.ts
COPY src /app/src

EXPOSE 5173

CMD ["pnpm", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]

