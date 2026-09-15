# H-02 目标数据库环境供应手册

> **2026-09-16 现用生产 Docker 库已到 V83：**停止全部应用写入并完成包含结构、数据、触发器、
> 存储过程和事件的流式压缩加密备份后，从 V72 前向迁移至 V83。供应脚本最终核对 138 张领域
> 表、83 条成功迁移、运行账号正负权限并重新锁定 schema owner；当前后端发布
> `20260915160954-736a9e1adeef` 已在 V83 健康运行。下文 V69/V72 等数值均为对应历史阶段，
> 当前恢复续跑以脚本中的 V83 目标为准。

> 最新候选 [P1BT：已授权未登记发送的恢复关门撤回](../../hardware/docs/review/uart2-recovery-close-withdrawal-p1bt-2026-09-13.md)：业务库先封住旧发送，永久层另存撤回事实并保留原授权历史；独立继任仅豁免准确祖先，候选循环已接自动核对，不发新动作、不启动云端、不恢复接单，业务39/永久3不变。
> 撤回专项53项与运行入口19项分别通过，最终集成/扩大回归见实施记录，完整契约25通过。已登记可能发送的未知效果、跨新启动号新动作、完整准入/云端/正常业务与main切换仍待接，P4/P5未完成。
> 未部署/烧录；OneNet2.2.0/UART rc.22/MySQL V79/权限40及发布25/39门槛不变。外部刷写/HIL不保证共用串口锁；RS485/HMI/完整固件容量保留。两项投递取舍已确认，下方历史“待确认”不再适用。

> 最新候选 [P1BH：后台确认可靠交接](../../hardware/docs/review/uart2-native-confirmation-p1bh-2026-09-13.md)：原始确认、报告与回执原子绑定，schema32；322项相关测试、25项完整契约检查通过。
> 投递重启缺最终包只归档、归档后迟到结果只追加证据，两项已获用户确认，不再待裁决；实现继续按P4/P5推进。确认交接不释放占用、不修改当前袋或准入，main尚未切换。
> 未部署/烧录；发布25/32、RS485与HMI等剩余门槛保留。以下为历史记录，历史“P4待确认”不再适用于上述两项决定。

> 最新硬件候选 [P1BG：原生上报与编号落库](../../hardware/docs/review/uart2-native-report-persistence-p1bg-2026-09-13.md)：实际 C 多轮/5 秒中位数/末重超时及照片快照已接可靠报告；缺测不补零，清运量为本次前重减后重。
> V78 只对齐四个业务测量编号，原质量/范围/空值约束保留；启动/供应目标同步，134 表/授权39不变。真实 MySQL 297 项、相关 Java 总计399项、Pi/隔离包50项、完整契约25项通过。
> 未部署/烧录；主程序、后端确认消费、当前袋/准入与完整异常仍待接。发布25/31、RS485、完整固件容量及 P4/HMI 待确认项保留，P3/P5/P7未整体完成。下方为历史。

> 最新未发布硬件候选 P1AW（2026-09-13）：源码与供应脚本推进到 V77，当前袋子满溢
> 支持中位数及原取值质量保存；134 张领域表/授权清单39不变，无新增 UPDATE 权限。
> 仅专用本地库验证，未执行现网迁移/部署。香橙派实际准入、原生接线与发布验收仍待完成。
> [实现、测试与回退边界](../../hardware/docs/review/uart2-fullness-state-median-p1aw-2026-09-13.md)。下方候选为历史。

> 未发布硬件候选 P1AS（2026-09-13）：源码新增 V73，正常投递可保存真实超时中位数，
> 新后端及供应脚本目标同步为 V73；134 张领域表、授权清单 38 不变。
> 仅完成隔离本地 MySQL 迁移/约束/事务测试，未执行现网迁移或完整生产权限验收。
> 余下清运/快照/基准/准入及成对发布仍未完成，不要把本候选直接部署到现场。
> [实施与验证边界](../../hardware/docs/review/uart2-delivery-median-persistence-p1as-2026-09-13.md)。

> 2026-09-13 01:01 设备禁用、恢复和报废规则已部署，最终发布 `20260912165854-db67124ba87e`；现网 V72 / 134 张业务表、72 条成功迁移、授权清单 38。完整备份恢复、权限和公网校验通过；[部署及回退边界](../operations/device-lifecycle-deployment-2026-09-13.md)。

> 2026-09-12 19:41 租户统一设备配置已部署，当前发布 `20260912112713-b1be0e4a4719`；现用库 V71 / 134 张领域表、71 条成功迁移、授权清单 38。完整备份恢复及上线验证通过；设备应用进度与 V70 不兼容回退边界见[部署记录](../operations/tenant-device-configuration-deployment-2026-09-12.md)。

> 2026-09-12 当前目标与现用库已推进到 V70：133 张领域表、70 条成功迁移、76 条权限定义、运行授权清单 37。V70 新增全局满溢策略及配置版本标记；支持从 V69 保留数据前向升级，也支持 V70 幂等授权核对。现用库完整备份恢复和权限验证已通过；[本次记录](../operations/global-fullness-deployment-2026-09-12.md)。下文较早执行记录的 V69 / 132 表为历史数值，当前命令按此段及脚本执行。

> 当前状态：`done`
>
> 操作人：`enveloping`
>
> 当前用途：Windows 本地开发演练与服务器阶段 3 供应手册；两处均已验证独立
> MySQL 8.4.10 容器/卷、五类身份、V1～V31 和脱敏权限探针。当前脚本目标已推进到
> V69。2026-08-29 已另在全新腾讯云 CDB 正式空库完成 V1～V60、运行/备份账号和
> 初始超级管理员供应，但尚未切换当前后端，也未迁移旧业务数据；
> 服务器既有执行结果另见
> [单机试验期生产整改计划](single-host-production-remediation-plan.md)，最新 CDB 现场事实见
> [H-02 目标数据库供应证据](../operations/h-02-target-database-evidence.md)。

> 2026-09-11 现用 Docker 目标库已从 V68 前向迁移到 V69，H02 完整权限矩阵验证通过；
> 132 张领域表、69 条成功迁移、76 条权限定义。完整加密备份已完成本机隔离恢复，不改变
> 新 CDB 的独立入口决定。见[云端部署记录](../operations/delivery-quarantine-deployment-2026-09-11.md)。

> [!NOTE]
> 腾讯云 CDB 不是本手册第 2 节的本地 Docker 固定对象，不能把容器创建/删卷命令直接
> 套到托管实例。项目负责人已明确决定保持实例级 `require_secure_transport=OFF`；
> 后续不得把它继续列为未决门。`ecobin_app`、`ecobin_backup` 仍须账号级
> `REQUIRE SSL`，正式 JDBC URL 必须显式要求 TLS。

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
4. 为锁定 trigger definer 授予 V9 旧版小程序触发器需要的精确读取权限，再安装到 V36；
5. 补齐 V36 永久设备归属触发器权限，再安装到 V39；
6. 补齐 V39 当前小程序渠道、机构账号和永久设备归属触发器权限，再安装 V40～V69；这样较早纪元带业务数据续跑时，V48/V49 的数据回填不会先于触发器授权；
7. 撤销表名、列名重命名前遗留的触发器读取权限，再为 V63 新资产初始化和防降级触发器，以及 V64～V67 业务发布、单台验证、安全取消和镜像基线状态机授予精确权限；V68 只收紧镜像基线事实的数据库判定，V69 新增投递异常隔离证据表；脚本会按系统权限表逐项核对最终权限并锁定 schema owner，已是 V69 的续跑环境也会幂等收敛；
8. 应用当前已冻结的表级/列级运行权限；
9. 执行正向 DML 和 DDL/GRANT/TRIGGER/事实删除/系统库访问负测；
10. 归档脱敏证据。

仓库发布前的 F-07 一次性 MySQL 验证会真实插入并清理一台探针资产，确认两张默认管理记录
都由触发器生成。现场供应脚本不向目标库写入探针资产，只应用已经通过该验证的精确权限。

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

若现有数据库已完整到 V30～V69 中任一受支持纪元、owner 已锁定，使用：

```powershell
.\tools\database\provision-h02-target.ps1 `
  -ResumeExistingMigratedEnvironment `
  -TransientSshAttempts 8
```

该模式要求 V30/V31/V32 为 96 张领域表、V33/V34 为 97 张领域表、V35 为 99 张领域表、
V36/V37/V38 为 93 张领域表、V39/V40/V41 为 95 张领域表、V42 为 96 张领域表，
V43/V44/V45 为 98 张领域表，V46～V51 为 99 张领域表，V52 为 112 张领域表，V53/V54 为
113 张领域表，V55 为 118 张领域表，V56/V57/V58/V59/V60/V61/V62 为 119 张领域表，V63 为
123 张领域表，V64 为 129 张领域表，V65/V66/V67/V68 为 131 张领域表，V69 为 132 张领域表，并且迁移历史精确停在对应纪元。
低于 V69 时会用一次性新密码解锁 schema owner，只执行尚缺的前向迁移直到 V69，完成后立即
重新锁定；V69 不解锁 owner、不重复迁移。现场库已于 2026-09-11 从 V68 完成 V69；
续跑时若已精确处于 V69，不再执行迁移，只核对目标版本并幂等收敛权限，不能假装重复迁移。
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
等持久化对象落到精确更新列。当前 grants 目录已按冻结状态机、不可变边界和 V69 DDL
补齐。

脚本只生成：

- 132 张领域表显式 `SELECT`；
- 除权限目录和不可变业务发布声明表外显式 `INSERT`；
- 四张当前槽位表和可删除的袋码批次表显式 `DELETE`；
- 对 82 张 P/O 表只授予矩阵明确列出的列级 `UPDATE`。

验收对每张 P/O 表执行一条获准列空集更新正测，并选择该表首个未授权列执行负测；
五张可删除表逐表验证 `DELETE`，备份身份以 `single-transaction` 数据读取探针验证。
任何 schema 级或整表 `UPDATE/DELETE` 仍视为失败。

目录版本 36 包含 V52 注册续作、平台小程序会话、厂家袋扫码/更正、远程端口槽和维护会话，
并覆盖 V53 自动审核及自动提现需要的精确列级更新；V54 新增的规则金额与订单快照在创建时写入、此后不可修改；V55 只给固件发布、灰度和部署状态机及设备固件投影授予精确更新列。固件进度与操作历史仍只授予
`SELECT/INSERT`。V56 仅增加设备验收代次推进列，以及封存授权从待确认、已确认/已取消到
`SEALED`（已封存）终态实际需要的投影列；V57 再增加微信授权展示包到期时间、验收/远程维护时钟质量和封存完成时钟质量的精确更新列，V58 不增加列或权限，只收紧恢复约束；V59 不增加列或权限，只扩大既有袋码数量检查约束；V60 增加验收证据与设备资产上的 MCU 远程升级线路能力三态列，并仅把资产投影列加入精确 UPDATE 白名单；V61 在 `ops_task_attempt` 增加可空的 `external_request_id`，并只把该结果字段加入既有表的精确 UPDATE 白名单；V62 只给 `ops_reliable_task` 增加出厂进度定位索引，不改变授权目录；V63 新增的业务发布声明只读，设备软件实际状态事实只允许查询和追加，设备管理架构与兼容性投影只允许更新状态机规定的列。V64 新增发布控制面六张表；V65 新增单台验证部署和进度事实；V66 增加由设备事实确认的安全取消闭环。发布声明、更新进度和取消结果等事实只允许查询和追加，发布、计划与部署投影只允许状态机规定的精确列更新。授权身份、验收证据、命令、可靠任务和创建事实
仍不可修改，整张封存授权表没有 `DELETE`。新增自动提现决策表同样只授予
`SELECT/INSERT`，没有增加整表 UPDATE 或业务事实任意删除权限。V67 只给部署记录增加创建时写入且此后不变的来源基线类型，并允许镜像基线的来源发布编号和序号为空；V68 只调整既有软件事实检查约束，二者都不扩大 UPDATE 权限。V69 新表默认只读和追加，只为可靠任务编号、问题状态、终态证据及版本列授予精确 UPDATE；它不扩大订单或资金表权限。厂家袋的袋码、
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
