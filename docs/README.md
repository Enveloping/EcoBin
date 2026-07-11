# EcoBin 文档中心

这里是 EcoBin 的文档入口。新成员或新的 AI 协作者建议先读“快速开始”，再按任务进入对应专题。

## 快速开始

1. [项目上下文](architecture/project-context.md)：当前阶段、跨会话决策、已知工程坑和续作入口。
2. [权限与角色设计](architecture/permission-design.md)：三类登录主体、多租户隔离和接口鉴权。
3. [数据库设计](architecture/database-design.md)：表结构、迁移版本和业务数据关系。
4. [未解决项](planning/open-items.md)：技术债、功能缺口和依赖外部条件的工作。

## 目录说明

### `architecture/` — 架构与领域设计

- [项目上下文](architecture/project-context.md)
- [权限与角色设计](architecture/permission-design.md)
- [数据库设计](architecture/database-design.md)

### `api/` — 接口契约与调试资产

- [前端对接接口](api/api-frontend.md)
- [Postman 集合](api/EcoBin.postman_collection.json)

### `iot/` — OneNet 与设备云端集成

- [OneNet 物模型说明](iot/onenet-thing-model.md)
- [OneNet 物模型 JSON](iot/onenet-thing-model.json)：控制台导入的单一结构来源。
- [MQTTX 设备模拟](iot/onenet-device-simulation.md)

### `deployment/` — 部署与运维

- [后端 Docker 部署](deployment/deploy-docker.md)
- [Web 管理端 Docker 部署](deployment/deploy-web-docker.md)

### `flows/` — 流程图与可视化说明

- [投递流程交互图](flows/delivery-flow.html)
- [照片上传流程交互图](flows/photo-upload-flow.html)
- [用户投递流程图](flows/用户投递流程图.jpg)
- [清运流程图](flows/清运流程图.jpg)

### `planning/` — 计划与待办

- [未解决项](planning/open-items.md)

### 其他目录

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

