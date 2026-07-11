# EcoBin 前端管理后台 Docker 部署指南

> 配套后端部署见 `docs/deployment/deploy-docker.md`。本文讲「前端 web 后台」怎么用 Docker 部署到同一台服务器，
> 并解决小程序登录报「未找到该小程序对应的租户」的问题。

---

## 0. 先搞清楚：为什么要部署 web 后台

小程序把请求地址切到服务器后，登录报错：

```text
未找到该小程序对应的租户: wx1e05b648c1d16f52
```

根因：后端 `AuthServiceImpl.wxLogin()` 是**按小程序 appid 反查租户**的
（`tenantService.getByMiniappAppid(appid)`），服务器是一个全新空库，里面没有任何租户记录，自然查不到。

而租户只能从**网页管理后台**创建。所以流程是：

```text
部署 web 后台 → 超管登录 → 新建租户(填小程序 AppID/Secret) → 小程序才能登录
```

这就是本文要做的事。

---

## 1. 概念热身

| 概念 | 一句话 | 在 EcoBin 里对应什么 |
|------|--------|---------------------|
| 前端构建产物 | React/Vite 项目 `npm run build` 后会变成一堆**纯静态文件**（html/js/css），没有"进程"可跑 | `frontend/web` build 后输出到 `dist/` |
| 为什么不能像后端那样 `java -jar` 跑 | 静态文件需要一个 **web 服务器**来对外提供（响应浏览器的 HTTP 请求） | 我们用 nginx |
| nginx 的两个角色 | ① **托管静态文件**（把 dist 发给浏览器）；② **反向代理**（把 `/api` 请求转发给后端） | 一个 nginx 容器同时干这两件事 |
| 同源 vs CORS | 前端和 `/api` 都从同一个地址（同域名同端口）出去，就是"同源"，浏览器不拦；如果前端直连后端的另一个端口，就会触发跨域(CORS)要额外处理 | nginx 反代让前端用相对 `/api`，天然同源 |
| SPA 路由回退 | 单页应用的路由（如 `/tenant`）是前端 JS 控制的，服务器上并没有这个文件；直接刷新会 404，需要让 nginx 把找不到的路径都回退到 `index.html` | `try_files $uri $uri/ /index.html` |
| 多阶段构建 | Dockerfile 里先用 Node 镜像编译，再把产物拷进体积小得多的 nginx 镜像，**最终镜像不含 Node 和源码** | 见下面 Dockerfile 两个 `FROM` |

**核心设计：nginx 同源反代。** 前端请求地址用相对路径 `/api`
（见 `frontend/web/src/api/request.ts`：`baseURL = (VITE_API_BASE || '') + '/api'`，生产构建时 `VITE_API_BASE` 为空，
所以就是 `/api`）。浏览器访问 `http://服务器/api/xxx`，nginx 再把它转发到后端容器。
**好处：前端代码不写死服务器 IP，换 IP / 上域名都零改动，还省掉跨域配置。**

---

## 2. 需要新增的文件（共 4 个）

```text
frontend/web/Dockerfile        # 怎么把前端打成镜像
frontend/web/nginx.conf        # nginx 配置（托管 + 反代）
frontend/web/.dockerignore     # 哪些不打进镜像
docker-compose.yml             # 在原有 mysql/backend 基础上加一个 web 服务
```

### 2.1 `frontend/web/Dockerfile`

```dockerfile
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
```

**逐点解释：**
- `COPY package*.json` 先于 `COPY . .`：Docker 分层缓存，依赖清单没变就不重新 `npm ci`（很慢的那步），只重编译源码。
- `npm ci` 而非 `npm install`：严格按 `package-lock.json` 安装，结果可复现。
- 两个 `FROM`（多阶段）：第一阶段的 Node + node_modules + 源码**都不会进最终镜像**，最终镜像只有 nginx + 一个 `dist/`，又小又干净。

### 2.2 `frontend/web/nginx.conf`

```nginx
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # 前端是 SPA（react-router）：找不到的真实文件一律回退到 index.html，
    # 否则刷新 /tenant 这类前端路由会 404。
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 同源反代：浏览器请求的 /api/* 由 nginx 转发到 compose 网络里的 backend 容器。
    # 用服务名 backend 是因为它们在同一 docker network，DNS 能解析到后端容器。
    # proxy_pass 不带尾部 URI，原始路径（含 /api）原样透传给后端。
    location /api {
        proxy_pass http://backend:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**逐点解释：**
- `proxy_pass http://backend:8080`：`backend` 是 `docker-compose.yml` 里后端服务的名字。同一个 compose 网络里，
  容器之间可以直接用**服务名**当主机名互访（Docker 内置 DNS 解析到对方容器 IP），所以不用写 IP。
- `proxy_pass` 末尾**不带路径**：nginx 会把浏览器原始路径（`/api/system/...`）原样转给后端，正好后端接口都在 `/api` 下。
- `try_files`：先找真实文件 → 找目录 → 都没有就返回 `index.html`，交给前端路由处理。

### 2.3 `frontend/web/.dockerignore`

```text
# 依赖和产物都在容器内重新生成，别带进构建上下文（又大又可能是旧的）
node_modules
dist

# 本地环境文件不进镜像（生产构建用相对 /api，不需要 .env.development）
.env
.env.*

# 杂项
.git
*.log
```

**为什么排除 `.env*`**：前端这套**不需要任何密钥**。生产构建走相对 `/api`，`.env.development` 只在本地 `npm run dev` 用。
把 env 排除掉，既减小构建上下文，也确保不会有任何环境文件被打进镜像。

### 2.4 `docker-compose.yml` 追加 `web` 服务

在**原有的 mysql、backend 两个服务不动**的前提下，新增一段（与 backend 同级）：

```yaml
  # 前端管理后台：nginx 托管构建产物 + 把 /api 同源反代到 backend
  web:
    build: ./frontend/web
    container_name: ecobin-web
    depends_on:
      - backend
    ports:
      - "80:80"
    restart: unless-stopped
```

**逐点解释：**
- `build: ./frontend/web`：用该子目录里的 Dockerfile 构建（构建上下文也是这个目录）。
- `depends_on: backend`：保证 backend 容器**先启动**，这样 backend 这个 DNS 名可解析，nginx 才能正常反代
  （注意它只保证"容器已起"，不保证后端 100% 就绪；后端没好时访问 `/api` 会短暂 502，等后端起来就好）。
- `ports: "80:80"`：宿主机 80 端口映射到容器 80。访问 `http://服务器IP`（80 是默认端口，URL 里不用写 `:80`）。

---

## 3. 部署到服务器

> 项目已在服务器 `/opt/ecobin/EcoBin`（目录属主是 root，所有 docker / 写文件操作都加 `sudo`）。
> 文件同步两种方式任选：① 推到 GitHub 再 `git pull`；② 直接把这几个文件拷上去。

把 4 个文件就位后，在服务器项目根目录执行：

```bash
cd /opt/ecobin/EcoBin

# 校验 compose 写法是否正确
sudo docker compose config >/dev/null && echo "compose OK"

# 只构建并启动 web（不动正在跑的 mysql/backend）。首次会 npm ci + 构建，稍慢。
sudo docker compose up -d --build web

# 看三个容器都在跑
sudo docker compose ps
```

期望看到 `ecobin-web` 状态 `Up`、端口 `0.0.0.0:80->80/tcp`。

**常用命令：**

| 命令 | 作用 |
|------|------|
| `sudo docker compose up -d --build web` | 改了前端代码/配置后，重新构建并滚动更新 web |
| `sudo docker compose logs -f web` | 实时看 nginx 日志 |
| `sudo docker compose restart web` | 仅重启（不重建） |
| `sudo docker compose ps` | 看所有服务状态 |

---

## 4. 放行端口 + 验证

### 4.1 放行 80 端口

去**腾讯云控制台 → 该实例安全组 → 入站规则**，放行 `TCP 80`（和当初放 8080 一样）。

### 4.2 验证

**先在服务器本机自测**（绕过外网，确认容器本身没问题）：

```bash
# 首页应 200
curl -s -o /dev/null -w "home=%{http_code}\n" http://localhost/

# /api 反代到后端：空 body 登录后端会返回 400，这是"通了"的标志（不是 502）
curl -s -o /dev/null -w "api=%{http_code}\n" -X POST http://localhost/api/system/auth/login \
  -H "Content-Type: application/json" -d "{}"
```

- `home=200` → 静态托管 OK
- `api=400` → 反代 OK（能打到后端拿到业务响应；若是 **502** 才是反代/后端有问题）

**再从浏览器**打开 `http://115.159.67.35`，应看到后台登录页。F12 → Network，登录时请求应打到
`http://115.159.67.35/api/...`（同源相对路径，不是写死的别的地址）。

---

## 5. 建租户，消除小程序报错

1. 浏览器打开 `http://115.159.67.35`，用**超管账号**登录。
2. **租户管理 → 新建租户**，填：
   - **小程序 AppID**：`wx1e05b648c1d16f52`
   - **小程序 Secret**：该小程序对应的 Secret（后端会按这个租户存的 secret 去调微信 `code2session`，**必须填对**）
   - 设好该租户的登录用户名/密码、名称等。
3. 保存后，再用小程序登录 →「未找到该小程序对应的租户」就消失了，登录这一环打通。

> 说明：后端登录走的是**租户表里存的** `miniappAppid` / `miniappSecret`（Secret 在库里 AES 加密存储），
> 和后端 `.env` 里的全局 `wechatAppid/wechatSecret` 不是一回事。所以每个要接入的小程序都要建一条对应租户。

---

## 6. 常见排错

| 现象 | 可能原因 / 处理 |
|------|----------------|
| 浏览器打不开、超时（curl 返回 000） | 80 端口没放行，去安全组放行 |
| 偶发 502，刷新又好 | 安全组刚放行的瞬间抖动，或 backend 还没完全就绪，稍等重试 |
| 页面空白 | dist 没拷进镜像 / 路径错；`sudo docker compose logs web` 看 nginx 启动；确认 Dockerfile 的 `COPY --from=build /app/dist ...` |
| 刷新子路由 404 | 漏了 `try_files ... /index.html`（SPA 回退） |
| `/api` 一直 502 | backend 没起好，或 nginx.conf 里服务名写错（必须是 compose 里的 `backend`） |
| 登录提示跨域/CORS | 说明前端没走同源 `/api`，检查是否被改成写死了某个 IP；本方案应保持相对 `/api` |

---

## 7. 上线前 TODO（本次开发联调不做）

- **HTTPS + 备案域名**：小程序正式版要求 `https` 且域名已备案；当前 `http://IP` 仅供开发版/真机预览联调。
- nginx 加 `gzip`、静态资源缓存头，优化首屏（当前主包 ~1.8MB）。
- web 与 backend 端口/反代规则收敛到统一入口（如再加一层网关）。
- 前端构建注入版本号，便于排查线上版本。
