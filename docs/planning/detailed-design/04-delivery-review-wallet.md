# 04｜投递、审核纠错与钱包入账纵向闭环

> [!IMPORTANT]
> 2026-08-02：投递完成事务已取消后端满溢检测 gate/采样任务。订单照常提交，设备后续独立上报当前袋状态；只有明确 `FULL` 影响下一场投递。下方相反描述由 [`../../architecture/fullness-reporting-v25.md`](../../architecture/fullness-reporting-v25.md) 覆盖。

> 上级索引：[EcoBin P0 详细设计与任务拆分](../detailed-design-draft.md)
>
> 状态：**已批准；已按 2026-07-24 会话级结算决策修订；尚未授权实施**
>
> 审查日期：2026-07-24
>
> 适用基线：R-051～R-080、D-014～D-020、D-036～D-040、I-021～I-025、I-041～I-050

## 1. 本章裁决

| 编号 | 裁决 |
|---|---|
| DD-021 | 一次有效扫码建立一个投递 session；该 session 内可以本地继续开关投递门任意次，但云端不建立中间投递周期，不接收中间重量、照片或过程事件。 |
| DD-022 | session 开始时冻结用户、机构、部署、投口、袋、单价、配置和规则；首次开门前重量与最终结束后的关门重量共同形成唯一 `DELIVERY_COMPLETE`，一个 session 最多形成一笔订单。 |
| DD-023 | 普通满溢判断只在整场投递结束后执行并影响下一会话；当前用户本地继续投递不等待云端、不因普通满溢判断被拒绝，门卡滞、执行器故障、称重过载、烟雾等硬安全故障仍必须阻止再次开门。 |
| DD-024 | 设备按下发的正整数克阈值检测本地轮次重量下降，默认 500 克；任一轮次下降达到阈值即锁存 `negativeWeightAnomaly=true`，只随最终完成载荷上报布尔标志，不单独上报事件或中间减少值。 |
| DD-025 | 投递审核只有“按原数据通过”和“修改最终重量后通过”；首次审核和不限期纠错追加 revision，金额由整场最终重量和 session 锁定单价确定，订单认定与钱包差额在同一事务生效。 |

### 1.1 PDD-001｜开始投递复合事务的所有者

PDD-001 只适用于建立投递 session 的云端授权。继续投递已经改为同一已授权
session 内的设备本地动作，不再触发云端复合事务、下一周期授权或继续解析任务。

    recycling 开启“开始投递”复合事务
      → identity 公开参与端口锁 tenant/organization/current miniapp
      → recycling 锁 delivery config head
      → identity 公开参与端口锁 organization user
      → funds 公开参与端口锁 user wallet
      → device 公开参与端口从 asset 锁根进入
      → device 写 session/occupancy/command
      → 各模块只写自己的表

这保持冻结 Maven DAG，不增加 `device → funds` 或 `device → recycling`。device
仍是资产、session、占位、设备命令和物理结果的唯一事实所有者；recycling 不访问
device 的 Mapper、Entity 或私表。实施必须保留 D-037 的逐节点锁序。

## 2. 纵向组件

### 2.1 device

公开用例和端口：

- `QueryDeliveryOptionsUseCase`；
- `AcquireDeliverySessionPort`；
- `MergeDeliveryDeviceObservationUseCase`；
- `ExpireDeliverySessionResultUseCase`。

device 拥有整机占位、投递 session、设备命令、唯一物理结果、投口待处理 session
指针以及设备安全和执行状态。云端没有 `dev_delivery_cycle`；MCU 内部的继续投递轮次
不是云端领域身份。

### 2.2 recycling

公开用例：

- `StartDeliveryUseCase`；
- `ApplyDeliveryCompleteUseCase`；
- `ReviewDeliveryOrderUseCase`；
- `CorrectDeliveryOrderUseCase`；
- `QueryDeliveryOrderUseCase`；
- `QueryPendingRewardUseCase`。

recycling 拥有投递订单和机构序号、首末重量与金额快照、异常、四个整场照片槽、
审核/纠错 revision 以及投递结束后的满溢检测 gate。

### 2.3 funds

funds 通过公开参与端口完成首次审核钱包入账或扣减、后续纠错差额、负余额停投闸和
提现联动。待审核返现仍由 recycling 订单查询得到，不提前写钱包。

## 3. 扫码、选项与开始授权

设备二维码只携带不可猜测的 `deploymentCode`。扫码查询不取得设备执行权；用户选择
投口后提交稳定 `Idempotency-Key`，由 `StartDeliveryUseCase` 开启复合事务。

事务依次锁定：

    tenant
    → organization
    → current miniapp
    → delivery config head
    → organization user
    → user wallet
    → device asset
    → deployment/runtime/session/occupancy
    → port/runtime/current bag/capacity

开始时必须复核：

- 用户有效且已绑定手机号，钱包投递闸未锁存；
- 租户、机构、小程序、部署、设备和经营开关有效；
- MCU、边缘、投递门、称重和本地存储可安全执行；
- 投口启用、当前袋存在、最高配置已精确应用；
- 上一会话结束后的满溢/检测/基准/清运/结果待处理状态允许新会话；
- 整机没有其他用户或清运作业；
- 当前单价和负重量检测阈值有效。

同一事务：

1. 创建唯一 `dev_delivery_session`；
2. 取得整机 `DELIVERY` 占位；
3. 冻结用户、部署、投口、袋码、单价、配置、投递规则、负余额阈值、人工认定上限和
   `negativeWeightThresholdGram`；
4. 创建以 `sessionUid` 为目标的 `START_DELIVERY_SESSION` 命令和可靠任务；
5. 写入首次开始授权期限和整场结果期限参数。

此时不创建订单、照片、钱包明细或云端投递周期。开始前任一步失败时全部回滚。

## 4. 设备本地投递状态机

香橙派必须先把完整 session、冻结摘要、授权截止点和照片范围可靠写入 SQLite，MCU
才能开始。一次 session 的物理链为：

    保存 session 授权
    → 尝试拍摄首次开门前内外照片
    → 取得首次开门前稳定总重量
    → 可靠落盘后打开投递门
    → 用户投递并关门
    → 取得本地轮次关门后稳定重量
    → 显示“继续投递 / 结束投递”
       ├─ 继续：在同一 session 内本地再次开门
       └─ 结束或选择窗口超时：进入最终收敛
    → 取得最后一次关门后稳定总重量
    → 尝试拍摄最终关门后内外照片
    → 可靠保存唯一 DELIVERY_COMPLETE

投递门仍由 MCU 的执行器、门状态和超时关门能力闭环控制。用户点击继续不向 OneNet
或后端发送请求，不生成新的云端命令或业务身份，也不等待普通满溢、价格、钱包或配置
重新校验。每次准备再次开门时，MCU 仍必须检查当前门状态和硬件安全故障。

中间轮次的重量、照片、按钮和开关门过程：

- 不进入 OneNet 业务事件；
- 不进入后端 inbox、数据库订单或审核证据；
- 不作为普通满溢判断或继续授权条件；
- 可以写入有界、可轮转的本地诊断日志，用于称重漂移和故障排查；
- 不能在日志中形成另一套可结算业务事实。

## 5. 负重量异常标志

配置项 `negativeWeightThresholdGram` 使用正整数克，机构默认值为 500。MCU 或香橙派对
每一本地轮次使用同源稳定称重计算：

    localDeltaG = localAfterWeightG - localBeforeWeightG

若 `localDeltaG <= -negativeWeightThresholdGram`，在当前 session 内锁存
`negativeWeightAnomaly=true`。锁存后直到 session 结束都不能因后续正向投递清除。

最终载荷只携带布尔标志和配置摘要，不携带触发轮次、轮次前后重量、最大减少值或继续
次数。它不是独立事件。后端把最终载荷中的布尔值原样固化到订单；为真时追加
`NEGATIVE_WEIGHT_ANOMALY`。后端可以校验整场首末重量和设备自报净重是否一致，但不得
用整场净重把该标志从 `false` 补判为 `true`，也不能从布尔标志反推出未上报重量。

系统只记录并交给人工审核，不自动认定偷取、不提前扣钱包、不冻结用户。P0 全部订单
本就人工审核；该标志同时为以后审核模式提供强制人工条件。

## 6. 结束、超时与最终完成

用户在最终关门后点击结束，或本地继续选择窗口自然超时，都正常结束同一 session。
选择窗口超时不是异常，也不创建第二个作业。

若用户点击继续但硬安全条件阻止再次开门，设备在门已可靠关闭且末次稳定重量存在时结束
当前 session，并以该重量作为最终重量。门状态不可靠时进入结果恢复，不能伪造完成；
称重暂时不稳定时先在原 session 内恢复，最终确认无法取得可靠重量后，则可靠保存明确的
终态称重故障并用 `null + weightStatus/faultCode` 上报唯一完成事件，后端仍创建系统异常订单，
不得用 0 或任一中间重量代替。

`DELIVERY_COMPLETE` 是该 session 唯一业务完成事件，至少包含：

- `sessionUid`、`commandUid`、`eventUid`、部署和投口；
- 授权/配置摘要；
- 首次开门前和最终关门后带符号整数克；
- 设备自算整场净重及重量可靠性；
- `negativeWeightAnomaly`；
- 四个整场照片槽的状态、身份和可空 URL；
- 最终门状态、硬件故障以及结束原因；
- 不包含满溢采样；后端接收完成并建单时才建立独立检测 gate，后续采样使用自己的 `detectionUid` 和事件身份。

不得包含中间轮次重量、照片或过程列表。

整场结果期限到达仍无可信完成结果时：

- session 进入 `RESULT_PENDING_RECOVERY`；
- 门安全且旧命令不会再执行时，释放整机占位前把 session 写入原投口
  `pendingDeliveryResultSessionUid`；
- 不创建零重量、零金额或虚假订单；
- 可信迟到结果仍只归原 session 和原用户，追加系统恢复异常并强制人工审核；
- session 身份或冻结摘要不能验证时进入技术隔离，不归给后来用户。

## 7. DELIVERY_COMPLETE 权威事务

integration/operations 已完成可信收件后，recycling 发起权威事务：

    按 D-037 从 asset 支配锁根进入
    → deployment/runtime/session/occupancy
    → port/runtime/bag/capacity
    → 原 edge event 与唯一 physical result
    → organization order counter 与订单
    → 最终满溢 detection gate
    → inbox 完成和业务确认任务

事务内：

1. 核对 `eventUid + payloadSha256 + sessionUid + authorizationDigest`；
2. 幂等保存该 session 唯一物理结果；
3. 以后端重算 `rawNetWeightG = finalWeightG - initialWeightG`；
4. 保留设备自报净重并比较，不一致追加系统异常；
5. 使用 session 开始时冻结单价计算原始金额；
6. 分配机构可见订单序号和订单号；
7. 创建唯一订单和四个整场照片槽；
8. 按第 5 节把最终载荷的负重量异常标志原样固化到订单；
9. 创建投递结束后的满溢检测及必要任务/gate；
10. session 推进 `BUSINESS_CONFIRMED/ENDED`；
11. 创建唯一边缘业务确认任务；
12. 匹配时清除原投口待处理 session 指针，由新检测 gate 接管。

任一步失败全部回滚，设备继续用原 `eventUid`（仍指向同一 `sessionUid`）重投。业务确认只表示订单和
必要派生事实已经提交，不表示照片齐全、检测终结、审核通过或钱包入账。

## 8. 重量、单价与金额

存储单位：

- 重量：带符号整数克；
- 单价：元/千克乘 10000 的整数；
- 金额：带符号整数分。

原始金额：

    rawAmountCent =
      roundHalfUp(rawNetWeightG × unitPriceTenThousandth / 100000)

整场只使用 session 开始时展示并冻结的单价；继续期间机构改价不影响当前订单。审核人员
不能输入金额，只能按两位小数千克修改整场最终认定重量，金额仍由后端公式计算。

## 9. 照片、异常与待审核返现

每笔订单固定四个照片槽：

- `BEFORE_INNER`、`BEFORE_OUTER`：首次开门前；
- `AFTER_INNER`、`AFTER_OUTER`：最终结束且投递门关闭后。

中间关门不建立云端照片槽。照片缺失、补传中或永久缺失不阻止订单和返现，也不作为用户
异常。

待审核返现只汇总有归属、`PENDING`、原始金额可靠且严格大于 0 的 session 订单。
负金额、零金额、金额未知和无主事实不进入钱包三项。负重量异常标志即使整场净额为正，
也保留在订单上供审核查看。

## 10. 初审、纠错与钱包差额

审核决定只有：

- `ORIGINAL_APPROVED`；
- `MODIFIED_APPROVED`。

不存在投递驳回。首次审核请求携带 `expectedRevisionNo=0`；修改后通过只接受整场最终
重量和可空原因。recycling 事务：

    锁 current delivery config head
    → 锁 order，校验 PENDING 和 expectedRevisionNo
    → 计算最终重量与金额
    → 插入 INITIAL_REVIEW revision
    → 更新订单当前认定
    → 差额非 0 时调用 funds 写唯一钱包差额
    → 写操作审计

已通过订单的不限期纠错使用当前 `expectedRevisionNo`，只追加 `CORRECTION` revision，
以新旧订单总金额差额改变钱包。它不修改首末原始重量、session 单价、照片、用户归属、
异常标志、既有 revision 或既有微信终态。

两名审核员竞争同一版本时只能一人成功；revision、订单投影、钱包差额和负余额/提现联动
必须同事务提交。

## 11. 查询和客户端

小程序只展示当前机构用户自己的 session 订单。订单详情包含一次扫码的开始/结束时间、
首末重量、整场净重、锁定单价、四图、负重量异常标志和当前认定，不展示中间本地轮次。

Web 的 `delivery.read`、`review.execute`、`delivery.correct` 权限边界、创建水位和稳定
游标规则保持 I-024。列表中的“订单数”按有效 session 订单计数，不再等同于投递门开关
次数。

## 12. 真实 MySQL 与跨端测试

1. 同一完成事件并发重投只形成一个 physical result、订单、四槽、检测 gate 和确认任务；
2. 同一设备两个用户并发开始只有一个取得占位；
3. 扫码或开始前失败不建订单，第一次开门后结束只建一单；
4. 一次 session 本地继续 0、1、N 次都只上报一个完成事件并形成一单；
5. 中间重量、照片和继续按钮不会进入 OneNet 或后端数据库；
6. 继续期间价格变化不改变当前订单；
7. 选择窗口超时使用最后一次可靠关门重量正常结束；
8. 普通满溢只在结束后判定并阻断下一会话；
9. 硬安全故障阻止本地再次开门；
10. 任一本地轮次下降达到阈值时只锁存一个布尔标志，最终载荷不泄漏中间减少值；
11. 正、零、负整场重量及正负 `HALF_UP` 边界正确；
12. 照片 0～4 张均可建单和审核；
13. revision 与钱包差额任一步失败整体回滚；
14. 结果超时和迟到完成只归原 session；
15. 两机构相同自然人、设备和订单完全隔离。

## 13. 设计追踪项（已映射到正式任务）

| 草案 ID | 标题 | 类型 | 依赖 | 验收结果 |
|---|---|---|---|---|
| DEL-01 | 扫码选投口与 session 授权 | AFK | IAM、DEV、钱包、配置 | 单设备只一活动用户，失败不开门。 |
| DEL-02 | session 边缘落盘与首次开门 | AFK + 设备 HITL | DEL-01、可靠 OneNet/UART | 首重、前图和冻结摘要先可靠保存。 |
| DEL-03 | 本地继续与最终完成 | AFK + 真机 HITL | DEL-02 | 中间不上云，结束/超时只形成一个完成事件。 |
| DEL-04 | 一次完成到待审核订单 | AFK + 真机 HITL | DEL-03 | 物理结果、订单、四槽、检测 gate、确认原子成立。 |
| DEL-05 | 负重量标志与系统异常 | AFK | DEL-03/04 | 阈值布尔锁存，不上传中间减少值，不自动处罚。 |
| DEL-06 | 初审/纠错到钱包差额 | AFK | DEL-04、FND-F01 | 两种通过、无限期纠错、并发只一 revision/entry。 |
| DEL-07 | 结果超时和迟到恢复 | AFK + 真机 HITL | DEL-04、满溢检测 | 不造假、不串用户、不按旧容量开放。 |

## 14. 主审否决项

- 为每次本地继续投递创建云端 cycle、订单或完成事件；
- 中间上传重量、照片、按钮过程或负重量具体减少值；
- 继续投递再次调用后端授权，或用普通满溢判断阻止当前用户完成整场投递；
- 发生门卡滞、执行器故障、称重过载或烟雾告警时仍强行继续开门；
- 一次 session 使用多份单价或按中间重量分段结算；
- 把 `negativeWeightAnomaly` 作为独立上报事件；
- 设备“最近用户”会话被后来扫码覆盖；
- 以 OneNet/MQ 消息 ID、当前时间或设备当前用户生成订单身份；
- 用设备自报净重代替后端首末重量重算；
- 重量失败用 0、负重量截为 0；
- 负重量自动认定偷取并直接处罚或扣款；
- 审核前把负金额写入钱包；
- 零重量或缺图标记用户异常；
- 投递审核提供驳回或接受客户端提交最终金额；
- 修改原始重量、原始金额、旧 revision 或既有微信终态；
- 订单 revision 与钱包差额分成提交后补写；
- 结果超时自动生成订单或把迟到结果归后来用户；
- 为展示待审核返现提前写可撤销钱包明细。

## 15. 2026-07-24 修订说明

本次修订以项目负责人最新确认覆盖 2026-07-23 的“每次开关门一个云端周期和一笔订单”
设计。模块职责、租户/机构隔离、审核与钱包同事务、原始事实不可覆盖、真实设备结果和
可靠确认边界不变；变化集中在投递结算单位、继续投递位置、最终照片/重量以及满溢触发
时点。
