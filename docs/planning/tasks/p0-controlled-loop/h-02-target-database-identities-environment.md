---
task_id: H-02
title: 目标数据库身份与环境供应
status: done
executor: human
owner: "enveloping — project owner / database provisioning operator"
effort_range: "1-2 person-days"
earliest_start: "after F-06 is done and explicit operational authorization"
blocked_by:
  - F-06
implementation_authorized: true
---

# H-02｜目标数据库身份与环境供应

## 目标

供应物理隔离的目标 MySQL 8.4 环境和五类最小权限身份，使 V1～V10 可以由一次性迁移作业安装，而运行应用只有获准业务 DML 权限。

## 要执行什么

- 供应物理隔离的 MySQL 8.4 LTS，并固定完整版本和镜像 digest。
- 配置 UTC、严格 SQL mode、目标事务默认值和禁止旧库访问的环境边界。
- 供应实例初始化/恢复管理员、`ecobin_schema_owner`、`ecobin_trigger_definer`、
  `ecobin_app` 和 `ecobin_backup` 五类身份。
- 只在一次性迁移作业中注入 schema owner；运行应用和容器不得获得该凭证。
- 使用 F-06 的完整 V1～V10 执行迁移、运行应用权限正测和 DDL/旧库等权限负测。
- 归档脱敏 grants、数据库版本、迁移结果和权限验证证据。

## 验收与证据

以下验收项必须在选定的服务器目标环境通过。本地开发演练不替代服务器事实：

- [x] MySQL 8.4 实例满足主审隔离决定：独立容器/网络/卷已证明，主机级物理隔离或
  前期同主机例外已明确记录；版本、镜像和 digest 已记录。
- [x] 时区、SQL mode 和目标连接配置符合冻结设计。
- [x] 五类身份职责和最小 grants 与设计一致。
- [x] `ecobin_schema_owner` 和触发器 definer 凭证不进入运行容器。
- [x] `ecobin_app` 正常业务 DML 和只读 Flyway history 正测通过。
- [x] `ecobin_app` 执行 DDL、GRANT、触发器管理、受保护删除/更新和旧库访问均失败。
- [x] 证据全部脱敏，仓库和普通日志中没有数据库秘密。

服务器数据库验收项已全部完成。2026-07-27 项目负责人重新裁定当前前期受控试验的
凭证保管门：操作机 ACL 受限的 `.ecobin` 作为当前长期原件位置，相关风险由项目
负责人明确接受；加密密码库和异机密码库密文副本延期到下一版本加固。该门不再阻断
H-02，任务验收为 `done`。

开发演练已完成：

- [x] 本机独立 MySQL 8.4.10 容器和数据卷完成 V1～V10。
- [x] 本机五类身份、完整列级 grants 和权限正负探针通过。
- [x] 本机 schema owner/definer 锁定，秘密和证据留在仓库外。

## 阻塞与最早开始

- 前置 [F-06](f-06-database-v6-v10-funds-operations.md) 已完成，完整 V1～V10、
  权限目录和最小权限矩阵已经可用。
- 2026-07-25 进入 `ready` 时只表示任务依赖已解除；项目负责人明确授权目标环境操作后
  才可开始。
- 完成后解除 H-06 的数据库环境依赖。

## 排除范围

- 使用 root 或 schema owner 运行后端。
- 让应用启动时自动迁移、baseline 或修复目标库。
- 用旧实例中的另一个 schema 代替物理隔离实例。
- 把数据库凭证、完整 grants 中的秘密或真实数据写入仓库。

## 权威来源

- [第 01 章：数据库身份、配置和验证](../../detailed-design/01-foundation-modules-database.md)
- [第 08 章：H-02](../../detailed-design/08-implementation-sequence.md)
- [D-041～D-045：迁移和交付](../../database-design/09-migration-delivery-d041-d045.md)
- [H-02 本地开发演练证据](../../../operations/h-02-target-database-evidence.md)
- [单机试验期生产整改计划](../../../deployment/single-host-production-remediation-plan.md)

## 进展记录

- 2026-07-23：正式任务发布；等待 F-06，保持 `blocked`；尚未授权供应或修改数据库环境。
- 2026-07-25：F-06 完成，任务依赖解除并转为 `ready`；真实环境供应仍未授权，
  不创建账号、不执行 GRANT、不接触凭证。
- 2026-07-26：项目负责人 `enveloping` 明确授权 H-02 实施并担任人工操作人，选择
  独立 MySQL 8.4 容器与独立数据卷；任务转为 `in-progress`。实施范围限于目标空库、
  五类身份、V1～V10、最小权限和脱敏验收，不接触旧库、不切换真实入口。
- 2026-07-26：供应 `ecobin-h02-mysql84`、独立数据卷和回环端口目标环境，完成
  MySQL 8.4.10、V1～V10、83 表、71 行权限目录和零业务数据验证；补齐 F-04/F-05
  列级权限矩阵，52 张 P/O 表获准/禁止更新逐表正负测、四张槽位删除、备份读取及
  DDL/GRANT/TRIGGER/系统库负测全部通过。owner/definer 已锁定，脱敏证据归档。
- 2026-07-26：项目负责人澄清上述环境只是本地开发演练，生产阶段继续使用现有单台
  Ubuntu CVM，暂不引入独立云数据库；允许维护窗口内停止旧后端。H-02 保持
  `in-progress`，服务器目标容器/卷、全新生产凭证、公网收敛、异机备份和恢复验证
  尚待实施；执行入口见单机试验期生产整改计划。
- 2026-07-27：主审 `enveloping` 接受前期受控试验的同主机隔离例外，并授权执行
  单机整改阶段 0/1。阶段 0 已完成 SSH/sudo、旧 Compose/镜像/卷、H-01 摘要和
  公钥加密往返探针；服务器只安装公钥并建立 root-only 备份目录。因尚不能确认腾讯云
  控制台/VNC，也无法取得安全组 IPv4/IPv6 规则快照，按停止条件未停止旧容器、未改
  UFW/安全组/数据库，阶段 1 尚未开始。H-02 保持 `in-progress`。
- 2026-07-27：项目负责人确认 VNC 可用和 24 小时最大停机窗口，并提供云防火墙规则
  截图；阶段 0 完成。阶段 1 停止旧 Web/backend，在无活动应用会话后完成最终流式
  加密备份及异机解密探针，停止旧 MySQL 并保留 `ecobin_ecobin-mysql-data` 卷。
  管理端公网出口随后澄清为动态 IP，项目负责人决定暂时保持云防火墙/UFW 的 22 对
  全部 IPv4 开放且不关闭 SSH 密码登录；自动回退保护下修改后第二条 SSH/sudo 通过。
  最终云规则只保留 IPv4 的 443/22/80/ICMP，独立外部主机确认 8080/3306/8642 均
  不可连接。阶段 0/1 完成；阶段 2 及服务器目标数据库供应尚未授权，H-02 保持
  `in-progress`。
- 2026-07-27：项目负责人授权阶段 2，决定使用本地加密密码库加异机密文副本，要求
  所有待导入秘密先集中到操作机受限 `.ecobin` 目录；旧栈继续离线至阶段 3 完成，
  SSH 密码登录暂时保留。已生成全新生产 DB/JWT/AES 秘密并建立服务器
  `root:root 0600` 持久源和按服务 tmpfs 运行副本；UID 10001 断网只读探针通过。
  2 GiB swap、`swappiness=10`、既有 NTP 同步、五分钟容量检查和 Docker 日志上限
  已落地，Docker 未重启，旧三容器保持 stopped。用户密码库导入、异机密文复制和
  副本开启验证仍待 `enveloping` 完成；阶段 3 未授权，H-02 继续 `in-progress`。
- 2026-07-27：项目负责人明确授权阶段 3。服务器供应 `ecobin-target-mysql84`、
  独立 `ecobin-target-mysql84-data` 卷和 `ecobin-target-db` 内部网络，无任何宿主
  端口映射；固定 MySQL 8.4.10 digest，完成 V1～V10、83 表、71 条权限定义、五类
  身份、52 张表列级权限矩阵、正负权限探针和零业务数据验证。公网独立主机确认
  3306/13306 不可连接，宿主机监听数为 0。随后用 `ecobin_backup` 流式生成 CMS
  密文并复制到操作机 `.ecobin`，完成内存解密和一次性独立容器恢复；恢复后的迁移、
  表、目录、零业务数据和散列均通过，临时恢复容器/卷/网络已精确删除，生产对象和旧
  卷保留。阶段 3 技术门完成，阶段 4 未授权，旧栈继续离线。操作人密码库导入与异机
  密文副本开启验证仍待完成，因此 H-02 保持 `in-progress`，不提前进入 `in-review`。
- 2026-07-27：项目负责人明确改用当前试验期凭证保管例外：所有生产长期秘密继续
  保存在操作机 ACL 受限 `.ecobin`，不在本版本建立加密密码库；加密密码库及其异机
  密文副本延期到下一版本。主审复核阶段 0～3 技术证据、服务器实时状态、完整 Maven
  测试、脚本语法和仓库秘密扫描，无 P0/P1 问题；三项非阻断改进按负责人决定延期。
  H-02 转为 `done`。该完成只解除 H-06 的数据库环境依赖，不授权阶段 4、F-12 seed、
  应用部署、旧数据导入或真实入口切换；旧栈继续离线。
