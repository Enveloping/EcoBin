# EcoBin 文档中心

这里是 EcoBin 的文档入口。新成员或新的 AI 协作者建议先读“快速开始”，再按任务进入对应专题。

## 快速开始

1. [项目上下文](architecture/project-context.md)：当前阶段、跨会话决策、已知工程坑和续作入口。
2. [产品需求基线](planning/requirements-baseline.md)：当前目标需求；与旧实现冲突时用于判断后续应实现什么。
3. [一周 P0 范围基线](planning/p0-scope-baseline.md)：近期受控真实闭环的承诺范围和验收边界。
4. [P0 业务模型基线](planning/business-model-baseline.md)：已冻结的业务主体、事实、状态机和不变量；2026-07-24 已同步 session 一单与清运电子锁事实。
5. [P0 系统架构设计基线](planning/system-architecture-draft.md)：已冻结的系统边界、九模块布局、一致性、安全、运行与验证方案。
6. [P0 目标数据库设计基线](planning/database-design-draft.md)：已确认的目标表族、约束、事务、并发和迁移方案。
7. [P0 目标接口设计基线](planning/interface-design-draft.md)：已冻结的 Web、小程序、OneNet、微信、UART、模块公开端口和机器契约。
8. [P0 详细设计与任务拆分](planning/detailed-design-draft.md)：已批准的施工方案、跨端责任、任务依赖与目标窗口。
9. [P0 受控闭环正式任务](planning/tasks/p0-controlled-loop/00-index.md)：29 项独立任务、状态、执行主体、依赖、工作量和验收证据；H-01、F-01、F-02、F-04 已完成，F-03/F-05/F-09 已 `ready` 但未授权；F-10 通用三语言黄金样本与 `0x300` 真机 HIL 已通过但 MCU 实际工具链黄金程序未收口，F-11 已完成配置命令软件/真机纵切并继续 `in-progress`。
10. [权限与角色设计](architecture/permission-design.md)：当前旧实现的三类登录主体、多租户隔离和接口鉴权。
11. [旧运行数据库设计](architecture/database-design.md)：V1～V14 旧实现的表结构、迁移版本和业务数据关系；独立目标 V1～V4 尚未切换旧应用。

## 目录说明

### `architecture/` — 架构与领域设计

- [项目上下文](architecture/project-context.md)
- [F-01 九模块骨架与过渡退出清单](architecture/f-01-module-transition-inventory.md)
- [F-02 identity 边界与可信上下文实施证据](architecture/f-02-identity-boundary-evidence.md)
- [权限与角色设计](architecture/permission-design.md)
- [数据库设计](architecture/database-design.md)

### `api/` — 当前旧接口与调试资产

- [当前前端对接接口](api/api-frontend.md)：用于旧实现联调，不是目标接口设计基线。
- [Postman 集合](api/EcoBin.postman_collection.json)

### `iot/` — OneNet 与设备云端集成

- [OneNet 物模型说明](iot/onenet-thing-model.md)：当前旧实现资料；目标契约以 `planning/interface-design/` 为准。
- [OneNet 物模型 JSON](iot/onenet-thing-model.json)：当前控制台结构，不是尚未实施的目标机器契约。
- [MQTTX 设备模拟](iot/onenet-device-simulation.md)

### `deployment/` — 部署与运维

- [后端 Docker 部署](deployment/deploy-docker.md)
- [Web 管理端 Docker 部署](deployment/deploy-web-docker.md)

### `operations/` — 操作与恢复证据

- [H-01 旧栈恢复单元与所有权证据](operations/h-01-legacy-recovery-evidence.md)

### `flows/` — 流程图与可视化说明

- [投递流程交互图](flows/delivery-flow.html)：旧实现/历史交互图；目标投递以详细设计第 04 章为准。
- [照片上传流程交互图](flows/photo-upload-flow.html)
- [用户投递流程图](flows/用户投递流程图.jpg)
- [清运流程图](flows/清运流程图.jpg)

### `planning/` — 需求、计划与待办

- [产品需求基线](planning/requirements-baseline.md)
- [一周 P0 范围基线](planning/p0-scope-baseline.md)
- [P0 业务模型基线](planning/business-model-baseline.md)
- [P0 系统架构设计基线](planning/system-architecture-draft.md)
- [P0 目标数据库设计基线](planning/database-design-draft.md)
- [F-04 V1～V4 数据库验证矩阵](planning/database-design/f-04-v1-v4-verification-matrix.md)
- [P0 目标接口设计基线](planning/interface-design-draft.md)
- [P0 详细设计与任务拆分](planning/detailed-design-draft.md)
- [P0 受控闭环正式任务](planning/tasks/p0-controlled-loop/00-index.md)

### 其他目录

- `agents/`：本地任务仓库、状态词汇和工程领域导航配置。
- `archive/`：已结束审查和历史设计快照，只用于追溯，不代表当前实现。
- `review/`：仍有参考价值的代码审查结果。
- `references/`：外部官方资料和示例代码留档，不作为本项目实现的直接规范。
- `sql/`：联调和测试辅助 SQL。

硬件侧的专用文档位于 [`hardware/docs/`](../hardware/docs/)，包括架构说明、部署说明和 UART 协议审计。它们与香橙派代码放在一起，避免主项目文档与设备运行资料再次混杂。

## 维护规则

- 新文档先确定类别再落盘，不要直接堆到 `docs/` 根目录。
- 当前行为以代码、测试和 Flyway 迁移为准；设计文档应在同一变更中同步更新。
- 历史内容移入 `archive/`，并在开头标明替代它的当前文档。
- 文档内使用仓库相对链接；移动文件时必须全仓库搜索旧路径。
- 密钥、`.env`、设备密钥、服务器凭证和真实用户数据不得写入文档。

