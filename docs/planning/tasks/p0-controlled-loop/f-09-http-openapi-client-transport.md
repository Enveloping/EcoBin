---
task_id: F-09
title: HTTP OpenAPI 3.1 与客户端传输基础
status: ready
executor: agent
owner: "TBD / api-client-platform-owner"
effort_range: "3-6 person-days"
earliest_start: "F-02 done 后"
blocked_by:
  - F-02
implementation_authorized: false
---

# F-09｜HTTP OpenAPI 3.1 与客户端传输基础

> `status: ready` 表示 F-02 前置依赖已经完成；`implementation_authorized: false`
> 表示依赖解除不构成 F-09 编码授权。

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

- [ ] OpenAPI 3.1 文件可以通过机器校验。
- [ ] Web Cookie+CSRF、小程序 Bearer 和外部签名安全方案均被明确表达。
- [ ] 401、409、202、ProblemDetail、`statusUrl` 和建议轮询间隔行为一致。
- [ ] Web 不从 localStorage 读取、保存或刷新会话 Token。
- [ ] 同一用户意图重试复用幂等键，同键异摘要显示明确冲突。
- [ ] 页面刷新后可以按稳定资源身份恢复异步状态查询。
- [ ] 金额和单价不使用 JavaScript `number` 计算。
- [ ] 当前页面可以使用 Stub 契约继续开发；后续 V 任务可增量增加业务 paths/schema/examples。

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
