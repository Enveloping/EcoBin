---
task_id: V-02
title: 机构用户首次注册并获得独立零余额钱包
status: in-progress
executor: mixed
owner: "Codex / organization-user-wallet-slice-owner"
effort_range: "6-10 person-days"
earliest_start:
  software: "V-01、F-06、F-09 全部 done"
  integration: "software 完成，且试点机构 AppID/AppSecret、真实小程序和指定测试用户可用"
  acceptance: "integration 完成，可取得真实 wx.login、getPhoneNumber 和 Web 人工绑定证据"
phase_progress:
  software: done
  integration: in-progress
  acceptance: not-started
blocked_by:
  - V-01
  - F-06
  - F-09
implementation_authorized: true
---

# V-02｜机构用户首次注册并获得独立零余额钱包

> `status: in-progress`：software 阶段已在独立 worktree 中完成并通过 MySQL 8.4、
> 全仓及客户端自动回归；真实 `wx.login` 已联通，integration 正在进行。真实手机号、
> 设备来源注册和完整人工验收尚未完成，任务级仍保持 `in-progress`。
> 开发环境中“小程序码可进入、首次注册来源仍为空”的问题已按项目负责人决定延期到
> [P0-FOLLOWUP-01](p0-followup-01-wechat-qr-registration-attribution.md)，不在本轮继续
> 试错；该延期不等于真实设备来源注册已经验收通过。

## 目标

让普通用户在当前机构小程序首次登录时建立独立机构身份和零余额钱包，完成手机号绑定后具备后续投递与提现资格；让 Web 人工绑定的工作人员再次登录时自动进入当前机构管理入口。

## 要构建什么

贯通小程序、identity、funds 和 Web：按机构 AppID 执行 `wx.login`；首次创建机构用户时不可变记录注册时间及可空来源部署；在同一事务内通过 identity 定义、funds 实现的同步注册参与端口创建零余额钱包和会话；使用微信动态码首次绑定手机号；由 Web 有权限人员人工把工作人员绑定到对应机构用户，随后小程序按 `MANAGEMENT > CLEANING > USER` 自动进入单一入口。

内部 FK 只能通过 DD-004 冻结的按主体强类型、服务端不可序列化的同事务构造引用传递。funds 不得回查 identity 私表，也不能把该参与端口扩展为通用事件或插件链。

## 验收标准

- [x] 相同机构 `AppID + OpenID` 并发首次登录只创建一个机构用户、一个零余额钱包和有效会话。
- [x] 钱包初始化或会话创建失败时，机构用户、注册归因、钱包和会话整体回滚。
- [x] 直接进入小程序时注册来源为空；首次经可信设备二维码注册时固定来源部署和注册时间，后续登录或扫码不能回填、覆盖。
- [ ] 未绑定手机号的用户已计入注册统计，但不能投递或提现。
- [x] 手机号仅通过 `getPhoneNumber` 动态码绑定，同机构手机号唯一，日志和普通审计保持脱敏。
- [x] Web 人工绑定使用精确手机号查找和双侧版本快照；并发换绑最多一个成功，并撤销冲突旧绑定及会话。
- [x] 有效工作人员绑定自动进入 `MANAGEMENT`；否则按清运能力进入 `CLEANING`，再否则进入 `USER`。
- [x] funds 不回查 identity 私表；跨模块不存在裸 `Long`、Entity/Mapper 泄漏或可序列化内部 FK。
- [x] 两机构使用不同 AppID/OpenID 身份和独立钱包，不能跨机构复用。
- [ ] 真实小程序 HITL 证据包含 `wx.login`、`getPhoneNumber`、直接注册、设备来源注册和工作人员免密进入管理页；Stub 只能关闭软件阶段。

## 阻塞与最早开始

| 阶段 | 最早开始条件 |
|---|---|
| software | V-01、F-06、F-09 全部完成 |
| integration | software 完成，且试点机构 AppID/AppSecret、真实小程序和指定测试用户可用 |
| acceptance | integration 完成，并能采集真实 `wx.login`、`getPhoneNumber` 和 Web 人工绑定证据 |

`V-01、F-06、F-09` 已全部完成，software 阶段也已完成自动验收。integration 和
acceptance 阶段仍须满足表中真实小程序、机构凭据和指定测试用户条件；三个阶段全部通过
后才可以把任务级状态标记为 `done`。

## 排除范围

- 管理小程序的完整统计内容；
- 普通投递、清运、充值和提现；
- 用户预先实名；
- 小程序内普通/管理模式自由切换；
- 自动以手机号匹配工作人员；
- 后台创建、合并或迁移机构用户。

## 权威来源

- [详细设计第 02 章](../../detailed-design/02-identity-device-configuration.md)
- [实施依赖第 08 章](../../detailed-design/08-implementation-sequence.md)
- [I-006～I-010](../../interface-design/02-auth-session-scope-i006-i010.md)
- [I-011～I-015](../../interface-design/03-identity-directory-i011-i015.md)
- [I-051～I-055](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [D-011～D-015](../../database-design/03-identity-device-d011-d015.md)
- [D-036～D-040](../../database-design/08-transactions-concurrency-d036-d040.md)

## 进展记录

- 2026-07-23：发布任务文件；仅完成设计与任务拆分，尚未授权实施。
- 2026-07-27：V-01 完成复审并转为 `done`，V-02 的软件前置 V-01、F-06、F-09
  全部解除，任务由 `blocked` 转为 `ready`；这不构成实施或外部配置授权。
- 2026-07-27：项目负责人明确要求开始推进 V-02，并指定后续修改均在独立 worktree
  `database-refactor-v02-organization-user-registration-wallet` 中进行；任务转为
  `in-progress`，software 阶段开始，实施授权不扩展到真实微信凭据或外部平台配置。
- 2026-07-27：software 阶段完成。MySQL 8.4 专项 3 项、全仓 Java 110 项、
  Web HTTP 基础 3 项、小程序 TypeScript 和 OpenAPI 本地引用检查全部通过；详细证据见
  [`v-02-organization-user-registration-wallet-evidence.md`](../../../architecture/v-02-organization-user-registration-wallet-evidence.md)。
  未使用真实 AppSecret、真实微信用户或外部平台配置，integration/acceptance 仍为
  `not-started`。
- 2026-07-28：补齐 I-015 后端机构用户列表/详情、冻结/恢复、清运能力授予/撤销及
  平台协助入口；注册来源安全摘要继续通过公开端口读取，不让 identity 查询 device
  私表。MySQL 8.4 专项扩展为 5 项并全部通过，全仓 Java 111 项零失败，HTTP OpenAPI
  官方校验 5 项通过。真实 `wx.login` 已验证，因此 integration 转为 `in-progress`；
  真实 `getPhoneNumber`、可信设备来源注册和完整人工验收尚未完成。
- 2026-07-28：真实开发版小程序码可以进入登录入口，但两次精确重建测试账号后，
  `registered_via_deployment_id` 仍为空；Java 21 后端 MySQL 单项测试证明请求携带
  部署码时服务端能够正确归因。项目负责人决定停止在受限开发环境继续处理，问题转入
  独立的 [P0-FOLLOWUP-01](p0-followup-01-wechat-qr-registration-attribution.md)，
  待实际上线后复核；实验性生命周期修改未提交，V-02 仍保持 `in-progress`。
