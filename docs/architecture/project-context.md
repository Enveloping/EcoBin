# EcoBin 项目上下文（Claude Code → Codex）

> 整理日期：2026-07-11。来源为项目 `CLAUDE.md`、`~/.claude/projects/C--D-004-Project-002-Java-EcoBin/memory/`、近期 Claude Code 会话/计划、仓库文档与 Git 状态。

> [!IMPORTANT]
> 2026-08-29 已在全新腾讯云 CDB 的拟生产目标 `ecobin` 空库完成 V1～V60、119 张领域表、
> 76 条有效权限定义、最小权限运行/备份账号和唯一初始超级管理员供应；未导入旧库业务
> 数据。当前现场 Web/后端实际写入的现用测试库已核对为 V60，新 CDB 尚未作为生产入口切换，
> COS 对象存储迁移也尚未执行。项目负责人明确决定不启用实例级
> `require_secure_transport`；这不是待办，应用和备份账号仍以账号级 `REQUIRE SSL` 约束，
> 未来生产 JDBC 连接必须显式使用 TLS。资格库和旧库均保留，未经明确授权不得删除。详细现场
> 证据、候选制品、首启副作用清理和切换清单见
> [`H-02 目标数据库供应证据`](../operations/h-02-target-database-evidence.md)。
> 项目负责人进一步明确：现用 V60 数据库和现用 COS 都是测试环境，尚未完成的
> 设备归属、配置、皮重及业务闭环继续在该环境验证；迁移不是当前下一步。只有当前测试完成
> 后，才执行 CDB/COS 切换并从那时起按生产环境对待，既定“不迁移旧业务数据”不变。

> [!IMPORTANT]
> 2026-09-02 香橙派业务程序远程更新九阶段中的第二阶段已完成仓库实现：V63 新增业务
> 发布声明、设备管理架构代次、只追加的软件实际状态事实和兼容性投影 4 张表，目标基线为
> 123 张领域表；OneNet 候选增加“设备软件实际安装与运行状态上报”这一独立可靠事件，现为
> 15 服务 / 19 事件 / 0 属性。旧直连设备继续沿用原投递和清运规则；明确切换到永久管理层
> 的设备只有在业务准入明确允许时才可预览和创建新投递/清运，状态暂停、缺失或无法确认均
> 失败关闭。Web 只展示中文结论，不暴露内部状态码。本阶段没有业务程序更新下行、发布写
> 接口、部署或灰度状态机；现场数据库和 OneNet 控制台也不会随仓库改动自动升级。

> [!IMPORTANT]
> 2026-09-04 第七阶段“后端发布控制面”已形成默认不下发的仓库候选：V64 新增发布顺序、
> 发布生命周期、只追加操作审计、灰度计划、设备资格快照和灰度操作 6 张表，目标基线推进为
> 129 张领域表。平台管理员页面可以创建草稿、上传后端固定路径的离线签名制品、重新读回并
> 校验、单独批准/暂停/恢复/归档，以及依据设备当前实际安装事实建立和停止灰度演练计划。
> 控制面没有 OneNet 或可靠任务依赖，启动时硬拒绝真实下发开关；页面也没有启动验证设备或
> 推进批次的入口。真实私有 COS、正式业务签名公钥、现场 V64 迁移和应用部署尚未执行，
> 因此这不是远程更新已经可用的声明。

> [!IMPORTANT]
> 2026-09-03 已在当前 v13 单卡开发/HIL 设备上，通过受控维护载荷持久安装第三阶段永久通信
> 代理、设备更新器、三个低权限身份、三个本机接口权限组及两个按需 root 助手。首次 r2 安装在
> 后置审计发现更新器私有子目录用户组漂移后自动完整回滚，保留数据库经严格校验后受控归档；
> 基于提交 `4120579c` 的 r3 重试通过预检、演练安装、正式安装、多轮审计、真实低权限 UART/
> 双摄像头/目录/本机接口正例、清空附加用户组的权限缺失负例，以及通信代理和更新器独立重启。
> 旧业务最终恢复，OneNet 所有权、远程更新、永久作业闸门和两个助手的变更动作继续关闭。完整
> 证据见
> [`hil-stage3-live-maintenance-20260903`](../../hardware/image-artifacts/evidence/hil-stage3-live-maintenance-20260903/README.md)。
>
> 上述结果只关闭 v13 受控维护路径的第三阶段在线低权限门禁。整机暖重启尚未执行；从提交
> `42cc922c` 构建的 v16 也没有写卡，并且不包含后续 `4120579c` 的最终更新器私有目录用户组
> 修复，因此 v16 只能作为历史离线候选。随后从干净提交 `16cd154f` 完整重建的 v17，其无秘密
> 候选、受控 HIL 副本、注入后审计、第二次独立复验和最终只读身份检查均通过；它当时被选为
> 下一次写卡源，但最终没有写卡、全范围回读或冷启动。在低权限全业务回归、MCU 更新迁移、
> 断电恢复及真实动作防重验收完成前，第四阶段不得启用。v17 历史证据见
> [`hil-updater-directory-groups-20260903-17`](../../hardware/image-artifacts/evidence/hil-updater-directory-groups-20260903-17/README.md)。
> v17 离线复验通过后，服务器实际机器验收允许列表当时追加了
> `hardware-runtime-20260903-17`。后续复核又移除了从未写卡的 v15，以及缺少最终权限修复、已
> 降为历史候选的 v16；v13 继续保留。两次修改均保留 root 私有备份，最终生产预检、应用
> 重载、后端与 Web 健康检查、容器内精确核对和运行秘密隔离探针均通过。这只表示后端能够认可
> 当时可能出现的 v17 上报，不表示 v17 已经写卡或产生真实设备验收证据。历史脱敏部署证据见
> [`v17 后端允许列表部署记录`](../operations/orangepi-v17-backend-allowlist-deployment-2026-09-03.md)。

> [!IMPORTANT]
> 2026-09-03 第四阶段又完成一组默认关闭的安全基础。永久更新器新增仅 root 可调用的门禁事实
> 查询、带证据激活和人工安全锁；独立持久标记记录“本次候选启用是否已经审核激活”，候选
> 关闭、旧版程序写回关闭、遗留许可或动作收敛、通用状态迁移及上一周期命令重放都不能自动
> 打开新业务门。业务程序本机接口新增 MCU 维护观察、安全交出 UART、烧录后身份与自检验证
> 的可注入协议基础，证据严格绑定两类 MCU 查询、串口所有权、更新/交接编号和助手回执。
> 默认构造、`main.py`、systemd、真实 UART、两个特权助手变更、MCU 更新状态机和 OneNet 所有权
> 均未接入或启用。这些新增代码是在 v17 构建之后完成；它们后来进入 v19，但不能把 v17 离线
> 证据当作这组实现或 v19 的镜像证据。
> 永久层卸载回退也已失败关闭：只有精确版本 3 + 附加层版本 1、候选从未激活且没有任何控制
> 操作、许可、物理动作或锁历史时，才可恢复不理解这些事实的旧业务；安装器在停机前和关闭
> 更新器写入口后各做一次只读复核，数据库缺失/损坏、未知扩展及 WAL/并发变化都不会被忽略。
> 维护安装与卸载又改为带 root 独占锁、磁盘同步意图、全局及逐服务启动围栏的可恢复事务；
> 系统用户/目录创建的部分副作用、服务启动、定位标记替换、孤立操作、删除配置后的卸载续跑
> 和终态清理都有明确恢复方向，预检查与模拟执行保持整棵文件树零写入。已经越过服务启动但
> 尚未审计的安装只能用同一载荷向前收敛；完整审计后进入卸载的操作只能继续幂等卸载。
> Windows 与 Linux/Python 3.11 测试、真实上一版更新器读取附加表、以及当前 v13 经 COM3 的
> 普通用户无动作影子验收已经通过；维护安装器专项 129 项和 Linux 真实文件锁 6 项也已通过。
> 详情见
> [`stage4-controlled-gate-mcu-handoff-20260903`](../../hardware/image-artifacts/evidence/stage4-controlled-gate-mcu-handoff-20260903/README.md)。

> [!IMPORTANT]
> 2026-09-03，v18 在构建审计期间因暴露 maintenance unit（维护服务单元）的 systemd 启动
> 条件围栏校验缺陷而中止，只保留为诊断过程：它没有完成构建、没有写卡，也从未进入后端
> 机器验收允许列表。提交 `60b42b196ef8e931a80e854c8014df3e1848985f` 修复并收紧了该校验器；
> v19 随后从这个干净提交完整构建。镜像版本为 `0.1.0-single-card.20260903.19`，业务运行时为
> `hardware-runtime-20260903-19`；无秘密候选 SHA-256 为
> `85110de169565d1676bc7363e439c6c66ea1bfb1d7d41f51c28f796895806829`，受控 HIL 镜像副本
> SHA-256 为 `7e3d9153b70ef001b90434d5d5da68c2153b0976f9dd4e52fc6a387c5ee9f5b4`。
>
> v19 已包含第四阶段默认关闭的受控激活、人工安全锁、MCU 维护交接、维护安装/卸载恢复，
> 以及最终的 systemd 围栏验证。`hardware/` 代码完整自动化套件为
> `1683 passed, 96 skipped, 5 subtests passed`，Linux root 真实锁与文件系统场景为
> `6 passed`；候选和 HIL 镜像文件的离线审计及独立审查均通过。证据见
> [`hil-stage4-maintenance-recovery-20260903-19`](../../hardware/image-artifacts/evidence/hil-stage4-maintenance-recovery-20260903-19/README.md)。
>
> 生产机器验收允许列表已经以配置提交 `e67fd9ab` 从 v17 切换到 v19：保留 v13，加入
> `hardware-runtime-20260903-19`，并排除 v15、v16、v17 和从未加入的 v18。服务器只重载已有
> 应用的启动期配置，没有重新生成后端 JAR 或运行时镜像。脱敏记录见
> [`v19 后端允许列表部署记录`](../operations/orangepi-v19-backend-allowlist-deployment-2026-09-03.md)。
> 指定 TF 卡经项目负责人确认后，已完成 `2571108352` 字节写入和同范围完整回读，回读摘要
> 与上述 HIL 镜像一致。设备从断电状态启动后完成厂家接入、后端 v19 识别、单向封存、一次
> 正常投递和一次正常清运；正式运行目标已启动，出厂热点与网页已停止，永久通信代理和更新器
> 以独立低权限身份稳定运行，普通账号权限缺失负例及第四阶段默认关闭事实通过。冷启动期间
> Air780E 已取得地址和默认路由，但 DNS 约 10 分钟后才自主恢复；恢复后的蜂窝和可信时间连续
> 采样稳定，该现象作为可靠性问题保留。
>
> 上述结果关闭的是 v19 默认关闭基础的指定单卡真机门禁，不代表远程业务更新或量产资格已经
> 获得。当前业务程序仍以旧 root 服务运行；未来 `ecobin-business` 低权限全业务回归、OneNet
> 连接所有权切换、特权助手真实变更、MCU 更新状态机迁移、故障/断电注入、正式签名和量产
> 信任策略仍未完成。

> [!IMPORTANT]
> 2026-09-03，提交 `119fa06d9ae05ba408e4b43fb37ebed52ed459d0` 完成第四阶段永久 MCU
> 更新仓库候选，并构建了 v20 无秘密候选和受控 HIL 镜像。镜像版本为
> `0.1.0-single-card.20260903.20`，业务运行时为 `hardware-runtime-20260903-20`；候选/HIL
> SHA-256 分别为 `43fc3c778099f13b447fd7268bbc939a75724f072a060408dc814aa4d4494e38` 和
> `ae7d4f9aa2321477311a82e162a0ee5d1d132acf0085308088f82069a96eb99b`。Windows 全量
> `1809 passed, 101 skipped, 5 subtests passed`、Linux/Python 3.11 专项 159 项以及镜像
> 默认关闭、无网络、本机固件队列边界审计通过。证据见
> [`hil-stage4-mcu-updater-20260903-20`](../../hardware/image-artifacts/evidence/hil-stage4-mcu-updater-20260903-20/README.md)。
>
> v20 曾通过配置提交 `0da1a25c` 在保留 v19 的同时加入生产机器验收允许列表；记录见
> [`v20 后端允许列表部署记录`](../operations/orangepi-v20-backend-allowlist-deployment-2026-09-03.md)。
> 准备真实 MCU HIL 时确认原公钥对应的签名私钥不可用，因此没有绕过验签或沿用 v20 身份；
> v20 从未写卡，现已从允许列表移除并保留为历史离线候选。
>
> v21 从干净提交 `d97bcca2a6d4943e8c72094b65036d2765cefbac` 完整重建，镜像身份为
> `0.1.0-single-card.20260903.21` / `hardware-runtime-20260903-21`。候选加入仓库外生成的 HIL
> 专用 MCU 公钥，HIL 副本只在 root 专用暂存区预置一对已签名目标/回滚包；私钥不在仓库、
> WSL、受控构建目录或镜像中。候选/HIL SHA-256 分别为
> `2aebb70134cc6b75d61d6fba67d62760f872fbf91152284d76a48580afb129c0` 和
> `d0d7199b23433b9929363cd2ba0be5a4fbaddc57d2fa54a7541ca49818946bab`，综合离线审计通过；证据见
> [`hil-stage4-mcu-real-hil-20260903-21`](../../hardware/image-artifacts/evidence/hil-stage4-mcu-real-hil-20260903-21/README.md)。
> 生产允许列表已通过配置提交 `d7aff1d0` 从 v19+v20 切换为 v19+v21，并完成独立健康核验；记录见
> [`v21 后端允许列表部署记录`](../operations/orangepi-v21-backend-allowlist-deployment-2026-09-03.md)。
> 项目负责人随后现场反馈 v21 已写入 TF 卡，并完成封存、正常投递和正常清运；仓库尚未归档
> 该轮写后回读、真实 MCU 更新/回滚/断电注入或完整代理链证据，不能据此宣称第四、第五阶段
> 全部完成。候选更新服务和全部远程更新入口继续保持关闭。

> [!IMPORTANT]
> 2026-09-04，第六阶段“本地验证设备更新器”已进入默认关闭的 v23 可写卡候选：业务发布包精确排除
> 永久通信代理、更新器、OneNet 凭据/直连代码和旧 MCU 烧录器，只包含业务源码、ARM64/
> Python 3.11 离线依赖、迁移目录和签名身份；MCU、系统运行时、业务发布三类信任公钥必须
> 相互独立。永久更新器只接受 root 在固定目录放置的包和签名，按包大小、SHA-256、Ed25519
> 签名、内层清单、磁盘余量和当前数据库大小验真，暂停新业务并最多排空 30 分钟，在迁移前
> 备份业务数据库，切换低权限业务版本并观察 30 分钟；失败时恢复数据库和上一稳定版本。
> 首份签名包可从镜像内桥接服务一次性迁移到 `/opt/ecobin/business/current`，失败则恢复原
> 桥接服务；业务更新与 MCU 更新不能并发。提交 `0e24d17f` 修复业务包专用构建入口，提交
> `59302a54` 补齐 root 固定目录排队与状态查询命令。由后者构建的
> `0.1.0-single-card.20260904.23` / `hardware-runtime-20260904-23` 无秘密候选和受控 HIL
> 镜像 SHA-256 分别为
> `352e7fe03c3db1e7fa55a0b508a386194f4d8e4372303e004876427eda804733` 与
> `c9c4318d91f03f7be3d91033cbad3fdfb29fd223e178d0b54056a0a6ec9be07c`。HIL 副本只在 root
> 暂存目录预置一份成功包和一份故意不健康包，自动队列为空、候选服务默认关闭、远程触发关闭，
> 私钥不在仓库或镜像中；第二次独立离线审计通过。证据见
> [`hil-stage6-business-runtime-update-20260904-23`](../../hardware/image-artifacts/evidence/hil-stage6-business-runtime-update-20260904-23/README.md)。
> Windows 全量为 `1842 passed, 104 skipped, 5 subtests passed`，镜像工具套件为
> `63 passed, 8 skipped`。项目负责人随后确认 Windows 磁盘 1，v23 HIL 镜像完成
> 2,571,108,352 字节写入和同范围 SHA-256 回读；测试后端允许列表保留 v13、v19、v21 并加入
> `hardware-runtime-20260904-23`，可恢复备份、生产预检、双容器健康、回环请求和秘密隔离复核
> 通过；记录见
> [`v23 后端允许列表部署记录`](../operations/orangepi-v23-backend-allowlist-deployment-2026-09-04.md)。
> 设备随后冷启动并完成热点接入、单向封存、正常投递和正常清运；但第五阶段试切换在真正执行
> 签名业务包前发现结构性缺口并停止，设备已经恢复旧直连链。仍须用包含后续修复的下一版镜像
> 完成首次所有权切换、成功包、第二版本故意失败回滚和关键阶段断电恢复，才能关闭第五、六阶段。

> [!IMPORTANT]
> 2026-09-04，v23 受控串口试切换确认三个缺口：首次启动协调器会重新拉起已人工停止的旧直连
> 服务；共用 `/run` 目录可能被刚停止的服务清理；接入投影错误要求状态目录保持账号主组，因而
> 把 systemd 合法设置的本机接口组误判为权限错误。本轮没有留下“切换完成”事实，候选服务已
> 停止，设备继续由 v23 旧链提供业务。
>
> 后续仓库候选新增 root-only 一次性切换事务：先只读确认工作槽空闲且没有 MCU 维护，再让准备
> 标记先于停机落盘；固定停止并复核新旧服务后，在 SQLite 快照上第二次确认空闲，再复制完整
> 业务库，迁移活动照片路径/文件、稳定启动编号和配置，
> 再经 UID、私有权限、数据库完整性和结构基线复核后提交活动标记。准备中、标记损坏或身份冲突
> 都停止两条业务链；活动标记通过独立 root 门后才启动永久通信代理、永久更新器和低权限业务
> 目标。旧/新单元还有相反的 systemd 条件，共用运行目录停止后保留；切换与维护安装器、特权
> 助手共享互斥锁，且切换一旦开始便禁止永久层卸载。Windows 全量为
> `1861 passed, 107 skipped, 5 subtests passed`，Linux/Python 3.11 权限专项为
> `46 passed, 1 skipped`。提交 `c14fa0d3` 实现上述切换，第一次镜像构建由精确白名单门禁发现
> shell 载荷入口漏复制两个切换模块并在候选产生前停止；提交 `0aa61fd2` 补齐入口，镜像工具与
> 安装器专项为 `61 passed, 5 skipped, 24 subtests passed`。从该干净提交构建的
> `0.1.0-single-card.20260904.24` / `hardware-runtime-20260904-24` 无秘密候选和受控 HIL
> SHA-256 分别为 `5dbdd34de9afed4b0f805ff94147d214ea32a808eef3b1a9b54f0c6a074bab55` 与
> `d21f54a76796a8c4ea930d856588b373b5fcb63ef575f548acd6b3b70adcfee2`；构建内审和第二套独立
> 只读审计全部通过。测试后端允许列表随后通过配置提交 `31ef4551` 在保留 v13、v19、v21、
> v23 的同时加入 v24；可恢复备份、生产预检、双容器健康、回环请求、秘密隔离探针和独立复核
> 全部通过，记录见
> [`v24 后端允许列表部署记录`](../operations/orangepi-v24-backend-allowlist-deployment-2026-09-04.md)。
> 项目负责人随后确认 Windows 磁盘 1，序列号 `121220160204`；v24 HIL 镜像完成
> 2,571,108,352 字节写入及同范围回读，回读 SHA-256 与源镜像一致。v24 尚未冷启动或完成
> 真机切换，不能宣称第五阶段或远程更新已经可用。镜像及写卡证据见
> [`v24 业务运行时持久切换 HIL 候选`](../../hardware/image-artifacts/evidence/hil-stage5-runtime-cutover-20260904-24/README.md)。
> 详细边界见
> [`香橙派业务运行时发布与更新设计`](orangepi-business-runtime-release-and-update-design.md)。

> [!IMPORTANT]
> 2026-08-31 已实施设备出厂接入的双观察面：香橙派热点网页用九节点移动端进度链展示
> P7、蜂窝/校时、注册/K1 清理、正式服务/OneNet、厂家绑袋、P8、云端判定/授权和单向封存；
> 平台设备详情用一致性快照展示袋码数量、P8 可靠任务、权威绑定证据、最新历史证据、封存
> 授权和完成事实。设备只显示本机可证明的事实，不伪造后端袋码数量，也只在收到当前代次
> 封存授权后显示云端 `PASSED` 已生效。最终封存改为“事实先落盘、页面先绘制成功、浏览器
> 回执后清理”，同时保留 5 秒硬超时和重启后只向封存方向恢复。完整节点、信任边界、错误
> 语义和时序见
> [`factory-onboarding-observability-2026-08-31.md`](factory-onboarding-observability-2026-08-31.md)。

> [!IMPORTANT]
> 2026-08-29 项目负责人确认当前 15 服务 / 18 事件 / 0 属性的 OneNet 物模型此前已经导入
> 目标产品，仓库文档落后；无需重复导入。指定设备的机器验收为 `PASSED`、当前代次为
> `SEALED`，并已永久分配测试租户和该租户下的测试机构。机构分配事务自动建立 1 个投口、
> 厂家初始袋占用、运行投影和配置 v1；设备随后精确报告 `APPLIED`，系统自动完成初始空袋
> 皮重。当前袋、皮重、单价、经营开关、OneNet 在线和设备空闲等投递准入事实均满足，当前
> 测试流程下一步是由小程序用户扫描设备二维码，发起一次正常投递并核对待审核订单。

> [!IMPORTANT]
> 2026-08-22 已获整体实施授权并已完成仓库内 P1～P10 软件、测试入口与操作文档的
> [`香橙派可重复量产镜像与首次启动编排计划`](../planning/orangepi-production-image-first-boot-plan.md)：
> 同一锁定 Debian 12/Linux 6.1、Python 3.11 镜像固化 UART5、wPi 2/5 MCU 升级控制、版本化
> `/opt` 安装和 `/etc/ecobin/hardware.env`。正式上行直接使用现有 Air780E 载板的 USB RNDIS，
> 不要求用户确定或升级模组固件；版本/USB 标识只作逐台诊断记录，真实联网能力由试点和出厂
> 预检证明。当前单卡 HIL 固定使用 HSK/UNIQUESKY 外摄与 icSpring 内摄的 by-id 路径；未来
> 更换正式摄像头时只修改显式配置并重新验收，不依赖易漂移的 `/dev/videoN`。NRST 改为物理 11
> 号针 PC6 经 2N7002 开漏级控制，香橙派高电平触发复位，避免推挽驱动复位线。板载 Wi-Fi 不作
> Station；工厂接受全批次共用 WPA2 热点密码。量产介质为标称 32 GB TF 卡，镜像采用紧凑
> 固定布局并在首启幂等扩容；称重预检
> 暂用 500 g 砝码，允许差分误差 ±10 g。尚未
> 封存设备每次通电固定开启隔离 AP 和本地网页，先在不连接后端、不写生产 EdgeStore 的前提下
> 完成 revision 2/双摄/Bootloader 以及真实 AA/DD 投递、EE/EF 清运动作验收，再验证蜂窝上行、
> 校时、注册、K1 清理和正式运行时。动作测试只允许最小本地安全恢复状态，AA/EE 不自动重发；
> 验收通过前由整机出站锁阻止任何后台服务联网，转正式 UART 前复位/清缓冲并隔离迟到 DD/EF；
> AP 只在后端当前代次机器验收 `PASSED` 并可靠下发 `AUTHORIZE_FACTORY_SEAL` 后，才由操作员
> 写入单向 `SEALED` 并关闭，封存中断也不重开；
> 含 K1 的最终镜像只进入受控制品库。按负责人决定不增加运行时防克隆检查，新 MCU 由厂家线下
> 预烧 revision 2。当前仓库内实现已经收口；真实 32 GB 介质资格、正式外部信任策略、K1 注入、
> Orange Pi/MCU/Air780E/双摄/500 g 砝码的整机 HIL（hardware-in-the-loop，硬件在环）验收仍保持
> 失败关闭，取得并复核这些现场证据前不得量产放行。

> [!IMPORTANT]
> 2026-09-04 通过香橙派串口读取稳定 `/dev/v4l/by-id` 身份并分别采集预览帧，确认当前外摄为
> HSK/UNIQUESKY（画面朝屏幕），当前内摄为 Generic USB Camera（画面朝天花板）。出厂验收、
> 日常业务和低权限启动自检统一在服务启动时选择摄像头：当前型号优先，当前型号缺失时外摄
> 可回退到历史 DECXIN、内摄可回退到历史 icSpring；此前使用的 HSK 外摄与当前外摄具有同一
> 稳定型号身份，天然继续兼容。新旧型号同时存在时不回退；某个角色的新旧型号都缺失时保持
> 该角色失败并阻止验收或业务启动，不允许借用另一角色的摄像头，也不依赖 `/dev/videoN`。

> [!IMPORTANT]
> 上述摄像头选择、永久服务真实就绪结果和蜂窝域名解析重试展示已经进入从干净提交
> `39e52561` 构建的 v26。`0.1.0-single-card.20260904.26` /
> `hardware-runtime-20260904-26` 的无秘密候选与最终受控 HIL 镜像 SHA-256 分别为
> `cf7b31b2f4672da5e6ed7375ee6bf64765839b340647087c9502333f182ff58e` 和
> `79be69b679de96f7731131410557456189b0ec2468c2576cdccf72c719e6fa18`。第一次 HIL
> 副本因旧签名业务包不符合新增模块后的业务源码白名单而被独立审计拒绝；没有放宽门禁，
> 而是从 v26 当前源码重建成功/故意失败两份 HIL 包后再次生成镜像。最终候选和 HIL 的
> 文件系统、精确软件清单、关键源码、四条摄像头配置、包验签、默认关闭、空自动队列和
> 私钥缺失边界均通过第二套只读审计。测试后端允许列表随后通过配置提交 `589ca52b` 保留
> v13、v19、v21、v23、v24 并加入 v26；可恢复备份、生产预检、双容器健康、回环请求、
> 秘密隔离探针和独立复核全部通过，记录见
> [`v26 后端允许列表部署记录`](../operations/orangepi-v26-backend-allowlist-deployment-2026-09-04.md)。
> v25 从未接纳或写卡。项目负责人随后确认覆盖序列号 `121220160204`、容量
> 31,268,536,320 字节的 Windows 磁盘 1；v26 最终 HIL 镜像实际写入和全范围回读均为
> 2,571,108,352 字节，回读 SHA-256 与源镜像一致。v26 尚未冷启动和真机验收，证据见
> [`v26 永久服务就绪与新旧双摄选择 HIL 候选`](../../hardware/image-artifacts/evidence/hil-runtime-readiness-cameras-20260904-26/README.md)。

> [!IMPORTANT]
> 2026-08-30 单卡冷启动证明：`ecobin-runtime.target` 为 active 不等于它的 Wants 成员都在
> 运行；成员自己的瞬时 `ExecCondition` 失败会让 systemd 成功到达 target，却把成员留在
> inactive。首次启动协调器现在在运行门禁满足后持续核对 `ecobin-hardware.service` 和
> `ecobin-remote-support.service`，只补启动缺失成员，不重复启动健康成员。现场修复提交为
> `fbc6c471`；当前卡已热修。2026-08-30 已从锁定上游、当前包锁和干净提交 `e3455c84`
> 完整重建 v9，无密钥候选与含 K1 的单卡开发/HIL 镜像均通过离线验证，后者已经携带该修复、
> 当前 HSK/icSpring 配置和其余已提交设备侧改动。项目负责人随后明确确认磁盘 1、序列号
> `121220160204`，受控工具完成 `2571108352` 字节写入和同范围完整回读，回读 SHA-256 与
> 源镜像一致。下一步是装回香橙派并从空白状态重走接入、封存、租户及机构分配流程。
> 2026-08-26 调整为逐台能力：BOOT0/NRST 线可不安装，操作员在离线验收开始时必须
> 显式选择。未安装时不发 F2 mode 02、不进入 ROM，仍可 PASSED/封存/运行业务，但设备能力
> 和平台资产均记为不具备 MCU 远程升级，所有升级命令终态拒绝。独立的
> `hardware_mcu/factory_sim` 固件用真实 PA9/PA10 UART 模拟缺少的 MCU 传感器、屏幕和执行器；
> 人工验收操作不自动，该来源以 `SIMULATED_PERIPHERALS` 告警、再确认并永久入报告。
>
> 2026-08-29 进一步明确两类结论必须分开：当前使用模拟 MCU 的轮次用于验证“接入流程
> 正确性”，只要它按同一固定帧协议返回相同语义的数据，从 P7 本地验收通过开始，后续
> Air780E 联网、可信校时、HTTPS 注册、OneNet 运行、P8 云端授权和单向封存状态机与真实
> MCU 没有第二套路径；模拟来源只作为证据元数据保存，不降低该流程结论。真实 MCU、线路、
> 传感器、屏幕和执行机构只在另行开展“真实硬件资格/HIL”时用于判断物理质量。未进入该
> 阶段时，任务推进和状态汇报不得反复以“数据来自模拟固件”为附加保留条件；只有评价
> 真实硬件资格或正式量产放行时才引用这项边界。

> [!IMPORTANT]
> 2026-08-20 已实施 STM32F103C8T6 经香橙派 UART 的远程固件升级。MCU 固定帧修订号 2
> 增加 F2 停机执行准备和 F3 固件身份/执行结果；香橙派负责升级准入、生命周期状态机、
> Ed25519 包验签、BOOT0/NRST、ROM
> Bootloader 擦写、断电恢复、三次目标尝试和自动回滚。后端 V55 增加不可变发布、单机
> 验证、人工推广和逐批放行，OneNet 下行只在实际发送时附加短期只读 COS 凭证，任何回滚
> 或失败都会阻止下一批。接线、首次旧固件迁移、签名发布和故障恢复见
> [`../../hardware/docs/mcu-remote-firmware-update-runbook.md`](../../hardware/docs/mcu-remote-firmware-update-runbook.md)。
> 2026-08-21 进一步冻结职责：香橙派收到升级命令后依据本地维护锁、工作槽和未决物理命令
> 决定是否准入；忙碌时由香橙派可靠上报 `REJECTED` 且不发送 F2。MCU 收到 F2 模式 02 后
> 不得以 BUSY/UNSAFE 再否决，只执行停止本地活动、关闭输出和锁存升级状态。现有投递、
> 清运和屏幕状态机仍留在 MCU。本地 MCU 的完整可构建 Keil 源码已纳入 `hardware_mcu/`，
> 构建输出、个人配置、备份和发布生成身份不纳入版本管理。
> 2026-08-21 补齐 F2 与边缘日志的失效安全边界：SQLite v14 在发送 F2 前持久化原 F3
> 身份和“F2 可能已执行”标记。此后即使 `PREPARED` 写入失败或进程重启，也必须先复位、
> 核对原身份并通过 F1 才能 `REJECTED` 解锁；否则进入 `FAILED_LOCKED` 继续阻止业务。
> 2026-08-21 增加两层升级模拟：Linux PTY 虚拟 MCU 覆盖 F2/F3 身份、准备锁存和应答故障；
> 跨平台内存适配器再模拟 BOOT0、NRST、擦写和应用重启，并驱动真实边缘升级状态机验证
> 成功、拒绝、自动回滚及失败锁定。生产入口仍禁止为模拟 MCU 启用真实烧录。
> 2026-08-21 严格模拟复现并修复目标重试/回滚绕过 F2：每次应用复位都会清除准备锁存；
> 只要目标应用已启动，下一次目标擦写或回滚都必须重新持久化当前 F3 身份并执行 F2。
> 只有已证明仍在系统 Bootloader 的擦写失败可以直接重试；运行模式不确定时保持维护锁。

> [!IMPORTANT]
> 2026-08-20 补充首次准入闭环：设备启动或本地升级完成后查询 F3，只有
> `STATUS=00 + revision 2` 的完整身份才随已认证运行快照登记到资产；因此 SSH 首次迁移不再
> 依赖一次不可能先发起的云端升级。COS 临时失败跨可靠任务唤醒总计最多尝试 3 次；签名、
> 板型、摘要或发布身份错误，以及现场忙碌/升级禁用，均以可靠 `REJECTED` 终态收敛。

> [!IMPORTANT]
> 2026-08-29 修正运行快照和扫码配置准入边界：fixed-frame / UART v1 只由
> `uartProtocolMajor/uartProtocolMinor` 是否成对存在判定，F3 返回的真实固件版本及发布身份
> 只用于追溯，不能把 fixed-frame 快照误判成 UART v1；版本对只有一项存在属于永久格式错误。
> 小程序查询与开始投递事务统一使用可信 `configurationProgress: APPLIED` 形成的当前应用
> 版本和双摘要。运行快照中的 `appliedConfig` 是诊断事实，允许为空或暂时落后，不能让已经
> 精确应用的配置继续显示“设备配置尚未生效”。

> [!IMPORTANT]
> 2026-08-17 已冻结 V54 投递审核金额阈值和 Web 配置中心。自动审核模式必须设置单笔
> 结算金额上限，新订单把上限固化为快照；超过上限的正常订单等待人工审核且不记异常。
> 提现继续复用现有单次最大提现金额。Web 只集中投递审核规则与提现审核规则，不迁移设备或渠道
> 配置。详见
> [`delivery-review-amount-limit-and-configuration-center-v54.md`](delivery-review-amount-limit-and-configuration-center-v54.md)。

> [!IMPORTANT]
> 2026-08-19 已把香橙派反向 SSH 从普通硬件主进程拆为独立
> `ecobin-remote-support.service`。普通硬件进程仍是唯一 OneNet 客户端，通过 root 专用
> Unix Domain Socket 下发 OPEN/CLOSE，并把代理私有 SQLite 中的状态事实幂等转入
> EdgeStore；代理以低权限账号独立持有 OpenSSH 子进程和隧道凭证。只重启或更新
> `ecobin-hardware.service` 不再中断已建立的维护连接，代理或整机重启才按原期限重连。
> 设备侧实现与首次切换步骤见
> [`../../hardware/docs/enrollment-and-remote-support.md`](../../hardware/docs/enrollment-and-remote-support.md)
> 和
> [`../deployment/device-enrollment-and-remote-support-rollout.md`](../deployment/device-enrollment-and-remote-support-rollout.md)。

> [!IMPORTANT]
> 2026-08-29 真机确认 H616/Air780E 会把反向 SSH 的部分 SYN 和返回数据误判为 conntrack
> `invalid`。封版防火墙因此仅为 `ecobin-remote` UID、受保护凭据解析出的跳板 IPv4 和
> SSH 端口增加双向精确例外，并把它们分别放在 `invalid-output`、`invalid-input` 之前；
> 返回例外匹配的是跳板 SSH **源端口**，不开放设备 22 端口。生产防火墙同时由蜂窝协调器
> 和首次启动封存清理协调，更新规则实现时必须重启两者并跨协调周期复核，避免常驻进程用
> 内存中的旧模块覆盖新规则。完整故障矩阵、诊断顺序和安全部署边界见
> [`反向 SSH 远程维护排障与验收手册`](../operations/remote-support-reverse-ssh-troubleshooting.md)。

> [!IMPORTANT]
> 2026-08-17 已重新冻结并实施 V53 投递自动审核与审核后自动提现。机构可选择全部
> 人工、正常订单立即自动审核、收到后 24 小时或 48 小时自动审核；负重量、不可可靠计算
> 或带用户/系统异常的订单仍由人工处理。首次审核形成正返现后，只有机构已启用、金额在
> 范围内、用户有有效微信自动收款授权、没有活动提现且双方余额满足时才同步创建并冻结
> 自动提现；不满足属于本次安全跳过，不回溯补建。详见
> [`delivery-auto-review-and-withdrawal-v53.md`](delivery-auto-review-and-withdrawal-v53.md)。

> [!IMPORTANT]
> 2026-08-15 已实施 V52 设备自注册、厂家初始袋和按需远程维护：香橙派用厂家全局 K1
> 完成一次性 HTTPS 注册，原子保存加密返回的 OneNet/SSH 配置后删除 K1 与注册实现；厂家
> 共享小程序通过独立厂家操作员身份进入隐藏设备出厂端，必须真实扫描每个投口的 EB1
> 初始袋，之后自动机器验收才可启动；用户端和清运端不显示厂家入口。平台管理员
> 只登记一次 Ed25519 公钥，后端经 OneNet 控制设备使用四个复用端口建立短期反向 SSH。
> 完整边界见
> [`device-enrollment-factory-acceptance-remote-support-v52.md`](device-enrollment-factory-acceptance-remote-support-v52.md)。

> [!IMPORTANT]
> 2026-08-10 完成设备接入、投递和清运字段级审查。厂家安装袋是历史事实；当前占用袋、
> 容量当前袋和有效重量基准袋必须属于同一代。审查确认投递满溢/重量基准准入、清运旧基准
> 信任、清运运行快照门槛以及配置应用与激活耦合等问题；软件修复、分层回归及独立
> MySQL 8.4 集成验证已经完成。完整结论和
> 实施状态见
> [`device-delivery-clean-generation-consistency-review-2026-08-10.md`](device-delivery-clean-generation-consistency-review-2026-08-10.md)。

> [!IMPORTANT]
> 2026-08-10 项目负责人重新冻结配置失败恢复：只有当前最高配置应用仍为 `PENDING`、
> 尚未边缘落盘，且原可靠任务以明确下发前原因阻断时，才允许同版本重同步并唤醒原任务。
> 应用一旦 `FAILED`，原命令不再重发；排除故障后必须发布更高版本，以新的应用、命令和
> 任务继续。原版本和摘要完全一致的迟到可信 `APPLIED` 可以纠正失败事实，但迟到
> `EDGE_SAVED` 不重新打开应用。证据超时、确认超时和未知阻断原因默认禁止重发。

> [!IMPORTANT]
> 2026-08-10 项目负责人冻结 V46 运行快照策略：`DEVICE_RUNTIME_SNAPSHOT` 只用于诊断，
> 不参与 OneNet 在线判断。启动、重连和实际状态变化时上报，变化最多每 5 秒合并一次；
> 空闲兜底默认 60 分钟，由平台全局配置且最小 10 分钟，不允许单设备覆盖。禁用设备
> 不下发，恢复时自动补齐最新策略；历史事实暂不清理或归档。完整裁决见
> [`runtime-snapshot-reporting-v46.md`](runtime-snapshot-reporting-v46.md)。

> 2026-08-09 清运 Web 纵切推进到 V44：普通/平台 Web 均可按机构读取清运操作列表和详情，
> 并与既有清运记录、直接修正和只追加历史互相深链。可信香橙派命令阶段开始投影清运
> 操作的边缘保存、可能解锁、需要恢复和解锁前结束事实；七种正式状态不再兼容
> `PRE_OPEN_ENDED`。V44 只增加机构/状态时间查询索引，不增加表或修改清运业务事实。
>
> 同日清运小程序正常主线推进到 V45：清运员可按全部、在线、24 小时无投递、24 小时未清运、
> 已满和满载超 2 小时六类设备查询；失去权威业务目标的历史可信边缘事件改为终态隔离并回执，
> 不再无限重试。V45 只增加活动查询索引和隔离原因，不增加表，也不改变清运结束/恢复流程。
> 本文记录对话中形成、仅靠代码不容易恢复的决策。代码和更新日期更晚的专题文档若与本文冲突，以较新的事实为准。

> [!IMPORTANT]
> 2026-08-10 项目负责人补充冻结 V36 未分配设备事实作用域：已注册但尚未永久分配机构的
> 设备是正常平台资产，不得把其运行快照、故障或安全变化仅因缺少 `organization_id` 判为
> 不可信。分配前事实按平台收件并在可靠事件上回执，不生成机构投影；分配后产生的新事实
> 进入永久机构。裁决使用可信事件发生时间与不可变机构分配时间，保证同一事件跨分配重投时
> 作用域不变。无需修改 OneNet、UART 或边缘 SQLite 契约。

> [!IMPORTANT]
> 2026-08-25 袋码签发补充为 V59：平台管理员单批可生成 1～500 个袋码；Web 可按批导出
> “一列、无表头、每行一个袋码”的内容 Excel，或“只有二维码图片、无序号和文字”的
> 二维码 Excel。Excel 依赖仅在点击导出时加载，不增加服务端文件生成接口。数据库只扩大
> V43 两个既有数量检查约束，库存和首次使用建袋边界不变。
>
> [!IMPORTANT]
> 2026-08-09 项目负责人冻结 V43 平台防伪袋码：平台按批签发并打印 EB1 标签，但不预建
> 租户/机构库存；厂家初始袋和清运换入袋由后端验真，未知有效码首次使用时才在当前机构
> 建袋。旧格式当前袋可继续收敛，取下后不能重新装入。完整裁决见
> [`authenticated-bag-labels-v43.md`](authenticated-bag-labels-v43.md)。

> [!IMPORTANT]
> 2026-08-13 项目负责人补充设备二维码刷新规则：固定帧模式除保存新 URL、香橙派启动和
> 串口重开时立即发送外，还在投递/清运均空闲且串口打开时默认每 60 秒重发本地最新
> URL；作业占用和写入失败均按 5 秒复查。该周期发送复用命令消费者线程，不与物理命令
> 并发。当前仍是静态公开 URL，重复发送本身不防远距离扫码；未来动态码必须使用后端可
> 验证的短期签名，裸时间戳不能作为防重放凭据。完整边界见
> [`device-entry-url-edge-delivery-v42.md`](device-entry-url-edge-delivery-v42.md)。

> [!IMPORTANT]
> 2026-08-08 项目负责人冻结 V42 设备入口 URL 下发：完整公开 URL 在平台机器验收时由
> `requestDeviceAcceptance` 携带，在全局基础地址改变时由独立可靠命令下发；香橙派先
> 保存到 SQLite，再通过无应答的固定 195 字节 `A0` 帧交给 MCU，并在自身重启或串口
> 重开后重发。二维码验收只证明香橙派保存的摘要正确，不检查 MCU 或屏幕。完整裁决见
> [`device-entry-url-edge-delivery-v42.md`](device-entry-url-edge-delivery-v42.md)。

> [!IMPORTANT]
> 2026-08-08 项目负责人冻结 V41 全局设备二维码入口：所有设备统一使用 `https://www.jinshoubao.com/device-entry/`，每台设备只通过唯一公开码形成不同二维码；入口地址改为应用全局非秘密配置，渠道、租户和机构不再保存或展示该字段。微信校验文件随 Web 静态资源发布。完整裁决见 [`global-miniapp-device-entry-v41.md`](global-miniapp-device-entry-v41.md)。

> [!IMPORTANT]
> 2026-08-08 项目负责人重新冻结 D-048 / I-058 / V39：一个平台小程序渠道可以服务多个租户和机构；AppID 只用于微信渠道和 OpenID 作用域，设备公开码通过永久资产归属决定机构。同一微信身份允许创建多个相互隔离的机构账号；V39 当时按最近注册账号选择，已由下方 V49 的最近成功登录规则覆盖。无扫码且从未注册的用户保持游客。小程序首页游客可见，登录和手机号授权只在“我的”或受保护操作中触发。完整裁决见 [`shared-miniapp-multi-organization-identity-v39.md`](shared-miniapp-multi-organization-identity-v39.md)。

> [!IMPORTANT]
> 2026-08-14 项目负责人以 D-049 / I-059 / V49 补充冻结多机构账号选择：客户端有效
> 会话继续使用；需要签发新会话且没有设备公开码时，按最近成功登录时间选择第一个可用
> 机构账号，设备扫码仍优先。最近账号不可用时回退到次近账号，不向客户端公开时间字段。
> 完整裁决见
> [`recent-miniapp-organization-account-v49.md`](recent-miniapp-organization-account-v49.md)。

> [!IMPORTANT]
> 2026-08-14 项目负责人冻结 V50 平台管理员治理：平台管理员表完全为空时创建唯一受
> 保护的默认管理员；升级前唯一且登录名匹配的现有管理员只改变账号类别，保留原密码；
> 默认管理员可以创建、停用、启用、永久逻辑删除及重置其他普通平台管理员密码，普通
> 平台管理员只能修改自己的密码。默认管理员不能被停用、删除或由他人重置。完整裁决见
> [`platform-administrator-governance-v50.md`](platform-administrator-governance-v50.md)。

> [!IMPORTANT]
> 2026-08-07 项目负责人重新冻结 D-047 / I-057 / V36，并以 V38 修订机器验收条件：设备资产只允许一次性永久分配租户和机构；部署实例、部署码、回收/调拨、现场人工验收和人工经营开关全部退出。平台在分配前自动机器验收，MCU/摄像头是否模拟只作诊断且不影响结果，机构只安装联网；小程序使用永久设备公开码，OneNet/COS/边缘 v2 不含部署码。禁用/报废只阻止新业务，既有作业继续收敛，物理安全事件例外。实施不保留旧协议兼容层。完整裁决见 [`permanent-device-ownership-v36.md`](permanent-device-ownership-v36.md)。

> [!IMPORTANT]
> 2026-08-02：满溢准入已切换到 V25“设备被动上报、只有当前袋明确 `FULL` 才阻止下一次投递”。后端不再为投递/清运创建检测 gate 或主动下发 `SAMPLE_FULLNESS`；无上报、失败或旧袋结果不新增阻断，新袋默认 `NOT_FULL`。投递选项与开始接口使用同一当前袋 `FULL` 事实。完整裁决见 [`fullness-reporting-v25.md`](fullness-reporting-v25.md)，本文及旧规划中相反描述均由该裁决覆盖。

> [!IMPORTANT]
> 2026-07-28：需求、P0 范围、业务模型、系统架构、数据库设计、接口设计和详细设计修订已确认。H-01、H-02、F-01～F-11、V-01、V-02 已完成；V-02 的个人主体和开发版微信限制已由项目负责人接受，设备来源归因由非阻塞 P0-FOLLOWUP-01 延期跟踪。H-03、V-09 为 `ready` 但尚未授权。F-11 的 SQLite v4、OneNet 命令受理、固定帧 MCU 适配、照片/COS 和故障自动测试已按负责人接受的当前范围转为 `done`。后续真实双摄验证发现数字索引会重复选择 DECXIN，现已改用稳定 `by-id` 路径并通过真实 STS 上传和 URL 下载验收；项目负责人决定暂不继续处理香橙派当时的默认路由/DNS 波动。项目负责人确认现有 MCU 使用协商后的固定帧协议，香橙派保留云端契约；对 MCU 不支持的能力，按逐项决策采用香橙派本地保存、fixed-frame 正常兼容投影或明确拒绝/失败，不增加物理命令重发、MCU 作业重启恢复或双事实安全锁；真实线路和执行器验收归 H-03。2026-07-29 又确认当时 OneNet 控制台为 9 服务 / 13 事件版本，并以“先跑起来”为首要目标：冻结 MCU 无法提供的状态允许由 fixed-frame 兼容层构造；`applyConfiguration` 只要由香橙派可靠保存并设为活动配置即可返回成功和 `APPLIED`，不要求真实下发 MCU。该数量仅是历史快照；当前候选已经演进为 15 服务 / 19 事件。逐项决策持续记录在 [`onenet-edge-handling-decisions.md`](../../hardware/docs/review/onenet-edge-handling-decisions.md)。其他任务仍须逐项授权，`ready` 只表示依赖允许领取。正式上游依次为
> [`requirements-baseline.md`](../planning/requirements-baseline.md)、
> [`p0-scope-baseline.md`](../planning/p0-scope-baseline.md) 和
> [`business-model-baseline.md`](../planning/business-model-baseline.md)，冻结的系统结构见
> [`system-architecture-draft.md`](../planning/system-architecture-draft.md)，冻结的目标数据库见
> [`database-design-draft.md`](../planning/database-design-draft.md)，冻结的目标接口见
> [`interface-design-draft.md`](../planning/interface-design-draft.md)，施工入口见
> [`detailed-design-draft.md`](../planning/detailed-design-draft.md)，正式任务入口为
> [`tasks/p0-controlled-loop/00-index.md`](../planning/tasks/p0-controlled-loop/00-index.md)；截至 2026-08-06，I-001～I-056 均已确认。I-051～I-053 已包含同事务 FK 构造引用、首次注册同步参与扩展以及 recycling 复合投递协调/device 事实所有权的窄边界；I-055 明确首版前不强制跨端契约 CI、完整自动契约门禁或自动 HIL 发布门禁；I-056 增加一次性免确认收款授权及授权后自动收款。本文下方若描述旧实现或历史决策，不能替代这些新基线。

## 1. 项目与组件

EcoBin 是智慧环保回收箱系统：Spring Boot 4.0.6 + Java 21 的 Maven 多模块后端，配套 Web 管理后台、微信小程序和香橙派设备程序。

- `ecobin-framework`：Security/JWT、通用 Web 响应与异常契约、多租户和通用基础设施；F-01 后不再保存外部平台实现。
- `ecobin-module-identity`：F-02 已承接原 system 的管理员、租户、用户和认证行为，并建立可信执行上下文与公开身份边界。
- `ecobin-module-device`：设备、投口、设备会话，并通过迁移期公开端口提供设备查询与统计。
- `ecobin-module-funds`：F-03 已承接旧钱包、提现行为，并保留 F-02 首次注册事务参与端口。
- `ecobin-module-recycling`：F-03 已承接旧投递审核、清运和袋行为。
- `ecobin-module-operations`：F-03 已承接旧统计聚合，并只通过其他模块公开端口读取数据。
- `ecobin-integration`：F-01 后承接 OneNet/Pulsar、COS 和微信适配实现。
- `ecobin-bootstrap`：依赖组装、配置、Flyway、启动入口。
- `frontend/web`：React 18 + TypeScript + Vite + Ant Design/ProComponents。
- `frontend/miniprogram`：原生 TypeScript 微信小程序 + TDesign。
- `hardware`：香橙派 Python 设备侧；目标运行时 Python 3.11，1 GB 内存，不运行 Chromium。

后端结构、命令和通用约定见根目录 `CLAUDE.md`。

当前已是 framework、identity、device、funds、recycling、operations、integration、
bootstrap 共 8 个模块的终态物理 reactor；原 `system` 已在 F-02 退出，原 `business`
已在 F-03 搬迁并退出，`ecobin-common` 又随不可达旧栈于 2026-07-29 删除。跨业务模块只
导入 `.api`，通用 Web 技术契约由 framework 提供。完整依赖和事务规则见
[`system-architecture-draft.md`](../planning/system-architecture-draft.md) 与
[`I-051～I-055`](../planning/interface-design/11-module-ports-machine-contracts-i051-i055.md)。

DD-004 保留内部 `BIGINT` 复合外键，只允许点名同步端口在同线程同事务传递不可序列化
的关系专用 FK 构造引用；首次机构用户由 identity 事务同步调用 funds 参与者创建零余额
钱包。修订后的 PDD-001 只规定“开始投递”由 recycling 按 D-037 协调，device 仍唯一
拥有和写入 session 等设备作业事实；继续投递属于已授权 session 内设备本地动作。

## 2. 不应反复推翻的业务决策

### 身份与租户

- 不引入全局“平台用户 → 多租户成员关系”模型。
- B 端：平台管理员创建租户，只创建 `sys_tenant`。
- C 端当前 V36 实现允许直接进入小程序或扫描设备二维码注册；首次 `wx.login` 成功创建机构用户记录即写入独立注册时间和可空的来源永久设备资产（`registered_via_asset_id`），用于按设备和时间统计地推成果，尚未绑定手机号的用户也计入注册。直接注册的来源为空，后续扫码不回填；手机号绑定时间另存，归因不影响权限、钱包或投递资格。二维码只携带永久 `deviceCode`，不携带租户主键、设备凭证或已废弃的部署身份。
- 所有业务表带 `tenant_id`；普通业务请求由登录上下文和租户拦截器隔离，平台域账号按既定规则放行。
- 完整权限模型见 `docs/architecture/permission-design.md`。

### 投递

- 当前模型是“设备上传后建单”，不是用户开门时预建订单。
- 小程序开门只激活 `biz_device_session` 并下发开门；设备完成称重后发 `deliveryComplete`，后端再创建每袋独立订单。
- 当前旧实现的归属取设备活跃用户会话。无会话或会话过期时仍建无主单（`user_id=null`），不返现；会话被后来的用户覆盖时接受“最近用户”语义。该规则仅描述待替换现状，不是 2026-07-24 目标设计。
- 当前旧实现使用 OneNet 消息 ID（缺失时回退 MQ message ID）作为幂等键，历史列
  `delivery_token` 承载该键；目标使用后端 `sessionUid`、香橙派持久 `eventUid`、
  规范摘要和设备资产内全局事件序号。不要从旧字段名反推目标语义。
- 2026-07-24 目标投递改为一次有效扫码 session 最多一单：session 单独是订单业务唯一根，
  `eventUid` 只负责可靠事件去重/冲突。可空首末重量及状态、开始单价和整场四图只在唯一
  `DELIVERY_COMPLETE` 中上报；明确终态称重失败仍形成系统异常订单，后置满溢采样不在
  完成载荷内，中间继续轮次不上传。任一本地轮次重量减少达到后端下发阈值（默认 500 克）
  时，只随最终载荷上报 `negativeWeightAnomaly` 布尔标志；后端原样固化，不按整场净重补判。
- 照片由设备决定 COS 对象 key；投递或清运完成事件先回传四个槽位的当时状态，仍在拍摄或上传的槽位 `url=null`。实际上传完成后再由 `photoStatusReported: AVAILABLE` 回传可信 URL，后端不自行拼 URL。

### 清运：现状与目标不要混淆

后端的目标正常清运切片已经落地：清运员准备操作后，后端冻结旧袋/基准、预留新袋并下发 `START_CLEAN_OPERATION`；可信命令阶段单调推进 `EDGE_SAVED/IN_PROGRESS/RECOVERY_REQUIRED/PRE_UNLOCK_ENDED`，固定帧执行期重启仍由既有事务中止为 `ABORTED` 并建立安全联锁；可信 `CLEAN_COMPLETE` 到达后在同一事务创建清运记录、交换袋关系、处理新基准、建立清运后检测 gate 并提交确认意图。清运员本人查询、Web 操作/记录查询以及 `clean.edit` 直接修改和只追加变更历史也已实现。当前未闭合的是香橙派/MCU 的目标屏幕状态机、再次解锁、主动解锁前结束和超时恢复等跨端真机路径；设备侧旧 D1 毛重/皮重链仍须退出，不能与后端目标事实混用。

2026-07-23 已正式确认 [`I-026～I-030`](../planning/interface-design/06-cleaning-bags-fullness-recovery-i026-i030.md)：

1. 清运员扫描永久设备二维码、选择投口并扫描换入袋；后端创建可恢复 `operationUid`、冻结旧袋/旧基准、预留新袋并取得整机占位，不提前创建清运记录，也不使用最近心跳重量冒充开门前重量。
2. 香橙派必须先把完整操作上下文和期限可靠写入 SQLite，再从 MCU 取得并保存本次真实开门前稳定总重量，二者都成功且开始授权仍有效才允许 MCU 开门。MCU 负责屏幕/按钮、门控和稳定称重；香橙派负责操作身份、基准、照片、结果计算、COS 和 OneNet。
3. 清运门没有门磁或关门执行器，MCU 只给电磁阀通电解锁且门自动弹开；第一次解锁可能
   已执行即为不可逆边界。以后只能在原操作内再次解锁、由清运员人工关门确认完成，或
   超时进入原清运员恢复。电磁阀通断不能推定物理门位，门位保持 `UNKNOWN`，
   `SAFE_CLOSE` 不适用于清运门。恢复中
   只有“断电 + 原清运员现场确认门扇关闭”才可释放整机占位，原投口、操作和袋预留仍锁定。
4. 最终安全关门并由清运员确认后，香橙派可靠保存单一 `cleanComplete`；后端一次事务创建清运记录、交换袋关系、建立/清空新基准、把新袋容量初始化为 `NOT_FULL` 并结束操作。香橙派随后可以按 V25 规则上报新袋状态变化，后端不主动发起采样。
5. 袋没有生命周期状态；无效新皮重仍只能由确认当前袋为空后的真实重测恢复。满溢准入只认当前袋设备上报的 `FULL/NOT_FULL`；投递结果待处理和严重安全锁继续通过各自目标接口规定的现场确认边界恢复。
6. 清运员是可信工作人员；清运完成事务直接建立已完成记录，不进入审核、认定、通过或驳回流程，也不产生钱包、积分、返现或其他奖励。有 `clean.edit` 权限的后台人员可以直接修改允许修改的清运业务数据，保存后立即生效并追加变更留痕；设备原始证据以及袋关系、重量基准、容量 gate 和设备安全状态仍只能由对应真实业务事实推进。
7. 2026-08-05 已补当前清运员袋扫码追溯：`aud=miniapp` 只能在当前机构、当前有效清运资格下读取一个已扫描袋的安装周期及周期内投递证据；周期由后端安装/取走边界和订单冻结的袋、投口、会话时间确定，不按设备时钟或当前位置倒推。V25 满溢读取使用被动 `fullness-state-changes` 历史，未恢复旧主动检测接口。

旧家目录计划 `~/.claude/plans/tender-hopping-moth.md` 和其中 `G1` 示例只保留历史参考价值；若与当前接口、数据库设计或 UART 审计冲突，以仓库内较新的设计为准。OneNet/COS 和边缘 SQLite 语义现已由 I-041～I-045 冻结；正式实施仍须生成机器物模型、完成 UART 契约及跨端测试，不能直接把旧示例代码视为已验证实现。

### 钱包、充值与提现

- 当前代码已有 Native 充值、机构双账本、微信免确认授权、授权查单、授权后转账、小程序授权页，以及手动/投递自动提现各自的免审阈值。历史 `USER_CONFIRM` 单仍沿原链收敛。
- 2026-07-23 确认的 [`I-031～I-035`](../planning/interface-design/07-funds-recharge-withdrawal-wechat-i031-i035.md) 负责充值、提现和渠道终态基础；[`D-046`](../planning/database-design/10-merchant-transfer-authorization-d046.md) 与 [`I-056`](../planning/interface-design/12-merchant-transfer-authorization-i056.md) 冻结免确认授权；V53 在这些边界之上增加投递自动审核和首次正返现自动提现，不改变微信原单收敛规则。
- 2026-08-04 项目负责人确认：该普通商户号从未接入微信支付平台证书，首次真实 APIv3 接入就使用 `pub_key.pem + 微信支付公钥 ID`；后端只支持公钥验签，不引入平台证书依赖或双模式过渡。
- 充值先建立本地单和固定下单意图，微信成功事实与机构净额入账分为两个可恢复事务。提现创建先冻结用户和机构两侧资金，审核通过后才建立唯一 `out_bill_no`；只有微信 `SUCCESS/FAIL/CANCELLED` 终态可以结算。
- 新提现创建前必须有当前 `ACTIVE` 授权，创建和提交各锁定复核一次并保存授权身份快照；历史 `USER_CONFIRM` 单继续使用原 `package_info` 收敛，不批量改挂新授权。
- V51 把微信明确未受理的授权创建请求保存为本地终态 `CREATE_REJECTED`（对外
  `FAILED`）：旧不可变请求和任务不得再次外调，旧单与观察继续保留但释放当前槽；用户排除
  原因后以新操作、新单号重新申请。网络中断或响应验签失败进入 `UNKNOWN`，继续占槽并查
  原单，禁止换号。
- `NOT_ENOUGH` 表示公司公共运营账户流动性不足，只暂停系统商户出款闸门；机构本地额度继续可信。V53 自动提现在机构本地额度不足时只记录安全跳过并产生聚合告警，不创建失败提现；P0 不实现充值退款、机构额度人工调整或更换微信原单重试。

### 运营治理与最小概览

- 2026-07-23 已正式确认 [`I-036～I-040`](../planning/interface-design/08-operations-audit-alert-reconciliation-statistics-i036-i040.md)：平台技术页只能查看并恢复原可靠任务、确认隔离项，不能直接重放消息或修改业务终态。
- 操作审计、技术尝试、聚合告警、对账问题和业务事实保持分层；工作人员确认告警或标记对账问题已处理都不等于来源已经恢复。
- M0 每日资金对账只做 EcoBin 内部逐交易收敛和可证明的完整遗漏补齐，不宣称已经接入微信官方账单文件；复杂财务归档仍是 M1。
- 最小运营概览按北京时间日期范围查询，并把期间流量与当前快照分开。operations 通过各业务模块公开查询端口，在仅供该只读概览使用的 `REPEATABLE READ` 事务中从同一快照组装；业务写入仍使用 `READ COMMITTED`，且不保存可人工修改的累计统计。
- 2026-08-05 已实现 I-036、I-037、I-038、I-040 的后端/OpenAPI：平台可查看并精确恢复原可靠任务、查看尝试、确认隔离项；普通/平台 Web 可按实时授权读取审计和告警并确认告警；工作人员小程序只读当前机构告警和概览。设备故障、当前满溢、可靠任务阻断及出款余额不足会投影为告警，只有来源真实收敛才解决。I-039 每日资金对账整体暂缓，当前不发布对账运行、问题查询或人工处置端点。

## 3. IoT、OneNet 与照片链路

- 正式上行：设备 MQTT → OneNet → 北向 Pulsar MQ → `OneNetMqConsumer` 解密 → `OneNetEventDispatcher` 分发。
- 正式下行：后端调用 OneNet 物模型服务 API。设备连接 token 与平台下行 API token 是两套凭证、版本和资源路径，不能混用。
- P0 后端只配置一个 OneNet 产品 ID，可信设备名固定等于硬件 SN；资产表不重复保存该可推导映射。设备 Key 只配置在对应香橙派，后端下行使用产品级 AccessKey。
- 整条链路只需要出站连接：上行订阅 MQ、下行调用 OneNet HTTP、设备访问 COS；没有 OneNet HTTP 公网入站回调需求。
- 2026-07-23 已正式确认 [`I-041～I-045`](../planning/interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)，并由 V36 改为永久资产寻址：可靠边缘事实使用稳定 `eventUid`、原作业身份、规范摘要和设备资产内全局 `edgeEventSequence`；OneNet 传输 ACK、设备受理、物理结果和后端业务确认严格分层。后端权威事务与确认意图共同提交，设备持久化确认并回执后才清理原事件。
- 2026-08-03 最终把“能否新建物理业务”收敛为 OneNet 传输在线事实：资产级 `oneNetConnectionStatus` 的唯一权威来源是北向 `deviceOnline` / `deviceOffline` 生命周期通知；只有明确 `ONLINE` 才能创建新的投递或清运任务，`OFFLINE` 和尚无生命周期事实的 `UNKNOWN` 都阻止创建。普通已鉴权消息、运行快照、OneNet 下行错误和数据库时间差都不能覆盖或推断该在线状态。接口还可返回资产级 `edgeConnectionStatus` 供诊断，但它不再是后端创建投递/清运的准入条件。
- 2026-08-24 最终冻结跨机器时钟边界：微信或设备产生的时间是原始诊断证据，后端产生的 `received_at/created_at` 是本地业务顺序依据，两者不得用数据库大小约束互相证明。设备时间比后端快超过 60 秒只告警且不参与归属、期限或状态裁决；迟到积压照常接收。香橙派只有在操作系统确认 NTP 同步时才发送 `clockQuality=SYNCED + occurredAt`，否则发送 `ESTIMATED/UNAVAILABLE + occurredAt=null`，本地另存原始墙钟；先自动修复 NTP，连续 5 分钟仍未同步才上报不阻断业务的 `CLOCK_UNSYNCED`。本地时钟不可信时不执行外部绝对期限拒绝；清运等本地相对窗口仍由单调时钟扣减，外部期限继续由后端或外部权威执行。V57 建立边界，V58 补强恢复不变量。
- 后端只判断自己能够权威确认的条件，例如用户和机构权限、永久归属、自动机器验收、资产生命周期、整机是否已有后端作业占用、机构规则、OneNet 当前是否明确在线，以及已经由可信重启事件形成的清运中断锁。不存在人工经营开关；资格由这些当前事实自动计算。称重、烟感、投口执行器、满溢和香橙派本地清运锁等需要观察现场的条件，由香橙派收到命令后在物理动作开始前判断；拒绝时上报稳定错误码，后端不再用较早的运行快照替香橙派猜测当前物理状态。
- OneNet `10421` 只把当前任务暂停为 `PENDING / DEVICE_OFFLINE`，不改写资产在线事实；后续 `deviceOnline` 通知重新计算并唤醒任务。如果开始授权已过 60 秒，并且任务仍因 `DEVICE_OFFLINE` 或 `DEVICE_PRESENCE_UNKNOWN` 停在下发前，后端在同一事务结束会话、释放整机占用/清运新袋预留并取消该未下发任务。只要香橙派已经回报受理或物理动作已经开始，后来离线就只阻止新任务，不自动结束、不释放旧占用，也不根据离线推断作业结果。`10410` 表示当前产品下找不到设备，任务阻断为 `DEVICE_IDENTITY_UNRESOLVED`，不能盲目重试。
- 物理命令和配置命令被 OneNet 接受后不再自动重复下发，而是进入 `AWAITING_DEVICE_EVIDENCE` 等待香橙派命令观察或最终业务事实；到期仍无证据时阻断为 `DEVICE_EVIDENCE_TIMEOUT`，迟到可信证据仍可完成原任务。`CONFIRM_EDGE_EVENT` 和 `PROVIDE_PHOTO_UPLOAD_GRANT` 是安全幂等控制命令，继续保留重试。
- 运行快照用于诊断和当前配置/安全投影：启动、重连和状态变化时上报，状态变化最多每 5 秒合并一次，正常在线期间默认每 60 分钟兜底一次；周期由平台全局配置且最小 10 分钟，不参与 OneNet 在线判断。后端在收件事务中直接更新投影并提交后 ACK，不为每份快照建立可靠处理任务。运行快照仍属于只追加设备证据，日常应用账号不得删除；本阶段暂不实施归档或清理，也不扩大 `ecobin_app` 的删除权限。
- OneNet 返回 HTTP 200 但业务码非零时，只将明确的临时平台内部错误 `10500` 归为可重试；`10415` 等参数、物模型、权限及其他未进入临时白名单的业务错误直接永久失败，不能让冻结载荷持续重放。
- 当前联调产品为 `tB6NlBWW0V`，唯一应保留的设备名/硬件 SN 为 `test-divice-1`。历史假设备及其关联事实应使用受控清理工具处理，不能只删资产主表；操作手册见 [`fake-device-cleanup.md`](../operations/fake-device-cleanup.md)。
- 2026-07-23 已正式确认 [`I-046～I-050`](../planning/interface-design/10-uart-protocol-i046-i050.md)：UART 1.0 使用 `0xEC42`、最大 256 字节、big-endian 和 CRC-16/CCITT-FALSE 的有界二进制帧；启动先 HELLO/QUERY_STATE，命令 ACK 与物理结果分层，关键 MCU 事件提交边缘 SQLite 后才 ACK。`txSequence`、`mcuCommandUid`、`mcuBootId + mcuEventSequence` 和云端作业身份互不替代，任一端重启都禁止自动重放旧开门。
- 投递和清运照片统一为设备直传 COS。对象 key 由设备在 `ecobin/{workType}/{workUid}/` 授权前缀内生成，不放入永久公开码或任何设备凭证；临时凭证只在发送时附加且不进入稳定摘要/日志。完成事件回传四个槽位状态，尚未上传完成的槽位 URL 为空；上传完成后再由照片状态事件回传可信 URL。
- Jackson 使用 Spring Boot 4 的 Jackson 3 包 `tools.jackson.databind`；不要在 framework 模块误用 `com.fasterxml.jackson.databind`。
- 当前物模型机器来源是 `contracts/onenet/thing-model.mapping.yaml`，控制台导入件是
  `contracts/onenet/generated/onenet-thing-model.candidate.json`，现为 15 服务 / 19 事件 / 0
  属性。`docs/iot/onenet-thing-model.md` 和同目录 JSON 只保留早期联调背景，不能作为当前
  导入源。修改契约时必须同步生成物、代码、说明和 OneNet 控制台模型，禁止保留旧写协议
  作为生产兼容层。
- I-019/I-020 的目标契约要求配置以版本和摘要分别证明“香橙派已可靠落盘”和“必要 MCU 项已同步”。当前 fixed-frame 阶段采用 2026-07-29 确认的运行优先例外：`applyConfiguration` 可靠保存并成为香橙派活动配置后即可返回成功及 `APPLIED`，允许构造兼容 `mcuCommandUid`；这不表示 MCU 已真实接收配置。具体边界见 [`onenet-edge-handling-decisions.md`](../../hardware/docs/review/onenet-edge-handling-decisions.md)。

## 4. 硬件侧当前上下文

- 香橙派是云侧/业务侧小电脑：OneNet MQTT、COS 上传、流程编排；MCU 连接屏幕、传感器和执行器，通过 UART 与香橙派通信。
- 物理 UART 通常至少连接交叉的 TX/RX 和共地 GND。F-11 已把 `hardware/main.py`
  的正式入口切向 SQLite 新骨架，当前 schema 已推进到 v6；当前现有单片机使用双方
  确定的 `AA/BB/EE/F0` 下行与
  `DD/EF/F1/CC` 上行固定帧适配器。规范 `uart-v1` 只保留为未来明确选择的可选实现，不是
  当前 MCU 的通信路径，也不是 F-11 完成门。
- 固定帧兼容保留香橙派—OneNet—后端完整契约，但 MCU 实际能力不完整：配置只在香橙派
  保存，缺失的远程控制明确返回不支持，无法查询的作业/门状态返回
  `UNKNOWN/NOT_SAMPLED`；F0/F1 可查询真实重量、红外和烟感快照，CC 只上报烟感
  `00/01/02` 之间的变化，`02` 明确表示 MCU 无法读取烟感。DD/EF 满溢位只作为红外
  原始观测，业务满溢仍按香橙派保存的规则判断。
- 固定帧只有单投口且无 ACK、CRC、流程编号和通用作业状态查询。启动和机器验收各自
  发送新的 F0；启动自检没有取得完整健康证据时，香橙派仍连接 OneNet 并上报降级事实，
  同时在没有投递或清运占用时按 5、10、20、40、60 秒退避重查 F0；每次间隔从上次
  查询结束后计算，之后最多每 60 秒
  一次。合法 F1 必须同时证明通信正常、`VALID=3`、重量为 0～350000 克整数、红外为
  布尔值，且最新烟感为 `NORMAL/OK` 或 `ALARM/OK`；证据完整后停止重查，健康状态下
  不做周期轮询。`ALARM/OK` 证明烟感通信健康，但仍由安全准入阻止新的投递和清运。
  作业槽被占用时跳过自检，绝不重发开门或清运等物理启动命令。
- 重查取得合法 F1 后会关闭活动的 `UART/UART_PROTOCOL` 故障；如果只有通信恢复而重量、
  红外或烟感证据仍无效，则继续退避重查。相同超时或相同烟感投影只刷新故障发现次数和
  最后发现时间，不重复制造 MCU inbox 或可靠安全事件；真正恢复只产生一次恢复事实。
  `CC` 可以更新最新烟感，但不能单独替代 F0/F1 对通信、重量和红外的完整证明。
- 活动的 `UART/UART_PROTOCOL` 故障或已经关闭的串口优先于历史正常 F1；二者任一存在时，
  新投递和清运仍以 `SAFETY_SENSOR_UNHEALTHY` 拒绝，直到新的自检证据完成恢复。
- 香橙派不恢复或重放 MCU 作业；重启后只把本地未决投递、清运、满溢检测和空袋基准测量置为
  `FAILED / EDGE_RESTARTED` 并释放槽位；已经可靠写入发件箱的完成事件优先保留并继续上传。
  被重启中断的清运还会设置持久清运锁，后续投递必须拒绝，直到现场重新完成一次完整清运，
  后端收到可信 `CLEAN_COMPLETE` 后两端才清除该锁。当前不增加 UART v1 快照完整性锁、
  SQLite/MCU 作业冲突锁或额外设备身份启动锁，也不要求把 systemd 的 `Restart=on-failure`
  改为 `Restart=no`。
- 命令观察的可靠身份是 `commandUid + stage + errorCode`（无错误阶段使用空错误身份）。同一
  身份重发必须复用原事实；同一阶段的不同稳定错误可以按边缘序号追加。例如清运先上报
  `FAILED / COMMAND_EXPIRED` 并保留恢复占位，随后香橙派重启再追加
  `FAILED / EDGE_RESTARTED`；后端保留两条不可变证据，再按后一个事实中止清运、释放占位并
  建立清运安全联锁，不能用重启事实覆盖或删除先前的超时事实。
- 运行时必须显式选择 `fixed-frame` 或 `uart-v1`，不自动探测或失败回退；固定帧适配
  只存在于香橙派硬件边界，不改变 OneNet、后端或业务事件结构。旧 D1 和旧 gross/tare
  清运路径仍须退出。
- 固定帧实施记录和能力降级矩阵见
  `hardware/docs/review/fixed-frame-mcu-adapter-2026-07-27.md`；真实线路、屏幕和执行器
  行为仍由 H-03 验收，软件测试不得冒充真机能力。
- 当前 OneNet 候选为 15 服务 / 19 事件；其中既有 15 服务 / 18 事件的逐项处理结论见
  `hardware/docs/review/onenet-edge-handling-decisions.md`，V63 新增的一项只负责上报设备软件
  实际安装与运行状态；后续讨论结果统一增量写入该文档。
- 当前待写卡候选是从干净提交 `60b42b19` 构建的 v19，而不是历史 v17 或中止的 v18。v19 的
  软件和镜像离线检查已经通过，但写卡、写后全范围回读、断电冷启动和真机 HIL 尚未完成；
  在这些现场事实取得前，不能把后端已经允许识别该运行时理解为设备已具备远程更新或量产资格。
- 2026-07-11 迁移记忆时工作区已有用户修改：`hardware/main.py`、`hardware/pyproject.toml`，以及未跟踪的 `hardware/docs/`。这些不是 Codex 创建的，必须保留。

## 5. 已知工程坑

- 2026-08-03 的
  [`投递异常情况与恢复边界`](delivery-exception-recovery-review-2026-08-03.md)
  已增加“事件驱动、边缘物理准入、重启直接取消”的冻结简化规则。文档后半仍保留早期复杂恢复
  枚举作为风险清单；若其中的“恢复旧物理作业”建议与冻结规则冲突，以文档开头的最终决定为准。
- 2026-08-02 的 V25 投递联调已经打通真实 OneNet/COS、模拟 MCU/双摄、订单、业务确认
  回执和当前袋 `FULL` 准入。期间遇到的 systemd 双实例、旧边缘数据、V24→V25 迁移、
  `.m2` 旧 jar、OneNet 确认引用缺少 `PORT_FULLNESS_STATE`、BLOCKED 原任务受控恢复和
  时钟偏差等问题，已整理为
  [`投递全链路联调复盘与复跑手册`](../operations/delivery-e2e-integration-retrospective-2026-08-02.md)。
  下次跨端联调先按该手册执行环境与契约预检，不以 OneNet `code=0` 或出现订单单独宣称
  闭环完成。
- `./mvnw spring-boot:run -pl ecobin-bootstrap` 不会重建其他模块，会直接使用 `.m2` 的旧 jar。改过 identity/framework/device/business 或其他模块后先 `./mvnw install -DskipTests`，再运行 bootstrap。不要用 `-am spring-boot:run`，它会尝试在父 POM 找主类。
- IDEA 运行使用各模块 `target/classes`，代码编译后仍需 Stop/Run 重启 JVM 才能替换已加载类。
- `AdminController.list` 与 `TenantController.list` 返回 `Result<List<T>>`，Web 端做客户端分页；用户、设备、投递、清运、提现等主要列表返回 `PageResult`，由服务端分页。
- 微信 `jscode2session` 返回 JSON 内容但可能标为 `text/plain`，要先取字符串再用 Jackson 手工解析。
- 小程序 TDesign 曾完全无样式，根因是 `ignoreDevUnusedFiles=true` 丢弃 npm 组件，加上 `es6=false/enhance=false` 不转译 ESM；不是 `style:v2`。修复后需重新构建 npm、清缓存并重启开发者工具。
- 本地数据库与渠道密钥统一保存在 Git 忽略的
  `.ecobin/application-local-secrets.yml`；IDEA、开发脚本和根 Compose 共用该文件，
  Fake/Real 只切 profile。容器内数据库地址仍由 `docker-compose.yml` 的 backend
  environment 覆盖为 `mysql` 服务名；生产继续使用 `/run/secrets`。

## 6. 产品与协作偏好

- 用户明确说“目前先计划，不改代码”时严格停留在计划阶段。
- Web 后台视觉改版曾被明确搁置，等用户与导师确定方向；目前优先保证功能与跨端契约。

## 7. 资料导航与权威顺序

1. 当前代码、测试和 Flyway 迁移：运行事实。
2. `docs/iot/onenet-thing-model.md`、`docs/architecture/database-design.md`、`docs/api/api-frontend.md`、`docs/architecture/permission-design.md`：专题设计。
3. `docs/planning/requirements-baseline.md` → `p0-scope-baseline.md` → `business-model-baseline.md` → `system-architecture-draft.md` → `database-design-draft.md` → `interface-design-draft.md` → `detailed-design-draft.md` → `tasks/p0-controlled-loop/00-index.md`：判断目标实现、施工顺序和任务状态的正式上游链。
4. `hardware/docs/review/uart-protocol-audit.md`：最近 UART 现状审查。
5. 本文：跨会话决策、旧新差距和工作方式。
6. `~/.claude/projects/C--D-004-Project-002-Java-EcoBin/*.jsonl`：只有在上述资料无法回答时才回溯的原始会话证据。

### 7.1 应用部署与重新发布索引

- 后端、Web 或运行时镜像相关代码修改后，日常重新部署统一从
  [`应用修改后重新部署操作手册`](../deployment/application-redeployment-runbook.md) 开始。
  该文档负责判断是否需要打包、是否先升级数据库、哪些服务器控制脚本需要单独同步，
  并给出构建、上传、安装、预检、激活、验证和回退的完整顺序。
- 新服务器第一次部署、目标网络/目录、systemd、Nginx 和 Fake 首启使用
  [`目标单机部署手册`](../deployment/target-single-host-deployment.md)。
- `/etc/ecobin` 配置、秘密文件、微信支付公钥/商户证书和机构小程序配置使用
  [`生产部署配置、密钥与证书清单`](../deployment/production-configuration-secrets-certificates.md)。
- 目标数据库首次供应或新增迁移使用
  [`H-02 目标数据库手册`](../deployment/h02-target-database.md)；运行 JAR 不携带迁移器，
  数据库必须先到达新后端要求的 epoch 才能激活应用。
- 发布脚本的机器入口分别是
  [`New-EcobinLocalRelease.ps1`](../../tools/deployment/New-EcobinLocalRelease.ps1)、
  [`ecobin-install-local-release.sh`](../../tools/deployment/ecobin-install-local-release.sh) 和
  [`ecobin-production-preflight.sh`](../../tools/deployment/ecobin-production-preflight.sh)。

## 8. 建议的续作入口

DD-004、修订后的 PDD-001、29 项任务粒度/依赖、`status/executor` 分类和
2026-07-30 仅作风险排序均已确认。29 项任务已发布并同步 2026-07-24 修订到
[`p0-controlled-loop/00-index.md`](../planning/tasks/p0-controlled-loop/00-index.md)：H-01、
F-01、F-02、F-03、F-04、F-05、F-06、F-07、F-09、H-02 已完成；H-02 已通过本地
MySQL 8.4.10 开发演练和服务器整改阶段 0～3 验收，项目负责人接受当前试验期的
`.ecobin` ACL 受限明文保管例外，加密密码库及其异机密文副本延期到下一版本；
2026-08-04 又完成服务器安全更新和重启验收，并把仍为空业务库的目标数据库从 V10
精确重建到 V31（96 张领域表、77 条权限定义、其余业务数据 0）；当前代码已增加 V32～V64，
部署含当前纪元门禁的应用前必须现场核对服务器实际版本，并用 H-02 续跑模式完成前向升级到 V64（129 张领域表、76 条有效权限定义）；
F-10 的机器来源、通用三语言黄金样本和适配责任边界已完成，任务已转为 `done`；
F-08 已完成 Fake 可靠任务 tracer、MySQL 8.4
验收和两项 P1 复审；F-11 已按当前软件范围转为 `done`；
V-01 已完成身份/Web 纵切、六项 P1 复审修复和真实 MySQL 验收并转为 `done`；
V-02 已按项目负责人接受的受限微信环境边界转为 `done`，设备来源归因保留在非阻塞
P0-FOLLOWUP-01；H-03 的 F-11 依赖已经解除并转为 `ready`，V-09 的 V-02/F-08
依赖也已解除并转为 `ready`，两项均未获实施授权。
当前共 `done` 15、`ready` 2、`in-progress` 0、`blocked` 12。任务依赖解除没有自动
授予实施权限。

实施入口已经明确：

- 当前最终 8 模块物理边界已经完成，后续目标业务必须在该边界内通过公开
  `.api` 端口实现，不能恢复旧 system/business 大模块；
- V1～V31 已完成前向迁移和 MySQL 权威验证；V32～V51 保留既有身份、设备、可靠任务和资金状态演进；V52 增加设备自注册、OneNet 供应、厂家验收小程序、初始袋标签占用和按需远程维护；V53 增加投递自动审核、提现审核规则与自动提现决策；V54 增加自动审核金额阈值和订单快照；V55 增加 MCU 固件发布、灰度部署和进度事实；V56 增加封存授权与设备验收代次；V57 取消微信/设备外部时间与后端时间之间的数据库先后约束，增加显式时钟质量和授权展示包有效期；V58 显式拒绝封存空时钟质量并允许微信缺失渠道创建时间时继续收敛；V59 将单批袋码和批内序号上限扩大到 500；V60 增加验收证据及设备资产的 MCU 远程升级线路能力三态事实；V61 为可靠任务尝试增加可空的外部技术请求号诊断字段；V62 为出厂进度轮询增加按设备资产和任务类型定位可靠任务的非唯一索引；V63 增加业务发布声明、永久管理层与设备软件实际状态、管理架构代次和兼容性投影接收面；V64 增加业务发布生命周期、只追加审计和不下发的灰度计划；F-07 又完成固定 V1 marker，当前代码已推进为
  V64 epoch/readiness guard、最小 `ecobin_app` 空业务库启动和 Fake 外联硬阻断；
  并在两项 P1 补强后通过复审。2026-08-29 又完成全新腾讯云 CDB 拟生产目标空库的 V60
  供应和初始超级管理员创建；同日后续现场 Web/后端写入的现用测试库已升级并核对为 V60，
  但新 CDB 生产入口、旧数据导入、正式发布制品安装、COS 迁移和入口切换均未执行；
- F-09 HTTP/OpenAPI 客户端传输已完成复审并由项目负责人确认；
- V-01 身份/Web 纵切已完成复审并获准合入；V-02 已按项目负责人接受的个人主体和
  开发版环境边界转为 `done`，真实设备来源归因由非阻塞 P0-FOLLOWUP-01 跟踪；
  V-09 已解除任务依赖成为 `ready`，但尚未获得实施授权；
- F-11 的固定帧适配、照片/COS、MQTT 重连、强杀/COS 自动诊断、契约生成边界和能力
  降级文档已按当前范围完成；真实双摄已按 DECXIN 外部、icspring 内部完成拍照、上传
  和 URL 下载校验。H-03 后续在同一适配版本上执行固定帧线路、屏幕状态机和物理行为
  真机验收，发现 F-11 范围内问题时重开；
- 后续按投递及其审核与钱包、清运、满溢恢复、充值、提现和 operations 的纵向切片推进；
- MCU 固件、数据库环境、真实微信、真机验收和成对切换分别保持 HITL；
- 完整跨端契约 CI 和自动 HIL 发布门禁按 I-055 留到共同首版形成后的升级阶段，但人工门安全、断电恢复、真实资金和真机验收不能延期。

当前 OneNet MQTT 已联通，开发环境 STS/COS upload/head/delete smoke 和真实香橙派
双摄上传/匿名下载已通过；固定帧 MCU 适配已合入，传感器使用 F0/F1 自检和 CC 烟感
变化上报，不支持的 MCU 功能仍按“本地保存/明确失败/未知占位”降级。Python 3.11
硬件套件当前在 Python 3.11/Linux 为
`353 passed, 4 skipped, 5 subtests passed`，在 Python 3.11/Windows 为
`396 passed, 13 skipped, 5 subtests passed`；契约单元套件为 `59 passed`，生成/跨语言
校验为 `24 passed, 0 notes`。香橙派当时的默认路由/DNS 波动按
项目负责人决定暂不处理且不阻塞 F-11；微信支付/商家转账仍不可联调。真实条件或
软件链路缺失时只能标记相应软件阶段，不能宣称 M0。
