# EcoBin 文档中心

这里是 EcoBin 的文档入口。新成员或新的 AI 协作者建议先读“快速开始”，再按任务进入对应专题。

## 快速开始

当前硬件计划入口：[MCU—香橙派契约对齐与分批实施计划](planning/mcu-edge-contract-implementation-plan-2026-09-12.md)。
已对照现有 contracts 列出差异、具体文件、测试、HMI/烧录与切换顺序；尚未授权编码或部署。
分工与共识背景见[总体方案](architecture/mcu-edge-refactor-plan-2026-09-11.md)，未决事项不因计划完成而自动确认。

1. [项目上下文](architecture/project-context.md)：当前阶段、跨会话决策、已知工程坑和续作入口。
2. [V52 设备自注册、厂家初始袋与按需远程维护](architecture/device-enrollment-factory-acceptance-remote-support-v52.md)：全局厂家引导密钥的一次性清理、OneNet 自动创建设备、验收前真实装袋、管理员公钥一次登记及四端口按需反向 SSH。
3. [V52 设备出厂与远程维护代码审查](review/device-enrollment-factory-remote-support-v52-review-2026-08-15.md)：跟踪厂家袋扫码、验收证据代次、远程租约与生产证书权限的 4 项 P1、3 项 P2 及修复证据。
4. [V53 投递自动审核与自动提现](architecture/delivery-auto-review-and-withdrawal-v53.md)：正常投递的版本化自动审核、异常转人工、首次正返现自动提现及安全跳过边界。
5. [V54 投递审核金额阈值与 Web 配置中心](architecture/delivery-review-amount-limit-and-configuration-center-v54.md)：按结算金额决定自动或人工审核，复用单次最大提现金额，并收拢两类机构业务规则入口。
6. [香橙派可重复量产镜像与首次启动编排计划](planning/orangepi-production-image-first-boot-plan.md)：统一面向 32 GB TF 卡的 Debian 12/Python 3.11 镜像构建、UART5、可选 BOOT0/NRST 升级能力、Air780E USB RNDIS 上行、隔离验收热点、500 g±10 g 称重预检、真实或明确标记的 MCU 模拟外设证据、K1 清理、单向封存、revision 2、双摄预检和镜像发布物；真实 HIL 门禁仍关闭。
7. [V36 设备永久归属、自动验收与无部署码模型](architecture/permanent-device-ownership-v36.md)：设备全链路目标，覆盖旧部署、调拨和人工激活模型；2026-08-10 补充未分配设备运行、故障与安全事实的平台作用域。
8. [V41 全局固定设备二维码入口](architecture/global-miniapp-device-entry-v41.md)：普通二维码统一使用全局地址，设备公开码区分设备和机构，渠道/机构不再保存入口地址。
9. [V42 设备入口 URL 下发](architecture/device-entry-url-edge-delivery-v42.md)：验收时保存完整 URL，全局地址改变时自动下发，MCU 无应答且屏幕结果不进入平台验收。
10. [V43 平台防伪袋码与标签打印](architecture/authenticated-bag-labels-v43.md)：平台按批签发 EB1 标签，不预建库存；厂家登记和清运换袋时由后端验真。
11. [V46 设备运行快照全局策略](architecture/runtime-snapshot-reporting-v46.md)：状态变化最多每 5 秒合并，空闲默认每 60 分钟兜底，平台全局配置且不清理历史事实。
12. [V49 最近登录机构账号选择](architecture/recent-miniapp-organization-account-v49.md)：多机构用户需要新会话时进入最近成功登录的可用账号，设备扫码仍优先。
13. [V50 平台管理员引导与治理](architecture/platform-administrator-governance-v50.md)：空管理员表创建受保护的默认账号；只有默认管理员能治理其他平台管理员，普通管理员只能自行改密。
14. [应用修改后重新部署操作手册](deployment/application-redeployment-runbook.md)：代码提交后本地构建 JAR/dist、上传服务器、制作运行镜像、预检、激活、验证和回退的日常入口。
15. [V25 设备上报当前袋满溢状态](architecture/fullness-reporting-v25.md)：覆盖旧主动检测方案；只有当前袋明确 `FULL` 阻止下一次投递。
16. [设备接入、配置恢复、投递与清运一致性审查](architecture/device-delivery-clean-generation-consistency-review-2026-08-10.md)：说明厂家袋、当前袋、容量和重量基准之间的不变量，以及配置失败后的新版本恢复边界，并跟踪本轮逐项修复证据。
17. [投递全链路联调复盘与复跑手册](operations/delivery-e2e-integration-retrospective-2026-08-02.md)：真实 OneNet/COS、模拟 MCU/双摄的历史联调复盘；自 V38 起模拟来源不再阻止平台机器验收，真实物理质量仍由厂家质检和 H-03 验证。
18. [产品需求基线](planning/requirements-baseline.md)：当前目标需求；与旧实现冲突时用于判断后续应实现什么。
19. [一周 P0 范围基线](planning/p0-scope-baseline.md)：近期受控真实闭环的承诺范围和验收边界。
20. [P0 业务模型基线](planning/business-model-baseline.md)：已冻结的业务主体、事实、状态机和不变量；设备章节由 V36 专题覆盖。
21. [P0 系统架构设计基线](planning/system-architecture-draft.md)：已冻结的系统边界、当前八模块布局、一致性、安全、运行与验证方案。
22. [P0 目标数据库设计基线](planning/database-design-draft.md)：已确认的目标表族、约束、事务、并发和迁移方案。
23. [P0 目标接口设计基线](planning/interface-design-draft.md)：已冻结的 Web、小程序、OneNet、微信、UART、模块公开端口和机器契约。
24. [P0 详细设计与任务拆分](planning/detailed-design-draft.md)：已批准的施工方案、跨端责任、任务依赖与目标窗口。
25. [P0 受控闭环正式任务](planning/tasks/p0-controlled-loop/00-index.md)：历史任务入口；设备相关旧部署步骤由 V36 裁决覆盖。
26. [权限与角色设计](architecture/permission-design.md)：三类登录主体、多租户隔离和接口鉴权。
27. [旧运行数据库设计](architecture/database-design.md)：V1～V14 旧实现的表结构；新栈目标迁移已推进到 V63，并由只读 epoch guard 校验，运行制品不携带 Flyway 或迁移脚本。

## 目录说明

### `architecture/` — 架构与领域设计

- [项目上下文](architecture/project-context.md)
- [单片机与香橙派最小事实逐项共识](architecture/mcu-edge-minimal-facts-consensus-2026-09-11.md)：讨论中、未整体实施；已确认启动回复、编号关联、防重复动作、结果保存确认和按编号查询。门状态保留最近有效方向（01=关、10=开）；PB5 仅处理关门防夹，不单独判故障或阻止其他条件已满足后的下一笔开门。投递失败后香橙派自动关门并检查；清运缺失数据留异常，由原清运员完成确认后核对袋子、建立新空袋基准。恢复由香橙派本机负责，后端不指挥，但开始时已有操作登记，最终依据上报应用实际结果；旧重启中止及异常结果接收需要适配。准确度交人工；已确认 250 毫秒目标读取间隔、最近 5 次最大最小差不超过 100 克取均值，最多等 5 秒，纯波动超时取中位数继续业务，无可用数据才走测量失败。需解耦清运锁计时并同步跨端规则，现场响应速度及具体协议仍待验证/设计。
- [香橙派业务程序发布与远程更新设计](architecture/orangepi-business-runtime-release-and-update-design.md)：区分出厂程序、可替换业务程序和永久设备管理层，记录九阶段迁移顺序；第三阶段已在 v13 完成受控在线验收，第四阶段默认关闭基础已在 v19 完成指定单卡接入和普通业务真机验收；v23 已完成写卡、接入、封存、投递和清运，并在第一次所有权试切换发现问题后安全恢复；持久切换修复现已进入 v24 无秘密/HIL 候选并通过两套离线审计，尚待加入允许列表、写卡和真机更新/回滚/断电恢复，远程下发继续关闭。
- [V52 设备自注册、厂家初始袋与按需远程维护](architecture/device-enrollment-factory-acceptance-remote-support-v52.md)
- [V52 设备出厂与远程维护代码审查](review/device-enrollment-factory-remote-support-v52-review-2026-08-15.md)
- [V53 投递自动审核与自动提现](architecture/delivery-auto-review-and-withdrawal-v53.md)
- [V54 投递审核金额阈值与 Web 配置中心](architecture/delivery-review-amount-limit-and-configuration-center-v54.md)
- [V25 设备上报当前袋满溢状态](architecture/fullness-reporting-v25.md)
- [V41 全局固定设备二维码入口](architecture/global-miniapp-device-entry-v41.md)
- [V42 设备入口 URL 下发](architecture/device-entry-url-edge-delivery-v42.md)
- [V43 平台防伪袋码与标签打印](architecture/authenticated-bag-labels-v43.md)
- [V46 设备运行快照全局策略](architecture/runtime-snapshot-reporting-v46.md)
- [V49 最近登录机构账号选择](architecture/recent-miniapp-organization-account-v49.md)
- [V50 平台管理员引导与治理](architecture/platform-administrator-governance-v50.md)
- [设备接入、配置恢复、投递与清运一致性审查](architecture/device-delivery-clean-generation-consistency-review-2026-08-10.md)
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

- [投递异常隔离收口部署与新卡账本启用](operations/delivery-quarantine-deployment-2026-09-11.md)：2026-09-11 OneNet 18/22、V69 和后端/Web 已上线；经确认完成 v37 保留数据切换、仅账本模式修正和永久激活，更新器重启保持、后台兼容接入正常。未执行异常收口或机构动作，新 F3 `0x0B` 尚不满足收口安全要求。
- [香橙派量产镜像、写卡与整机验收手册](deployment/orangepi-production-image-factory-runbook.md)：从输入锁、两次构建、受控注密、签名发布和写卡复读，到离线硬件验收、Air780E 注册、云端授权、单向封存、冷启动放行与返工边界；当前真实 HIL 和 32 GB 布局锁未完成，不得量产放行。
- [v37 自定义重量报告校验镜像证据](../hardware/image-artifacts/evidence/hil-weight-report-validation-20260910-37/README.md)：2026-09-10 已将跨环节重量校验修复制成镜像，2180 项自动化测试及镜像内 ARM64 完整报告测试通过，指定 TF 卡完整写入/回读摘要一致。断电后验证已完成软件包并复用缓存，只重做中断的镜像组装；随后补齐后台认可配置，设备新报告已通过云端机器验收，实物称重精度仍未校准。
- [v37 后端认可版本部署与现场复验](operations/orangepi-v37-backend-allowlist-deployment-2026-09-10.md)：用户确认后只追加 v37 并重载既有应用，备份及独立健康/配置核验通过；21:44 新报告通过、原失败记录保留，设备已确认封存授权。不部署 V69、OneNet 新模型或后端代码。
- [v36 称重标准与采样展示镜像证据](../hardware/image-artifacts/evidence/hil-weight-reference-20260910-36/README.md)：2026-09-10 已完成镜像、2121 项自动化测试、ARM64 离线执行及指定 TF 卡完整写入/回读，摘要一致；保留并验证下载缓存复用。用户随后冷启动发现非 500 克报告被下游拒绝，该软件缺陷已在 v37 修复并写卡；真实 500 克测得约 28 克的精度问题仍未解决。后续诊断见[称重报告校验记录](../hardware/docs/review/factory-weight-reference-and-measurements-2026-09-10.md)。
- [v35 投递异常隔离镜像证据](../hardware/image-artifacts/evidence/hil-delivery-recovery-quarantine-20260910-35/README.md)：2026-09-10 已完成本地镜像、2080 项自动化测试及 ARM64 离线执行，并向用户确认的新卡写入与完整回读，摘要一致。用户随后反馈热点 500 克验收不通过；旧身份与账本不迁移，服务器旧记录未清理，配套部署与真机异常隔离验收仍未完成。
- [设备自注册、厂家验收与按需反向 SSH 部署手册](deployment/device-enrollment-and-remote-support-rollout.md)：V52 数据迁移、服务器 SSH 边界、生产秘密、香橙派注册包、试点验收和功能回退。
- [应用修改后重新部署操作手册](deployment/application-redeployment-runbook.md)：日常代码发布的范围判断、构建、上传、安装、预检、激活、验证与回退步骤。
- [生产部署配置、密钥与证书清单](deployment/production-configuration-secrets-certificates.md)：逐项说明服务器配置文件、秘密、微信支付公钥/商户证书、机构小程序配置和启动前验收。
- [目标单机部署手册](deployment/target-single-host-deployment.md)：当前 `115.159.67.35` 目标栈的安装、启动、Nginx 切换与回退步骤。
- [后端 Docker 部署](deployment/deploy-docker.md)
- [Web 管理端 Docker 部署](deployment/deploy-web-docker.md)

### `operations/` — 操作与恢复证据

- [H-01 旧栈恢复单元与所有权证据](operations/h-01-legacy-recovery-evidence.md)
- [H-02 目标数据库供应证据](operations/h-02-target-database-evidence.md)：包含 2026-08-29
  腾讯云 CDB V60 空库、初始超级管理员、账号级 TLS、未切换旧 V59 后端和待迁移 COS 的
  当前接力状态。
- [投递全链路联调复盘与复跑手册](operations/delivery-e2e-integration-retrospective-2026-08-02.md)：V25 真实云链路、模拟 MCU/双摄的故障分层、复跑顺序和证据清单。
- [香橙派 v17 后端允许列表部署记录](operations/orangepi-v17-backend-allowlist-deployment-2026-09-03.md)：记录 v17 加入、未写卡 v15/v16 移除、可恢复配置备份以及生产重载和健康核验结果，不包含运行秘密。
- [香橙派 v19 后端允许列表部署记录](operations/orangepi-v19-backend-allowlist-deployment-2026-09-03.md)：记录第四阶段镜像身份替换 v17、v15/v16/v18 继续排除、并发安全回退、可恢复配置备份以及生产重载和健康核验结果；允许版本不代表已经写卡或通过真机验收。
- [香橙派 v20 后端允许列表部署记录](operations/orangepi-v20-backend-allowlist-deployment-2026-09-03.md)：记录曾在保留 v19 的同时追加 v20 的历史部署；v20 从未写卡，随后已由 v21 替换。
- [香橙派 v21 后端允许列表部署记录](operations/orangepi-v21-backend-allowlist-deployment-2026-09-03.md)：记录允许列表从 v19+v20 切换为 v19+v21、可恢复配置备份、生产重载和独立健康核验；允许版本不代表真实 MCU 更新已通过或远程更新已开放。
- [香橙派 v23 后端允许列表部署记录](operations/orangepi-v23-backend-allowlist-deployment-2026-09-04.md)：记录 v23 指定 TF 卡完整写入回读、在保留 v13/v19/v21 的同时加入后端允许列表、可恢复配置备份、生产重载和独立健康核验；允许版本不代表本地业务更新真机验收或远程下发已经通过。

### `flows/` — 流程图与可视化说明

- [投递流程交互图](flows/delivery-flow.html)：旧实现/历史交互图；目标投递以详细设计第 04 章为准。
- [照片上传流程交互图](flows/photo-upload-flow.html)
- [用户投递流程图](flows/用户投递流程图.jpg)
- [清运流程图](flows/清运流程图.jpg)

### `planning/` — 需求、计划与待办

- [香橙派可重复量产镜像与首次启动编排计划](planning/orangepi-production-image-first-boot-plan.md)：32 GB TF 卡的 Debian 12/Python 3.11 量产镜像、可选 MCU 远程升级线与能力事实、Air780E RNDIS 上行、固定离线验收热点、出厂 MCU 外设模拟、云端验收后单向封存、首启恢复和无需 SSH 出厂验收；真机门禁保持关闭。
- [产品需求基线](planning/requirements-baseline.md)
- [一周 P0 范围基线](planning/p0-scope-baseline.md)
- [P0 业务模型基线](planning/business-model-baseline.md)
- [P0 系统架构设计基线](planning/system-architecture-draft.md)
- [P0 目标数据库设计基线](planning/database-design-draft.md)
- [D-046 微信免确认收款授权数据库设计](planning/database-design/10-merchant-transfer-authorization-d046.md)
- [F-04 V1～V4 数据库验证矩阵](planning/database-design/f-04-v1-v4-verification-matrix.md)
- [F-05 V5 recycling 验证矩阵](planning/database-design/f-05-v5-recycling-verification-matrix.md)
- [F-06 V6～V10 funds/operations 验证矩阵](planning/database-design/f-06-v6-v10-funds-operations-verification-matrix.md)
- [F-07 epoch guard/Fake bootstrap 验证矩阵](planning/database-design/f-07-epoch-guard-fake-bootstrap-verification.md)
- [P0 目标接口设计基线](planning/interface-design-draft.md)
- [I-056 微信免确认收款授权接口设计](planning/interface-design/12-merchant-transfer-authorization-i056.md)
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
