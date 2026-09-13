# S3：连续离线占用释放与原结果补报实施审计

日期：2026-09-13。

状态：**已确认需求的实现拆分；本项 S3 尚未开始实施，未部署。** 本文保存只读源码审计及主代理已认可的最小实现建议，不重新提出业务待确认项，不表示下述新增字段、释放服务或回归测试已经实现。

范围：只覆盖连续断网超过 600 秒后的用户/设备占用释放、原结果补报及防串单。S3 中“最终称重失败的异常订单接入”仍由[总实施计划](mcu-edge-simplified-implementation-plan-2026-09-13.md)单独推进，不在本文放宽其校验。

依据：[简化恢复讨论 D13 及其只读审计](../architecture/mcu-edge-simplified-recovery-discussion-2026-09-13.md)。该文 D13 从当前第 427 行开始，原只读审计从第 459 行开始；未找到独立命名为 `offline_ten_minute` 或 `remaining` 的专题审计文件。以下源码行号是本次审计快照，后续实施以同名方法和 SQL 常量定位为准。

## 1. 已确认规则与最小结论

“仅断网”的前提是单片机（MCU）控制通信与香橙派本地必要存储仍正常。香橙派继续处理原业务，可靠保存原结果并等待补报，不接离线新业务。

后台以**可信 OneNet 连接事件的首次离线确认时间**开始计时；连续离线严格超过 600 秒后，释放原用户和设备的业务占用。不能从业务开始、授权期限、结果等待期限或任意一次重复离线消息重新计时。

释放占用只表示“原业务不再阻止用户去使用其他正常设备”，不表示原业务失败、单片机已经停止、结果丢失或问题归档。原业务身份、用户归属、袋关系、原始数据、待补报结果继续保留。香橙派原物理工作槽也不能被定时器清空。

具体例子：用户 A 在设备甲投递，设备甲随后离线。超过 600 秒后，用户 A 可以使用在线设备乙；甲仍不能离线接单。甲重连后先补报原投递，订单仍属于用户 A，不得删除用户 A 在乙的新占用。甲自己的下一笔业务要等原结果处理及本地收尾结束后才开放。

最小实现为三个可空时间字段、一个后台定时释放入口、几处精确的占用校验例外及查询调整。复用原业务状态、可靠任务、最终结果去重，不新增另一套机械恢复或补报任务系统。

## 2. 当前源码基线，不是部署事实

本次直接检查得到：

- 迁移目录最高文件是 [V79__native_delivery_issue.sql](../../ecobin-bootstrap/src/main/resources/db/p0-migration/V79__native_delivery_issue.sql)，其次为 V78、V77。
- [P0DatabaseEpochPolicy](../../ecobin-bootstrap/src/main/java/org/enveloping/ecobin/bootstrap/database/epoch/P0DatabaseEpochPolicy.java) 第 20 行的 `MINIMUM_MIGRATION_VERSION` 为 `79`。
- [DatabaseEpochGuard](../../ecobin-bootstrap/src/main/java/org/enveloping/ecobin/bootstrap/database/epoch/DatabaseEpochGuard.java) 第 62 行使用 `P0_V1_TO_V79`。
- [运行账号权限清单](../../tools/database/h02-runtime-grants.psd1) 第 2 行 `CatalogVersion = 40`；[供应脚本](../../tools/database/provision-h02-target.ps1) 第 1394 行和[权限验证脚本](../../tools/database/tests/verify_h02_runtime_grants.ps1)仍校验 137 张领域表。

这些数字只描述当前仓库的完整目标基线。本次没有连接现场或共享数据库，没有验证现网迁移版本，也没有创建迁移文件。后续实施应领取当时实际下一个迁移号，不能在本文预占 V80，或修改已经存在的 V1～V79。

## 3. 现有纵向调用路径与缺口

### 3.1 可信连接事实和连续离线起点

现有入口：

`ReliableDeviceInboxWorkerService` 的平台连接事件分支（第 109～118 行）→ `TrustedDeviceTransportPresencePort.apply` → `TrustedDeviceTransportPresenceService.apply` → `merge`。

源码：

- [ReliableDeviceInboxWorkerService](../../ecobin-module-operations/src/main/java/org/enveloping/ecobin/operations/application/reliability/ReliableDeviceInboxWorkerService.java)。
- [TrustedDeviceTransportPresenceService](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/target/TrustedDeviceTransportPresenceService.java)：`apply` 第 35 行、`merge` 第 119 行、`newer` 第 192 行。
- [V24 连接事实表](../../ecobin-bootstrap/src/main/resources/db/p0-migration/V24__device_transport_presence_and_dispatch_gates.sql)：`dev_device_transport_state`。

该服务只接受可信来源的连接事实更新在线/离线状态；`observeOutboundOffline` 与 `observeAuthenticatedMessage` 不把下发错误或普通上行消息当作权威连接切换。现有 `newer` 已处理事件观测时间顺序以及同一观测时间的冲突，不应另写一套乱序规则。

实际缺口是：每次接受较新的 OFFLINE 消息都会更新 `status_received_at`。即使设备一直没上线，重复 OFFLINE 仍会延后这个时间，不能直接拿它计算连续离线。

建议在同一 `merge` 事务中维护新增的 `offline_since_at`：

| 被现有顺序规则接受的事件 | 起点处理 |
|---|---|
| 从非 OFFLINE 转为 OFFLINE | 写当前后台数据库接收时间 |
| 已经 OFFLINE，再次收到 OFFLINE | 保留首次起点，不刷新 |
| 转为 ONLINE | 清空起点，结束本次连续离线 |
| 旧事件被现有规则拒绝 | 不改起点和当前连接状态 |

计时使用后台数据库 UTC 时间，不信任香橙派本地时钟，也不把设备上报的业务开始时间当作断网时间。

### 3.2 用户占用与设备占用是两个实际判断

投递启动调用 [StartDeliveryDeviceParticipationService](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/startdelivery/StartDeliveryDeviceParticipationService.java) 的 `startWithinTransaction`（第 100 行附近），先查询同一机构用户的活跃会话，再检查永久资产、组织、在线事实、整机占用及配置。

[JdbcStartDeliveryDeviceRepository](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/startdelivery/JdbcStartDeliveryDeviceRepository.java) 中：

- `LOCK_ACTIVE_SESSION_SQL` 第 19 行读取 `dev_delivery_session`，状态为 `PREPARED/AUTHORIZATION_QUEUED/IN_PROGRESS/RESULT_PENDING_RECOVERY` 时阻止该用户再开始投递。
- `LOCK_TRANSPORT_PRESENCE_SQL` 第 70 行读取权威在线状态。
- `LOCK_OCCUPANCY_SQL` 第 77 行读取 `dev_device_occupancy`。

因此只删除 `dev_device_occupancy` 不会释放用户。需要使上述活跃会话查询排除 `offline_occupancy_released_at IS NOT NULL` 的原会话，同时保留原会话状态和归属。

[StartDeliveryDevicePolicy](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/startdelivery/StartDeliveryDevicePolicy.java) 的 `requireOnenetOnline`（第 64 行）和软件准入检查继续保留。离线设备即使占用已释放也不允许接单。

清运启动走 [StartCleanOperationService](../../ecobin-module-recycling/src/main/java/org/enveloping/ecobin/recycling/application/clean/StartCleanOperationService.java)：第 394 行附近检查在线事实，第 414 行检查整机占用；`requireNoConflictingPortWork` 第 670 行另查投口的清运、满溢检测和空袋基准任务。没有发现需要为清运另建一个全局“清运员占用表”的依据，原清运员身份继续保存在原操作中。

投递和清运启动都应增加一个相同含义的本设备检查：**只要该设备存在占用已释放但原业务仍未结束的待补报记录，就不发起新的本设备业务。** 这是旧结果收尾的业务准入条件，不是重新占住用户。仅凭占用行为空、旧软件准入投影仍为允许或设备刚上线，不能绕过该检查。

### 3.3 正常最终结果接收

现有可信事件调用链：

`ReliableDeviceInboxWorkerService` 第 208 行分发 `DELIVERY_COMPLETE` → recycling 的 `ApplyDeliveryCompleteService.apply` → device 的 `TrustedDeliveryCompletionService.complete` → recycling 的 `persistBusinessFacts`，在原可信事务中落设备事实及投递业务结果。

`CLEAN_COMPLETE` 在同一分发器第 212 行进入 recycling 的 `ApplyCleanCompleteService.apply`，处理清运事实、清运记录、换袋与基准。

投递源码：

- [TrustedDeliveryCompletionService](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/delivery/TrustedDeliveryCompletionService.java)：第 189 行先检查是否已经完成；第 202 行调用 `lockOccupancy`；第 552 行的方法要求原投递占用存在；第 341 行附近最终删除占用并要求恰好一行。
- [ApplyDeliveryCompleteService](../../ecobin-module-recycling/src/main/java/org/enveloping/ecobin/recycling/application/delivery/ApplyDeliveryCompleteService.java)：第 147 行核对当前袋，第 238 行 `requireCurrentBag` 保留原袋绑定与冻结袋身份约束；第 809 行 `capacityProjectionBlockReason` 处理容量投影条件。
- [DeliveryCompletionPersistenceFacts](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/api/result/DeliveryCompletionPersistenceFacts.java) 已携带原用户、原袋、原价格和原配置，不应改为查询当前占用用户来建单。

清运源码：

- [ApplyCleanCompleteService](../../ecobin-module-recycling/src/main/java/org/enveloping/ecobin/recycling/application/clean/ApplyCleanCompleteService.java)：第 158 行为已完成结果早返回；第 481 行 `lockDeviceOccupancy` 要求原清运占用；第 505 行 `lockBagOccupancies` 核对原旧袋绑定与新袋预留；第 1474 行 `releaseDeviceOccupancy` 要求删除恰好一行。
- 该类 `swapBags` 第 1193 行、`projectCapacity` 第 1339 行及第 1462 行清除清运重启检查记录，均是当前业务状态写入，不能被未识别的旧结果再次触发。
- `touchRuntimeAndClearPendingDelivery` 第 1494 行已通过原 pending session 身份有条件清空投口指针，保留这种精确匹配，不改为无条件清空。

本项需要修改的只是“原占用必须还存在/必须再删除一行”这一前提。完整最终包、来源、原业务编号、原开始命令、配置、重量和袋身份校验继续执行；不是因为断网释放就接收任意结果。

### 3.4 所有占用删除出口的准确例外

建议保持同一判断规则：

- 原业务未写离线释放标记：原有占用核对和删除一行要求不变。
- 原业务已写标记，原占用确已释放：允许按原业务编号执行删除时影响零行。
- 影响零行不是普遍成功；必须先核对该标记确实属于当前正在处理的原业务。
- 删除条件始终包含租户、机构、资产、占用类型及原 `delivery_session_id` 或 `clean_operation_id`，不能按用户或资产宽泛删除。
- 若遇到另一个业务的占用，不能把它当原占用。如果旧结果已经完成，则走现有重复结果早返回；若旧结果尚未完成而发生这种不应由正常准入产生的冲突，保留原包并进入既有冲突隔离路径，不自动写当前袋或清除别人的占用。

不能只修改正常完成器；还要覆盖以下真实出口：

| 出口 | 当前位置 | 修改边界 |
|---|---|---|
| 投递正常完成 | `TrustedDeliveryCompletionService` | 只对原会话释放标记允许占用缺失/精确删除零行 |
| 清运正常完成 | `ApplyCleanCompleteService` | 同上，原袋和新袋预留校验不放宽 |
| 投递真实未开门失败 | [ApplyDeliveryCommandObservationService](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/delivery/ApplyDeliveryCommandObservationService.java) 的 `endBeforeOpen`，第 198 行 | 不因之前已释放占用而回滚真实失败；原失败原因不变 |
| 清运真实未解锁失败 | [ApplyCleanCommandObservationService](../../ecobin-module-recycling/src/main/java/org/enveloping/ecobin/recycling/application/device/ApplyCleanCommandObservationService.java) 的 `endBeforeUnlock`，第 200 行 | 原设备占用可精确删除零行；只有此真实未解锁终止才按既有规则释放对应新袋预留 |
| 从未派发的投递授权过期 | [ExpireUnstartedDeliveryService](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/delivery/ExpireUnstartedDeliveryService.java) | 保留只处理命令仍排队、没有执行租约等原条件；删除零行例外同上 |
| 从未派发的清运授权过期 | [ExpireUnstartedCleanService](../../ecobin-module-recycling/src/main/java/org/enveloping/ecobin/recycling/application/device/ExpireUnstartedCleanService.java) | 同上；不得拿此类服务直接处理已经开始的断网业务 |

“600 秒先释放，稍后才收到真实未开门失败”必须能正常收口。否则这几处 `requireSingle` 会因旧占用已经删除而使整个真实失败事务回滚。

### 3.5 可靠任务和问题归档无需重造

[ReliableDeviceCommandCompletionService](../../ecobin-module-operations/src/main/java/org/enveloping/ecobin/operations/application/reliability/ReliableDeviceCommandCompletionService.java) 的证据等待超时通过 `projectBlockedCommand`（第 295 行）投影是否确定未接受；不能把它的超时期限当作连续断网 600 秒。

其“不知道结果”的分支可使原业务进入 `RESULT_PENDING_RECOVERY/RECOVERY_REQUIRED`，但不因此生成正常订单或把原开始命令强制写成物理失败。正常最终结果完成器本来允许核对这些未决业务。

[ReliableDeviceTaskProofService](../../ecobin-module-operations/src/main/java/org/enveloping/ecobin/operations/application/reliability/ReliableDeviceTaskProofService.java) 的 `completeFromTrustedProof`（第 83 行）已允许原任务从 `PENDING/BLOCKED` 变为 `DONE`。因此本项不需要另建补报任务、不取消原任务、不重放开始命令，也不让离线定时器伪造可信完成证明。

[NativeDeliveryIssueService](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/delivery/NativeDeliveryIssueService.java) 是问题归档入口，不是网络离线释放器。已经问题归档的旧业务，后来完整结果仍只能补证据，不能因为新增释放标记而恢复结算。单纯离线后取得可信正常结果的原业务仍按既有正常业务及审核规则处理，不因离线一律归档为无业务价值。

## 4. 释放事务与前向迁移建议

### 4.1 三个字段，不新增业务终态

| 表 | 建议字段 | 含义 |
|---|---|---|
| `dev_device_transport_state` | `offline_since_at DATETIME(3) NULL` | 当前连续离线的首次后台确认时间；ONLINE 时清空 |
| `dev_delivery_session` | `offline_occupancy_released_at DATETIME(3) NULL` | 原投递因连续离线释放用户/设备占用的时间；写后保留历史 |
| `rec_clean_operation` | `offline_occupancy_released_at DATETIME(3) NULL` | 原清运因连续离线释放设备占用的时间；不代表换袋完成 |

历史 OFFLINE 记录没有可靠首次起点时，迁移保守以迁移时的数据库时间起算，不猜测业务开始时间或最后消息时间就是首次离线时间。现有 ONLINE/UNKNOWN 记录保持空起点。

投递、清运的业务状态和结束时间不由释放器修改，因此不扩张它们的终态枚举或现有状态约束。清运 `active_port_id` 生成列及其唯一约束继续保留，原投口/袋关系不能因释放整机使用占用而失去保护。按真实查询计划选择离线时间扫描和用户/资产未决查询所需索引，不先添加大量猜测性索引。

### 4.2 后台独立定时执行，不依赖离线设备请求

候选扫描形状如下，实际锁定与范围校验必须在事务中重做：

```sql
WHERE onenet_connection_status = 'OFFLINE'
  AND offline_since_at < UTC_TIMESTAMP(3) - INTERVAL 600 SECOND
```

使用严格小于，因此正好 600 秒还不释放。定时扫描允许一个正常扫描周期的处理延迟，不需要每条离线事件创建长寿命内存计时器。

建议每次按单个资产执行短事务：

1. 锁定原永久资产和连接事实，重新核对仍 OFFLINE 且本次起点已超过 600 秒。
2. 通过业务所属模块锁定原投递会话或清运操作，确认仍属该资产、租户及机构，仍未结束且未标记释放。
3. 核对原设备占用仍准确指向该业务。
4. 写原业务释放时间并按原业务编号删除该占用；首次释放必须删除恰好一行。
5. 同事务提交。任一步冲突或存储失败应整体回滚，不能只删占用而没记释放依据。

已经释放的业务重复扫描不重写时间、不重复处理。原占用本来就缺失但没有标记的记录不能被扫描器补标成正常释放，避免掩盖其他数据问题。

候选扫描不持有长事务；正式写入按现有资产→业务→占用顺序协调，并与连接事件、完成器及开始服务做真实并发验证。投递开始服务先锁用户活跃会话的既有顺序也必须纳入验证，不能仅凭 SQL 文本宣称没有死锁。

跨模块继续使用 `.api` 同事务端口：device 管理连接与整机占用，recycling 管理清运操作/袋。必要时增加一个小型释放参与端口，不直接跨模块导入内部服务、传递无约束裸主键或在 device 新写清运表业务逻辑。不要用异步事件或 `REQUIRES_NEW` 把标记和删除拆成两次提交。

### 4.3 清运新袋预留绝对不由此定时器删除

`rec_bag_current_occupancy` 中的 `CLEAN_RESERVED` 是原清运新袋预留。断网时该袋可能已实际装入设备，后台还没收到结果。因此释放器不得删除它，不得把旧袋解除绑定，也不得自动建立新袋皮重。

真正的正常清运完成继续核对原旧袋和原新袋预留后换袋；真正未解锁的失败/过期才按其既有终止规则释放预留。若人工已经改变袋绑定，保留原结果并沿现有冲突处理，不通过离线例外重建当前袋。

### 4.4 迁移必须同时覆盖权限和版本门槛

除下一份前向迁移及必要索引外，还需同步：

- `h02-runtime-grants.psd1` 中三个表的 UPDATE 字段名单（当前分别在第 473、584、665 行附近），并推进权限清单版本。
- `P0DatabaseEpochPolicy`、`DatabaseEpochGuard`、[RuntimeSafetyConfigurationTest](../../ecobin-bootstrap/src/test/java/org/enveloping/ecobin/bootstrap/RuntimeSafetyConfigurationTest.java) 及数据库版本策略测试。
- H02 供应/恢复续跑脚本、`verify_h02_runtime_grants.ps1`、[verify-f07-bootstrap.ps1](../../tools/database/verify-f07-bootstrap.ps1) 的迁移范围、授权版本和正确/过旧数据库门槛。

本方案不新增表，领域表数量应仍为 137；若 S3 其他并行项另有表变化，由主代理统一核对，不能机械保留这个数字。不得通过给运行账号授予整表宽权限来省掉字段授权更新。

## 5. Pi、查询页面与防旧结果覆盖

[hardware/native_business_runtime.py](../../hardware/native_business_runtime.py) 的 `_check_start`（第 143 行）检查联网、健康及工作槽；`_work_poll`（第 411 行）继续查询原工作、保存并上报原结果。这里不应增加 600 秒直接清理原 `work_slot` 的逻辑。后台释放的是用户/设备使用占用，本地工作槽保留的是正在执行或尚未交接的真实业务身份。

[hardware/main.py](../../hardware/main.py) 的云端断开回调通知待上报队列断开，不应改为自动失败归档；重连优先处理原结果。

[hardware/native_business_completion.py](../../hardware/native_business_completion.py) 第 167 行对已应用完成标记早返回；第 178 行按原 `work_uid/work_type/port_no` 精确释放工作槽。在完成确认已经处理、设备又开始下一笔之后，重放旧确认不得清空新槽或覆盖新皮重。这些保护已有代码，本项主要补齐组合回归。

后端同样保留已完成结果早返回，以及原事件/业务去重。只调整“原占用已由离线规则释放”的准确例外，不放宽完整包或袋身份。正常流程通过本设备待补报准入检查避免新业务抢先；异常的其他业务占用或人工袋变更不得触发旧结果写当前投影。

查询至少应增加释放时间或等价明确标志，显示“占用已释放，原结果待补报”，不能继续只提示用户在原设备等待，也不能显示为已失败/已完成：

- [OwnedDeliverySessionSnapshot](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/api/result/OwnedDeliverySessionSnapshot.java)。
- [JdbcMiniappDeliveryDeviceQueryRepository](../../ecobin-module-device/src/main/java/org/enveloping/ecobin/device/application/deliveryquery/JdbcMiniappDeliveryDeviceQueryRepository.java) 的 `FIND_OWNED_SESSION_SQL`（第 167 行）。
- [MiniappDeliveryQueryService](../../ecobin-module-recycling/src/main/java/org/enveloping/ecobin/recycling/application/deliveryquery/MiniappDeliveryQueryService.java) 的 `presentSession`（第 214 行）。
- [CleanQueryService](../../ecobin-module-recycling/src/main/java/org/enveloping/ecobin/recycling/application/clean/CleanQueryService.java) 的原操作查询及 `operationView`（第 397 行）。

这些查询仍按原用户/机构归属读取，不需要把原结果转交给当前正在使用设备的用户。页面的最终文案/布局可随 S4 完成，但 S3 的服务端查询必须能准确表达待补报与已释放的区别。

## 6. 最小独立测试矩阵

以下均是**待实施的验收项，不是已经通过的测试结果**。关键迁移、事务、锁和真实结果接收使用隔离 MySQL 8.4，不能以 H2 或只匹配 SQL 字符串替代。

| 编号 | 场景 | 必须证明 |
|---|---|---|
| T01 | 连续离线 599 秒、600 秒、600 秒之后 | 前两者不释放，严格超过阈值才释放；不是从业务开始计算 |
| T02 | 连续多次 OFFLINE | 首次起点及首次释放时间不被重复事件/扫描刷新 |
| T03 | ONLINE 后再次 OFFLINE，夹杂乱序旧事件 | 新连续离线重新计时；旧事件不回退当前状态或误释放 |
| T04 | 普通消息、下发错误、错误来源、跨租户/机构 | 均不能代替可信连接事实启动计时或释放别人业务 |
| T05 | 正常投递/清运超过阈值 | 原用户/设备使用占用释放；业务状态、归属、数据、任务和袋关系保留 |
| T06 | 用户 A 转用设备乙，原设备甲离线及重连 | A 不被旧会话阻挡；甲离线拒单，重连未补报完仍拒新业务 |
| T07 | 原投递结果迟到，原可靠任务已等待超时 BLOCKED | 正常核对、归原用户、只建一次业务结果，原任务可由可信证明完成 |
| T08 | 原清运结果迟到 | 原新袋仍预留；按原袋身份正常完成一次换袋/基准/记录，无凭空补袋 |
| T09 | 完成过的旧包/确认在新业务、新袋出现后重复 | 后端与 Pi 都早返回，不删除新占用、不清新槽、不重写新袋或皮重 |
| T10 | 原占用缺失但没有释放标记，或出现不同业务占用 | 不把删除零行普遍当成功；保留冲突原包，不删别人占用、不自动写当前袋 |
| T11 | 已释放后才收到真实未开门/未解锁失败 | 原业务正确结束，不因旧占用已经删除而回滚；清运预留只按真实未解锁证据处理 |
| T12 | 已释放后命中从未派发授权过期 | 保持未派发判定；精确删除零行可收口，不误取消已开始业务 |
| T13 | 原投递已经问题归档，随后迟到完整结果 | 只补原问题证据；不恢复结算、不增加余额、不触发自动提现 |
| T14 | 释放与 ONLINE、正常结果、开始请求并发 | 事务按锁后事实收敛；不半释放、不误阻止其他设备、不跨业务删除；实际验证锁顺序 |
| T15 | 标记写入后、删除前模拟异常；重复执行 | 整体回滚或一次提交；没有仅删占用/仅记标记的中间事实 |
| T16 | V1 到新目标完整迁移、最小运行账号 | 新字段可用且授权足够；旧目标被版本门槛拒绝，无整表宽授权绕过 |
| T17 | Pi 离线完成持久保存，重连补报、确认、下一笔 | 原业务编号/最终包/待上报记录保留；确认前不开放本设备新业务，确认重放不串单 |

测试可拆成三个独立组：连接/释放计时与迁移组；真实投递/清运完成和失败出口组；Pi/查询状态与重放组。共享迁移、字段名及释放例外由主代理统一集成，避免每个完成器自行定义不同规则。

Java 必须使用 21。修改跨模块代码后，运行 bootstrap 集成测试前先执行 `./mvnw install -DskipTests`（Windows 为 `.\mvnw.cmd install -DskipTests`），确保不读取本地仓库旧模块。MySQL 测试使用本机回环、独立临时库和模拟身份，不连接共享库或线上；执行与清理范围另行记录，不能把本次只读审计写成真实数据库验证。

## 7. 本次记录与后续入口

本次仅完成源码只读审计及本文保存，检查了上述源码引用、实际迁移上限和权限清单版本。没有启动 S3 代码实施，没有执行新迁移、真实 MySQL 测试、部署、SSH、串口、GPIO 或固件烧录。

文档按文档协作技能区分了已确认业务规则、源码现状、建议修改和待验证证据；业务决定已经明确，因此不再向用户重复询问。总实施计划的链接和后续任务安排由主代理统一维护。
