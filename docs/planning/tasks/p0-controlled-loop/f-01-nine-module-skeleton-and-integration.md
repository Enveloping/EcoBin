---
task_id: F-01
title: 九模块骨架与 integration 提取
status: done
executor: agent
owner: "Codex / backend-platform-owner"
effort_range: "3-5 person-days"
earliest_start: "H-01 done 后"
blocked_by:
  - H-01
implementation_authorized: true
---

# F-01｜九模块骨架与 integration 提取

> [!NOTE]
> 任务名称和结果描述的是 2026-07-24 的历史实施范围。2026-07-29 清理不可达旧栈后，
> `ecobin-common` 已退出，当前根 reactor 为八模块。

> `status: done`：H-01 回退基线已经完成，九个目标模块骨架和 integration 边界已经
> 实施并通过全量构建与测试；旧 system/business 只作为 F-02/F-03 的显式过渡模块保留。

## 目标

建立能够承接后续纵向切片的九模块物理骨架，并先把 OneNet、COS、微信等外部协议与 SDK
实现提取到 `ecobin-integration`。本任务只改变模块和装配边界，保持当前可观察行为。

## 要构建什么

- 建立九个目标模块的 POM、目标包骨架和最小 `.api` 目录。
- 为各模块建立显式 Spring 配置和本模块 Mapper 扫描，移除 bootstrap 的全项目通配扫描。
- 将 framework 和旧 business 中的 OneNet、COS、微信协议适配及 SDK 依赖迁入
  integration。
- 由消费能力的业务模块定义出站端口，integration 提供适配器实现；入站消费者和出站客户端
  使用不同 Bean。
- 记录过渡阶段仍位于旧 system/business 的源码和退出任务。它们可暂时保留到 F-02/F-03，
  但不得承接新的目标业务行为。

## 验收条件

- [x] 九个目标模块 POM 和 `.api` 骨架可以编译。
- [x] OneNet、COS、微信 SDK 与协议实现只位于 integration。
- [x] framework 不再保存外部平台实现。
- [x] bootstrap 不再使用全项目通配 Mapper 扫描。
- [x] 全量 Maven install/test 通过。
- [x] 旧 Controller 的可观察请求、响应和既有测试行为未改变。
- [x] 仍待 F-02/F-03 搬迁的旧模块和源码有明确退出清单，没有形成长期双实现。

模块结果、适配迁移和 legacy 退出清单见
[F-01 九模块骨架与过渡退出清单](../../../architecture/f-01-module-transition-inventory.md)。

## 阻塞与最早开始

- [H-01](h-01-legacy-stack-recovery-baseline.md) 已完成，前置阻塞已经解除。
- 完成本任务不表示最终九模块收口完成；旧 system/business 源码分别由 F-02、F-03
  继续迁移。

## 排除范围

- identity 的正式迁移和目标身份业务。
- 旧 business 的最终拆除与业务域完整搬迁。
- 目标 V1～V10、数据库切换和 seed。
- 新投递、清运、充值、提现或设备业务行为。
- 首个共同版本形成前的完整契约 CI 或自动 HIL 门禁。

## 权威来源

- [详细设计 01：模块、数据库与切换基础](../../detailed-design/01-foundation-modules-database.md)
- [详细设计 08：实施任务依赖图](../../detailed-design/08-implementation-sequence.md)
- [系统架构基线](../../system-architecture-draft.md)
- [I-051～I-055：模块端口与机器契约](../../interface-design/11-module-ports-machine-contracts-i051-i055.md)

## 进展记录

- 2026-07-23：从已批准的 29 项任务拆分发布；尚未授权实施。
- 2026-07-24：项目负责人在 H-01 完成后授权 F-01。已建立九个目标模块和显式模块级
  Mapper 扫描，将 OneNet、COS、微信适配及 SDK 依赖迁入 integration，并以消费方端口
  倒置依赖；九目标模块加两个 legacy 模块组成过渡 reactor。Java 21 下 `mvn.cmd test`
  通过 59 项测试，随后 `mvn.cmd install -DskipTests` 成功；任务完成。
