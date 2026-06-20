# ===== 构建阶段：用 Node 把前端源码编译成静态文件 =====
FROM node:20-alpine AS build
WORKDIR /app

# 先只拷依赖清单，单独成一层：package*.json 没变时这层走缓存，不重复 npm ci
COPY package.json package-lock.json ./
RUN npm ci

# 再拷源码做生产构建，产物输出到 /app/dist
COPY . .
RUN npm run build

# ===== 运行阶段：用 nginx 托管静态文件 + 反代 /api =====
FROM nginx:alpine AS runtime

# 拷贝构建产物到 nginx 默认站点目录
COPY --from=build /app/dist /usr/share/nginx/html
# 用我们的配置覆盖 nginx 默认站点配置（SPA 回退 + /api 反代）
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80