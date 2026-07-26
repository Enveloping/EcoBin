---
task_id: F-09
title: HTTP OpenAPI 3.1 与客户端传输基础
status: done
executor: agent
owner: "Codex / api-client-platform-owner"
effort_range: "3-6 person-days"
earliest_start: "F-02 done 后"
blocked_by:
  - F-02
implementation_authorized: true
---

# F-09｜HTTP OpenAPI 3.1 与客户端传输基础

> `status: done`：机器契约、两端传输基础、首轮 review 修复和自动验证均已完成，
> 项目负责人已确认验收通过。

## 目标

建立 HTTP 唯一机器来源以及 Web、小程序共用的安全、幂等、并发、异步状态、错误和十进制
传输基础；后续纵向任务只需向同一契约增加自己的业务路径和页面。

## 要构建什么

- 建立并校验 `contracts/http/openapi.yaml`，使用 OpenAPI 3.1。
- 定义 Web Cookie+CSRF、小程序 Bearer 和外部签名入口的安全方案。
- 定义公共业务身份、ProblemDetail、分页、幂等冲突、版本冲突和 `202 + statusUrl` Schema
  与示例。
- 建立 Web 统一 API client，处理 Cookie、CSRF bootstrap/rotation、correlation ID、
  idempotency key、expected version 和状态轮询。
- 建立小程序单 Token、单入口路由和统一错误/重新登录基础。
- 建立金额、单价、重量和时间的无损字符串/专用 formatter，禁止客户端浮点计算资金。
- 让尚未实现的业务页面可以基于同一 Stub 契约开发。

## 验收条件

- [x] OpenAPI 3.1 文件可以通过机器校验。
- [x] Web Cookie+CSRF、小程序 Bearer 和外部签名安全方案均被明确表达。
- [x] 401、409、202、ProblemDetail、`statusUrl` 和建议轮询间隔行为一致。
- [x] Web 不从 localStorage 读取、保存或刷新会话 Token。
- [x] 同一用户意图重试复用幂等键，同键异摘要显示明确冲突。
- [x] 页面刷新后可以按稳定资源身份恢复异步状态查询。
- [x] 金额和单价不使用 JavaScript `number` 计算。
- [x] 当前页面可以使用 Stub 契约继续开发；后续 V 任务可增量增加业务 paths/schema/examples。

实施和复验证据见
[F-09 HTTP OpenAPI 与客户端传输基础实施证据](../../../architecture/f-09-http-client-transport-evidence.md)。

## 阻塞与最早开始

- [F-02](f-02-identity-boundary-and-trusted-context.md) 已完成，identity 的公开身份、可信
  上下文和会话边界已经稳定，任务依赖已经解除。
- 本任务仍须由项目负责人明确授权后，才能实施 HTTP 机器契约和客户端传输基础。

## 排除范围

- 一次性定义全部业务端点。
- 具体身份、设备、审核、资金或运营页面。
- 由客户端自行推算金额、权限或业务终态。
- 首个共同版本形成前的完整跨端生成物漂移 CI。

## 权威来源

- [详细设计 07：客户端、运营与验收](../../detailed-design/07-clients-operations-acceptance.md)
- [I-001～I-005：HTTP 横切规则](../../interface-design/01-cross-cutting-i001-i005.md)
- [I-006～I-010：认证、会话与作用域](../../interface-design/02-auth-session-scope-i006-i010.md)
- [I-051～I-055：模块端口与机器契约](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：F-02 完成后任务由 `blocked` 转为 `ready`；尚未获得 F-09 编码授权。
- 2026-07-26：项目负责人授权 Codex 接取 F-09；任务转为 `in-progress`，后续实施限定在
  `codex/f09-http-openapi-client-transport` 独立分支和 worktree。
- 2026-07-26：完成 OpenAPI 3.1、七份契约样例、HTTP 自动门禁、Web Cookie/CSRF 客户端、
  小程序单 audience 会话、两端幂等/版本/202/十进制基础；Web build、小程序严格类型检查、
  25 项契约测试和独立 Redocly 校验通过，任务转为 `in-review`。
- 2026-07-26：根据首轮 review 修正 UUIDv4 契约、拆分小程序登录创建/当前会话模型，
  增加 Web/小程序旧 Bearer 存储启动清理及服务端切换失效策略，并让 audience 或
  entryMode 任一变化都切换入口且不重放；新增 4 项契约反例和 3 项客户端回归测试。
- 2026-07-26：项目负责人确认 F-09 完成，任务由 `in-review` 转为 `done`。
