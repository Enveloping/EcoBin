---
task_id: V-02
title: 机构用户首次注册并获得独立零余额钱包
status: blocked
executor: mixed
owner: "待指派 - 机构用户与小程序端到端切片负责人"
effort_range: "6-10 person-days"
earliest_start:
  software: "V-01、F-06、F-09 全部 done"
  integration: "software 完成，且试点机构 AppID/AppSecret、真实小程序和指定测试用户可用"
  acceptance: "integration 完成，可取得真实 wx.login、getPhoneNumber 和 Web 人工绑定证据"
phase_progress:
  software: not-started
  integration: not-started
  acceptance: not-started
blocked_by:
  - V-01
  - F-06
  - F-09
implementation_authorized: false
---

# V-02｜机构用户首次注册并获得独立零余额钱包

## 目标

让普通用户在当前机构小程序首次登录时建立独立机构身份和零余额钱包，完成手机号绑定后具备后续投递与提现资格；让 Web 人工绑定的工作人员再次登录时自动进入当前机构管理入口。

## 要构建什么

贯通小程序、identity、funds 和 Web：按机构 AppID 执行 `wx.login`；首次创建机构用户时不可变记录注册时间及可空来源部署；在同一事务内通过 identity 定义、funds 实现的同步注册参与端口创建零余额钱包和会话；使用微信动态码首次绑定手机号；由 Web 有权限人员人工把工作人员绑定到对应机构用户，随后小程序按 `MANAGEMENT > CLEANING > USER` 自动进入单一入口。

内部 FK 只能通过 DD-004 冻结的按主体强类型、服务端不可序列化的同事务构造引用传递。funds 不得回查 identity 私表，也不能把该参与端口扩展为通用事件或插件链。

## 验收标准

- [ ] 相同机构 `AppID + OpenID` 并发首次登录只创建一个机构用户、一个零余额钱包和有效会话。
- [ ] 钱包初始化或会话创建失败时，机构用户、注册归因、钱包和会话整体回滚。
- [ ] 直接进入小程序时注册来源为空；首次经可信设备二维码注册时固定来源部署和注册时间，后续登录或扫码不能回填、覆盖。
- [ ] 未绑定手机号的用户已计入注册统计，但不能投递或提现。
- [ ] 手机号仅通过 `getPhoneNumber` 动态码绑定，同机构手机号唯一，日志和普通审计保持脱敏。
- [ ] Web 人工绑定使用精确手机号查找和双侧版本快照；并发换绑最多一个成功，并撤销冲突旧绑定及会话。
- [ ] 有效工作人员绑定自动进入 `MANAGEMENT`；否则按清运能力进入 `CLEANING`，再否则进入 `USER`。
- [ ] funds 不回查 identity 私表；跨模块不存在裸 `Long`、Entity/Mapper 泄漏或可序列化内部 FK。
- [ ] 两机构使用不同 AppID/OpenID 身份和独立钱包，不能跨机构复用。
- [ ] 真实小程序 HITL 证据包含 `wx.login`、`getPhoneNumber`、直接注册、设备来源注册和工作人员免密进入管理页；Stub 只能关闭软件阶段。

## 阻塞与最早开始

| 阶段 | 最早开始条件 |
|---|---|
| software | V-01、F-06、F-09 全部完成 |
| integration | software 完成，且试点机构 AppID/AppSecret、真实小程序和指定测试用户可用 |
| acceptance | integration 完成，并能采集真实 `wx.login`、`getPhoneNumber` 和 Web 人工绑定证据 |

任务整体仍受 `V-01、F-06、F-09` 阻塞，三个阶段全部通过后才可以标记 `done`。发布本任务不代表已经授权修改代码或外部配置。

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
