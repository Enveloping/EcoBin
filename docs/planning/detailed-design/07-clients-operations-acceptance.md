# 07｜Web/小程序、运营治理、部署与 M0 验收

> 上级索引：[EcoBin P0 详细设计与任务拆分](../detailed-design-draft.md)
>
> 状态：**已批准；已同步 2026-07-24 投递会话与清运电子锁决策；真实微信场景当前外部阻塞；尚未授权实施**
>
> 审查日期：2026-07-24
>
> 适用基线：A-016～A-020、D-026～D-045、I-001～I-015、I-031～I-040、I-051～I-055、M0 场景 1～12

## 1. 本章裁决

| 编号 | 裁决 |
|---|---|
| DD-031 | Web 建立统一 Cookie/CSRF、十进制金额、幂等写入、版本冲突和异步轮询基础；页面不得各自解释状态或乐观修改资金。 |
| DD-032 | 小程序登录后按服务端单一 `entryMode` 进入普通、清运或管理页；工作人员管理端 P0 只读当前机构统计、容量和告警。 |
| DD-033 | operations 只保存可靠执行、审计、聚合告警、对账问题和只读概览；它不成为第二套订单、设备或资金真相。 |
| DD-034 | 每个 M0 场景都生成机器测试结果和人工证据清单，并明确 `SIMULATED/REAL`；只有 12 个场景和强制真实依赖全部通过才标记 M0。 |
| DD-035 | 7 月 30 日目标不降低真实资金、门安全、租户/机构隔离和恢复要求；微信缺失时只能交付软件模拟闭环，不能用产品文案掩盖。 |

## 2. Web 客户端基础

### 2.1 统一 API 客户端

Web 所有请求通过一个基础客户端：

```text
credentials: include
CSRF token bootstrap/rotation
correlation ID capture
ProblemDetail error mapping
Idempotency-Key lifecycle
expectedVersion handling
202 statusUrl polling
no-store response handling
```

禁止页面自行从 localStorage 读取 Bearer Token。旧前端的 token/userType/数字角色导航必须整体替换。

### 2.2 写请求

每个用户意图在首次点击时生成 UUIDv4 `Idempotency-Key`：

- 网络重试复用原 key；
- 用户修改关键输入后形成新意图，使用新 key；
- 同 key 异请求摘要显示明确冲突，不能自动换 key 重做；
- 重复点击在 pending 期间禁用，但前端禁用不是后端幂等替代。

依赖当前投影的写入携 `expectedVersion` 或 `expectedRevisionNo`。`409` 后刷新服务端事实并让用户重新确认，不静默覆盖。

### 2.3 金额、重量和时间

- 金额和单价只使用字符串/专用 decimal formatter；
- 不用 JavaScript `number` 计算余额、手续费或返现金额；
- 重量输入先按两位千克字符串校验，最终以后端克值/金额为准；
- 时间按服务端 ISO-8601 UTC 解析，页面按明确业务时区展示；
- “上次检测值”必须显示检测时间，不称为实时。

### 2.4 异步状态

`202` 只表示业务意图可靠受理。客户端：

1. 保存 `operationId/resourceId/statusUrl`；
2. 使用后端 `recommendedPollAfterMs`；
3. 页面刷新后可由稳定资源身份恢复轮询；
4. 到终态或不可恢复错误后停止；
5. 不把 task `DONE`、OneNet `code=0`、微信受理或确认页调起显示为业务完成。

## 3. Web 页面边界

### 3.1 平台

```text
平台登录
租户目录与启停
库存资产/机构调试部署
技术可靠任务与 attempts
隔离消息
平台告警
系统商户对账运行/问题
NOT_ENOUGH 出款闸门恢复
平台镜像业务详情
```

平台路径显式带目标租户/机构公开号。平台管理员不能被伪装成租户工作人员，平台操作使用分型操作者并完整审计。

### 3.2 租户/机构 Web

```text
当前会话和机构范围
机构、工作人员、任职、权限和小程序绑定
机构用户、冻结/恢复、清运能力、钱包调账
设备、投口、配置、运行和容量
投递订单审核/纠错
清运操作/记录审核、袋追溯
充值、机构账户、提现审核/处置
告警、审计、对账问题
最小运营概览
```

菜单只用于改善体验；每个请求仍由后端实时能力判断。租户主体和机构负责人天然权限在会话视图中体现，但后端不能因为页面路径跳过校验。

## 4. 小程序路由

一次 `wx.login` 返回：

```text
audience
entryMode = MANAGEMENT | CLEANING | USER
expiresAt
organization
principal safe view
```

客户端只保存当前受众短期 Token，并按服务端入口跳转：

| entryMode | 页面 |
|---|---|
| `MANAGEMENT` | 当前机构精简统计、设备容量/满溢、活动告警 |
| `CLEANING` | 清运扫码、操作状态和原操作恢复 |
| `USER` | 设备扫码投递、订单、钱包和提现 |

P0 不提供客户端模式切换。会话 401 时清除当前 Token，最多自动执行一次 `wx.login`，避免循环。

管理入口只允许白名单：

```text
statistics.read
alert.read
```

即使工作人员在 Web 有写权限，小程序也不能执行价格、人员、审核、调账、恢复或告警确认。

## 5. operations 应用结构

```text
reliable-task/
  QueryReliableTaskUseCase
  ResumeBlockedTaskUseCase
quarantine/
  QueryQuarantineUseCase
  AcknowledgeQuarantineUseCase
audit/
  QueryAuditUseCase
alert/
  QueryAlertUseCase
  AcknowledgeAlertUseCase
reconciliation/
  RunDailyReconciliationUseCase
  QueryReconciliationIssueUseCase
  RequestReconciliationActionUseCase
statistics/
  QueryOperationsOverviewUseCase
```

operations 可以调用各业务模块公开查询/受控恢复端口，但不能直接更新它们的表。

## 6. 可靠任务和隔离处置

平台只可以：

- 查询任务/attempt；
- 对 `BLOCKED` 且无活动租约的原任务提交恢复；
- 确认永久隔离项已经看过。

恢复必须保留：

```text
taskUid
taskKey
target
payload digest
external business ID
attempt history
```

只递增 `wakeVersion` 并写审计。人工 `causeFixedConfirmed=true` 不保证成功，worker 仍重新检查领域事实。

隔离确认只做 `OPEN → ACKNOWLEDGED`，不创建处理任务、不重放消息、不猜测租户/用户/设备，也不建订单。

## 7. 操作审计

审计解释：

```text
谁
通过什么入口/会话
对哪个稳定目标
请求什么动作
系统是否接受
何时发生
可空原因
```

它不替代订单 revision、资金明细、设备事件或微信观察。

审计：

- 只追加，不提供编辑/删除/补写/重新归属；
- 按记录固化的作用域重新授权；
- 普通租户看不到平台/未解析审计；
- 平台安全视图也只返回脱敏 IP/User-Agent 摘要；
- 不返回请求正文、密码、手机号、OpenID、AppSecret、密钥、原通知或 SQL。

## 8. 聚合告警

同一持续问题只有一条活动告警：

```text
sourceKey
scope
severity
firstDetectedAt
lastDetectedAt
occurrenceCount
acknowledgedAt?
resolvedAt?
```

人工确认只表示“已看到”，不会：

- 恢复设备或安全锁；
- 改变满溢；
- 打开出款闸门；
- 释放资金；
- 解决对账问题。

只有来源领域真实恢复或检测器证明条件消失后，告警才 `RESOLVED`。解决后复发创建新告警身份。

工作人员小程序只返回当前机构精简安全视图，没有确认端点。

## 9. 每日资金对账

P0 对账是内部逐交易收敛，不是微信官方账单文件归档。

每个系统商户、每个已经结束的北京时间业务日唯一一个 run。自动补建遗漏日期，不允许人工建立第二轮。

扫描：

- 充值单、支付观察和机构 `RECHARGE_POSTED`；
- 提现、双侧钱包/机构明细、活动槽；
- 微信转账观察和本地终态；
- 账户投影与最后明细；
- 平台闸门和相关观察。

明确可补齐的遗漏唤醒原领域任务；相反终态、单侧资金、证据不足或投影不一致创建 reconciliation issue 和聚合告警。

人工动作只允许：

```text
添加备注
标记已处理
请求原单查证
请求受控原任务重试
```

没有人工 `RESOLVE`。只有系统重新读取权威事实并证明一致，才追加 `RESOLUTION_VERIFIED` 并解决问题。

## 10. 最小运营概览

查询最长 31 个 Asia/Shanghai 业务日：

- 普通 Web：当前全部授权机构或指定可见机构；
- 平台：指定租户，可选其机构；
- 工作人员小程序：固定当前机构；
- 不提供跨租户排行。

operations 显式开启只读 `REPEATABLE READ`，首次读建立快照；identity/device/recycling/funds/operations 查询端口以 `REQUIRED` 加入。

响应分开：

```text
period totals        # 业务日期范围
current snapshot     # asOf 当前待办、设备、告警、资金
organizations[]
```

该事务禁止写、锁行或外调。M0 不建立可修正统计表、日累计、余额缓存或复杂报表。纠错后历史指标按当前权威认定自然变化。

## 11. 环境和部署

### 11.1 环境

| 环境 | 外部连接 |
|---|---|
| 开发 | Fake OneNet/微信/COS；不得触发真实设备或资金 |
| 自动测试 | 临时 MySQL 8.4、SQLite 临时持久目录和 Stub；网络硬阻断 |
| M0 acceptance | 真实试点 OneNet/COS/微信、白名单和小额上限；人工 opt-in |
| 生产 | 独立数据/凭证、真实适配器、严格缺配置失败 |

目前 OneNet、COS、真机可联调，微信支付/转账不可联调。因此 acceptance 暂时只能对非微信场景标记 `REAL`。

### 11.2 单机部署

M0 受控环境：

```text
Nginx :443
  → Web static
  → backend /api
backend single instance
MySQL 8.4
```

- 80 只跳转 HTTPS；
- 8080/3306 不暴露公网；
- OneNet、COS、微信由后端/香橙派主动出站；
- 微信通知只开放精确路径；
- OneNet 不需要公网入站回调；
- readiness 校验数据库纪元和必要内部组件；
- 第三方短暂不可用只影响任务/告警，不触发重启风暴。

切换仍按第 01 章成对执行。

## 12. M0 证据包

每次验收建立一个不含秘密的 evidence manifest：

```text
runUid
startedAt/completedAt
app commit + artifact digest
database epoch/version
Web/miniprogram version
edge version
MCU firmware/protocol version
tenant/organization/deployment public test identities
configuration versions/digests
scenario results[]
external dependency status
reviewer/operator
```

每个场景：

```text
scenarioNo
mode = REAL | SIMULATED
preconditions
steps
stable business references
automated test report
manual evidence references
expected/actual
result
known limitations
```

证据引用可以指向受控截图、脱敏日志、数据库校验报告和真机视频/照片位置；文档本身不得嵌入密钥、完整手机号、OpenID、支付凭证或原回调。

## 13. 十二个场景的完成判据

| # | 场景 | 必须证明 | 当前可用性 |
|---:|---|---|---|
| 1 | 机构与身份 | 真 AppID 登录/手机号绑定；第二机构账号、钱包、业务越权失败 | 微信登录/手机号能力需实际核验 |
| 2 | 机构充值 | 真扫码、0.6% 向上取整、净额一次入账 | **微信阻塞** |
| 3 | 正常投递 | 真机开关门、称重、四图 COS、建单、审核入账一次 | 可联调，需新协议/实现 |
| 4 | 连续投递 | 一次扫码本地继续至少两轮，中间不上云，结束/超时只形成一个完成事件和一笔订单 | 可联调，需新协议/实现 |
| 5 | 负重量与纠错 | 本地下降达到默认 500g 阈值只在最终载荷带布尔标志；审核前不扣、审核后扣、纠错只差额 | 可由真机/测试数据 |
| 6 | 断网与重复 | 门安全、原作业恢复、不重单、不串后来用户 | 可故障注入 |
| 7 | 清运换袋 | 新袋、首次解锁、再次解锁、人工关门确认、换袋、基准和记录 | MCU 配合后可联调 |
| 8 | 手动提现成功 | 双冻结、审核、确认收款、真零钱到账 | **微信阻塞** |
| 9 | 渠道异常 | 超时/重复/FAIL/CANCELLED/乱序不多扣多退 | Fake 可完成 |
| 10 | NOT_ENOUGH | 暂停新出款、原单冻结、人工恢复后原单续办 | Fake 可完成；不得耗尽真实账户 |
| 11 | 安全故障 | 投递门/称重/存储故障停止服务；清运电磁阀推定状态和人工确认限制后台可见 | 可故障注入/真机 |
| 12 | 重启恢复 | 充值、投递、清运、提现关键边界幂等 | 非微信可真；微信部分阻塞 |

只有：

```text
12 个场景通过
and 场景 2/8 真实
and OneNet/COS/MCU/试点资源真实
```

才允许 `milestone=M0`。否则使用：

```text
SOFTWARE_SIMULATION_COMPLETE
DEVICE_CONTROLLED_SLICE_COMPLETE
```

等准确描述。

## 14. 验收门槛

### 自动测试

- Maven 模块编译/测试；
- MySQL 8.4 迁移、约束、权限、事务、并发；
- adapter 固定样例；
- Python 3.11 SQLite 强杀/重启；
- UART 编解码黄金向量；
- Web API 状态和金额 formatter；
- 小程序入口/提现状态单元测试。

### 人工

- MCU 投递门、清运电磁阀、重量、按钮、CRC/重试、人工关门确认和断电；
- OneNet 下行、北向上行、业务确认/回执；
- COS 四图和缺图；
- Web/小程序真实身份和权限；
- 微信真实 Native 充值、通知、查单；
- 商家转账、确认页和零钱到账；
- 成对切换与闩锁前回退。

I-055 只允许共同首版前暂不建立完整自动 CI/自动 HIL 门禁，不取消上述人工资金和物理验收。

## 15. 设计追踪项（已映射到正式任务）

以下本章编号只用于覆盖追踪；正式任务、依赖和状态见
[`p0-controlled-loop`](../tasks/p0-controlled-loop/00-index.md)。

| 草案 ID | 标题 | 类型 | 依赖 | 验收结果 |
|---|---|---|---|---|
| CLI-01 | Web Cookie/CSRF 与统一 API 客户端 | AFK | IAM-01、OpenAPI | 登录无 JS Token，幂等/版本/202/错误统一处理。 |
| CLI-02 | Web 身份、设备与审核工作台 | AFK | 对应后端切片 | 人员/设备/投递/清运页面按能力和真实状态工作。 |
| CLI-03 | Web 资金与运营工作台 | AFK | 资金/operations | 充值、提现、闸门、告警、对账不乐观伪成功。 |
| CLI-04 | 小程序单 Token 入口与普通投递/钱包 | AFK + 真机 HITL | IAM/DEL/FUNDS | USER 入口完成绑定、订单、钱包、提现。 |
| CLI-05 | 小程序清运与管理只读入口 | AFK + 真机 HITL | IAM/CLN/OPS | CLEANING 可清运，MANAGEMENT 仅当前机构只读。 |
| OPS-01 | 技术任务、隔离、审计和告警 | AFK | FND-05 | 原任务恢复、确认不等于解决、作用域与脱敏正确。 |
| OPS-02 | 内部资金对账和受控处置 | AFK | 完整资金主链 | 逐交易收敛、问题只追加、系统验证后才解决。 |
| OPS-03 | 最小一致运营概览 | AFK | 各域查询端口 | RR 同快照、最长 31 日、无第二套统计真相。 |
| ACC-01 | M0 evidence runner 与 12 场景清单 | AFK + HITL | 所有切片 | 每个场景有版本、模式、自动/人工证据和结果。 |
| ACC-02 | 新旧栈切换与受控验收 | HITL | FND-07、ACC-01 | 所有权唯一、闩锁前回退和真实入口清单通过。 |

## 16. 主审否决项

- Web 继续使用 localStorage Bearer Token 或客户端 `userType`；
- 页面自行计算资金、手续费或业务终态；
- 202、任务 DONE、OneNet ACK 或微信受理显示为业务完成；
- 工作人员小程序开放写操作或跨机构切换；
- 平台管理员伪装租户员工复用普通路径；
- operations 直接更新订单、钱包、设备或渠道终态；
- 人工任务恢复创建新 task/外部单号；
- 隔离确认自动重放消息；
- 告警确认同时恢复来源；
- 对账人员手工标记问题已解决或直接覆盖余额；
- 用累计表/缓存形成第二套 M0 经营真相；
- 开发/自动测试连接真实设备、COS 或微信；
- M0 证据混用模拟和真实而不标注；
- 微信未联调却宣称机构充值、零钱到账或完整 M0；
- 因 7 月 30 日排期跳过租户隔离、资金双侧原子、门安全或恢复测试。
