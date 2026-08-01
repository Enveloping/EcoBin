# EcoBin P0 目标接口设计：运营治理与最小统计（I-036～I-040）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-036～I-040 已确认**
>
> 说明：本文件定义平台技术处置、不可变操作审计、聚合告警、每日资金对账和最小运营概览的 P0 契约。完整投诉工单、外部告警通知、复杂经营报表、官方账单文件归档和财务导出继续留到 M1。

## 本章统一边界

1. 业务事实、操作审计、技术执行记录、聚合告警、对账问题和统计读模型是不同资源：
   - 订单、钱包、机构额度及微信观察解释真实业务结果；
   - 审计解释谁以什么入口执行了什么动作，以及系统是否接受；
   - 任务与尝试解释技术执行和重试；
   - 告警只表达当前需要关注的问题；
   - 对账问题解释为什么现有事实暂时无法一致；
   - 统计只汇总权威事实，不形成第二套业务真相。
2. operations 表已从普通 MyBatis 租户拦截中排除，因此每个查询和命令都必须由 operations 应用端口显式校验 `PLATFORM/TENANT/ORGANIZATION/UNRESOLVED` 作用域。不能以“查到该行”为已授权，也不能把无作用域记录挂到默认租户。
3. 平台技术任务和隔离消息只开放平台管理员。租户与机构人员只能通过各业务页面、告警和本机构对账问题执行本章明确允许的动作，不能直接操纵任务租约、消息正文或系统商户级对账运行。
4. 本章所有人机写操作继续要求 UUIDv4 `Idempotency-Key`。依赖当前投影的确认、恢复和对账处置必须携带 `expectedVersion`；同键同摘要返回原结果，同键异摘要冲突。
5. 追加时间线使用不透明稳定游标；任务、告警和对账问题等可变运营投影使用 `page/pageSize/total`。当前投影可能因后台恢复而移动到其他页，客户端完成动作后应重新加载当前页，不能把页码当作历史快照。
6. 所有响应只返回脱敏诊断摘要、稳定公开号和白名单导航键。原始外部报文、完整规范载荷、签名、Token、OpenID、密钥、带凭证 URL、异常栈和 SQL 不进入普通响应、导出、日志或审计摘要。
7. operations 查询及最小统计响应统一使用 `Cache-Control: no-store`。P0 不提供审计、技术任务、隔离消息、告警、对账问题或最小概览的 CSV/Excel 导出。
8. 工作人员小程序仍是只读精简管理端：本章只开放当前机构告警和最小概览，不开放告警确认、任务恢复、对账处置或跨机构切换。

## 路径、权限与客户端边界

### 路径基准

```text
普通 Web：      /api/v1/web
平台 Web：      /api/v1/web/platform
工作人员小程序：/api/v1/miniapp-staff
```

- 普通 Web 查询从当前工作人员会话取得租户，并把结果与实时授权机构求交；请求体不能自报租户。
- 平台 Web 使用显式平台路径。平台若处理租户或机构记录，目标作用域来自资源本身或路径中的稳定 `tenantCode/organizationCode`，不能把平台管理员伪装成租户员工。
- 工作人员小程序会话固定当前 `organization_miniapp_id + organization_id`，本章接口不接收机构切换参数。

### 新增稳定能力码

| 能力码 | 作用域 | 能力边界 |
|---|---|---|
| `audit.read` | `TENANT/ORGANIZATION` | 查询授权范围内的脱敏操作审计；不包含平台安全字段或任何审计写操作 |
| `alert.read` | `TENANT/ORGANIZATION` | 查询授权范围内聚合告警；不包含确认或来源业务恢复 |
| `alert.acknowledge` | `TENANT/ORGANIZATION` | 在 Web 中确认已看到授权范围内告警；不包含解决告警 |
| `reconciliation.read` | `TENANT/ORGANIZATION` | 查询授权范围内对账问题及其追加动作；不包含系统商户级运行 |
| `reconciliation.handle` | `TENANT/ORGANIZATION` | 对授权范围内问题追加备注、标记已处理、请求原单查证或受控重试 |
| `statistics.read` | `TENANT/ORGANIZATION` | 查询本章固定的安全聚合指标；不隐含读取各指标的明细资源 |

- 六项能力互不隐含，也不由 `fund.read`、`device.read`、`delivery.read` 等明细能力自动推导。
- 租户主体账号天然拥有本租户全部能力；机构负责人天然拥有本机构全部能力。普通工作人员按现有租户级或机构级授权取得。
- 平台任务恢复、隔离确认、平台对账运行读取和平台级问题处置属于平台固定用例，不定义成可下放的租户权限码。
- `aud=miniapp-staff` 的 P0 渠道白名单只允许当前机构的 `alert.read` 和 `statistics.read`；即使工作人员在 Web 具有其他本章能力，小程序也不能调用对应写接口。

## I-036 平台技术任务与异常消息处置

**已确认：平台只能恢复原可靠任务或确认隔离项已经看过；不能重建任务、直接重放隔离消息、编辑外部证据或借技术页面修改业务终态。**

### 1. 可靠任务查询

```http
GET /api/v1/web/platform/operations/reliable-tasks
GET /api/v1/web/platform/operations/reliable-tasks/{taskUid}
GET /api/v1/web/platform/operations/reliable-tasks/{taskUid}/attempts
```

任务列表使用普通管理分页，支持：

```text
state=PENDING|DONE|CANCELLED|BLOCKED
executionLane=DEVICE|FUNDS
taskKind=BUSINESS_INTENT|INBOX_PROCESSING|TIMER|RECONCILIATION
taskType
targetType
targetKey
createdFrom / createdTo
page / pageSize
```

详情至少返回：

- `taskUid/taskType/taskKind/executionLane/state/version`；
- 脱敏目标类型和稳定业务键、作用域安全摘要；
- `nextRunAt`、当前租约是否存在及截止时间，不返回 worker 内部凭证；
- 自动尝试上限、尝试次数、连续失败次数、`wakeVersion/handledWakeVersion`；
- 最近阻断时间和安全原因、关联/因果 ID；
- 当前业务资源的白名单导航引用和 `nextActions`。

尝试时间线按 `attemptNo DESC` 使用不透明游标，返回动作种类、技术结果分类、领取/外调可能开始/结果时间、HTTP 状态、外部 API 错误码和限长诊断摘要。它不返回请求/响应原文，也不能把一次尝试的 `TECHNICAL_SUCCESS` 表述为订单、设备或微信业务成功。

### 2. 恢复 `BLOCKED` 原任务

```http
POST /api/v1/web/platform/operations/reliable-tasks/{taskUid}/resumptions
Idempotency-Key: <UUIDv4>
```

```json
{
  "expectedVersion": 7,
  "causeFixedConfirmed": true,
  "reason": null
}
```

仅当任务当前为 `BLOCKED`、没有活动租约、处理器仍受支持且目标稳定身份仍可安全解释时允许恢复。成功事务：

1. 保留原 `taskUid/taskKey`、作用域、目标、请求快照、外部单号和全部尝试历史；
2. 增加唤醒代际，把原任务改回可领取 `PENDING`，安排立即重新核查；
3. 写一条平台操作审计；既有技术告警保持独立，不能因人工恢复而确认或解决；
4. 不修改订单、设备、钱包、机构额度、微信状态或来源 inbox。

返回 `202`，至少包含：

```json
{
  "operationId": "6dc95c4b-...",
  "resourceId": "9d894c77-...",
  "taskUid": "9d894c77-...",
  "state": "PENDING",
  "version": 8,
  "statusUrl": "/api/v1/web/platform/operations/reliable-tasks/9d894c77-...",
  "recommendedPollAfterMs": 2000
}
```

`causeFixedConfirmed=false` 直接拒绝且不改变任务。`PENDING` 由系统自动重试，`DONE/CANCELLED` 已经收敛或取消，P0 均不提供人工“重新排队”旁路。恢复后执行器仍须重新校验当前业务状态和安全闸门；人工确认不能保证下一次执行成功。

### 3. 隔离消息查询与确认

```http
GET  /api/v1/web/platform/operations/message-quarantines
GET  /api/v1/web/platform/operations/message-quarantines/{quarantineUid}
POST /api/v1/web/platform/operations/message-quarantines/{quarantineUid}/acknowledgements
```

列表支持 `state`、`reasonCode`、`sourceNamespace`、`firstDetectedFrom/To` 和分页。详情只返回：

- `quarantineUid/state/version/reasonCode`；
- 可空的可信作用域摘要、来源命名空间和脱敏来源主体；
- 可空外部消息 ID 摘要、原始/规范内容摘要；
- 首次/最近发现时间、发现次数；
- 可空冲突 inbox 的安全导航引用；
- 严格限长的脱敏诊断载荷。

确认请求为：

```json
{
  "expectedVersion": 2,
  "reason": null
}
```

成功只将 `OPEN` 推进为 `ACKNOWLEDGED` 并写审计，不创建 `PROCESS_INBOX`、不修改冲突 inbox、不猜测租户/用户/设备、不创建订单，也不重放消息。以后确需恢复永久隔离消息时必须另行设计受验证的修复用例，不能复用本确认端点。

主要错误包括：

```text
404 RESOURCE.NOT_FOUND
409 OPERATIONS.TASK_VERSION_CONFLICT
409 OPERATIONS.TASK_STATE_CONFLICT
409 OPERATIONS.QUARANTINE_VERSION_CONFLICT
409 OPERATIONS.QUARANTINE_ALREADY_ACKNOWLEDGED
422 OPERATIONS.CAUSE_FIX_NOT_CONFIRMED
422 OPERATIONS.TASK_RESUMPTION_UNSAFE
```

## I-037 不可变操作审计查询

**已确认：审计接口只读且按记录固化作用域重新授权；它解释动作请求，不替代订单修订、资金明细、设备证据或微信渠道结果。**

### 1. Web 查询端点

```http
GET /api/v1/web/audit-logs
GET /api/v1/web/audit-logs/{auditUid}

GET /api/v1/web/platform/audit-logs
GET /api/v1/web/platform/audit-logs/{auditUid}
```

普通 Web 需要 `audit.read`，结果与当前租户及实时授权机构求交；机构级能力看不到同租户其他机构或纯租户级审计。平台端点可查看 `PLATFORM/TENANT/ORGANIZATION/UNRESOLVED`，普通端点永远不返回 `PLATFORM/UNRESOLVED`。

列表支持：

```text
scopeKind
organizationCode
actorKind
actorUid
actionCode
result=SUCCEEDED|DENIED|FAILED
targetType
targetKey
requestUid
operationUid
occurredFrom / occurredTo
cursor / limit
```

默认按 `occurredAt DESC + auditUid DESC`；游标绑定全部筛选条件。`limit` 默认 20、最高 100。时间范围左闭右开。

### 2. 返回字段与可见性

租户与机构安全视图至少包含：

- `auditUid/requestUid/operationUid`；
- 固化作用域及可见的 `tenantCode/organizationCode`；
- `actorKind`、操作者公开 UID 和非敏感展示快照；
- 稳定 `actionCode`、主要目标 `type + stableKey`；
- `entryChannel=WEB|MINIAPP_MANAGEMENT|SYSTEM_TASK|SECURITY_ENTRY`；
- `result=SUCCEEDED|DENIED|FAILED`；
- 可空原因和脱敏 `safeChangeSummary`；
- `correlationId/causationId/occurredAt`。

平台详情可以额外返回脱敏 IP、User-Agent 摘要、会话 UID 和安全入口信息；这些字段不进入普通租户响应。任何视图都不返回密码、完整手机号、OpenID、AppSecret、APIv3 密钥、设备密钥、请求正文、通知明文或原始 Header。

### 3. 语义限制

- `SUCCEEDED` 表示本地敏感动作或异步意图已经被系统可靠接受，不保证后续门动作、微信转账、查单或任务执行成功。
- `DENIED/FAILED` 是独立请求尝试，不占用成功业务操作槽；它们不能证明业务对象失败，也不能作为重放输入。
- 具有业务明细读取能力但没有 `audit.read` 的人员，仍可在原业务详情看到该领域明确允许的修订/审核事实；不能借此遍历统一审计目录。
- P0 不提供创建、编辑、删除、补写、重新归属或导出审计的接口。审计详情中的目标引用只用于白名单导航，不能成为通用跨表更新入口。

主要错误包括：

```text
400 COMMON.INVALID_CURSOR
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
```

## I-038 聚合告警查询与人工确认

**已确认：一次持续问题只对应一条活动告警；工作人员确认与来源真实恢复严格分离，小程序只读。**

### 1. Web 与工作人员小程序端点

```http
GET  /api/v1/web/alerts
GET  /api/v1/web/alerts/{alertUid}
POST /api/v1/web/alerts/{alertUid}/acknowledgements

GET  /api/v1/web/platform/alerts
GET  /api/v1/web/platform/alerts/{alertUid}
POST /api/v1/web/platform/alerts/{alertUid}/acknowledgements

GET /api/v1/miniapp-staff/alerts
GET /api/v1/miniapp-staff/alerts/{alertUid}
```

普通 Web 分别要求 `alert.read` 或 `alert.acknowledge`；平台路径使用平台固定能力。工作人员小程序只在当前机构和 `alert.read` 渠道白名单内返回精简视图，没有确认端点。

列表使用管理分页，支持：

```text
state=OPEN|RESOLVED
acknowledgementState=UNACKNOWLEDGED|ACKNOWLEDGED
severity=INFO|WARNING|CRITICAL
category=DEVICE|FULLNESS|FUNDS|RECONCILIATION|RELIABLE_EXECUTION|SECURITY
alertCode
organizationCode
firstDetectedFrom / firstDetectedTo
page / pageSize
```

默认排序为活动告警优先、当前严重级降序、最近发现时间降序、`alertUid` 降序。平台或无法解析作用域的安全/系统商户告警只在平台端点出现。

### 2. 告警安全视图

Web 详情至少返回：

- `alertUid/state/version/category/alertCode`；
- 当前严重级、历史最高严重级；
- 固化作用域和安全目标摘要；
- `sourceKind/sourceType/sourceStableKey` 白名单导航引用；
- 首次/最近发现时间、发现次数；
- 是否已确认、确认人安全摘要和确认时间；
- 可空恢复时间；
- 脱敏展示参数和按来源计算的 `nextActions`。

小程序精简视图只返回当前机构理解问题所需的标题、严重级、设备/投口或资金业务短标识、首次/最近发现时间、持续时间、状态和安全导航类型；不返回任务尝试、渠道错误原文、安全来源、其他机构信息或平台内部诊断。

### 3. 人工确认

```json
{
  "expectedVersion": 5,
  "reason": null
}
```

成功事务只写一条 `SUCCEEDED` 审计并把当前告警标记为已确认。它不会：

- 恢复设备或清除严重安全锁；
- 清除满溢、容量检测 gate 或投递结果待处理；
- 打开平台出款闸门；
- 释放提现冻结或补资金；
- 解决对账问题；
- 停止来源事件的持续时间计算。

相同持续事件继续发生时更新同一告警的最近发现时间、次数和严重级；已经确认不会因普通重复发现自动变回未确认。只有来源领域先提交真实恢复事实，或技术检测器重新证明条件消失，operations 才把告警推进为 `RESOLVED`。解决后的同类新事件创建新 `alertUid`。

P0 不提供批量确认、人工解决、重新打开、删除、订阅人配置、微信通知、短信/电话通知或 10/30 分钟、24/48 小时外部升级接口。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 ALERT.VERSION_CONFLICT
409 ALERT.ALREADY_ACKNOWLEDGED
409 ALERT.STATE_CONFLICT
```

## I-039 每日资金对账与受控处置

**已确认：P0 每日对账是 EcoBin 对自身交易、渠道观察和双侧账本进行逐交易收敛，不是完整微信账单文件归档；明确遗漏复用原领域事务补齐，矛盾或证据不足才形成问题。**

### 1. 平台对账运行

```http
GET /api/v1/web/platform/reconciliation-runs
GET /api/v1/web/platform/reconciliation-runs/{runUid}
```

运行列表仅平台可见，按 `channelBusinessDate DESC + runUid DESC` 使用稳定游标，支持 `state`、`businessDateFrom/To`、`cursor/limit`。系统为每个系统商户和已结束的北京时间业务日建立唯一运行；服务恢复时自动补建遗漏日期，P0 不提供人工创建第二轮运行的端点。

运行状态固定为：

```text
CREATED
RUNNING
COMPLETED
```

详情至少返回：

- `runUid/runType=WECHAT_DAILY_TRADE/reconciliationMode=INTERNAL_TRANSACTION_CONVERGENCE`；
- 系统商户安全标识、`channelBusinessDate`、UTC `snapshotCutoffAt`；
- 状态、版本、开始/最近进度/完成时间；
- 已检查充值数、已检查提现数、确定性自动修复数、完成时发现问题数；
- 协调任务 UID、安全任务状态和关联告警；
- `nextActions`。

`COMPLETED` 只表示该截止点的扫描已经完成，不表示问题数为零。执行任务暂时失败时运行保持 `CREATED/RUNNING` 并由原任务恢复；达到自动尝试上限后由 I-036 恢复同一任务，不能再创建同日期运行。

### 2. 对账问题查询

```http
GET /api/v1/web/reconciliation-issues
GET /api/v1/web/reconciliation-issues/{issueUid}
GET /api/v1/web/reconciliation-issues/{issueUid}/actions

GET /api/v1/web/platform/reconciliation-issues
GET /api/v1/web/platform/reconciliation-issues/{issueUid}
GET /api/v1/web/platform/reconciliation-issues/{issueUid}/actions
```

普通 Web 需要 `reconciliation.read`，只返回当前租户及授权机构问题；平台可查看平台、租户、机构和未解析问题。列表使用管理分页，支持：

```text
state=UNRESOLVED|RESOLVED
handled=true|false
severity
issueCode
subjectType
subjectKey
organizationCode
firstDetectedFrom / firstDetectedTo
page / pageSize
```

详情返回初始不可变证据摘要、当前安全摘要、首次/最近发现运行、发现次数、最近人工处理时间、系统验证解决时间、关联告警、业务目标白名单导航和允许动作。动作时间线只追加，按 `occurredAt DESC + actionUid DESC` 使用游标。

### 3. 人工处置动作

普通与平台问题路径分别提供：

```http
POST {issueBase}/{issueUid}/notes
POST {issueBase}/{issueUid}/handled-marks
POST {issueBase}/{issueUid}/channel-queries
POST {issueBase}/{issueUid}/controlled-retries
```

其中 `{issueBase}` 分别为：

```text
/api/v1/web/reconciliation-issues
/api/v1/web/platform/reconciliation-issues
```

所有动作需要 `reconciliation.handle` 或平台固定能力，并携带 `expectedVersion`。备注和标记已处理同步创建只追加 action 与审计；查单和受控重试返回 `202`，创建或唤醒原稳定任务。四类请求分别为：

`POST .../notes`：

```json
{
  "expectedVersion": 4,
  "note": "已核对机构反馈，等待渠道结果"
}
```

`POST .../handled-marks`：

```json
{
  "expectedVersion": 4,
  "note": null
}
```

`POST .../channel-queries`：

```json
{
  "expectedVersion": 4,
  "note": null
}
```

`POST .../controlled-retries`：

```json
{
  "expectedVersion": 4,
  "causeChecked": true,
  "note": null
}
```

合法边界为：

- `NOTE_ADDED`：追加脱敏备注，不改变问题状态；
- `MARK_HANDLED`：记录人员已经完成当前核查，不等于事实一致；
- `CHANNEL_QUERY_REQUESTED`：仅对已有固定微信原单且仍在相应官方查询边界内的目标，复用原单号请求查证；
- `CONTROLLED_RETRY_REQUESTED`：仅当注册的原领域恢复阶段仍完整、没有单侧资金结果或相反终态时，唤醒原幂等任务；`causeChecked=true` 只用于明确确认阻塞原因已经排除。

以上人工动作只允许作用于 `UNRESOLVED` 问题；`RESOLVED` 问题只读。不存在人工 `RESOLVE`。只有系统重新读取权威订单、渠道观察、双方明细及账户投影并证明一致，才能追加 `RESOLUTION_VERIFIED`、把问题改为 `RESOLVED` 并关闭其告警。已经解决的问题不重新打开；同一差异以后复发时创建新的问题身份。

### 4. 自动补齐与明确排除

运行发现下列确定性遗漏时，可以不先创建问题，直接调用原领域事务：

- 充值已可靠处于 `PAID_PENDING_POST`，唯一净额入账明细尚不存在；
- 提现已有单一可信微信终态，活动占位与用户/机构双侧冻结完整，且双方该阶段 `FINAL` 明细均不存在。

补齐必须一次性形成原业务应有的全部事实，并追加 `AUTO_REPAIR_APPLIED` action。出现单侧明细、投影矛盾、相反终态、未知微信状态、金额/身份不一致或证据不足时，停止自动资金变更并建立问题。

P0 不提供以下能力：

- 修改资金明细、提现金额、收款人、AppID/OpenID 或微信终态；
- 直接人工增加/扣减机构额度；
- 将“已处理”直接改成“已解决”；
- 读取微信基本账户或运营账户实时余额；
- 下载、解析、长期归档微信交易账单/资金账单文件；
- 完整财务导出、电子回单归档或自动补资恢复。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 RECONCILIATION.ISSUE_VERSION_CONFLICT
409 RECONCILIATION.ISSUE_STATE_CONFLICT
422 RECONCILIATION.ACTION_NOT_ALLOWED
422 RECONCILIATION.CAUSE_CHECK_NOT_CONFIRMED
422 RECONCILIATION.CHANNEL_QUERY_UNAVAILABLE
422 RECONCILIATION.CONTROLLED_RETRY_UNSAFE
```

## I-040 最小运营概览

**已确认：P0 只提供可追溯的当前机构轻量概览和总部跨机构汇总，不建立排行榜、任意维度报表或异步累计余额。**

### 1. 端点与查询范围

```http
GET /api/v1/web/statistics/operational-overview
GET /api/v1/web/platform/tenants/{tenantCode}/statistics/operational-overview
GET /api/v1/miniapp-staff/statistics/operational-overview
```

查询参数：

```text
businessDateFrom=YYYY-MM-DD
businessDateToExclusive=YYYY-MM-DD
organizationCode={optional, Web only}
```

- 日期按 `Asia/Shanghai` 日历解释，范围左闭右开，最长 31 天；允许包含尚未结束的当天，不允许未来起始日。
- 普通 Web 省略 `organizationCode` 时汇总当前全部授权机构并返回逐机构明细；指定机构时必须在当前可见范围，否则返回 `404`。
- 平台路径固定一个目标租户，可选指定该租户机构；P0 不提供跨租户排行榜或全平台经营汇总。
- 工作人员小程序固定当前机构，不接收 `organizationCode`，并继续校验 `statistics.read` 的实时权限和客户端渠道白名单。

### 2. 时间段流量与当前快照分离

响应显式分为两类：

1. **期间流量**：按业务发生时间落入请求日期范围；
2. **当前快照**：在响应 `asOf` 时点的待办、设备、告警和资金投影，不受日期范围裁剪。

示例：

```json
{
  "period": {
    "timeZone": "Asia/Shanghai",
    "businessDateFrom": "2026-07-23",
    "businessDateToExclusive": "2026-07-24"
  },
  "asOf": "2026-07-23T11:20:30.123Z",
  "scope": {
    "tenantCode": "ecobin",
    "organizationCode": null,
    "organizationCount": 2
  },
  "totals": {
    "registrations": {
      "registeredUserCount": 120,
      "directEntryCount": 20,
      "deviceAttributedCount": 100
    },
    "delivery": {
      "createdOrderCount": 86,
      "recognizedOrderCount": 70,
      "recognizedWeightKg": "352.40",
      "recognizedCashbackYuan": "281.92",
      "currentPendingReviewCount": 16
    },
    "cleaning": {
      "createdRecordCount": 4,
      "anomalousRecordCount": 1
    },
    "operations": {
      "currentOnlineDeploymentCount": 8,
      "currentFullPortCount": 2,
      "currentOpenAlertCount": 3
    },
    "funds": {
      "succeededWithdrawalYuan": "210.00",
      "currentProcessingWithdrawalYuan": "18.20",
      "currentAvailablePayoutYuan": "9765.80"
    }
  },
  "organizations": [
    {
      "organizationCode": "org-a",
      "organizationName": "A省部门",
      "metrics": {},
      "registrationAttribution": {
        "directEntryCount": 8,
        "byDeployment": [
          {
            "deploymentCode": "DEP-A-001",
            "deploymentName": "A站点一号机",
            "registeredUserCount": 62
          }
        ]
      }
    }
  ]
}
```

`organizations[].metrics` 与 `totals` 使用相同结构；示例省略重复字段只是为了可读性，真实响应不能返回一个含义不明的空对象。

### 3. 固定统计口径

| 指标 | P0 口径 |
|---|---|
| 注册人数 | `registeredAt` 落入期间的机构用户数；首次 `wx.login` 创建即计入，包括未绑手机号用户 |
| 直接进入 | 上述注册中 `registeredViaDeployment` 为空；不回填到以后扫码设备 |
| 设备归因 | 上述注册按首次创建时固化的部署分组；设备迁址或调拨不改写历史归因 |
| 投递单数 | `deviceOccurredAt` 落入期间且已创建的全部投递订单，包括尚待审核和无主技术订单 |
| 已认定订单/重量/返现金额 | 期间订单在 `asOf` 时已经有当前最终认定且具备有效用户经营归属；重量和金额按当前最终认定带符号求和，无主技术订单不计入有效重量和经营金额 |
| 当前待审投递 | `asOf` 时当前授权机构全部待首次审核订单数，不按期间裁剪 |
| 清运记录数 | 清运物理发生时间落入期间的已创建记录数 |
| 异常清运记录数 | 清运物理发生时间落入期间，且保存至少一个清运系统异常的已完成记录数；异常不生成审核待办，后来修改当前有效重量不删除原异常 |
| 当前在线设备 | 当前有效部署的边缘在线投影为 `ONLINE` 的数量，不把最近历史心跳自行推导成在线 |
| 当前满溢投口 | `asOf` 时存在活动满溢事件的投口数；疑似检测中与故障不计成“已满” |
| 当前活动告警 | `asOf` 时当前授权机构全部 `OPEN` 告警数 |
| 成功提现金额 | 微信成功终态时间落入期间的提现金额之和 |
| 当前提现处理中金额 | 机构出款账户当前冻结提现额度；跨机构汇总只求和，不调拨 |
| 当前可出款额度 | 机构出款账户当前可用额度；不等于微信运营账户余额 |

订单在以后被不限期纠错时，历史期间的“已认定重量/返现金额”会按新的当前最终认定变化。响应必须携带 `asOf`，界面不得把它描述为已冻结财务报表。需要不可变历史截面、排行榜或结算凭证时进入 M1 另行设计。

### 4. 一致性和实现边界

- operations 为本端点显式开启一个只读 `REPEATABLE READ` 事务，并在首次一致性读时建立快照；identity、device、recycling、funds 和 operations 自身的公开查询端口都以 `REQUIRED` 方式加入该事务，再从同一快照组装 `totals + organizations`。这是只读统计的窄例外，所有中心侧写用例仍使用既定的 `READ COMMITTED`；该事务不得执行写入、`SELECT ... FOR UPDATE` 或外部调用。
- 不得跨模块导入 Entity/Mapper，也不得由前端并行请求后自行相加。实现必须通过集成测试证明 `totals` 与逐机构数据来自同一读快照。
- M0 不建立统计累计表、缓存余额、异步日汇总或可人工修正的指标表。所有值都能导航回权威业务查询；`statistics.read` 只授权聚合，不自动授予底层明细读取。
- 金额和重量继续使用两位小数字符串，不使用 JSON 浮点数。跨机构汇总仅对调用者当前有权机构求和。
- P0 不提供任意维度分组、排行榜、趋势图数据集、同比环比、复杂图表、统计导出或地推人员自动结算。设备归因只证明注册时使用了该部署二维码，不证明现场到访或某名推广人员业绩。

主要错误包括：

```text
400 STATISTICS.DATE_RANGE_INVALID
400 STATISTICS.DATE_RANGE_TOO_LARGE
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
```

## 微信官方契约依据

本章没有把官方账单文件下载纳入 P0。后续若在 M1 接入，必须以普通商户 APIv3 官方文档重新冻结下载、校验和归档契约：

- [下载账单开发指引](https://pay.weixin.qq.com/doc/v3/merchant/4013071218)：交易账单和资金账单在次日生成，申请后取得有时效的下载链接及哈希值，下载内容必须校验完整性。
- [商家转账：获取账单和电子回单](https://pay.weixin.qq.com/doc/v3/merchant/4013748430)：商家转账已完成记录可通过资金账单核对；电子回单是另一种单笔证明资源。

I-039 的 M0 `INTERNAL_TRANSACTION_CONVERGENCE` 只复用 I-032/I-035 已冻结的原单查证、渠道观察和本地账本事务，不能在 UI 或验收材料中表述为“已经完成微信官方账单对账”。

## 本批落实约束

1. operations Controller 只调用 operations 应用端口；平台技术处置、租户运营查询和工作人员小程序只读视图使用不同入口 DTO，不共享一个可自报作用域的万能请求。
2. 任务恢复、隔离确认、告警确认和对账动作都必须同时写不可变操作审计；异步动作的审计成功只表示意图已可靠受理。
3. 本章所有通用 `type + stableKey` 都来自后端白名单注册表，只用于安全导航、技术执行或诊断；禁止通过反射按表名更新任意业务记录。
4. 契约测试至少覆盖跨租户/机构不可见、平台与普通路径互斥、任务原身份恢复、隔离确认不重放、告警确认不恢复、对账处理不解决、确定性补齐幂等、统计当前快照一致性及纠错后历史指标变化。
5. 下一批 IoT/COS/边缘契约必须让设备业务确认超时、本地写入阻断和消息隔离能够产生本章可解释的任务、尝试和告警，但不能绕过对应业务状态机。
