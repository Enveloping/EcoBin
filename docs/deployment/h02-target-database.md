# H-02 目标数据库环境供应手册

> 当前状态：`done`
>
> 操作人：`enveloping`
>
> 当前用途：Windows 本地开发演练与服务器阶段 3 供应手册；两处均已验证独立
> MySQL 8.4.10 容器/卷、五类身份、V1～V31 和脱敏权限探针。当前脚本目标已推进到
> V54；服务器目标库须现场核对并用续跑模式升级；
> 服务器既有执行结果另见
> [单机试验期生产整改计划](single-host-production-remediation-plan.md)。

## 1. 本手册不会做什么

- 不连接或修改旧 V1～V14 数据库；
- 不导入旧余额、订单、设备状态或其他业务数据；
- 不执行试点 seed；
- 不启动后端，不开放真实 API、OneNet、COS 或微信入口；
- 不切换 Web、设备、MQ 或可靠任务所有权；
- 失败时不自动 `repair`、删库、删容器或删数据卷。

H-02 只供应目标空库。试点 seed 属于 F-12，入口切换和回退签署属于 H-06。

## 2. 固定对象

| 对象 | 固定值 |
|---|---|
| Compose project | `ecobin-h02` |
| 容器 | `ecobin-h02-mysql84` |
| 数据卷 | `ecobin-h02-mysql84-data` |
| 内部网络 | `ecobin-h02-network` |
| 本机端口 | `127.0.0.1:13306` |
| 数据库 | `ecobin` |
| MySQL | `8.4.10` |
| 固定镜像引用摘要 | `mysql@sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |
| 本地 Docker image ID | `sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |
| 服务器 Linux/amd64 image ID | `sha256:9cffaceb9b62d4280247acdb2324b380d2b36208ae34dfe9f0afb62eeaf70f08` |

容器只把数据库端口绑定到本机回环地址，不能从局域网直接访问。Compose 使用独立
bridge 网络供后续目标应用容器接入；数据卷与旧栈没有共享关系。

## 3. 秘密与证据位置

供应脚本在仓库外创建：

```text
C:\tmp\ecobin-h02-secrets
C:\tmp\ecobin-h02-evidence\<UTC run id>
```

秘密目录保存初始化管理员、`ecobin_app` 和 `ecobin_backup` 的密码文件。脚本会收紧
Windows ACL，不显示密码，也不会生成仓库内 `.env`。schema owner 密码只在单次迁移
进程中存在，迁移后配置文件被删除且账号锁定。触发器 definer 从创建起就是锁定账号。

`C:\tmp` 不是长期秘密管理系统，也不是生产秘密来源。本地三个密码只服务本次开发
演练；服务器生产密码已重新生成。2026-07-27 项目负责人接受当前前期受控试验把
操作机 ACL 受限的 `.ecobin` 作为长期原件位置，服务器保留 root-only 运行副本；
加密密码库和异机密码库密文副本延期到下一版本。任务文档只记录位置类别，不记录
秘密值。

证据目录只包含版本、配置、迁移标记、计数、脱敏 `SHOW GRANTS` 和权限结论。

## 4. 执行

### 4.1 迁移来源必须可复现

`provision-h02-target.ps1` 在接触 Docker、密码目录或目标数据库之前，只检查
`ecobin-bootstrap/src/main/resources/db/p0-migration`：

- 已跟踪迁移文件存在未提交或已暂存修改时拒绝执行；
- 迁移目录出现未跟踪 SQL 草稿时拒绝执行；
- 工作树其他目录存在修改不影响 H-02；
- 成功执行时把 Git 提交号、所有迁移文件的 SHA-256 清单以及清单摘要写入证据目录。

这个保护用于避免本地 H-02 再次从“尚未提交的迁移草稿”建库。它不允许通过
Flyway `repair` 掩盖校验和差异；遇到历史草稿库时应备份后重建。

### 4.2 运行供应脚本

在 H-02 worktree 的 PowerShell 中运行：

```powershell
.\tools\database\provision-h02-target.ps1
```

脚本在产生副作用前检查：

- Docker Desktop 可用；
- 固定 JDK 21.0.10 可用且 `mvn.cmd` 实际使用 Java 21；
- 已审计镜像 digest/ID 一致；
- 容器、数据卷和网络名称尚不存在；
- `13306` 端口可用；
- 秘密目录尚不存在，避免覆盖已有凭证。

迁移按以下顺序执行：

1. 创建独立容器、网络和数据卷；
2. 创建目标数据库和五类身份；
3. schema owner 安装 V1～V8；
4. 为锁定 trigger definer 授予两个触发器需要的精确读取权限；
5. schema owner 安装 V9～V54；
6. 锁定 schema owner；
7. 应用当前已冻结的表级/列级运行权限；
8. 执行正向 DML 和 DDL/GRANT/TRIGGER/事实删除/系统库访问负测；
9. 归档脱敏证据。

首次安装失败时，脚本保留容器和数据卷用于诊断。不得对半库执行 Flyway `repair`。
确认诊断证据后，如需丢弃半库并重装，必须再次明确批准要删除的容器、网络、数据卷和
秘密目录。

若迁移尚未创建任何表，只因 `internal` 网络导致宿主机迁移器无法访问，可保留数据卷
并恢复：

```powershell
.\tools\database\provision-h02-target.ps1 `
  -ResumeExistingEmptyEnvironment
```

恢复模式只接受目标数据库存在且表数为 0 的环境；它用 `docker compose down`
重建容器和网络但不删除数据卷，重新生成一次性 schema owner 密码后继续首次迁移。

若现有数据库已完整到 V30～V54 中任一受支持纪元、owner 已锁定，使用：

```powershell
.\tools\database\provision-h02-target.ps1 `
  -ResumeExistingMigratedEnvironment `
  -TransientSshAttempts 8
```

该模式要求 V30/V31/V32 为 96 张领域表、V33/V34 为 97 张领域表、V35 为 99 张领域表、
V36/V37/V38 为 93 张领域表、V39/V40/V41 为 95 张领域表、V42 为 96 张领域表，
V43/V44/V45 为 98 张领域表，V46～V51 为 99 张领域表，V52 为 112 张领域表，V53/V54 为 113 张领域表，并且迁移历史精确停在
对应纪元。低于 V54 时会用一次性新密码
解锁 schema owner，只执行尚缺的前向迁移直到 V54，完成后立即重新锁定；V54 不解锁
owner、不重复迁移。
`TransientSshAttempts` 只允许在这个已迁移、操作均幂等的续跑模式使用；它只重试
SSH 连接层错误。SQL 或权限错误不会被重试为成功，SSH 255 也不能冒充权限负测通过。

上面的默认续跑仍按“目标空库”验收，发现权限目录之外的业务行就停止。正式库已经有
租户、用户、资金等业务数据时，必须先停止所有写入方并完成可恢复备份，再显式使用：

```powershell
.\tools\database\provision-h02-target.ps1 `
  -ResumeExistingMigratedEnvironment `
  -AllowExistingBusinessRows `
  -TransientSshAttempts 8
```

`AllowExistingBusinessRows` 只跳过“业务行必须为零”这一项空库断言，不跳过迁移历史、
表数量、权限目录、账号锁定、运行账号正负权限或备份读取探针；该参数不能用于首次建库
或空环境恢复。

## 5. 列级权限完成门

H-02 实施审查发现 F-04/F-05 原矩阵只有表和写类，没有把 identity/device/recycling
等持久化对象落到精确更新列。当前 grants 目录已按冻结状态机、不可变边界和 V54 DDL
补齐。

脚本只生成：

- 113 张领域表显式 `SELECT`；
- 除权限目录外显式 `INSERT`；
- 四张当前槽位表和可删除的袋码批次表显式 `DELETE`；
- 对 70 张 P/O 表只授予矩阵明确列出的列级 `UPDATE`。

验收对每张 P/O 表执行一条获准列空集更新正测，并选择该表首个未授权列执行负测；
五张可删除表逐表验证 `DELETE`，备份身份以 `single-transaction` 数据读取探针验证。
任何 schema 级或整表 `UPDATE/DELETE` 仍视为失败。

目录版本 27 包含 V52 注册续作、平台小程序会话、厂家袋扫码/更正、远程端口槽和维护会话，
并覆盖 V53 自动审核及自动提现需要的精确列级更新；V54 新增的规则金额与订单快照在创建时写入、此后不可修改，因此不增加运行时 UPDATE 列。新增自动提现决策表只授予
`SELECT/INSERT`，没有增加整表 UPDATE 或业务事实任意删除权限。厂家袋的袋码、
标签、操作者和安装时间只允许由受审计的安装/更正用例按精确列推进，不能通过普通设备管理
接口修改。

## 6. 日常启停

使用秘密目录中的 `compose.env`：

```powershell
docker compose `
  --project-name ecobin-h02 `
  --env-file C:\tmp\ecobin-h02-secrets\compose.env `
  --file .\docker-compose.h02.yml `
  stop

docker compose `
  --project-name ecobin-h02 `
  --env-file C:\tmp\ecobin-h02-secrets\compose.env `
  --file .\docker-compose.h02.yml `
  start
```

停止容器不会删除数据。不要执行 `docker compose down -v`，也不要直接删除
`ecobin-h02-mysql84-data`。

## 7. 服务器阶段 3 固定对象

2026-07-27 首次供应、2026-08-04 按相同隔离边界重建到 V31 并验证的服务器对象为：

| 对象 | 固定值 |
|---|---|
| Compose project | `ecobin-target` |
| 容器 | `ecobin-target-mysql84` |
| 数据卷 | `ecobin-target-mysql84-data` |
| 内部网络 | `ecobin-target-db` |
| 宿主机 MySQL 端口 | 无 |
| Compose 文件 | `/etc/ecobin/h02/docker-compose.h02-server.yml` |
| 非秘密 Compose 环境 | `/etc/ecobin/h02/compose.env` |
| root secret | `/etc/ecobin/secrets/mysql-root-password`，`root:root 0600` |

服务器 Compose 模板是
[`deploy/production/docker-compose.h02-server.yml`](../../deploy/production/docker-compose.h02-server.yml)。
它没有 `ports`，数据库只在 internal Docker 网络内可达。日常只读检查可用：

```bash
sudo docker inspect ecobin-target-mysql84
sudo docker compose \
  --project-name ecobin-target \
  --env-file /etc/ecobin/h02/compose.env \
  --file /etc/ecobin/h02/docker-compose.h02-server.yml \
  ps
```

不要为方便管理临时发布 3306/13306；需要执行受控迁移时使用一次性 SSH 隧道并在操作
结束后验证宿主机没有监听。停止目标 MySQL 不会自动授权启动旧栈，阶段 4 应继续等待
单独授权。

2026-08-04 V31 供应摘要位于操作机仓库外：

```text
C:\Users\24217\.ecobin\production\115.159.67.35\evidence\h02-v31\20260804T091716Z\summary.json
```

服务器最终审计和加密备份证据位于：

```text
/var/lib/ecobin/evidence/h02-post-v31/20260804T092236Z/
/var/backups/ecobin/h02/20260804T092236Z/
```

最终结果为 31 条成功迁移、96 张领域表、77 条权限定义、其余业务数据 0；owner 和
trigger definer 均锁定，运行账号最小权限矩阵完整，目标容器重启后健康，宿主机没有
3306/13306 监听。旧三容器继续停止，旧数据卷未修改；未执行 seed、应用部署或入口切换。
