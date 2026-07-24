---
task_id: F-02
title: identity 边界、可信上下文与公开端口
status: done
executor: agent
owner: "Codex / identity-security-owner"
effort_range: "3-5 person-days"
earliest_start: "F-01 done 后"
blocked_by:
  - F-01
implementation_authorized: true
---

# F-02｜identity 边界、可信上下文与公开端口

> `status: done`：旧 system 已迁入 identity，可信执行上下文、公开身份边界、首次注册
> 同事务参与扩展和受限 FK 构造引用已经实施，并通过全量构建、测试与边界审查。

## 目标

将旧 system 迁为 identity，建立可信执行上下文、身份/组织/作用域公开边界，并按已确认
DD-004 落地首次注册同步参与扩展和同事务 FK 构造引用。完整新身份业务仍由 V-01/V-02
实现。

## 要构建什么

- 按目标包结构把旧 system 行为迁入 `ecobin-module-identity`，保持当前行为基线。
- 在 framework 建立不可变可信执行上下文；业务作用域只能由服务端会话和数据库事实解析。
- 为身份、机构、机构用户、会话和作用域建立最小公开命令、查询、结果、业务 ID 与端口。
- 在 `identity.api.port` 声明唯一
  `OrganizationUserRegistrationParticipant`，由 funds 模块提供实现并以
  `Propagation.REQUIRED` 参加首次注册事务。
- 实现逐关系强类型、服务端不可序列化的 FK 构造引用，只允许在已点名的同进程、同线程、
  同事务参与点使用。
- 为包边界、序列化边界、事务整体回滚和重复登录不重复参与建立测试。

## 验收条件

- [x] 其他模块不导入 identity Entity、Mapper、Repository 或内部 Service。
- [x] 普通公开命令、查询和结果只使用稳定公开身份。
- [x] `OrganizationUserRegistrationParticipant` 只在 `identity.api.port` 声明，
  funds 只提供唯一实现，不形成反向 Maven 依赖。
- [x] 首次创建参与失败时，用户、钱包初始化和会话整体回滚。
- [x] 重复登录不再次调用首次注册参与者。
- [x] FK 构造引用按关系强类型、不是裸 `Long`、不实现 Java `Serializable`，且
  `toString()` 不输出内部键值。
- [x] 引用不能进入 HTTP、OneNet、UART、微信、MQ、可靠任务、缓存、日志、审计、异常或
  跨事务状态。
- [x] 接收模块不能借引用查询 identity 私表。

## 阻塞与最早开始

- [F-01](f-01-nine-module-skeleton-and-integration.md) 已完成，结构前置阻塞已经解除。
- 项目负责人已授权本任务；system 迁移和公开边界固定均已完成。

## 排除范围

- V-01 的完整人员、任职、权限和 Web 页面。
- V-02 的真实微信注册、手机号和小程序闭环。
- 修改目标 DDL 或把全部跨模块关系改成公开 UID 外键。
- 通用事件总线、`afterCreate` 插件链、动态参与者列表或异步补建钱包。
- 允许内部键跨线程、跨事务或跨进程。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [详细设计 02：身份、设备与配置](../../detailed-design/02-identity-device-configuration.md)
- [I-006～I-010：认证、会话与作用域](../../interface-design/02-auth-session-scope-i006-i010.md)
- [I-011～I-015：身份目录](../../interface-design/03-identity-directory-i011-i015.md)
- [I-051～I-055：模块端口与机器契约](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)
- [F-02 identity 边界实施证据](../../../architecture/f-02-identity-boundary-evidence.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；DD-004 已确认，尚未授权实施。
- 2026-07-24：F-01 已完成，任务由 `blocked` 转为 `ready`；尚未获得 F-02 编码授权。
- 2026-07-24：项目负责人授权 F-02。旧 system 已退出 reactor，原行为迁入 identity；
  framework 建立可信执行上下文，identity/funds 完成唯一首次注册同步参与扩展，并以内部
  工厂限制同事务 FK 构造引用的发行与消费。Java 21 下 Maven 共验证 11 个 reactor
  projects、77 项测试、0 failures；边界、事务、序列化和上下文清理审查通过，任务完成。
