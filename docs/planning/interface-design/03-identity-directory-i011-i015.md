# EcoBin P0 目标接口设计：身份目录与权限（I-011～I-015）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-011～I-015 已确认**
>
> 说明：本文件定义租户、机构、小程序配置、工作人员、任职、权限和机构用户的目标 HTTP 契约。所有接口继续遵守 I-001～I-010 的版本、信封、可信作用域、幂等、并发、会话和公开 ID 规则。

## 本章统一边界

1. 普通 Web 接口从工作人员会话确定租户，不接受客户端自报 `tenantId` 或租户编码作为授权事实。平台跨租户处置始终使用 `/api/v1/web/platform/tenants/{tenantCode}/**` 独立入口并记录平台操作者。
2. 租户、机构、账号、任职和机构用户都不提供通用 `DELETE` 或任意状态覆盖。创建使用 `POST`，资料完整替换使用 `PUT .../profile`，启用、禁用、冻结、恢复和授权使用语义明确的子资源。
3. 所有创建、状态、密码重置、任职、授权、能力和配置写操作携带 `Idempotency-Key`；修改既有对象还携带对应资源的 `expectedVersion` 或 `expectedAuthVersion`。同一事务锁定最新行后重新鉴权和校验版本。
4. 账号密码、完整手机号、OpenID、Token 和完整 AppSecret 不进入请求日志、审计变更摘要或异常详情。AppSecret 的已确认回显例外只适用于 I-012 的小程序配置详情响应，不改变其他凭证的禁止回显边界。
5. 成功的目录、安全和授权变化与 `authVersion` 更新、受影响会话撤销及 `SUCCEEDED` 审计在同一事务提交。原因字段继续可空；失败和拒绝按 I-004 使用新的 `requestUid` 分别审计。
6. 工作人员账号禁用和机构用户冻结是主体级临时暂停，不自动删除其任职、直接权限或清运能力，恢复后按届时仍有效的事实重新生效；机构任职停用则表示解除该机构授权，必须清除负责人标记和机构直接权限，重新任职时不得静默恢复旧授权。

### 版本与乐观并发矩阵

| 资源 | 查询必须返回 | 修改请求携带 | 成功后的版本语义 |
|---|---|---|---|
| 租户 | `version` | 资料、启停和主体账号创建携带 `expectedVersion` | 租户 `version` 恰好 `+1`，返回新值 |
| 机构 | `version` | 资料和启停携带 `expectedVersion` | 机构 `version` 恰好 `+1`，返回新值 |
| 机构小程序配置 | `version` | 首次创建使用 `expectedVersion=0`，以后配置、激活和登录启停携带当前 `expectedVersion` | 配置 `version` 恰好 `+1`，返回新值 |
| 工作人员账号 | `version + authVersion` | 资料只带 `expectedVersion`；启停、管理员重置和自助改密同时带 `expectedVersion + expectedAuthVersion` | 资料只使 `version +1`；安全状态/密码使两者各 `+1` 并返回新值 |
| 机构任职 | `version + 目标工作人员 authVersion` | 新建只带目标 `expectedAuthVersion`；启停和授权同时带 `expectedVersion + expectedAuthVersion` | 任职 `version` 与目标 `authVersion` 各 `+1`，返回新值 |
| 机构用户 | `version + authVersion` | 冻结、恢复及清运能力授予/撤销同时带 `expectedVersion + expectedAuthVersion` | 两者各 `+1`，返回新值 |
| 工作人员小程序绑定 | 工作人员当前绑定查询及用户手机号查找都明确返回当前绑定 `bindingUid + version`，无绑定返回 `null` | 设置/换绑必须同时提交工作人员侧与机构用户侧的预期绑定快照；撤销提交目标 `expectedVersion` | 冲突旧绑定及撤销目标各自 `version +1`；新绑定从 `version=0` 开始并返回 |

主体账号创建成功时租户版本递增；创建普通工作人员、机构或机构用户属于新资源创建，不要求不存在资源的版本。租户权限完整替换只以目标工作人员 `expectedAuthVersion` 防止授权并发并使其 `authVersion +1`；它不因无关展示资料变化失败。工作人员自己的版本由 I-006 当前会话视图提供，不要求本人拥有 `staff.read`。所有 `409` 版本冲突响应只返回安全的当前版本提示，客户端重新查询后由人员决定，不自动覆盖。

### 读取能力与作用域矩阵

| 读取资源 | 普通 Web 所需能力 | 返回范围 |
|---|---|---|
| 当前租户资料 | `tenant.read`；直接修改所需安全详情也允许 `tenant.manage` | 只返回会话租户；租户主体天然满足 |
| 机构列表 | `organization.read` | 租户级能力返回本租户全部机构；机构级能力只返回已授权机构 |
| 机构详情 | `organization.read` 或该机构 `organization.manage` | 管理能力只补足直接目标的安全详情和版本，不因此开放机构列表 |
| 小程序配置详情 | `miniapp.manage` | 只返回能力覆盖机构；完整 AppSecret 仅在该详情响应出现 |
| 工作人员列表 | `staff.read` | 租户级返回本租户账号；只有机构级能力时返回已授权机构任职人员的安全投影 |
| 工作人员详情 | `staff.read` 或对目标有效的 `staff.manage` | 管理能力只补足直接目标的安全详情、`version + authVersion`，不因此开放列表 |
| 权限定义目录 | 任一有效作用域的 `permission.read` 或 `permission.manage` | 返回系统权限定义；不返回任何租户授权关系 |
| 本人有效权限 | 当前有效工作人员会话 | `GET /api/v1/web/staff-accounts/current/effective-access`，只返回本人当前投影 |
| 他人有效权限 | `permission.read` 或对目标有效的 `permission.manage` | 租户级可看目标在本租户的完整投影；机构级只返回双方可见机构的交集 |
| 任职列表 | `staff.read` | 只返回能力覆盖机构；直接权限集合仅在同时具备该机构 `permission.read` 时返回，否则省略该字段 |
| 任职详情 | `staff.read`，或对该任职有效的 `staff.manage`、`permission.manage`、`organization-manager.manage` | 写能力只返回完成对应命令所需的安全状态与版本；直接权限仍需 `permission.read/manage` |
| 机构用户列表及手机号精确查找 | `user.read` | 只返回能力覆盖机构，且手机号仍按 I-009 脱敏 |
| 机构用户详情 | `user.read`，或对目标有效的 `user.freeze`、`cleaner.manage` | 写能力只补足直接目标的安全状态、能力和版本，不因此开放列表或手机号查找 |
| 工作人员小程序当前绑定/变更 | `staff.bind` | 只作用于能力覆盖机构；绑定查询只返回身份与版本，读取用户昵称/脱敏手机号及手机号查找仍须 `user.read` |

租户主体天然满足本租户全部读取能力，机构负责人天然满足本机构全部读取能力。平台接口使用独立平台能力和显式目标租户/机构，不借用本表中的工作人员能力。

## I-011 租户生命周期与唯一主体账号

**已确认：平台先创建禁用的租户，再通过独立接口建立该租户唯一主体账号；租户满足主体账号前置条件后才可启用，禁用不删除或批量改写其业务历史。**

### 1. 平台租户目录

```http
GET  /api/v1/web/platform/tenants
POST /api/v1/web/platform/tenants
GET  /api/v1/web/platform/tenants/{tenantCode}
PUT  /api/v1/web/platform/tenants/{tenantCode}/profile
```

创建请求只包含规范化 `tenantCode`、企业名称及可空联系人、联系电话和地址。成功返回 `201`，并且只创建状态为 `DISABLED` 的 `iam_tenant`；不得顺带创建机构、小程序、主体账号、资金账户或跨租户人员关系。

- `tenantCode` 全平台唯一，创建后不可修改、不可释放给其他企业复用；资料接口只能完整替换企业名称、联系人、电话和地址。
- 列表使用 I-002 的普通管理分页，支持状态、租户编码和企业名称筛选；返回稳定租户编码和安全资料，不暴露数据库主键。
- 平台租户详情在主体账号存在时返回安全 `principalAccount` 摘要，包括 `staffAccountUid`、状态、`version` 和 `authVersion`，供平台构造受控密码恢复；不返回密码资料。
- 租户自身使用 `GET /api/v1/web/tenants/current` 和 `PUT /api/v1/web/tenants/current/profile` 查看、修改当前租户资料。租户主体天然允许；普通工作人员查看需要租户级 `tenant.read`，修改需要租户级 `tenant.manage`。

### 2. 主体账号和租户状态

```http
POST /api/v1/web/platform/tenants/{tenantCode}/principal-account
POST /api/v1/web/platform/tenants/{tenantCode}/principal-account/password-resets
POST /api/v1/web/platform/tenants/{tenantCode}/activations
POST /api/v1/web/platform/tenants/{tenantCode}/deactivations
```

- 主体账号创建请求包含全平台唯一规范化 `loginName`、`initialPassword`、展示名和可空联系电话；密码只参与自适应哈希，响应和审计均不回显。成功返回已确认的 `staffAccountUid` 和 `accountKind=TENANT_PRINCIPAL`。
- 一个租户最多存在一个主体账号。普通工作人员创建、禁用、任职和授权端点都不能创建、转换、替换或禁用主体账号。
- 启用租户前必须已经存在一个未禁用主体账号；不要求先创建机构。租户允许 `DISABLED → ENABLED → DISABLED → ENABLED`，不提供删除。
- 主体账号忘记密码时由平台专用端点设置新密码；成功递增主体 `authVersion`、撤销其全部 Web/工作人员小程序会话并审计，但不替换账号公开身份或登录名。
- 禁用租户立即撤销该租户全部人员和小程序活动会话，阻止新登录、新投递、清运、提现及其他人工业务命令；它不批量改写机构、员工、用户、设备、钱包或订单状态。已经可靠受理的设备结果、微信通知、资金收敛和恢复任务仍必须按原冻结作用域处理。
- 重新启用租户只恢复租户总开关，不自动启用此前被单独禁用的机构、账号、小程序登录或设备。

主要错误包括 `IDENTITY.TENANT_CODE_ALREADY_USED`、`IDENTITY.TENANT_PRINCIPAL_ALREADY_EXISTS`、`IDENTITY.TENANT_PRINCIPAL_REQUIRED` 和 `IDENTITY.TENANT_NOT_ACTIVATABLE`。

## I-012 机构生命周期与小程序配置

**已确认：机构是租户下不可移动的一级经营单位；机构状态、小程序身份激活和小程序登录开关分别管理。AppSecret 可在已有配置权限人员使用的配置详情响应中直接回显，普通日志只允许保存脱敏值。**

### 1. 精确接口映射

| 方法 | 租户 Web 路径 | 平台协助路径 |
|---|---|---|
| `GET` | `/api/v1/web/organizations` | `/api/v1/web/platform/tenants/{tenantCode}/organizations` |
| `POST` | `/api/v1/web/organizations` | `/api/v1/web/platform/tenants/{tenantCode}/organizations` |
| `GET` | `/api/v1/web/organizations/{organizationCode}` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}` |
| `PUT` | `/api/v1/web/organizations/{organizationCode}/profile` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/profile` |
| `POST` | `/api/v1/web/organizations/{organizationCode}/activations` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/activations` |
| `POST` | `/api/v1/web/organizations/{organizationCode}/deactivations` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/deactivations` |
| `GET` | `/api/v1/web/organizations/{organizationCode}/miniapp-configuration` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-configuration` |
| `PUT` | `/api/v1/web/organizations/{organizationCode}/miniapp-configuration` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-configuration` |
| `POST` | `/api/v1/web/organizations/{organizationCode}/miniapp-configuration/activations` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-configuration/activations` |
| `POST` | `/api/v1/web/organizations/{organizationCode}/miniapp-login/enablements` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-login/enablements` |
| `POST` | `/api/v1/web/organizations/{organizationCode}/miniapp-login/disablements` | `/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-login/disablements` |

平台路径的请求、版本、状态和响应契约与对应租户路径相同，但使用平台身份独立鉴权并记录目标租户；平台不能把身份塞入普通租户接口。

### 2. 机构目录和状态

- 创建机构只接收租户内唯一的规范化 `organizationCode`、机构名称、联系电话和地址，不接受 `parentOrganizationCode`；成功返回 `201` 且初始状态为 `DISABLED`。
- 机构编码、所属租户创建后不可修改，机构不能移动到其他租户，也不提供删除。资料接口不混入 AppID、AppSecret、价格、设备、商户或资金字段。
- 租户级 `organization.manage` 可以创建、启停任意本租户机构；机构级同名能力只允许维护本机构资料和状态，不能创建平级机构。机构负责人天然拥有本机构能力。
- 机构启用只表示组织身份有效，不代表小程序、价格、设备、满溢规则或资金已经满足具体业务前置条件，各业务接口继续分别校验。
- 禁用机构阻止新注册、新登录会话和新的投递/清运/提现/设备作业，撤销该机构普通用户与工作人员小程序会话，并立即从 Web 实时授权中移除该机构；已受理设备/微信结果及在途业务仍继续收敛。这里的“立即”以数据库提交顺序为线性化边界：机构及小程序禁用事务与新投递 session 建立事务共享 `tenant → organization → organization_miniapp` 锁前缀，禁用先提交后不得再建立新投递 session；session 授权先提交则整场属于在途作业，其设备本地继续轮次沿用冻结快照，不存在阶段 B、周期或继续开门云端授权，随后禁用只阻止下一次新扫码 session。重新启用不改变此前被单独关闭的小程序登录开关。

### 3. 小程序配置、激活和 AppSecret 回显

`PUT .../miniapp-configuration` 使用以下业务字段：

```json
{
  "appId": "wx...",
  "displayName": "A机构回收",
  "appSecret": "本次设置或轮换的新值",
  "expectedVersion": 3
}
```

- 首次配置必须提供 `appId + appSecret`；以后未轮换密钥时可以省略 `appSecret`。完整值以明文保存在 `iam_organization_miniapp.app_secret`，与 AppID、版本和登录开关共用数据库事务；日志、审计、普通响应和数据库运维输出必须脱敏。
- 激活前允许修正 AppID、展示名和 AppSecret。激活端点只在本地校验当前配置完整并一次性写入 `activatedAt`，不在数据库事务中等待微信调用；激活后 AppID、租户和机构不可修改，仍允许使用相同 AppID 轮换 AppSecret 和修改展示名。真实凭据是否可用由后续登录调用及错误状态体现，不能把本地激活冒充微信验证成功。
- 小程序登录只有在租户、机构、AppID 激活和登录开关都有效时才允许。关闭登录撤销该 AppID 下普通用户及工作人员小程序会话，但不删除用户、钱包和绑定；重新开启后客户端重新 `wx.login`。
- `GET/PUT .../miniapp-configuration` 只允许当前机构 `miniapp.manage`，租户级该能力覆盖全部机构，机构负责人天然具备；不增加特殊角色、独立回显步骤或再次输入密码。`GET` 配置详情直接返回 AppID、当前完整 `appSecret`、展示名、激活/登录状态和版本。
- `PUT` 成功响应只返回 AppID、展示名、激活/登录状态、新版本、`appSecretConfigured`、`maskedAppSecret` 和更新时间，不返回完整密钥。这样同一 `Idempotency-Key` 在后续再次轮换密钥后仍能重放首次非秘密结果；Web 保存后按需重新 `GET` 即可回显当前值。
- `GET` 配置响应设置 `Cache-Control: no-store`。V32 迁移不会把历史假 `secretRef` 复制为真密钥；这类行的 `appSecret` 返回 `null`、登录保持停用，直到有权限人员重新填写。不能用空字符串冒充“尚未配置”。机构列表、普通机构详情、小程序登录和其他业务响应不返回 AppSecret。
- 配置读取和修改都必须审计，但访问日志、应用日志和审计摘要只能记录固定脱敏值（例如前后少量字符加掩码）、AppID、操作者、目标机构和时间，不能记录完整 AppSecret。
- AppSecret 可以在授权 Web 页面中显示；不得写入 URL、浏览器持久缓存、错误详情、导出文件或版本库。API 文档和测试样例只使用假值。
- 这是项目负责人明确接受的 P0 风险边界：任何当前有效的 `miniapp.manage`（包括天然全权的机构负责人）都能从配置详情取得完整 AppSecret。实现不得自行增加独立秘密权限或强制重新认证；如果未来需要收紧，必须作为新的权限和兼容性变更另行确认。

AppSecret 写入采用以下数据库事务协议：

1. 首次配置或轮换 AppSecret 时，服务端在同一个身份模块事务中写入 AppID、`app_secret`、配置版本和时间；任一写入失败则整体回滚。
2. 后续修改未传 `appSecret` 时保留当前值；数据库中尚无有效值时不允许激活或开启登录。
3. 幂等指纹使用完整请求的不可逆摘要，审计记录只保存摘要和脱敏快照；相同 `operationUid` 但请求不同时按 I-004 返回幂等冲突。
4. 数据库提交后 `PUT` 只返回掩码和是否已配置，不返回完整密钥。数据库备份因包含 AppSecret，必须作为秘密数据加密保存并限制访问。这是本地数据持久化，不得扩展为绕过 I-005 的微信、OneNet、COS、资金或设备外部动作。

主要错误包括 `IDENTITY.ORGANIZATION_CODE_ALREADY_USED`、`IDENTITY.ORGANIZATION_DISABLED`、`IDENTITY.MINIAPP_APPID_ALREADY_USED`、`IDENTITY.MINIAPP_ALREADY_ACTIVATED` 和 `IDENTITY.MINIAPP_CONFIGURATION_INVALID`。

## I-013 工作人员账号

**已确认：租户工作人员只有 `TENANT_PRINCIPAL` 与 `STAFF` 两种账号类型；“总部员工”和“机构员工”由租户权限及机构任职表达。机构负责人可以原子创建账号和本机构任职，但不能因此获得跨机构账号控制权。**

### 1. 账号接口

```http
GET  /api/v1/web/staff-accounts
POST /api/v1/web/staff-accounts
GET  /api/v1/web/staff-accounts/{staffAccountUid}
PUT  /api/v1/web/staff-accounts/{staffAccountUid}/profile
POST /api/v1/web/staff-accounts/{staffAccountUid}/activations
POST /api/v1/web/staff-accounts/{staffAccountUid}/deactivations
POST /api/v1/web/staff-accounts/{staffAccountUid}/password-resets

PUT  /api/v1/web/staff-accounts/current/profile
POST /api/v1/web/staff-accounts/current/password-changes
POST /api/v1/web/organizations/{organizationCode}/staff-account-provisionings
```

平台协助使用以下完整管理入口；工作人员自助 `current/*` 依赖当前工作人员及其当前密码，不存在平台镜像：

```http
GET  /api/v1/web/platform/tenants/{tenantCode}/staff-accounts
POST /api/v1/web/platform/tenants/{tenantCode}/staff-accounts
GET  /api/v1/web/platform/tenants/{tenantCode}/staff-accounts/{staffAccountUid}
PUT  /api/v1/web/platform/tenants/{tenantCode}/staff-accounts/{staffAccountUid}/profile
POST /api/v1/web/platform/tenants/{tenantCode}/staff-accounts/{staffAccountUid}/activations
POST /api/v1/web/platform/tenants/{tenantCode}/staff-accounts/{staffAccountUid}/deactivations
POST /api/v1/web/platform/tenants/{tenantCode}/staff-accounts/{staffAccountUid}/password-resets
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-account-provisionings
```

### 2. 创建、资料与密码

- `POST /staff-accounts` 只创建当前租户的 `STAFF`，不接受 `accountKind` 或 `tenantId`。请求包含全平台唯一规范化 `loginName`、`initialPassword`、展示名、联系电话和可空的初始租户权限集合，并在一个事务内完成账号和初始授权。创建账号需要租户级 `staff.manage`；初始权限非空时还必须具有租户级 `permission.manage`，并执行与 I-014 完全相同的可委派上限校验。
- 机构负责人或拥有机构级 `staff.manage` 的人员使用 `staff-account-provisionings` 原子创建 `STAFF + 本机构有效任职 + 初始负责人/权限配置`。初始权限非空时还需要本机构 `permission.manage` 且不能授予操作者不具备的能力；`manager=true` 另按 I-014 的负责人任命权限校验，普通 `staff.manage` 不能单独创建负责人。登录名冲突或任职授权任一步失败时整体不创建。
- 登录名、账号类型和所属租户不可修改；资料接口只修改展示名和联系电话。列表采用普通分页，并根据当前主体的租户/机构可见范围返回安全投影，不返回密码摘要、完整会话或数据库主键。
- 自助资料和改密使用 I-006 当前工作人员会话视图返回的 `version + authVersion` 构造并发字段；成功响应返回新版本。因密码变化导致当前会话被撤销时，响应仍可安全返回本次已提交结果，随后请求必须重新登录。
- 自助改密必须提交当前密码和新密码；管理员重置只提交新密码。成功后递增账号 `authVersion`、撤销该账号全部 Web 和工作人员小程序会话，并要求重新登录；密码正文不进入响应、日志或审计摘要。

### 3. 账号禁用边界

- `GET /staff-accounts` 和账号详情需要租户级 `staff.read`；只有机构级 `staff.read` 时，仅返回当前主体有权查看机构的有效任职人员安全投影。目标账号资料修改、管理员密码重置以及整个账号的启停只允许租户主体或租户级 `staff.manage`，机构级能力不能执行这些全局动作。
- 任意有效工作人员可以使用 `current/profile` 修改自己的展示资料，并使用 `current/password-changes` 在验证当前密码后自助改密；这不授予其修改账号类型、登录名、任职或权限的能力。
- 租户主体或拥有租户级 `staff.manage` 的人员可以禁用、恢复普通 `STAFF`；机构级管理人员只能停用本机构任职，不能禁用可能仍服务其他机构或总部的整个账号。
- 账号禁用阻止该工作人员所有登录和后台操作并撤销全部工作人员会话，但不删除任职、权限、操作历史或其普通机构用户身份。恢复账号后任职和未被另行撤销的权限重新按当前事实生效，旧会话不恢复。
- 普通员工接口对 `TENANT_PRINCIPAL` 的创建、类型修改、禁用、恢复和管理员密码重置返回 `409 IDENTITY.TENANT_PRINCIPAL_PROTECTED`；主体密码恢复只走 I-011 平台端点。

其他主要错误包括 `IDENTITY.LOGIN_NAME_ALREADY_USED`、`IDENTITY.STAFF_ACCOUNT_DISABLED` 和 `IDENTITY.STAFF_ACCOUNT_OUTSIDE_MANAGEMENT_SCOPE`。

## I-014 机构任职、负责人和可配置权限

**已确认：系统维护稳定权限目录，租户和机构配置“谁拥有哪些能力”，不提供角色 CRUD。租户主体和机构负责人使用天然权限；普通工作人员的租户权限和机构任职授权分别原子替换。**

### 1. 权限目录和租户授权

```http
GET /api/v1/web/permission-definitions
GET /api/v1/web/staff-accounts/current/effective-access
GET /api/v1/web/staff-accounts/{staffAccountUid}/effective-access
PUT /api/v1/web/staff-accounts/{staffAccountUid}/tenant-permissions
```

- 权限定义由代码和数据库迁移版本化维护，Web 只读；租户不能创造任意权限码、改变含义或建立隐藏的角色模板。
- 本章先冻结身份目录所需能力：`tenant.read`、`tenant.manage`、`organization.read`、`organization.manage`、`organization-manager.manage`、`miniapp.manage`、`staff.read`、`staff.manage`、`staff.bind`、`permission.read`、`permission.manage`、`user.read`、`user.freeze` 和 `cleaner.manage`。其中 `organization-manager.manage` 只允许 `TENANT` 作用域，明确代表可以任命或撤销机构天然全权负责人；后续设备、投递、清运、资金、统计和运营章节继续增加各自稳定能力码。
- 同一权限码可以由目录分别声明 `TENANT` 和 `ORGANIZATION` 作用域。租户级授权覆盖本租户当前及以后机构；机构级授权只对一条有效任职所在机构生效。
- `PUT .../tenant-permissions` 接收完整目标 `permissionCodes` 和 `expectedAuthVersion`，服务端原子计算新增与撤销、递增一次目标账号 `authVersion` 并撤销其工作人员会话。租户主体天然全权，不生成授权行且不能通过此端点削弱。
- 修改租户权限需要租户级 `permission.manage`，并受“不能修改自己、不能授予操作者在相同或更大作用域不具备能力”的委派上限约束；租户主体作为天然全权主体不受后者限制。

### 2. 机构任职和授权

```http
GET  /api/v1/web/organizations/{organizationCode}/staff-memberships
POST /api/v1/web/organizations/{organizationCode}/staff-memberships
GET  /api/v1/web/organizations/{organizationCode}/staff-memberships/{staffAccountUid}
POST /api/v1/web/organizations/{organizationCode}/staff-memberships/{staffAccountUid}/activations
POST /api/v1/web/organizations/{organizationCode}/staff-memberships/{staffAccountUid}/deactivations
PUT  /api/v1/web/organizations/{organizationCode}/staff-memberships/{staffAccountUid}/authorization
```

- 任职以 `organizationCode + staffAccountUid` 作为稳定客户端身份，不暴露内部任职或授权主键。一个 `STAFF` 可以同时具有租户级权限并任职多个机构。
- 任职列表和详情均返回任职 `version` 及目标工作人员 `authVersion`，供客户端构造后续并发命令。新增任职只接受本租户已有 `STAFF`，请求携带 `staffAccountUid`、`manager`、完整 `permissionCodes` 和 `expectedAuthVersion`，并在同一事务建立有效任职及初始授权；已经存在禁用任职时必须走激活端点，不能插入第二条关系。
- 新增或重新激活任职需要本机构 `staff.manage`；初始权限非空时还需要本机构 `permission.manage` 并执行委派上限校验；`manager=true` 还必须通过下述负责人任命检查。复合命令任一授权条件失败时不得留下账号、任职或部分权限。
- `authorization` 使用完整替换请求：

```json
{
  "manager": false,
  "permissionCodes": ["user.read", "user.freeze"],
  "expectedVersion": 4,
  "expectedAuthVersion": 9
}
```

- `manager=true` 时 `permissionCodes` 必须为空，机构负责人天然拥有本机构全部当前及以后能力且不生成批量授权行；从负责人降为普通员工时必须在同一次请求明确给出降级后的完整权限集合，不能留下以后突然恢复的隐藏授权。
- 停用请求携带 `expectedVersion + expectedAuthVersion + reason`；同一事务清除负责人标记、撤销该机构全部直接权限，使其立即失去本机构访问。重新激活时携带 `manager + permissionCodes + expectedVersion + expectedAuthVersion + reason`，旧授权不会静默复活。它不影响账号在其他机构或租户级权限。
- 租户主体、拥有租户级 `permission.manage` 的总部人员和本机构负责人可以配置对应范围的授权。受委托的普通权限管理员不能修改自己的任职、负责人身份或权限，也不能授予自己在相同或更大作用域并不具备的目标能力。
- 任命或撤销机构负责人只允许租户主体、拥有租户级 `organization-manager.manage` 的人员或本机构另一名负责人执行；普通 `permission.manage` 本身无权授予天然全权身份。任何人不能通过普通权限集合把自己变成负责人，也不能修改自己的负责人状态。
- 每次任职、负责人或机构直接权限变化都把任职 `lockVersion` 和工作人员 `authVersion` 各恰好递增一次，成功响应返回新的 `version + authVersion`，并撤销其受影响的 Web/工作人员小程序会话；事务内重新检查操作者当前能力，不能只相信进入 Controller 时的判断。

平台使用以下明确入口；平台权限目录属于平台路径，任职和授权仍显式带目标租户及机构：

```http
GET /api/v1/web/platform/permission-definitions
GET /api/v1/web/platform/tenants/{tenantCode}/staff-accounts/{staffAccountUid}/effective-access
PUT /api/v1/web/platform/tenants/{tenantCode}/staff-accounts/{staffAccountUid}/tenant-permissions

GET  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships
GET  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffAccountUid}
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffAccountUid}/activations
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffAccountUid}/deactivations
PUT  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-memberships/{staffAccountUid}/authorization
```

主要错误包括 `IDENTITY.MEMBERSHIP_ALREADY_EXISTS`、`IDENTITY.MEMBERSHIP_DISABLED`、`IDENTITY.PERMISSION_NOT_DEFINED`、`IDENTITY.PERMISSION_NOT_DELEGABLE` 和 `IDENTITY.NATURAL_AUTHORITY_IMMUTABLE`。

## I-015 机构用户、冻结与清运能力

**已确认：机构用户只由当前机构小程序首次 `wx.login` 创建；Web 只提供安全查询、冻结/恢复以及 `CLEAN_OPERATION` 授予/撤销，不提供后台创建、删除、合并、迁移或身份改写。**

### 1. 查询接口

```http
GET /api/v1/web/organizations/{organizationCode}/organization-users
GET /api/v1/web/organizations/{organizationCode}/organization-users/{organizationUserUid}
```

- 列表使用 I-002 普通管理分页，支持状态、手机号是否绑定、注册时间、注册来源部署和是否具有清运能力筛选。按完整手机号精确寻找本人继续使用 I-009 已冻结的租户入口 `POST /api/v1/web/organizations/{organizationCode}/organization-user-lookups`；平台使用 `POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/organization-users/phone-lookups`。完整手机号不得进入 GET URL。
- 身份视图包含 `organizationUserUid`、昵称/头像安全引用、脱敏手机号、绑定状态、注册时间、注册来源部署安全摘要、`ACTIVE/FROZEN` 状态、清运能力和版本；不返回 OpenID、完整手机号、钱包余额、内部主键或可用于跨机构合并的身份。
- 钱包、订单、提现和清运历史由各自业务模块接口返回，机构用户身份接口不拼装可被误认为同一事务快照的跨模块大对象。

### 2. 冻结、恢复和清运能力

```http
POST /api/v1/web/organizations/{organizationCode}/organization-users/{organizationUserUid}/freezes
POST /api/v1/web/organizations/{organizationCode}/organization-users/{organizationUserUid}/restorations
POST /api/v1/web/organizations/{organizationCode}/organization-users/{organizationUserUid}/capabilities/clean-operation/grants
POST /api/v1/web/organizations/{organizationCode}/organization-users/{organizationUserUid}/capabilities/clean-operation/revocations
```

- 冻结要求当前范围的 `user.freeze`，原因可空。成功递增机构用户 `authVersion` 并撤销其全部 `aud=miniapp` 会话，阻止新的普通投递、清运、提现和其他用户命令。
- 冻结不修改钱包、余额、订单、提现、历史能力、注册归因或工作人员绑定，也不撤销独立 `aud=miniapp-staff` 会话。已经开门或已经可靠受理的设备/微信动作继续使用其冻结上下文安全完成并接收结果；已授权投递 session 内关门后的本地继续仍属于同一在途作业，不重新鉴权，也不存在下一周期。冻结只阻止下一次新扫码 session 和其他尚未受理的新用户命令。
- 恢复只允许 `FROZEN → ACTIVE`，不恢复旧会话；用户下次重新 `wx.login`。冻结期间保存的清运能力在恢复后重新生效，若不希望恢复必须另行撤销能力。
- `CLEAN_OPERATION` 是机构用户附加能力，不是工作人员角色或后台权限。`cleaner.manage` 可以授予/撤销；变化只撤销普通/清运小程序会话，不改变普通用户身份、钱包和历史清运记录。
- Web 不提供修改 AppID、OpenID、完整手机号、机构、注册时间、注册部署、删除或合并机构用户的接口。手机号 P0 仍只有 I-008 的用户首次自助绑定。

平台协助接口逐条使用以下完整前缀，资源后缀和方法保持一致：

```text
/api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/organization-users
```

主要错误包括 `IDENTITY.ORGANIZATION_USER_ALREADY_FROZEN`、`IDENTITY.ORGANIZATION_USER_ALREADY_ACTIVE`、`IDENTITY.USER_CAPABILITY_ALREADY_GRANTED` 和 `IDENTITY.USER_CAPABILITY_NOT_ACTIVE`。

## 后续细化边界

本章不定义设备、投递、清运、资金、运营或统计能力码及接口，也不因此扩大一周 M0 的管理界面范围。M0 必须实现真实会话撤销、最小受控账号准备、用户冻结和清运能力；完整员工/权限自助配置界面仍按范围基线留在 M1。下一章从设备资产、部署、投口和配置接口开始，不得绕过本章的租户、机构和能力作用域。
