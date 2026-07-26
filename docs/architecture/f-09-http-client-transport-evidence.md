# F-09｜HTTP OpenAPI 与客户端传输基础实施证据

> 验证日期：2026-07-26
> 任务：[F-09 HTTP OpenAPI 3.1 与客户端传输基础](../planning/tasks/p0-controlled-loop/f-09-http-openapi-client-transport.md)

## 1. 实施结果

- `contracts/http/openapi.yaml` 是目标 `/api/v1` 的 OpenAPI 3.1 唯一机器来源。首版包含
  14 个认证/会话/外部通知 operation、99 个可解析本地引用和 7 份受 Schema 校验的样例。
- Web Cookie 与 CSRF、小程序 Bearer、微信支付 APIv3 四签名头是三套互不替代的安全
  方案。Web 登录只提交 `loginName/password`，会话凭据只存在于服务端设置的
  `__Host-ecobin-web-session` HttpOnly Cookie。
- 公共组件已固定 UUIDv4 公开 UID/会话/操作身份、UTC 毫秒时间、分页/游标分页、
  金额/单价/重量字符串、
  `ProblemDetail`、幂等冲突、版本冲突和 `202 + Location + statusUrl`。
- Web 客户端使用 `withCredentials: true`，内存保存 CSRF Token 并在登录、登出或
  `SECURITY.CSRF_INVALID` 后轮换；浏览器存储只记录非凭据登录域和待查询资源，不读取、
  保存或刷新 Web Token。
- Web 与小程序均提供 UUIDv4 命令意图。一个意图的重试复用原键；若请求摘要改变，客户
  端先抛出明确冲突，服务端同键异摘要则以 `409 COMMON.IDEMPOTENCY_KEY_CONFLICT`
  表达。
- 两端均按稳定 `resourceId` 保存 `202` 接受结果，只允许轮询同源 `/api/v1` 状态路径，
  页面刷新后可继续查询，终态后删除本地 pending 记录。
- 小程序登录创建响应与当前会话安全投影已经分型：只有创建响应含 `accessToken`，
  `GET .../sessions/current` 不返回 Token、tokenType 或首次注册标记。
- 小程序只保存一个 audience 会话，不存在 Refresh Token 或数字角色路由。`401` 后最多
  重新执行一次 `wx.login`；安全查询可重试，写请求必须携带原始幂等键才允许重放。
  audience 或 entryMode 任一改变时切换到服务端返回的唯一入口，不在原入口重放。
- Web 启动删除旧 `localStorage["ecobin-auth"]`，小程序启动删除旧
  `ecobin_token/ecobin_role/ecobin_user_info`。正式切换还必须停止旧 Token 签发并在
  所有服务实例同步轮换 legacy JWT 验签密钥且不保留旧密钥；客户端清理不能替代服务端
  撤销。
- 金额固定两位、单价固定四位、业务重量固定两位，客户端以字符串校验、展示和比较；
  原始克重为整数，资金不经过 JavaScript 浮点计算。

## 2. 自动门禁

`contracts/tools/http_contract.py` 和
`contracts/tests/test_http_contract.py` 固定以下边界：

1. OpenAPI 必须是 3.1、operationId 唯一且所有本地 `$ref` 可解析；
2. Web path 不能静默改用 Bearer，小程序和微信签名入口不能混用安全方案；
3. Web 登录请求禁止增加客户端自报租户、角色或权限字段；
4. ProblemDetail、202、分页、版本、UID、UTC 和十进制 Schema 必须保留；
5. UUIDv7 不能通过 UUIDv4 公开身份/会话/操作 Schema；
6. 登录创建模型必须返回 Token，当前会话模型必须不含 Token；
7. JSON number 不能通过金额字符串 Schema；
8. 七份成功/冲突/会话样例必须通过对应组件 Schema；
9. 旧客户端存储键和服务端 legacy Bearer 失效策略不能从切换契约中移除。

HTTP 校验已经接入 `contracts/tools/validate_contracts.py`，因此后续 OneNet/UART 全量契约
校验也会同时检查 HTTP 机器源。

## 3. 复验证据

环境：Node.js 22.21.1、npm 10.2.3、Python 3.11.15。

```powershell
python -B contracts/tools/http_contract.py
python -B -m unittest discover -s contracts/tests -v
python -B contracts/tools/validate_contracts.py --skip-java --skip-c
npx --yes @redocly/cli@2.20.3 lint contracts/http/openapi.yaml

Set-Location frontend/web
npm ci
npm run test:http-foundation
npm run build

Set-Location ../miniprogram
npm ci
npx tsc --noEmit -p tsconfig.json
```

结果：

- HTTP 专项：5 项全部通过，14 个 operation、99 个本地引用、7 份样例均有效；
- 客户端迁移/路由专项：3 项通过；
- 全部契约单测：29 项通过，`failures=0`、`errors=0`；
- 契约总校验：20 项通过；Java/C 仅按命令参数跳过，F-09 不产生 Java/C 生成物；
- Redocly：OpenAPI description 有效；仅报告许可证未声明和为后续纵向切片预置但首版尚未
  引用的公共组件警告；
- Web：TypeScript project build 与 Vite production build 通过；
- 小程序：严格 TypeScript 检查通过；
- `git diff --check` 无空白错误，仅 Windows 工作区既有 LF/CRLF 提示。

`npm ci` 没有修改 lockfile，也没有新增运行依赖。当前 Web 既有生产依赖审计仍报告
2 个 moderate、5 个 high；小程序生产依赖为 0。依赖升级不属于本任务，需在独立任务中
评估兼容性后处理。

## 4. 范围边界

F-09 只交付横切机器契约和客户端传输基础，不实现身份、设备、投递、清运、资金或运营
业务纵切。现有页面中的旧 `/api/**` 业务调用将在对应 V 任务中迁入 `/api/v1`；这些任务
只向同一 OpenAPI 增加自己的 paths、schemas 和 examples，不重新定义会话、安全、错误、
幂等、并发、异步状态或十进制规则。
