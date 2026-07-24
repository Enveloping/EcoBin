# EcoBin P0 目标接口设计：清运、袋追溯、满溢与恢复（I-026～I-030）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-026～I-030 已确认；2026-07-24 已按清运门电磁阀实际能力修订**
>
> 说明：本文件定义清运员扫码换袋、清运操作恢复、完成记录与审核、袋码追溯、满溢查询/人工重检，以及与本批主链直接相关的最小设备恢复 HTTP 契约。OneNet、COS、边缘 SQLite 和 UART 已由 [`I-041～I-045`](09-onenet-cos-edge-confirmation-i041-i045.md) 与 [`I-046～I-050`](10-uart-protocol-i046-i050.md) 承接；这些后续协议不得改写本章冻结的操作身份、不可逆边界和业务确认语义。

## 本章统一边界

1. 清运员是当前机构普通用户附加 `CLEAN_OPERATION` 能力后的主体，继续使用 `aud=miniapp` 会话；工作人员小程序使用独立 `aud=miniapp-staff` 会话。两种主体、Token 和入口不能互换。
2. **清运操作**在开门前建立，拥有设备独占、新袋预留和失败恢复；**清运记录**只在设备已经完成真实换袋并上报可信结果后创建。任何响应都不能把“操作已准备”“OneNet 已受理”或“设备已保存”说成“清运已完成”。
3. 清运门没有门磁或受控关门执行器。MCU 只能控制电磁阀：通电解锁后门自动弹出，断电只表示停止解锁；门扇由清运员手动关闭。接口可以按电磁阀通断把清运门状态**推定**为 `OPEN/CLOSED`，但必须同时标明 `INFERRED_FROM_LOCK_POWER`，不得宣称这是物理门位检测。
4. 清运不可逆边界是首次解锁命令已经可能成功执行，即电磁阀首次通电或命令结果不再能证明“绝未通电”。边界前可以安全结束并释放新袋预留；边界后禁止普通取消，只能完成原操作或进入等待原清运员恢复。
5. 回收袋没有生命周期状态。袋码只是不可变身份；“当前绑定投口”和“被清运操作预留”是互斥位置关系，不是袋的可用/已清空/已结束状态。
6. 清运完成、清运审核、照片补齐、有效新皮重成立、清运后满溢检测完成和满溢事件恢复是不同事实。完成记录及物理换袋不等待审核、照片或满溢结论。
7. 满溢度和传感器值是带检测时间的最近一次后端已保存快照，不是持续实时值。人工按钮只能发起真实检测、真实称重或受约束的安全恢复，不能直接写“未满”“设备正常”或任意皮重。
8. 本章所有客户端可寻址资源使用稳定公开身份：清运操作 `operationUid`、清运记录 `cleanRecordNo`、满溢检测 `detectionUid`、满溢事件 `fullnessEventUid`、基准重测 `measurementUid`、设备故障 `faultUid` 和 URL-safe `bagQr`。不得暴露内部 `BIGINT` 主键。
9. 所有写操作继续遵守 I-004：使用 UUIDv4 `Idempotency-Key`，同一键同一摘要重放原结果，同键异摘要返回冲突；受理前拒绝不永久占用成功槽。修改已有投影还必须携带本章规定的预期版本。
10. 当前 `/api/app/clean/open`、`/api/iot/clean/gross`、`/api/iot/clean/tare` 以及“开门即建清运单、毛重/皮重分段回填”的旧契约与本章目标互斥。迁移时必须成组替换，不能保留任一旧写入口继续修改新表。

### 路径基准

普通租户和平台协助继续使用两套显式 Web 根：

```text
普通租户：/api/v1/web/organizations/{organizationCode}
平台协助：/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}
```

后文 `{organizationBase}` 表示上述二者分别展开；平台管理员不能调用普通租户路径，普通工作人员也不能在请求体自报租户。

设备部署下的 Web 资源使用：

```text
{deploymentBase} = {organizationBase}/device-deployments/{deploymentCode}
```

### 权限目录

本章新增三个允许 `TENANT` 和 `ORGANIZATION` 作用域的稳定能力码：

| 能力码 | 能力边界 |
|---|---|
| `clean.read` | 查询授权范围内清运操作、清运记录、清运异常、照片和袋位置/清运关系历史 |
| `device.detection.execute` | 对授权范围内投口发起真实满溢重新检测；不包含改写结果或恢复安全锁 |
| `device.recovery.execute` | 执行本章列明的基准重测、投递结果待处理恢复和严重安全锁恢复；不包含任意维护、校准或远程开门 |

- 清运记录首次审核继续复用 I-024 已定义的 `review.execute`；`clean.read` 不自动授予审核，`review.execute` 也只补足当前待审核直接目标，不开放全部清运历史。
- 容量、检测和满溢事件普通读取复用 `device.read`。只有写能力但没有 `device.read` 时，只能读取完成本次命令所需的直接目标安全状态和版本，不能借写能力遍历全部设备。
- 租户主体账号、租户总部和机构负责人按已冻结的天然权限取得其作用域内能力；其他工作人员由授权集合取得。平台管理员使用平台镜像端点并记录目标租户、机构和原因。
- `aud=miniapp-staff` 的 P0 渠道白名单只增加当前机构设备容量、满溢和告警的精简只读视图；本章所有重新检测与恢复写操作仍只开放 Web。

### 版本和并发基准

| 资源 | 查询返回 | 修改请求 | 成功语义 |
|---|---|---|---|
| 清运操作 | `version` | 开门前结束、恢复携带 `expectedVersion` | 成功受理命令或领域状态实际推进时版本递增 |
| 清运记录 | `currentRevisionNo` | 首次审核固定提交 `expectedRevisionNo=0` | 成功建立唯一修订号 1，P0 不再追加清运纠错版本 |
| 容量投影 | `capacityVersion` | 人工重检、基准重测和投递结果恢复携带 `expectedCapacityVersion` | 投影或当前 gate 实际改变时递增 |
| 设备运行投影 | `runtimeVersion` | 基准重测、投递结果恢复和安全恢复携带 `expectedRuntimeVersion` | 只由真实运行事实或受约束恢复事务递增 |
| 满溢检测/基准重测 | 各自 `version` | 客户端不覆盖终态 | 可信设备事实按单调状态机推进，终态不回退 |

新清运操作没有可预先提交的对象版本。服务端必须从“租户 → 机构 → 当前机构小程序 → 当前清运配置 head → 机构用户/清运能力”取得身份和配置锁前缀，再进入“物理资产 → 部署 → 占位/运行态 → 投口 → 全部相关袋按内部 ID 升序 → 袋占位 → 容量”的固定锁序。禁用、能力撤销和清运配置发布使用相同前缀，以数据库提交顺序决定新操作能否成立；已经可靠越过开门边界的操作仍允许真实结果收敛。

## I-026 清运选项与操作创建

**已确认：扫码后先执行无副作用查询；扫描换入袋后，后端只原子创建可恢复操作、预留和设备开始意图，不提前创建清运记录。**

### 1. 清运选项查询

```http
GET /api/v1/miniapp/device-deployments/{deploymentCode}/clean-options
Authorization: Bearer <aud=miniapp token>
```

- 请求要求当前机构用户有效且具有 `CLEAN_OPERATION`；清运能力失效返回 `403 AUTH.CAPABILITY_REQUIRED`，不降级成普通投递入口。
- 部署不属于当前 AppID 固定机构、二维码无效或资源不存在时统一返回 `404 RESOURCE.NOT_FOUND`，不得泄露其他机构、硬件 SN 或当前占用人。
- 查询无副作用，不要求幂等键，不预留袋、不创建操作、不取得占位，也不能作为稍后开门的授权凭证。

`data` 示例：

```json
{
  "deploymentCode": "dpl_4s8V...",
  "displayName": "A区1号回收箱",
  "address": "A区北门",
  "deviceBusy": false,
  "recoverableOperations": [],
  "asOf": "2026-07-23T09:00:15.123Z",
  "ports": [
    {
      "portNo": 1,
      "displayName": "一号投口",
      "currentBagQr": "BAG_A8x...",
      "fullnessStatus": "FULL",
      "fullnessPercent": "105.20",
      "cleaningAllowed": true,
      "blockers": []
    }
  ]
}
```

字段边界：

- `deviceBusy` 只表达存在整机 DELIVERY/CLEAN 占位，不返回占用主体。`recoverableOperations` 只列当前用户本人在该部署的 `RECOVERY_REQUIRED` 操作，每项返回 `operationUid + portNo + status + statusUrl`；不同投口可能各自保留历史未完成操作，因此使用数组。其他人的恢复操作仍只表现为对应投口不可用。
- `currentBagQr` 可以为 `null`。旧袋绑定缺失不阻止普通清运，只提示完成后会产生系统异常；不得通过容量基准反推出一个虚构袋码。
- `PORT_FULL`、既有满溢事件、旧基准缺失、已经终结的检测失败和投递结果待处理本身不阻止清运，因为真实换袋可以恢复容量。任何尚未终结的满溢检测或空袋基准重测仍是短暂冲突，清运准备不取消或改写它；等待原操作闭合后重试。
- 已知整机或投递门安全故障、清运电磁阀仍通电、MCU 或边缘离线、协议不兼容、当前投口存在未终结清运、整机被占用或无法可靠保存新作业时必须阻止清运。电磁阀断电只允许把清运门投影为“推定关闭”，不是门磁证明；存在上次解锁后的待恢复操作时仍不得创建新操作。称重在操作中途失败可以形成异常清运记录，但首次解锁前已经锁存为严重称重/本地存储故障时不能借清运绕过安全停用。
- `fullnessStatus` 固定为 `UNKNOWN/CHECKING/NOT_FULL/SUSPECTED_FULL/FULL/SOURCE_FAILED`，只作说明；`fullnessPercent` 继续遵守未知为 `null`、允许超过 100% 的规则。

### 2. 创建清运操作

```http
POST /api/v1/miniapp/device-deployments/{deploymentCode}/ports/{portNo}/clean-operations
Authorization: Bearer <aud=miniapp token>
Idempotency-Key: <UUIDv4>
Content-Type: application/json
```

请求只接受扫描得到的换入袋码：

```json
{
  "installedBagQr": "BAG_B9y..."
}
```

请求不接受清运员、租户、机构、旧袋、旧皮重、首次解锁前重量、设备状态、配置版本、门状态或照片凭证等客户端自报事实。

服务端在固定锁序中重新验证主体、机构、清运能力、当前清运配置、部署、设备安全、整机占位、投口以及不存在未终结满溢检测或基准重测后：

1. 读取并固定当时的旧袋绑定状态、可空旧袋及袋码；读取与旧袋一致时才可信的当前重量基准。
2. 规范化 `installedBagQr`：本机构首次扫描的全平台新码自动登记；既有同机构且未占位的袋允许复用；其他机构、其他投口或其他操作正在占用时拒绝。存在旧袋时新旧袋必须不同。
3. 建立新袋 `CLEAN_RESERVED` 占位。旧袋缺失时固定 `oldBagBindingState=MISSING`，不制造旧袋或取走事件。
4. 创建唯一 `PREPARED` 清运操作，冻结旧袋/基准、清运配置版本、30 分钟执行时限参数、当前设备配置摘要，以及可空的原投口待处理投递会话快照；中心的首次解锁前重量此时必须为 `null`，不能用最近心跳重量冒充现场读数。
5. 取得指向该操作的整机 `CLEAN` 占位，创建以 `operationUid` 为目标的 `START_CLEAN_OPERATION` 设备命令和唯一可靠任务，并写成功审计。

任何一步失败都整体回滚；不能留下孤儿袋预留、占位、操作或可执行命令。`PORT_FULL`、容量检测失败和非空投递结果待处理指针不直接拒绝清运，但后续只有真实完成换袋并建立新容量 gate 才能解除旧容量阻断。

设备开始命令必须包含稳定 `commandUid/operationUid`、部署/投口、旧袋与基准快照、新袋码、配置摘要、执行时长、照片上传授权范围和精确 `startAuthorizationExpiresAt`；不得包含内部主键。COS 临时凭证只在可靠任务实际下行时即时生成并附加到线协议，不写入业务命令、任务快照、日志或摘要；恢复命令重新取得新凭证。开始授权默认在后端提交后 60 秒失效。香橙派必须先把完整上下文、摘要和单调时钟截止点可靠写入 SQLite，再让 MCU 取得本操作真正的首次解锁前稳定总重量并同样可靠保存；只有二者都成功且授权未过期才允许电磁阀首次通电解锁。重量无法稳定、过载、称重故障、摘要不一致或本地无法落盘时必须拒绝解锁并可靠上报解锁前失败。中心可以从可信中间观察补写操作的首次解锁前重量，也可以由最终 `CLEAN_COMPLETE` 兜底，但不得因中间事件尚未到达而读取旧运行投影伪造。60 秒只限制能否开始首次解锁；在期限内开始后，操作使用冻结的 30 分钟时长等待人工换袋、人工关门、最终确认和结果保存。

### 3. 受理响应与错误

成功返回 `202 Accepted`，`Location` 指向 I-027 的操作资源：

```json
{
  "code": "OK",
  "data": {
    "operationId": "66eb2b36-...",
    "resourceId": "66eb2b36-...",
    "operationUid": "66eb2b36-...",
    "status": "PREPARED",
    "version": 0,
    "portNo": 1,
    "installedBagQr": "BAG_B9y...",
    "startAuthorizationExpiresAt": "2026-07-23T09:01:15.123Z",
    "statusUrl": "/api/v1/miniapp/clean-operations/66eb2b36-...",
    "recommendedPollAfterMs": 1000,
    "nextActions": ["WAIT"]
  },
  "requestId": "01..."
}
```

- `202` 只证明本地操作、预留、占位和可靠设备意图已经共同提交，不证明 OneNet 转发、设备受理或门已打开。
- 同一目标投口已有未终结操作时返回 `409 CLEAN.PORT_OPERATION_ACTIVE`；只在该操作属于当前用户时才可在安全错误详情中返回其 `operationUid/statusUrl`。同一清运员在其他投口存在 `RECOVERY_REQUIRED` 不形成全局人员锁，是否能开始本次操作仍由整机占位和目标投口条件决定。
- 同一新袋被并发扫描时最多一个事务成功；失败方整体回滚并返回统一 `409 CLEAN.BAG_UNAVAILABLE`，不能泄露其他机构或占用人。

主要错误至少包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 CLEAN.PORT_OPERATION_ACTIVE
409 CLEAN.BAG_UNAVAILABLE
409 CLEAN.NEW_BAG_EQUALS_OLD_BAG
409 DEVICE.DEVICE_BUSY
422 DEVICE.DEPLOYMENT_UNAVAILABLE
422 DEVICE.CONFIGURATION_NOT_APPLIED
422 DEVICE.CLEANING_UNAVAILABLE
422 DEVICE.PRE_CLEAN_WEIGHT_UNAVAILABLE
503 COMMON.RETRY_EXHAUSTED
```

## I-027 清运执行、再次解锁与原清运员恢复

**已确认：小程序只观察和恢复自己的操作；设备屏幕负责操作内再次解锁。首次解锁可能执行后，超时或重启不会伪造物理关门、完成或释放原操作，新操作也不能覆盖待恢复操作。**

### 1. 查询端点

清运员：

```http
GET /api/v1/miniapp/clean-operations/{operationUid}
Authorization: Bearer <aud=miniapp token>
```

普通 Web 与平台镜像：

```http
GET {organizationBase}/clean-operations
GET {organizationBase}/clean-operations/{operationUid}
```

- 小程序只有原清运员可以读取自己的操作；其他用户、其他机构和不存在统一返回 `404 RESOURCE.NOT_FOUND`。
- Web 列表需要 `clean.read`，支持 `status`、`deploymentCode`、`portNo`、`cleanerUserUid`、`createdFrom/createdTo` 和普通管理分页；恢复队列固定筛选 `RECOVERY_REQUIRED`。仅持有恢复写能力时只允许读取直接目标状态和版本，不开放列表。
- 查询不推动设备、操作或可靠任务状态。

操作详情固定返回：

```json
{
  "operationUid": "66eb2b36-...",
  "status": "IN_PROGRESS",
  "version": 3,
  "source": {
    "deploymentCode": "dpl_4s8V...",
    "portNo": 1,
    "cleanerUserUid": "54f9c3f5-...",
    "createdAt": "2026-07-23T09:00:15.123Z"
  },
  "bags": {
    "oldBagBindingState": "BOUND",
    "removedBagQr": "BAG_A8x...",
    "installedBagQr": "BAG_B9y..."
  },
  "execution": {
    "edgeSavedAt": "2026-07-23T09:00:20.123Z",
    "firstUnlockMayHaveExecutedAt": "2026-07-23T09:00:22.123Z",
    "cleanLockPowerState": "DEENERGIZED",
    "inferredCleanDoorState": "CLOSED",
    "cleanDoorStateBasis": "INFERRED_FROM_LOCK_POWER",
    "executionDeadlineAt": "2026-07-23T09:30:20.123Z",
    "reopenCount": 1,
    "recoveryCount": 0,
    "preOpenEndRequested": false,
    "resumeRequested": false
  },
  "completion": null,
  "nextActions": ["CONTINUE_ON_DEVICE"]
}
```

`status` 是闭集：

```text
PREPARED
EDGE_SAVED
IN_PROGRESS
RECOVERY_REQUIRED
PRE_OPEN_ENDED
COMPLETED
```

状态语义：

| 可信事实 | 原状态 | 新状态 | 约束 |
|---|---|---|---|
| 香橙派在授权期限内可靠保存完整上下文 | `PREPARED` | `EDGE_SAVED` | 第一次按后端时间投影 `executionDeadlineAt`，设备以本地单调时钟执行冻结时长；首次解锁前仍必须现场取得并可靠保存稳定重量 |
| 首次解锁命令可能成功执行 | `EDGE_SAVED` | `IN_PROGRESS` | 电磁阀首次通电或已无法证明绝未通电即写入且不可清除；从此越过普通取消边界 |
| 操作内断电或再次通电解锁 | `IN_PROGRESS` | `IN_PROGRESS` | 只追加锁输出真实观察并更新汇总，不覆盖首次解锁边界和旧袋快照；断电推定关闭但不是门位证明 |
| 已解锁操作超时或任一端重启 | `IN_PROGRESS` | `RECOVERY_REQUIRED` | 先保留原操作、整机保护和新袋预留；只有电磁阀断电且原清运员现场确认门扇关闭后，才可把保护降为原投口/操作/袋预留并释放整机占位。不得仅因断电自动释放或宣称门已关闭 |
| 原清运员恢复命令被设备可靠接受 | `RECOVERY_REQUIRED` | `IN_PROGRESS` | 取得新执行窗口，不创建新操作 |
| 可信完成结果 | `IN_PROGRESS/RECOVERY_REQUIRED` | `COMPLETED` | 必须同时具备原操作身份、清运员最终确认、电磁阀断电、断电所推定的关闭状态，以及最终稳定重量或明确终态称重故障；不声称存在物理门磁证明 |
| 可信证明首次解锁从未可能执行 | `PREPARED/EDGE_SAVED` | `PRE_OPEN_ENDED` | 释放预留和占位，不创建记录或照片槽 |

`PRE_OPEN_ENDED/COMPLETED` 是不可回退终态。中间观察乱序或缺失时，最终清运员确认、锁断电事实、最终稳定重量或明确称重故障，以及 `CLEAN_COMPLETE` 可以单调补进；没有可信证据的精确时间保持 `null`，不得用后端处理时间伪造设备动作。查询中任何 `inferredCleanDoorState` 都必须与 `cleanDoorStateBasis=INFERRED_FROM_LOCK_POWER` 成对出现。

### 2. 首次解锁前结束

```http
POST /api/v1/miniapp/clean-operations/{operationUid}/pre-open-end-requests
Authorization: Bearer <aud=miniapp token>
Idempotency-Key: <UUIDv4>
```

```json
{
  "expectedVersion": 1,
  "reason": null
}
```

- 只允许原清运员对首次解锁尚未可能执行的操作请求结束。请求不会仅凭客户端意愿写 `PRE_OPEN_ENDED`；服务端必须取消仍未外调的任务，或取得设备明确拒绝/停止且电磁阀从未通电、没有在途解锁动作的证据。
- 能在本地事务证明命令从未外调时可以同步结束并返回 `200`；已经可能外调时可靠受理停止意图并返回 `202 + statusUrl`，保持整机占位和新袋预留直至证据闭合。
- 电磁阀首次通电或解锁命令已经可能执行的事实先提交时，请求返回 `409 CLEAN.PRE_OPEN_BOUNDARY_CROSSED`，操作继续原流程；不能把已经发生的换袋风险改写成取消。
- 安全结束事务把操作置为 `PRE_OPEN_ENDED`，删除新袋当前预留、追加 `RESERVATION_RELEASED` 事件并释放本操作整机占位。它不创建清运记录、标准照片槽、基准或满溢检测。

开始授权过期、设备明确拒绝、清运员请求结束和租户/机构随后禁用都遵守同一边界。已越过首次解锁边界的操作不因账号、能力或机构后来禁用而丢弃真实设备结果。

`startAuthorizationExpiresAt` 到达后，后端取消仍为 `PENDING/BLOCKED` 且尚未外调的开始任务；香橙派必须按授权摘要和本地单调时钟永久拒绝迟到开始。任务从未外调时可直接安全结束；已经可能外调但尚无设备证据时，只有协议过期拒绝、锁输出历史和命令去重状态共同证明电磁阀绝未通电且旧命令不可能再执行后才能释放。否则操作保持保护并产生设备响应异常，不能因为“60 秒到了”猜测清运门物理状态。

### 3. 操作内再次解锁

清运员最终确认前的“重新开门”实际是再次给电磁阀通电解锁，由 MCU 屏幕状态机提供，不新增小程序远程开门端点：

1. 香橙派只在原 `operationUid` 仍为本地活动、当前锁输出已经断电、单调执行期限未到、MCU/电磁阀健康且操作未进入中心 `RECOVERY_REQUIRED` 时接受设备按钮事件。这里的断电只形成“推定关闭”，不证明门扇实际关闭。
2. 每次再次解锁使用新的 `mcuCommandUid` 和稳定设备事件 ID，但仍引用原操作、原清运员、旧袋/旧基准、首次解锁前重量和新袋预留。
3. 再次解锁不修改后端冻结配置，不重新扫描袋，不创建第二操作或记录。每次解锁后此前候选新皮重作废，只接受清运员最终确认完成时取得的稳定重量。
4. 已经授权并在边缘可靠保存的活动操作中途断网时，可以在本地完成同一操作内的再次解锁；这只是原操作内动作。进入 `RECOVERY_REQUIRED` 后必须先取得下节新的中心恢复授权，不能离线自行重启执行窗口。
5. 再次解锁次数不设固定业务上限，只受总执行时限和电磁阀保护约束；次数和每次锁输出结果作为诊断事实保存，异常过多可产生告警但不自动伪造操作终态。

### 4. 超时与原清运员恢复

首次解锁已经可能执行的操作达到冻结的 30 分钟执行期限仍未最终确认时：

- 操作一律进入 `RECOVERY_REQUIRED`，先保留原操作、整机 `CLEAN` 保护和新袋预留，等待原清运员现场恢复。电磁阀应进入断电安全输出，但断电本身只形成推定关闭，不能据此释放整机占位或宣称门扇已经关闭。只有原清运员已在现场确认门扇关闭且锁输出为断电时，服务端才可释放整机占位，同时继续锁住原投口、原操作和新袋预留；其他正常投口此后可以工作。
- 电磁阀仍通电、输出故障、MCU 不可达或投递门/其他硬安全状态异常时，另外锁存相应严重故障；超时本身不生成虚构的清运门故障或真实门位。
- 超时不创建清运记录、不交换袋、不释放预留，也不把候选称重当作最终新基准。香橙派或 MCU 重启遵守同一人工恢复边界。

原清运员重新扫描同一部署二维码后调用：

```http
POST /api/v1/miniapp/device-deployments/{deploymentCode}/clean-operations/{operationUid}/resumptions
Authorization: Bearer <aud=miniapp token>
Idempotency-Key: <UUIDv4>
```

```json
{
  "expectedVersion": 5,
  "onSiteRecoveryConfirmed": true
}
```

服务端重新验证当前会话仍是原机构用户、仍具有清运能力，锁定同一资产/操作/投口/预留袋，并要求：

- 操作精确为 `RECOVERY_REQUIRED`，部署、投口、新袋预留和旧快照未被改写；
- 清运电磁阀当前断电，原清运员提交 `onSiteRecoveryConfirmed=true` 并在现场核对门扇、袋和设备；该确认不是门磁数据；
- 其他严重安全锁已按 I-030 恢复；
- 整机没有后来作业占位；当前配置可以读取，但恢复继续使用原操作冻结配置和袋快照；
- 边缘和 MCU 在线、协议兼容且能够可靠恢复原操作。

成功恢复事务复用仍由原操作持有的整机 `CLEAN` 占位；若该占位已经在“断电 + 原清运员现场确认门扇关闭”后释放，则本事务必须原子重新取得整机占位，遇到其他投口正在作业时保持 `RECOVERY_REQUIRED` 并返回设备忙，不覆盖后来作业。随后创建唯一 `RESUME_CLEAN_OPERATION:<operationUid>:<recoveryCount+1>` 命令/可靠任务，递增操作版本和恢复次数并返回 `202`。执行器为本次恢复即时附加新的受限 COS 凭证；设备在 60 秒恢复开始授权内可靠加载原上下文后，操作回到 `IN_PROGRESS` 并获得一段新的原配置时长执行窗口。恢复本身不自动通电解锁；清运员在设备屏幕再次解锁时才生成同一 `operationUid` 下的新 MCU 命令。不重新扫描袋，不覆盖首次解锁前重量，不建立新操作或新清运记录。

P0 不提供其他人员接管、管理员强行改挂清运员、释放预留后重做或把待恢复操作直接标成完成的接口。原清运员无法继续属于已明确暂缓的后续用例。

主要错误包括：

```text
404 RESOURCE.NOT_FOUND
409 CLEAN.OPERATION_VERSION_CONFLICT
409 CLEAN.PRE_OPEN_BOUNDARY_CROSSED
409 CLEAN.OPERATION_NOT_RECOVERABLE
409 CLEAN.RESERVATION_CHANGED
409 DEVICE.DEVICE_BUSY
422 DEVICE.DOOR_NOT_SAFE
422 DEVICE.SAFETY_LOCKED
422 DEVICE.CLEAN_RECOVERY_UNAVAILABLE
```

## I-028 清运完成、记录查询与首次审核

**已确认：可信 `CLEAN_COMPLETE` 一次性形成清运记录和真实换袋结果；完成必须有清运员确认、锁断电推定关闭，以及最终稳定重量或明确终态称重故障，不能伪造门磁证明。M0 全部人工审核，审核只认定统计净重量，不回滚物理换袋。**

### 1. 可信完成结果边界

正式 `CLEAN_COMPLETE` 由后续 OneNet/IoT 契约承载，本章冻结其领域输入至少包括：

```json
{
  "eventUid": "4b999f65-...",
  "operationUid": "66eb2b36-...",
  "commandUid": "b2334ea7-...",
  "edgeEventSequence": 1042,
  "installedBagQr": "BAG_B9y...",
  "cleanerCompletionConfirmed": true,
  "cleanLockPowerState": "DEENERGIZED",
  "inferredCleanDoorState": "CLOSED",
  "cleanDoorStateBasis": "INFERRED_FROM_LOCK_POWER",
  "weightBeforeGram": 50200,
  "oldTareWeightGram": 1200,
  "removedNetWeightGram": 49000,
  "weightAfterGram": 1300,
  "newTareWeightGram": 1300,
  "beforeWeightStatus": "RELIABLE",
  "beforeWeightFaultCode": null,
  "afterWeightStatus": "RELIABLE",
  "afterWeightFaultCode": null,
  "photoFirstOpenInnerUrl": "https://...",
  "photoFirstOpenOuterUrl": null,
  "photoFinalCloseInnerUrl": "https://...",
  "photoFinalCloseOuterUrl": "https://...",
  "deviceOccurredAt": "2026-07-23T09:12:30.123Z"
}
```

- 克值都是有符号整数；缺失或不可靠时字段为 `null`，对应 `WeightStatus` 明确为非可靠且 `WeightFaultCode` 非空，不能传 `0` 冒充失败；可靠时状态为 `RELIABLE` 且故障码为空。设备计算值和后端复算值同时保留，不一致追加系统异常。
- `cleanerCompletionConfirmed=true`、`cleanLockPowerState=DEENERGIZED`、由断电推定的 `inferredCleanDoorState=CLOSED`，以及最终稳定重量或明确终态称重故障必须同时形成完整结果，且香橙派已经把它写入本地可靠发件箱。称重故障时重量为 `null` 并携明确状态/故障码，不能用 0；完成事务把新基准置为无效并保持投口阻断。这里没有清运门物理检测；任一响应、事件或审计都不得把推定状态描述为“门磁已确认关闭”。照片 URL 可以不完整，照片上传不阻断完成。
- `installedBagQr` 必须精确等于操作预留快照。P0 对不一致不猜测实际换袋，不创建记录、不更新袋或基准；消息进入技术隔离，原操作继续保持可核查状态。该防御不扩展为“袋码不一致处置业务”。
- 操作已经是 `PRE_OPEN_ENDED` 时到达的完成结果与“可信从未解锁”事实冲突，必须隔离并锁存设备一致性问题；不得回退终态或按结果强行换袋。操作缺少中间锁输出观察时，完整可信结果只有在自身能够证明首次解锁命令曾可能执行、清运员最终确认、当前锁断电，以及最终稳定重量或明确终态称重故障时才可越级收敛；不能补造物理开门/关门时间。
- 同一 `eventUid` 或 `operationUid` 同摘要重投命中原业务确认；同身份异摘要永久隔离，不能覆盖首次可信结果。

后端完成事务从资产锁根进入原操作并在同一事务：

1. 保存唯一设备物理结果，验证操作身份、作用域、部署、投口、新袋和冻结摘要。
2. 为本机构分配唯一递增的清运记录提交可见序号，创建唯一 `cleanRecordNo`、清运记录和系统异常，并确保四个标准照片槽已经按操作建立；首次解锁前照片已经创建的槽位按唯一键复用，不重复生成。
3. 旧袋绑定存在时删除旧袋投口槽并追加 `REMOVED_BY_CLEAN`；准备时已经固定为缺失时不伪造旧袋或取走事件。把新袋从本操作预留原子转换为原投口绑定并追加 `INSTALLED_BY_CLEAN`。
4. 新袋最终稳定重量可靠、非负且关系一致时建立新的有效重量基准，并令当前原始净重为 0；不可靠或矛盾时保存原值、清空当前有效基准并置 `baselineState=INVALID`，绝不能沿用旧袋基准。
5. 创建以本清运记录为唯一来源的 `CLEAN_COMPLETE` 满溢检测并让容量 gate 指向它。有效基准存在，或当前模式仍有红外来源可以贡献结论时创建可靠采样任务；`WEIGHT_ONLY` 且新基准无效时，检测直接以 `WEIGHT_BASELINE_UNAVAILABLE/SOURCE_FAILED` 收敛为 `FAILED`，不伪造无意义设备采样。无论成功或失败，已有旧袋满溢事件都不能仅因换袋直接恢复。
6. 若 I-026 快照的原投口待处理投递会话仍是当前指针，只有在新袋绑定和上述清运后检测 gate 已共同建立后才条件清除；若迟到投递结果已经先接管则不重复清除。以后到达的原投递结果仍可建立原订单，但其旧袋容量派生必须按代际检查成为过期事实，不能覆盖清运后的新袋容量。
7. 把操作推进为 `COMPLETED`、关联唯一清运记录；在清运员确认、锁断电和“最终稳定重量或明确终态称重故障”三项完成条件成立后释放本操作整机占位，写设备业务确认意图并把 inbox 标为已处理。释放仍不等于获得了物理门磁事实。

任一步失败整体回滚且不返回业务确认，设备继续使用原 `eventUid/operationUid` 重试。清运记录成功不等于清运后检测可靠不满；检测仍执行时或已经因基准/来源失败而闭合，原投口都保持不能投递，直至后续基准重测和真实重检恢复。

### 2. 查询端点与稳定分页

原清运员：

```http
GET /api/v1/miniapp/me/clean-records?cursor={opaque}&limit={1..100}
GET /api/v1/miniapp/me/clean-records/{cleanRecordNo}
```

普通 Web 与平台镜像：

```http
GET {organizationBase}/clean-records
GET {organizationBase}/clean-records/{cleanRecordNo}
```

- 小程序固定当前清运员和机构，只返回本人记录。Web 支持 `reviewStatus`、`resultKind`、`cleanerUserUid`、`deploymentCode`、`portNo`、`removedBagQr`、`installedBagQr`、`anomalyCode`、`photoCompleteness`、`occurredFrom/occurredTo`、`cursor` 和 `limit`。
- 排序按 `deviceCompletedAt + cleanRecordNo` 倒序。首屏冻结机构清运记录提交可见水位；后续页不读取更高水位的新记录，迟到完成的新记录只在刷新后出现。
- `reviewStatus` 和 `photoCompleteness` 是每页按当前值计算的可变筛选。并发审核/补图可以让尚未访问的既有记录进入或退出后续页，但稳定排序键不能让已经返回的记录重复；客户端要完整当前队列必须刷新首屏。
- Web 列表允许 `clean.read` 或 `review.execute`。仅有 `review.execute` 时固定为当前可审核 `PENDING` 队列；详情也只开放待审核直接目标。记录越权或不存在统一返回 `404 RESOURCE.NOT_FOUND`。

列表项至少包含：

```json
{
  "cleanRecordNo": "CR20260723...",
  "operationUid": "66eb2b36-...",
  "cleanerUserUid": "54f9c3f5-...",
  "deploymentCode": "dpl_4s8V...",
  "portNo": 1,
  "removedBagQr": "BAG_A8x...",
  "installedBagQr": "BAG_B9y...",
  "deviceCompletedAt": "2026-07-23T09:12:30.123Z",
  "removedNetWeightKg": "49.00",
  "weightReliability": "RELIABLE",
  "resultKind": "NORMAL",
  "reviewStatus": "PENDING",
  "currentRevisionNo": 0,
  "finalNetWeightKg": null,
  "anomalyCodes": [],
  "photoCompleteness": "INCOMPLETE"
}
```

详情完整保留：

- 操作、设备、投口、清运员和设备/后端时间；
- `BOUND/MISSING` 旧袋状态、取走袋和换入袋快照；
- 首次解锁前重量、旧皮重、设备报告净重、后端复算净重、清运员最终确认时总重量和候选新皮重，以及每项可靠性；
- 新基准是否建立及对应安全摘要、清运后满溢检测 UID/当前状态；
- 系统异常、四个照片槽和唯一审核修订。

小程序不返回后台操作者身份、诊断 JSON 或审计详情；Web 诊断信息也不得包含原始协议报文、密钥或其他主体信息。

### 3. 清运首次审核

普通 Web 与平台镜像：

```http
POST {organizationBase}/clean-records/{cleanRecordNo}/reviews
Idempotency-Key: <UUIDv4>
```

按原数据通过：

```json
{
  "expectedRevisionNo": 0,
  "decision": "ORIGINAL_APPROVED",
  "finalNetWeightKg": null,
  "reason": null
}
```

修改后通过：

```json
{
  "expectedRevisionNo": 0,
  "decision": "MODIFIED_APPROVED",
  "finalNetWeightKg": "48.50",
  "reason": "旧皮重异常，按现场记录认定"
}
```

- M0 清运配置固定 `ALL_MANUAL`，所有新记录初始 `PENDING/currentRevisionNo=0`；系统异常无论未来配置如何都必须人工审核。
- `ORIGINAL_APPROVED` 要求后端复算净重可靠，`finalNetWeightKg` 必须为 `null`。原始关系缺失、无效或矛盾时只能选择修改后通过。
- `MODIFIED_APPROVED` 只接受带符号、两位小数千克字符串，必须可无损转换为整数克并落入目标有符号整数存储范围；请求不得修改旧袋、新袋、原始重量、新皮重、照片、操作归属、完成时间或满溢状态。
- 成功创建唯一修订号 1，把记录置为 `APPROVED` 并返回 `201`。该认定只影响经营统计，不写用户钱包，也不回滚袋交换、重量基准、容量或设备事实。
- 没有“驳回并恢复旧袋”结果。P0 不提供已审核清运记录的再次纠错；重复审核返回冲突，同一幂等键重放原结果。
- 审核员可以审核自己执行的清运记录，原因选填，但操作人、时间、决定和前后值始终保存。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 CLEAN.REVISION_VERSION_CONFLICT
409 CLEAN.RECORD_ALREADY_APPROVED
422 CLEAN.ORIGINAL_DATA_UNRELIABLE
400 COMMON.VALIDATION_FAILED
```

## I-029 回收袋身份、当前位置与历史追溯

**已确认：袋码是本机构可重复使用的不可变身份；接口展示当前位置和只追加历史，不建立或推断袋生命周期状态。**

### 1. 袋码格式和首次登记

- 目标袋码是大小写敏感、URL-safe 的不透明 ASCII 字符串，长度 `8..64`，字符限定为 `[A-Za-z0-9_-]`。二维码可以承载 EcoBin URL，但小程序只把其中规范 `bagQr` 值提交后端；任意 URL、脚本或显示文本不能直接成为袋码。
- 袋码全平台唯一。它不是登录、授权或所有权凭证，扫描后仍必须通过 I-026 的清运员、机构、设备和并发校验。
- P0 不提供独立“创建袋”接口。本机构授权清运员第一次在 I-026 扫描全平台不存在的袋码时，清运准备事务自动创建袋身份并永久固化机构归属。
- 已属于其他机构的袋统一表现为 `CLEAN.BAG_UNAVAILABLE`，不返回租户、机构、投口或占用人；P0 不提供跨机构转移。
- 袋身份创建后不允许修改代码、机构或删除。重复使用只产生新的预留、安装和取走关系事件。

### 2. Web 查询接口

普通 Web 与平台镜像：

```http
GET {organizationBase}/bags/{bagQr}
GET {organizationBase}/bags/{bagQr}/occupancy-events?cursor={opaque}&limit={1..100}
GET {organizationBase}/bags/{bagQr}/clean-records?cursor={opaque}&limit={1..100}
GET {organizationBase}/bags/{bagQr}/delivery-orders?cursor={opaque}&limit={1..100}
```

- 袋详情、位置事件和关联清运记录需要 `clean.read`。关联投递订单端点还要求 `delivery.read`；只有审核能力时仍应从具体待审核订单读取袋快照，不能借袋接口遍历全部订单。
- 平台管理员使用平台镜像。袋不属于目标机构或不存在统一返回 `404 RESOURCE.NOT_FOUND`。
- P0 清运员小程序只负责扫码和查看自己的清运操作/记录，不开放全机构袋追溯页面。

袋详情示例：

```json
{
  "bagQr": "BAG_B9y...",
  "registeredAt": "2026-07-23T09:00:15.123Z",
  "currentOccupancy": {
    "kind": "PORT_BOUND",
    "deploymentCode": "dpl_4s8V...",
    "portNo": 1,
    "cleanOperationUid": null,
    "since": "2026-07-23T09:12:30.123Z"
  },
  "lastRelationChangedAt": "2026-07-23T09:12:30.123Z"
}
```

`currentOccupancy.kind` 固定为 `NONE/PORT_BOUND/CLEAN_RESERVED`：

- `PORT_BOUND` 只返回部署和投口，不返回清运操作；
- `CLEAN_RESERVED` 只返回预留它的 `cleanOperationUid`，部署/投口由该操作安全摘要展示；
- `NONE` 表示当前没有位置关系，不等于“已经清空”“可用”或任何生命周期状态。

### 3. 追加历史和关联事实

位置事件固定为：

```text
INITIAL_INSTALLED
RESERVED_FOR_CLEAN
RESERVATION_RELEASED
REMOVED_BY_CLEAN
INSTALLED_BY_CLEAN
```

- 事件按后端 `occurredAt + eventUid` 稳定倒序，游标绑定袋码和末项，不接受数据库 ID。事件只追加，不因袋再次使用覆盖旧投口或旧清运关系。
- `occupancy-events` 返回事件、部署/投口、可空操作 UID、后端发生时间和来源记录安全引用；不返回内部占位行或技术锁信息。
- `clean-records` 返回本袋作为 `removedBagQr` 或 `installedBagQr` 的角色和 I-028 清运记录摘要。一次记录新旧袋相同已在创建前禁止，不能在此端点出现双角色。
- `delivery-orders` 只按投递订单已经冻结的袋码快照查询。后来换袋、袋再次安装或当前占位变化不能改写历史订单；完整订单详情继续跳转 I-024 并重新鉴权。
- 三条历史链分别分页，不把不同表临时拼成一个难以稳定翻页的伪时间线。UI 可以按标签汇总展示，但不得声称一次响应是跨三域同一数据库快照。

### 4. 并发和隐私边界

1. 新清运准备对所有涉及的既有袋按内部 ID 升序锁定；首次登记依靠全平台袋码唯一键线性化。并发扫描相同新码最多一个完整事务成功，失败事务不能留下半个袋身份或预留。
2. 一个袋最多只有一条当前占位；一个投口最多绑定一只当前袋；一个清运操作最多预留一只新袋。数据库唯一约束是并发兜底，业务事务仍必须显式复核。
3. 首次解锁前安全结束删除预留并追加释放事件；首次解锁可能执行后的失败或 `RECOVERY_REQUIRED` 继续保留预留，不能让另一操作重用。
4. 清运完成在同一事务完成旧袋取走与新袋安装。事务失败时两者都不成立，不能出现一个袋同时在两个投口或新袋无历史地跳转。
5. 袋码可以出现在有权限业务响应和审计目标中，但不进入其他机构的错误详情。日志不得记录二维码原始 URL 或其中可能附带的非袋码参数。

## I-030 满溢查询、人工重检与最小设备恢复

**已确认：查询只展示带时间的最近事实；人工命令必须取得新的真实设备证据。恢复接口只解除精确目标阻断，不能任意改写容量、基准、订单或设备终态。**

### 1. 容量、检测和满溢事件查询

普通 Web 与平台镜像：

```http
GET {deploymentBase}/ports/{portNo}/capacity
GET {deploymentBase}/ports/{portNo}/fullness-detections?cursor={opaque}&limit={1..100}
GET {deploymentBase}/ports/{portNo}/fullness-detections/{detectionUid}
GET {deploymentBase}/ports/{portNo}/fullness-events?cursor={opaque}&limit={1..100}
GET {deploymentBase}/ports/{portNo}/fullness-events/{fullnessEventUid}
```

工作人员小程序精简只读入口：

```http
GET /api/v1/miniapp-staff/device-deployments/{deploymentCode}/ports/{portNo}/capacity
GET /api/v1/miniapp-staff/device-deployments/{deploymentCode}/ports/{portNo}/fullness-events/current
Authorization: Bearer <aud=miniapp-staff token>
```

- Web 完整读取要求 `device.read`。工作人员小程序还要求当前绑定、当前机构实时 `device.read` 和 P0 渠道白名单，固定当前机构且不接受租户/机构参数。
- 普通用户继续只通过 I-021 看到投口是否可用、检测中、已满或故障，不开放传感器、阈值、袋码和故障诊断详情。
- 检测与事件按后端创建/确认时间和公开 UID 稳定倒序；历史对象不可变或单调收敛，不使用大偏移分页。

容量详情示例：

```json
{
  "deploymentCode": "dpl_4s8V...",
  "portNo": 1,
  "capacityVersion": 18,
  "runtimeVersion": 42,
  "asOf": "2026-07-23T09:15:30.123Z",
  "baseline": {
    "state": "VALID",
    "versionNo": 4,
    "bagQr": "BAG_B9y...",
    "tareWeightKg": "1.30",
    "establishedAt": "2026-07-23T09:12:30.123Z"
  },
  "latestSnapshot": {
    "detectionUid": "80f3b14f-...",
    "trigger": "CLEAN_COMPLETE",
    "status": "COMPLETED",
    "disposition": "APPLIED",
    "detectedAt": "2026-07-23T09:12:45.123Z",
    "stableTotalWeightKg": "1.30",
    "rawNetWeightKg": "0.00",
    "fullnessPercent": "0.00",
    "infraredStatus": "CLEAR",
    "weightStatus": "RELIABLE",
    "decision": "NOT_FULL",
    "reason": null
  },
  "detectionGate": "READY",
  "activeBaselineMeasurement": null,
  "currentFullnessEvent": null,
  "blockers": []
}
```

空值和含义：

- `baseline.state` 固定为 `UNINITIALIZED/VALID/INVALID`。没有有效基准时其余基准字段为 `null`；不能回显旧袋基准冒充当前值。
- `detectionGate` 固定为 `UNKNOWN/PENDING/IN_PROGRESS/READY/FAILED`。`READY` 仍须结合结论、当前袋/基准/规则代际、设备健康和其他阻断判断是否能开门。
- `activeBaselineMeasurement` 只在空袋基准真实重测尚未终结时返回 `measurementUid + status + statusUrl`；它是独立操作阻断，不伪装成满溢检测或占用 `currentDetection` 指针。
- `fullnessPercent=max(rawNetWeight,0)/threshold*100`，保留两位且允许超过 100%；重量、基准或阈值不可用时为 `null`。负原始净重完整返回，不能被 0 覆盖。
- `latestSnapshot` 明确标注 `detectedAt`，Web 和小程序文案必须使用“上次检测值”，不得称为实时重量或实时满溢度。
- `currentFullnessEvent` 只在持续事件为 `ACTIVE` 时出现，包含首次/确认/最近检测时间、当前原因、检测次数和派生的 `over24Hours/over48Hours`；工作人员已查看告警不改变持续时间或恢复事实。
- 工作人员小程序省略基准内部版本、原始故障诊断和历史采样，只返回当前机构运营所需的投口、上次检测值、持续时长和安全提示。

### 2. 人工满溢重新检测

普通 Web 与平台镜像：

```http
POST {deploymentBase}/ports/{portNo}/fullness-rechecks
Idempotency-Key: <UUIDv4>
```

```json
{
  "expectedCapacityVersion": 18,
  "reason": null
}
```

服务端要求 `device.detection.execute`，并在资产/投口/容量锁下重新检查：

- 无投递或清运整机占位，原投口没有未终结清运操作，全部投递门可靠关闭且清运电磁阀断电；
- 香橙派、MCU 和本次模式需要的传感器当前可用，设备能够可靠保存检测上下文；
- 没有另一条未终结满溢检测或空袋基准重测；容量当前袋、按模式可空的基准和满溢规则指纹与请求读取版本一致；
- 投口不存在 `DELIVERY_RESULT_PENDING`。该阻断必须使用第 4 节专门恢复，以防普通重检绕过现场核查。

成功事务创建唯一 `MANUAL_RECHECK` 检测、容量当前 gate 和可靠设备任务，返回 `202 + detectionUid + statusUrl`。人工重检只执行一次真实采样，不走自动投递的“疑似满后再等 10 秒确认”流程：

- 适用于当前袋/基准/规则且可靠 `NOT_FULL`：检测 `COMPLETED/APPLIED`，恢复活动满溢事件；没有其他阻断时投口重新可用。
- 可靠 `FULL`：创建或延续同一活动满溢事件并更新最近检测，不重置首次确认和 24/48 小时时钟。
- 所需来源失败：检测 `FAILED`，已有满溢事件保持活动，没有事件时容量 gate 为 `FAILED`；不能把失败解释为不满。
- 检测期间袋、基准或规则发生变化：保存为 `STALE_IGNORED`，不覆盖当前容量或事件。需要判断新代际时再创建新检测。

终态检测不能重开。迟到设备结果只保存为设备证据；不得补写终态 sample 或改变容量。相同检测的可靠任务技术重试不创建第二检测。

### 3. 当前空袋重量基准重测

新袋重量无效时，不能用人工输入皮重恢复。普通 Web 与平台镜像使用：

```http
POST {deploymentBase}/ports/{portNo}/baseline-remeasurements
Idempotency-Key: <UUIDv4>

GET  {deploymentBase}/ports/{portNo}/baseline-remeasurements/{measurementUid}
```

```json
{
  "expectedCapacityVersion": 18,
  "expectedRuntimeVersion": 42,
  "emptyBagConfirmed": true,
  "reason": null
}
```

该命令要求 `device.recovery.execute`，且只用于当前袋基准为 `UNINITIALIZED/INVALID` 的恢复：

1. 当前投口必须有明确袋绑定，没有整机作业、未终结清运、投递结果待处理或进行中的检测；全部投递门可靠关闭且清运电磁阀断电。
2. 操作者必须现场确认当前袋为空；省略或 `false` 返回校验错误。该确认只说明为什么允许测量，不能代替设备读数。
3. 香橙派、MCU 和称重模块当前健康、校准版本有效且能够可靠落盘；已锁存的严重称重安全故障必须先按第 5 节恢复。
4. 同一事务创建唯一 `measurementUid`、冻结当前袋/配置/容量代际并创建可靠称重任务，返回 `202 + statusUrl`。活动重测自身阻止新作业，但不把容量 gate 伪装成一条尚不存在的满溢检测。

设备返回非负、稳定、量程内且身份/代际匹配的真实总重量时，完成事务创建来源为 `MANUAL_REMEASUREMENT` 的新基准版本，以同值更新当前总重量和原始净重 0；随后创建新的 `MANUAL_RECHECK` 检测并把容量 gate 指向它，直至该检测可靠结束。测量失败、负值、过载或不稳定时只保存失败证据，基准继续无效并维持原容量阻断；袋、配置或容量代际变化时保存为过期结果并由新代际状态继续决定 gate。两类结果都不能建立基准、沿用旧值或直接开放投递。`GET` 返回测量状态、真实设备结果的安全摘要、可空新基准版本和后续检测 UID，不返回内部任务或原始协议报文。

P0 不允许通过本接口修改传感器校准参数，也不允许对已有有效基准随意“重新置零”；正式校准属于以后维护设计。

### 4. 投递结果待处理的人工安全恢复

当原投递会话无法形成可信最终结果，且原投口长期保持 `DELIVERY_RESULT_PENDING` 时，普通 Web 与平台镜像使用：

```http
POST {deploymentBase}/ports/{portNo}/delivery-result-recoveries
Idempotency-Key: <UUIDv4>
```

```json
{
  "expectedRuntimeVersion": 42,
  "expectedCapacityVersion": 18,
  "pendingSessionUid": "8d476b7d-...",
  "bagAndContentsInspected": true,
  "doorAndSensorsInspected": true,
  "reason": null
}
```

该命令要求 `device.recovery.execute`。服务端必须精确锁定并复核：

- 当前投口指针仍等于请求 `pendingSessionUid`，对应会话确有设备受理或更后物理进展、没有可信首次开门前失败，且尚无订单/投递后检测接管；
- 没有可直接重试处理的可信完成 inbox/物理结果。已有可信结果时必须优先恢复原入站任务，不能用人工恢复跳过建单；
- 没有整机作业或原投口未终结清运，全部投递门可靠关闭、清运电磁阀断电且旧授权不会再执行，袋绑定和容量代际仍与现场核查目标一致；
- 两项现场确认均为 `true`，当前检测模式需要的基准/传感器足以执行真实重检。投递结果待处理与重量模式所需基准同时无效时，不能先调用第 3 节空袋重测，因为当前袋内容物是否为空并不可信；应先按 I-026/I-028 完成一次真实清运换袋。清运完成会建立新袋容量 gate 并安全接管该指针，随后新基准仍无效时才使用第 3 节恢复。

成功事务**先**创建新的 `MANUAL_RECHECK` 检测并把当前容量 gate 置为 `PENDING`，再条件清除精确匹配的待处理会话指针，二者之间不得出现可提交空窗；返回 `202 + detectionUid + statusUrl`。原投口仍因新 gate 阻断，只有真实检测可靠不满且不存在其他阻断时才恢复。

本操作不创建、删除、审核或改挂投递订单，也不伪造原会话重量。以后到达的可信原 `DELIVERY_COMPLETE` 仍按 I-023/I-024 为原 `sessionUid` 建立唯一订单；由于容量当前检测和代际已前进，其旧袋检测只能按过期规则保存，不能覆盖人工恢复后的新容量事实。I-028 的真实清运完成也可以在新袋和 `CLEAN_COMPLETE` 检测 gate 同时成立后安全清除仍匹配的指针，不需要先调用本接口。

### 5. 严重安全锁恢复

普通 Web 与平台镜像分别对整机或投口提供：

```http
POST {deploymentBase}/safety-recoveries
POST {deploymentBase}/ports/{portNo}/safety-recoveries
Idempotency-Key: <UUIDv4>
```

```json
{
  "faultUid": "f7ab3aed-...",
  "expectedRuntimeVersion": 42,
  "inspectionConfirmed": true,
  "reason": null
}
```

该命令要求 `device.recovery.execute`，并只恢复请求精确指向的活动 `SAFETY_BLOCKING` 故障：

1. 当前没有仍在驱动投递门、给清运电磁阀通电或执行其他物理动作的命令；全部相关投递门可靠关闭，清运电磁阀断电且旧命令不会再执行。清运门没有门磁，断电只形成推定关闭；仍处于 `RECOVERY_REQUIRED` 的清运操作必须由原清运员按 I-027 恢复，本接口不能替代其现场确认。因本故障而安全停住、仍保留占位/操作身份的原投递或清运作业允许存在，但本恢复事务既不结束它也不释放占位。
2. 设备已经在新鲜心跳/自检证据中报告对应组件正常，且证据发生在故障最后发现之后；普通一次心跳不能自行解除锁。
3. 操作者提交 `inspectionConfirmed=true`，代表已经完成需求规定的现场检查；原因仍选填。
4. `faultUid`、作用域、运行版本和当前活动故障精确匹配。设备仍异常、故障已经恢复或版本变化均返回冲突/不可恢复，不静默清除别的锁。

成功本地事务把精确故障事件推进为 `RECOVERED`、保存人工恢复审计并条件解除对应安全锁，返回 `200` 和新 `runtimeVersion`。其他活动严重故障继续阻断；恢复安全锁不改变满溢事件、容量 gate、重量基准、清运操作、投递会话/占位、投递待处理指针、订单或经营开关。若原作业此前因本故障停住，恢复后只唤醒原协调任务重新判断，不能在本事务内反向取得 recycling 锁或直接宣称作业已恢复。

本章不提供任意运行状态覆盖、后台直接清满、直接填写皮重、修改设备故障终态、远程任意开门、称重校准、其他人员接管清运或删除设备证据的接口。

主要错误包括：

```text
403 AUTH.CAPABILITY_REQUIRED
404 RESOURCE.NOT_FOUND
409 DEVICE.CAPACITY_VERSION_CONFLICT
409 DEVICE.RUNTIME_VERSION_CONFLICT
409 DEVICE.DETECTION_ALREADY_ACTIVE
409 DEVICE.RECOVERY_TARGET_CHANGED
409 CLEAN.PORT_OPERATION_ACTIVE
409 DEVICE.DEVICE_BUSY
422 DEVICE.DOOR_NOT_SAFE
422 DEVICE.SENSOR_UNAVAILABLE
422 DEVICE.BASELINE_REMEASUREMENT_NOT_ALLOWED
422 DEVICE.TRUSTED_RESULT_RETRY_REQUIRED
422 DEVICE.SAFETY_RECOVERY_NOT_ALLOWED
400 COMMON.VALIDATION_FAILED
```

## 本批跨端落实约束

1. 当前 OneNet `openCleanDoor(cleanOrderId)`、`cleanGross`、`cleanTare` 和旧 D1 UART 清运流程不能承载本章契约。正式协议必须以 `operationUid/commandUid/eventUid/edgeEventSequence` 为根，能表达上下文可靠落盘、首次解锁可能执行、操作内再次解锁、锁断电推定关闭、清运员最终确认、称重可靠性和业务确认。
2. 香橙派必须在 SQLite 原子保存清运操作、旧袋/基准、新袋预留摘要、执行期限、每次再次解锁序号、锁输出事实、清运员最终确认、最终称重、照片本地位置和待确认事件。只存内存 `cleanOrderId` 不满足恢复要求。COS 临时凭证不得持久化为长期秘密；恢复或补传需要新凭证时必须沿原 `operationUid` 获取同范围授权，不能创建第二业务操作。
3. MCU 负责清运屏幕/按钮、电磁阀通断和稳定称重；清运员负责手动关闭门扇，硬件没有清运门门磁。香橙派负责操作身份、基准、照片、结果计算和可靠通信。正式 UART 必须有版本、长度、序号、ACK/NACK、错误码、CRC 和有符号克值，不能继续依赖无确认的文本开门帧。`SAFE_CLOSE` 只适用于具有真实门控反馈的投递门，不适用于清运门；安全初始化最多使清运电磁阀断电，不能报告物理关门成功。
4. 目标后端只接收 OneNet 可信入站并按 inbox/可靠任务处理。匿名 `/api/iot/clean/**` 和明文 SN 信任不得进入生产新链路。
5. 机器可读 Schema 和跨端契约测试至少覆盖：旧袋缺失、并发扫描同袋、开始授权过期、首次解锁前结束竞争、首次通电 ACK 丢失、操作内多次再次解锁、锁断电但门位未知、超时/重启后原清运员恢复、缺少清运员确认不得完成、完成事件重投/冲突、照片缺失、新基准无效、清运与迟到投递结果竞争、满溢重检三种结论、基准重测过期、待处理指针恢复和多故障只恢复精确目标。
