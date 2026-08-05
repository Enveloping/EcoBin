# 02｜身份、机构、会话、设备资产与配置

> 上级索引：[EcoBin P0 详细设计与任务拆分](../detailed-design-draft.md)
>
> 状态：**已批准；V-01 已完成，V-02 software 已授权实施，其他任务仍须单独授权**
>
> 审查日期：2026-07-23
>
> 适用基线：R-001～R-050、D-006～D-015、D-036～D-040、I-006～I-020、I-051～I-055

## 1. 本章裁决

| 编号 | 裁决 |
|---|---|
| DD-016 | Web 平台账号与租户工作人员使用独立登录入口、数据库会话、HttpOnly Cookie 和 CSRF；业务请求不信任客户端角色、租户或机构字段。 |
| DD-017 | 首次 `wx.login` 由 identity 在一个事务中创建机构用户、注册归因、普通会话和 funds 空钱包；未绑定手机号也完成注册。 |
| DD-018 | 工作人员小程序只通过 Web 人工绑定免密进入管理页；单次登录按 `MANAGEMENT > CLEANING > USER` 选择一个入口，P0 不提供模式切换。 |
| DD-019 | M0 设备以“平台库存资产 → 指定机构调试部署”建立；资产、部署、全部投口和 UNKNOWN 安全投影必须同事务成立。 |
| DD-020 | 配置是整机+全部投口的不可变完整版本；OneNet 受理、边缘保存和 MCU 应用是不同事实，只有最高期望版本精确 `APPLIED` 才允许新作业。 |

## 2. identity 模块

### 2.1 应用用例

建议按以下能力拆分，而不是继续使用宽泛 `AuthService/UserService`：

```text
platform/
  LoginPlatformAdminUseCase
  ManageTenantUseCase
staff/
  LoginStaffUseCase
  ProvisionStaffUseCase
  ManageMembershipUseCase
  ManagePermissionGrantUseCase
miniapp/
  LoginMiniappUseCase
  BindPhoneUseCase
  SetStaffMiniappBindingUseCase
  RevokeStaffMiniappBindingUseCase
organization-user/
  QueryOrganizationUserUseCase
  FreezeOrganizationUserUseCase
  ManageCleanerCapabilityUseCase
session/
  ResolveTrustedSessionUseCase
  RevokeSessionUseCase
authorization/
  ResolveCapabilitiesUseCase
```

类名可以调整，但平台账号、工作人员账号、机构用户和三类会话不能重新压成一张“用户+角色”表。

### 2.2 可信执行上下文

framework 在认证过滤器完成后建立不可变上下文：

```text
principalKind
principalUid
audience
tenantUid?
activeOrganizationUid?
sessionJti
authVersion
requestId
```

业务用例按服务端会话和当前数据库事实解析租户、机构与能力。以下输入只作寻址候选，不能决定可信作用域：

- Web 请求体/查询参数中的 tenant/organization；
- 小程序自报 AppID、OpenID 或角色；
- JWT 中长期缓存的角色/权限；
- 设备二维码中的租户或机构明文；
- 前端菜单是否显示某按钮。

MyBatis 租户拦截器负责 `tenant_id` 基线，机构级业务表还必须显式加入 `organization_id` 条件和复合外键。

## 3. Web 登录与会话

平台和工作人员分别使用冻结路径，登录请求只包含登录名和密码，并先取得 CSRF Token。

成功后服务端设置：

```text
__Host-ecobin-web-session
Secure
HttpOnly
SameSite=Lax
Path=/
无 Domain
```

JavaScript 只读取独立 CSRF Token，并在非安全方法发送 `X-CSRF-TOKEN`；不得读取、保存或刷新 Web 会话 Token。

会话：

- 绝对有效期 8 小时，不滑动续期；
- 每个 `jti` 在 MySQL 有一条会话记录；
- 每次请求复核账号状态、租户/机构状态、`authVersion` 和必要权限；
- 禁用账号、任职/权限变化、改密或主体失效时，相关会话立即撤销；
- P0 不使用 Refresh Token，过期后重新输入密码。

登录名规则：

- Web 不输入租户编码；
- 工作人员登录名在所有租户间全平台唯一；
- 平台账号使用独立命名空间；
- 不同入口统一返回无账号枚举信息的 `AUTH.INVALID_CREDENTIALS`。

## 4. 小程序登录、注册与钱包初始化

### 4.1 入口验证

`wx.login` 请求携带：

```text
wxLoginCode
currentAppId
optional deploymentCode from scanned QR
```

integration 的微信登录适配器按后端识别的小程序配置换取 OpenID。日志不得保存 login code、完整 OpenID 或微信完整错误正文。

identity 校验：

1. AppID 全平台唯一；
2. 对应租户、机构和小程序均有效；
3. 登录开关开启；
4. 可选部署码由 device 解析，且部署属于同一租户、机构和 AppID 入口。

### 4.2 首次注册事务

锁序按 identity 根执行：

```text
tenant
→ organization
→ organization miniapp
→ organization user unique identity
```

同一事务：

1. 以 `AppID + OpenID` 幂等查找/创建机构用户；
2. 首次创建时由服务端写不可变 `registeredAt`；
3. 有可信设备上下文时一次写 `registeredViaDeploymentRef`，否则为 `NULL`；
4. 调用 identity 定义、funds 实现的 `OrganizationUserRegistrationParticipant` 创建零余额钱包；
5. 创建对应普通/清运或工作人员小程序会话；
6. 提交后返回安全视图。

钱包创建失败时用户和会话整体回滚。重复 `wx.login` 不重新创建钱包，也不能回填或覆盖注册来源。

DD-004 已确认把 `OrganizationUserRegistrationParticipant` 放在 `identity.api.port`，funds 只提供实现 Bean，不在 `funds.api` 重复声明。参与命令同时携带公开机构用户 UID、创建钱包所需受信事实和仅限当前线程/事务的强类型机构用户 FK 构造引用；后者只用于 funds 写自己的复合外键，不参与寻址、授权或幂等。该接口是单一必需 Bean，只在首次实际创建机构用户时调用；重复登录不调用，不按 `List` 注入，也不扩展为通用事件或插件链。funds 不得回查 identity 私表，任何参与失败都使用户、钱包和会话整体回滚。

未绑定手机号的用户：

- 已经是正式机构用户；
- 进入地推注册统计；
- 可以查看允许页面；
- 不能投递和提现，相关用例返回 `IDENTITY.PHONE_BINDING_REQUIRED`。

### 4.3 注册设备归因

注册来源只服务地推统计：

- 引用不可变的设备部署，不引用可调拨资产；
- 只在首次创建机构用户时决定；
- 直接进入小程序时为空；
- 后续扫码、设备调拨、手机号绑定或重新登录都不能修改；
- 不参与权限、钱包、投递资格或自动付款。

## 5. 手机号首次绑定

P0 只接受微信 `getPhoneNumber` 动态码，不接收客户端自报号码或旧 `encryptedData/iv`。

流程：

1. 普通机构用户会话提交动态码和稳定 `Idempotency-Key`；
2. integration 按当前 AppID 调微信换取手机号；
3. identity 规范化为 E.164；
4. 锁当前机构用户并检查尚未绑定；
5. 以 `(tenant, organization, phone_e164)` 唯一约束防止同机构重复钱包用户；
6. 写 `phoneBoundAt`，不改变注册时间/来源；
7. 返回脱敏号码。

动态码已消费但本地失败时，客户端取得新动态码并复用原业务幂等键；选择另一个号码属于新意图，使用新幂等键。

完整手机号不进入 GET URL、普通日志、审计摘要或错误详情。

## 6. 工作人员、负责人和可配置权限

### 6.1 账号与任职

- 一个租户主体账号自动拥有本租户全部业务权限；
- 总部工作人员可通过租户级任职/授权覆盖多个机构；
- 一个机构可有多个机构负责人；
- 机构负责人天然拥有本机构全部管理能力；
- 普通工作人员可以同时具有客服、审核等多个可配置能力；
- 租户总部和机构负责人可以授予权限；
- 禁用工作人员、撤销任职/授权均保留历史并立即使相关会话失效。

跨模块只使用能力码，例如 `review.execute`、`wallet.adjust`，不得把“客服”“审核员”重新做成互斥数字角色。

### 6.2 工作人员小程序绑定

绑定前目标机构用户必须已绑定手机号。Web 操作者：

1. 在有权限的机构范围以 POST 正文提交完整手机号精确查找；
2. 读取工作人员当前绑定和机构用户当前绑定；
3. 提交两侧 `bindingUid + version` 或显式 `null` 快照；
4. 后端校验同租户、同机构/AppID、工作人员有该机构管理路径；
5. 同一事务撤销冲突旧绑定/会话并创建新绑定；
6. 写完整审计。

手机号只辅助人工确认，不能自动匹配工作人员。

下一次 `wx.login` 的入口优先级固定：

```text
有效 staff binding + 当前管理路径 → audience=miniapp-staff, entryMode=MANAGEMENT
否则有 CLEAN_OPERATION 能力       → audience=miniapp, entryMode=CLEANING
否则                               → audience=miniapp, entryMode=USER
```

小程序 Token 绝对有效期 2 小时，无 Refresh Token；过期后重新 `wx.login`。

P0 的明确限制：工作人员绑定有效时默认只进入管理页，不在同一会话切回普通钱包/投递入口。撤销绑定后重新登录才回到清运/普通入口。

## 7. 机构用户冻结与清运能力

冻结机构用户：

- 要求本机构 `user.freeze`；
- `ACTIVE → FROZEN`，递增 `authVersion`；
- 撤销普通/清运小程序会话；
- 阻止新的投递、清运和提现；
- 不修改钱包、订单、提现、工作人员绑定或历史；
- 已开始的门/微信动作继续安全收敛。

恢复只允许 `FROZEN → ACTIVE`，不恢复旧会话。清运能力是机构用户附加能力，不是后台角色；授予/撤销只影响清运资格和普通小程序会话。

Web 不允许创建、删除、合并、迁移或修改机构用户的 AppID/OpenID、机构、注册时间和注册设备。

## 8. device 公开端口

最小业务能力：

```text
resolveTrustedDeployment(deploymentCode, expectedAppId)
registerInventoryAsset(...)
createCommissioningDeployment(...)
publishConfiguration(...)
mergeConfigurationProgress(...)
activateDeployment(...)
changeBusinessSwitch(...)
queryDeliveryOptions(...)
acquire/release physical occupancy through compound use cases
```

其他模块不读取 device Mapper/Entity。设备端口的普通结果返回公开部署码、投口号、配置版本/摘要，不返回裸内部主键；只有 DD-004 明确点名的当前事务 FK 构造端口可以返回逐关系强类型引用，并且只能用于接收模块自有表的复合外键写入。

## 9. 资产与部署

### 9.1 稳定身份

| 对象 | 公开身份 |
|---|---|
| 物理资产 | 不可变 `hardwareSn` |
| 一次部署 | 至少 128 位随机强度 `deploymentCode` |
| 投口 | `deploymentCode + portNo` |

设备二维码携带不可猜测部署码，不直接携带可信租户/机构。后端解析后恢复作用域。

### 9.2 M0 建立部署

平台把库存资产直接建立为指定机构调试部署。事务：

1. 锁资产并确认没有活动部署；
2. 校验租户和机构有效；
3. 创建不可变部署码和 `COMMISSIONING` 部署；
4. 创建资产当前部署槽；
5. 按资产声明的 `1..N` 一次创建全部投口；
6. 创建整机和投口 `UNKNOWN` 运行投影；
7. 建立 `INITIAL_COMMISSIONING` 安全锁。

任一步失败不留下部分部署。P0 不实现调拨；未来调拨仍是结束旧部署、创建新部署，历史事实保留在原部署。

## 10. 生命周期、经营开关和实时资格

三层事实分离：

```text
deploymentLifecycle = COMMISSIONING / ENABLED / DISABLED
businessSwitch       = ON / OFF
runtimeEligibility   = 实时计算
```

激活只允许：

- 部署处于调试态；
- 最高配置已经精确 `APPLIED`；
- OneNet、边缘、MCU 协议和设备身份有可信证据；
- 门、传感器、存储和安全状态满足验收；
- 设备无活动作业。

激活后经营开关仍保持关闭。开启经营开关还要重新检查部署、租户/机构、当前配置、在线、协议、门和安全锁。

实时查询分别计算 `deliveryAllowed` 与 `cleaningAllowed`。满溢、旧袋/基准缺失或投递结果待处理可以阻断投递，但不能不当地阻断一次能够换袋恢复的真实清运；整机/门安全、离线、协议不兼容、存储故障和活动占位仍阻断两者。

## 11. 完整配置版本

### 11.1 发布

配置请求必须覆盖整机和资产声明的全部 `1..N` 投口，包含：

- 展示名称、通用回收单价；
- 投口启停；
- 满溢模式、重量阈值、红外/重量采样参数；
- 投递门/称重相关安全时长，以及清运电磁阀通电保护参数；
- 默认 30 秒 `continueDeliveryWaitMs`，只供设备本地“继续/结束”选择，超时正常结束整场 session；
- 正整数克 `negativeWeightThresholdGram`，机构默认 500，设备在整场 session 内锁存布尔异常标志；
- MCU 和边缘需要的其他冻结字段。

发布事务：

```text
锁 deployment/current config head
→ 校验 expectedLatestVersion
→ 规范化完整配置
→ 计算 contentSha256
→ 插入下一不可变版本和全部投口快照
→ 切换最高期望版本
→ 创建唯一 configuration application
→ 创建 ENSURE_DEVICE_CONFIGURATION 任务
```

返回 `202 + applicationUid + statusUrl`。发布不要求在线，也不取消旧作业；旧作业继续使用原配置快照。

### 11.2 应用状态

```text
PENDING → EDGE_SAVED → APPLIED
   └──────────┴────→ FAILED

FAILED ──同版本重同步──→ PENDING
FAILED ──精确且更新的可信证明──→ EDGE_SAVED / APPLIED
```

只有可信事件能推进：

- `EDGE_SAVED`：完整配置、版本和摘要已经写入香橙派 SQLite；
- `APPLIED`：无作业、门安全，并且 MCU 原子应用了相同版本、完整摘要与 MCU 子集摘要；
- `FAILED`：保存精确设备证据，不因后来成功删除失败历史。

OneNet `code=0`、MQTT ACK、UART ACK、设备在线或旧单价帧均不能推进应用状态。

发布更高版本只改变“最高期望”，不伪造旧版本状态。只有最高期望版本 `APPLIED` 才解除新投递/清运的配置阻断。

### 11.3 重同步

只允许当前最高版本且：

- 应用处于可恢复 `FAILED`；或
- 原可靠任务为 `BLOCKED`。

重同步复用原 `applicationUid`、设备命令和 task key，只递增 `wakeVersion` 并新增 attempt；不能创建第二个应用身份。

## 12. 审计与秘密

以下操作写审计：

- 租户/机构/账号启停；
- 工作人员、任职、授权和负责人变化；
- AppID/AppSecret 配置；
- 工作人员小程序绑定/换绑/撤销；
- 机构用户冻结/恢复、清运能力；
- 资产登记、部署建立/激活/停用；
- 经营开关、配置发布和重同步。

每个机构的 AppSecret 以明文保存在 `iam_organization_miniapp.app_secret`，并与 AppID、版本和登录开关在同一个数据库事务中修改。具备 `miniapp.manage` 权限的人员可以在禁止缓存的配置详情中读取完整值；其他响应只返回掩码，日志和审计只保存脱敏值。数据库备份包含 AppSecret，必须按秘密数据加密并限制访问。V32 会废弃历史假 `secret_ref`、停用对应机构的小程序登录，只有重新填写 AppSecret 后才能激活或启用登录。

## 13. 测试矩阵

1. 两租户同名机构、不同机构用户和钱包不能串读写；
2. 工作人员登录名全平台唯一，Web 不需要租户码；
3. Cookie 属性、CSRF 登录/写请求/轮换正确；
4. 禁用账号、租户、机构、AppID、绑定或授权后会话立即失效；
5. 同一 AppID+OpenID 并发首次登录只创建一个用户和一个钱包；
6. 设备扫码注册与直接注册分别固定来源/空来源，后续不能改；
7. 同机构手机号重复绑定被唯一约束拒绝；
8. 双侧预期绑定快照下并发换绑只有一个成功；
9. `MANAGEMENT/CLEANING/USER` 入口优先级正确；
10. 资产部署并发建立最多一个当前部署，失败无部分投口；
11. 配置并发发布以 `expectedLatestVersion` 线性化；
12. 低版本迟到、同 ID 异摘要、仅 OneNet/UART ACK 均不能伪造 `APPLIED`；
13. 激活、经营开关与作业/门/安全故障并发时资格不穿透；
14. 机构调试设备、两个机构和注册归因统计在真实 MySQL 上验证。

## 14. 设计追踪项（已映射到正式任务）

以下本章编号只用于覆盖追踪；正式任务、依赖和状态见
[`p0-controlled-loop`](../tasks/p0-controlled-loop/00-index.md)。

| 草案 ID | 标题 | 类型 | 依赖 | 验收结果 |
|---|---|---|---|---|
| IAM-01 | 平台/工作人员 Web 会话与可信上下文 | AFK | 9 模块、V1/V3 | Cookie+CSRF、8 小时会话、实时失效和作用域测试通过。 |
| IAM-02 | 租户、机构、工作人员、任职与授权 | AFK | IAM-01 | 主体账号、总部/机构人员和可配置能力在两机构隔离。 |
| IAM-03 | 小程序登录、机构用户注册与空钱包 | AFK + 微信登录 HITL | IAM-01、funds 钱包 | 并发首次登录只一用户/钱包，注册来源不可变。 |
| IAM-04 | 手机号动态码首次绑定 | AFK + 真微信 HITL | IAM-03 | 动态码、机构唯一、幂等重试和脱敏边界通过。 |
| IAM-05 | 工作人员小程序人工绑定与免密入口 | AFK + 真机 HITL | IAM-02～04 | 原子换绑、会话撤销和三入口优先级正确。 |
| DEV-01 | 库存资产到机构调试部署 | AFK + 试点输入 HITL | IAM-02、V2 | 部署/投口/UNKNOWN 投影同事务，无部分状态。 |
| DEV-02 | 设备生命周期、经营开关与实时资格 | AFK | DEV-01 | 投递/清运 blockers 分离，安全状态不能被管理开关覆盖。 |
| DEV-03 | 完整配置发布与可靠应用投影 | AFK + 设备 HITL | DEV-01、可靠任务 | 版本/摘要、EDGE_SAVED/APPLIED 和取代/重同步收敛。 |
| DEV-04 | 设备激活与试点验收 | HITL | DEV-02、DEV-03、UART/OneNet | 真实身份、配置、门和传感器证据解除初始锁。 |

## 15. 主审否决项

- 继续使用“租户账号 + 数字角色”代替工作人员/任职/授权；
- Web Token 返回 JavaScript 或写入 localStorage；
- 登录接口不校验 CSRF，或全局关闭 CSRF；
- 信任前端提交 tenant/organization/role；
- 小程序以客户端自报 OpenID、明文手机号或长期 refresh token 登录；
- 用户创建成功而钱包在提交后异步补建；
- 后台创建/合并/迁移机构用户；
- 手机号、昵称或缓存自动绑定工作人员；
- 把普通用户清运能力当后台管理权限；
- 同一工作人员小程序同时签发普通和管理两个受众供客户端自由切换；
- 二维码携带的租户/机构明文直接成为可信作用域；
- M0 在原部署上更新机构完成“调拨”；
- 只创建部分投口或以缺失配置默认值激活设备；
- OneNet/UART 传输成功冒充配置 `APPLIED`；
- 配置失败后新建另一 application/task 绕过原身份；
- 日志记录 login code、OpenID、完整手机号、Token 或 AppSecret。
