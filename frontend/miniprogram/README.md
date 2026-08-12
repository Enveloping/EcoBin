# EcoBin 微信小程序

微信原生小程序（TypeScript）。F-09 已建立目标 `/api/v1` 客户端传输基础；具体投递、
清运、钱包等业务 path 仍由后续纵向任务按 OpenAPI 增量迁移，现有 `/api/app/**` 调用只
代表旧页面联调入口。

## 会话与入口

- 冷启动通过 `wx.login` 调用精确匿名入口
  `POST /api/v1/miniapp/auth/sessions`。
- 本地只保存一份会话和一个短期 Bearer Token，不存在 Refresh Token、数字角色 Token
  或第二套模式 Token。
- 每次启动先删除旧版 `ecobin_token/ecobin_role/ecobin_user_info`；登录创建响应返回
  Token，`GET .../sessions/current` 的安全投影不再次返回 Token。
- 当前小程序只保留用户端 `USER` 和清运端 `CLEANING`，经营管理统一使用
  Web 管理后台。过渡期收到旧 `miniapp-staff / MANAGEMENT` 会话时，客户端只引导
  选择机构账号并换发用户/清运会话，不再提供小程序管理页。
- `401` 最多重新执行一次 `wx.login`。安全查询可以重试；写请求只有携带原始
  `Idempotency-Key` 时才允许重放。重登录后的 audience 或 entryMode 任一变化时立即
  切换入口，不在原入口重放请求。

## 传输基础

```text
miniprogram/
├── app.ts / app.json
├── config/index.ts
├── utils/
│   ├── request.ts          # Bearer、ProblemDetail、受控 401 重登录
│   ├── auth.ts             # 单会话、单 audience、入口路由
│   ├── command-intent.ts   # UUIDv4 幂等意图与 expectedVersion
│   ├── async-operation.ts  # 202 状态持久化与同源轮询
│   ├── decimal.ts          # 金额/单价/重量字符串格式化与比较
│   └── guard.ts            # entryMode / capability 页面守卫
├── api/
├── types/api.d.ts
└── pages/
```

金额固定两位、单价固定四位、业务重量固定两位，均以字符串传输和展示。资金比较不经过
JavaScript 浮点运算；原始克重使用整数。

## 本地运行

1. `miniprogram/config/index.ts` 的 `BASE_URL` 直接保存小程序请求的服务器地址，
   不根据运行环境自动切换。需要更换目标时只修改该常量。当前备案域名尚不可用，
   开发者工具使用 HTTPS IP 联调时，需在“详情 → 本地设置”勾选“不校验合法
   域名、web-view 域名、TLS 版本以及 HTTPS 证书”。真机、体验版和正式版仍必须使用
   已备案、证书匹配且已配置为 request 合法域名的 HTTPS 域名。
2. 在微信开发者工具执行“工具 → 构建 npm”。
3. 开发期可在“详情 → 本地设置”关闭合法域名校验。
4. 改过后端非 bootstrap 模块时，先执行 `./mvnw install -DskipTests`，再启动
   `ecobin-bootstrap`。

## 类型检查

```powershell
npm ci
npx tsc --noEmit -p tsconfig.json
```

`tsconfig.json` 开启严格检查，并仅通过 `skipLibCheck` 跳过微信官方声明文件内部告警。
