---
task_id: V-01
title: 租户、机构和工作人员可以安全登录管理
status: in-review
executor: agent
owner: "Codex / identity-web-slice-owner"
effort_range: "6-10 person-days"
earliest_start: "F-02、F-03、F-04、F-09 全部 done"
blocked_by:
  - F-02
  - F-03
  - F-04
  - F-09
implementation_authorized: true
---

# V-01｜租户、机构和工作人员可以安全登录管理

> `status: in-review`：V-01 目标后端、Web、OpenAPI、审计及真实 MySQL 验收均已完成，
> 等待项目负责人复审和合入确认。

## 目标

让平台管理员、租户主体、总部员工和机构员工能够通过 Web 安全登录，并只管理服务端判定的租户与机构范围，为后续用户、设备和资金切片提供可信身份、会话与授权基础。

## 要构建什么

建立目标 identity 数据模型和公开端口，贯通平台/工作人员独立登录入口、全平台唯一登录名、MySQL 会话、同源 HttpOnly Cookie、CSRF、可信执行上下文，以及租户、机构、员工、任职和权限的 Web 管理页面。

业务请求必须根据服务端会话和当前数据库事实解析主体、租户、机构与能力，不能信任客户端提交的角色或作用域。禁用、改密、任职或授权变化必须立即撤销相关会话。平台跨租户访问只能进入明确的特权用例并留下审计。

## 验收标准

- [x] 平台管理员能创建、禁用租户，并通过明确审计的特权用例处理租户业务数据。
- [x] Web 登录只需要登录名和密码，不要求租户编码；工作人员登录名在全部租户间唯一。
- [x] 平台账号与租户工作人员使用独立入口和命名空间，认证错误不泄露账号是否存在。
- [x] Web 会话只使用符合冻结属性的 `Secure + HttpOnly + SameSite=Lax` Cookie，JavaScript 不接触会话 Token。
- [x] 登录及所有非安全方法执行 CSRF 校验，客户端不把 Web Bearer Token 写入 localStorage。
- [x] 租户主体和机构负责人天然权限正确；总部及普通员工的多职责能力可配置。
- [x] 两租户、两机构越权读写统一表现为不可见，客户端提交的 tenant、organization 或 role 不能扩大权限。
- [x] 禁用账号、租户或机构，或修改密码、任职、权限后，相关会话立即失效。
- [x] Web 完成租户、机构、员工、任职和授权的可操作闭环，并记录操作者、时间及必要前后值。
- [x] 真实 MySQL 集成测试覆盖会话竞争、全局登录名唯一、租户拦截和机构级显式隔离。

实施与复验证据见
[V-01 身份与 Web 管理纵切实施证据](../../../architecture/v-01-identity-web-slice-evidence.md)。

## 阻塞与最早开始

F-02、F-03、F-04、F-09 已全部完成，任务依赖已经解除。项目负责人已明确授权在独立
worktree 中实施 V-01；授权不扩展到本任务排除范围或下游 V-02/V-03。

## 排除范围

- 机构普通用户的微信注册、手机号绑定和钱包；
- 工作人员小程序免密管理入口；
- 设备部署、投递、清运和资金业务；
- 平台用户跨租户成员关系；
- 外部身份提供商或 Refresh Token。

## 权威来源

- [详细设计第 02 章](../../detailed-design/02-identity-device-configuration.md)
- [实施依赖第 08 章](../../detailed-design/08-implementation-sequence.md)
- [I-006～I-010](../../interface-design/02-auth-session-scope-i006-i010.md)
- [I-011～I-015](../../interface-design/03-identity-directory-i011-i015.md)
- [D-011～D-015](../../database-design/03-identity-device-d011-d015.md)
- [D-036～D-040](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：发布任务文件；仅完成设计与任务拆分，尚未授权实施。
- 2026-07-26：复核 F-02、F-03、F-04、F-09 均为 `done`；项目负责人授权 Codex
  接取并编码实施 V-01。建立分支 `codex/v01-tenant-organization-staff-login` 和独立
  worktree，任务由 `blocked` 转为 `in-progress`。
- 2026-07-26：完成目标 Web 会话、CSRF、可信实时授权、租户/机构/员工/任职/权限目录、
  成功变更审计、管理页面及 73-operation OpenAPI；Java 全仓回归、真实 MySQL 三项专项、
  HTTP 契约和 Web production build 全部通过，任务转为 `in-review`。
- 2026-07-27：完成复审提出的六项 P1 修复：禁止自我创建负责人任职、按授权作用域裁剪
  有效权限、恢复必填字段的 null 校验、将目标资源纳入幂等摘要、脱敏审计摘要，以及补齐
  平台跨租户读取和拒绝/失败请求审计。真实 MySQL 专项扩展至八项；全仓、契约和 Web
  回归通过，保持 `in-review` 等待复审。
