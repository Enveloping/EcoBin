# EcoBin 文档中心

这里是 EcoBin 的文档入口。新成员或新的 AI 协作者建议先读“快速开始”，再按任务进入对应专题。

## 快速开始

1. [项目上下文](architecture/project-context.md)：当前阶段、跨会话决策、已知工程坑和续作入口。
2. [V25 设备上报当前袋满溢状态](architecture/fullness-reporting-v25.md)：覆盖旧主动检测方案；只有当前袋明确 `FULL` 阻止下一次投递。
3. [投递全链路联调复盘与复跑手册](operations/delivery-e2e-integration-retrospective-2026-08-02.md)：真实 OneNet/COS、模拟 MCU/双摄的打通过程、易错点和下次检查清单。
4. [产品需求基线](planning/requirements-baseline.md)：当前目标需求；与旧实现冲突时用于判断后续应实现什么。
5. [一周 P0 范围基线](planning/p0-scope-baseline.md)：近期受控真实闭环的承诺范围和验收边界。
6. [P0 业务模型基线](planning/business-model-baseline.md)：已冻结的业务主体、事实、状态机和不变量；2026-07-24 已同步 session 一单与清运电子锁事实。
7. [P0 系统架构设计基线](planning/system-architecture-draft.md)：已冻结的系统边界、九模块布局、一致性、安全、运行与验证方案。
8. [P0 目标数据库设计基线](planning/database-design-draft.md)：已确认的目标表族、约束、事务、并发和迁移方案。
9. [P0 目标接口设计基线](planning/interface-design-draft.md)：已冻结的 Web、小程序、OneNet、微信、UART、模块公开端口和机器契约。
10. [P0 详细设计与任务拆分](planning/detailed-design-draft.md)：已批准的施工方案、跨端责任、任务依赖与目标窗口。
11. [P0 受控闭环正式任务](planning/tasks/p0-controlled-loop/00-index.md)：29 项独立任务、状态、执行主体、依赖、工作量和验收证据；H-01、H-02、F-01～F-11、V-01、V-02 已完成，V-02 的微信开发环境限制由非阻塞 P0-FOLLOWUP-01 延期跟踪，H-03、V-09 为 `ready` 但尚未获得实施授权；H-02 已通过本地 MySQL 8.4.10 开发演练及服务器整改阶段 0～3 验收，服务器目标空库已于 2026-08-04 重建到 V31（96 张领域表、77 条权限定义、业务数据 0），当前试验期由项目负责人接受操作机 ACL 受限 `.ecobin` 保管长期凭证原件，加密密码库和异机密码库副本延期到下一版本；F-11 后续验证发现范围内问题时重开，固定帧真机证据保留在 H-03。
12. [权限与角色设计](architecture/permission-design.md)：当前旧实现的三类登录主体、多租户隔离和接口鉴权。
13. [旧运行数据库设计](architecture/database-design.md)：V1～V14 旧实现的表结构、迁移版本和业务数据关系；新栈目标迁移已推进到 V25，并由只读 epoch guard 校验，运行制品不携带 Flyway/迁移脚本。

## 目录说明

### `architecture/` — 架构与领域设计

- [项目上下文](architecture/project-context.md)
- [V25 设备上报当前袋满溢状态](architecture/fullness-reporting-v25.md)
- [F-01 九模块骨架与过渡退出清单](architecture/f-01-module-transition-inventory.md)
- [F-02 identity 边界与可信上下文实施证据](architecture/f-02-identity-boundary-evidence.md)
- [F-03 业务模块边界搬迁实施证据](architecture/f-03-business-boundary-evidence.md)
- [F-09 HTTP OpenAPI 与客户端传输基础实施证据](architecture/f-09-http-client-transport-evidence.md)
- [V-01 身份与 Web 管理纵切实施证据](architecture/v-01-identity-web-slice-evidence.md)
- [V-02 机构用户注册、手机号、钱包与管理绑定软件证据](architecture/v-02-organization-user-registration-wallet-evidence.md)
- [权限与角色设计](architecture/permission-design.md)
- [数据库设计](architecture/database-design.md)

### `api/` — 当前旧接口与调试资产

- [当前前端对接接口](api/api-frontend.md)：用于旧实现联调，不是目标接口设计基线。
- [Postman 集合](api/EcoBin.postman_collection.json)

### `frontend/` — 当前客户端接入状态

- [Web 管理端能力地图](frontend/web-capability-map.md)：记录当前 `/api/v1` 页面、能力组合、后续业务切片和交付门槛。

### `iot/` — OneNet 与设备云端集成

- [OneNet 物模型说明](iot/onenet-thing-model.md)：当前旧实现资料；目标契约以 `planning/interface-design/` 为准。
- [OneNet 物模型 JSON](iot/onenet-thing-model.json)：当前控制台结构，不是尚未实施的目标机器契约。
- [MQTTX 设备模拟](iot/onenet-device-simulation.md)

### `deployment/` — 部署与运维

- [后端 Docker 部署](deployment/deploy-docker.md)
- [Web 管理端 Docker 部署](deployment/deploy-web-docker.md)

### `operations/` — 操作与恢复证据

- [H-01 旧栈恢复单元与所有权证据](operations/h-01-legacy-recovery-evidence.md)
- [投递全链路联调复盘与复跑手册](operations/delivery-e2e-integration-retrospective-2026-08-02.md)：V25 真实云链路、模拟 MCU/双摄的故障分层、复跑顺序和证据清单。

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
- [F-05 V5 recycling 验证矩阵](planning/database-design/f-05-v5-recycling-verification-matrix.md)
- [F-06 V6～V10 funds/operations 验证矩阵](planning/database-design/f-06-v6-v10-funds-operations-verification-matrix.md)
- [F-07 epoch guard/Fake bootstrap 验证矩阵](planning/database-design/f-07-epoch-guard-fake-bootstrap-verification.md)
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

