# EcoBin P0 目标接口设计：认证、会话与作用域（I-006～I-010）

> 总索引：[interface-design-draft.md](../interface-design-draft.md)
>
> 状态：**I-006～I-010 已确认**
>
> 说明：本文件定义 Web、普通/清运小程序和工作人员小程序的登录、手机号首次绑定、工作人员微信身份设置、会话失效及可信作用域契约；业务资源接口不得绕过本章重新解释身份。

## I-006 Web 登录、Cookie 与 CSRF

**已确认：平台管理员与租户工作人员使用独立登录路径和账号命名空间，但共用一套 Web Cookie/CSRF 机制；登录请求只提交登录名和密码，JavaScript 永远拿不到 Web 会话 Token。**

### 1. 端点

```text
GET    /api/v1/web/auth/csrf-token

POST   /api/v1/web/auth/sessions
GET    /api/v1/web/auth/sessions/current
DELETE /api/v1/web/auth/sessions/current

POST   /api/v1/web/platform/auth/sessions
GET    /api/v1/web/platform/auth/sessions/current
DELETE /api/v1/web/platform/auth/sessions/current
```

- 普通 Web 入口服务租户主体账号、总部员工和机构员工；平台入口只服务平台管理员。
- 两类登录请求都严格只有 `loginName + password`，不接收租户编码、租户 ID、机构 ID 或 `userType`。
- `GET /api/v1/web/auth/csrf-token` 是精确匿名入口，返回 CSRF Token 及固定请求头名 `X-CSRF-TOKEN`；响应和其余认证响应都设置 `Cache-Control: no-store`。
- 登录请求本身也必须携带有效 CSRF Token。登录成功和登出后轮换 CSRF Token，前端重新获取后才能继续提交非安全方法。

### 2. 登录结果与 Cookie

- 登录成功返回 `201` 和当前会话视图，不在响应体返回 JWT。
- 服务端设置 `__Host-ecobin-web-session` Cookie，属性固定为 `Secure + HttpOnly + SameSite=Lax + Path=/`，不设置 `Domain`。
- 平台和工作人员共用这一个 Cookie 名，但使用不同会话受众；同一浏览器配置只能保持一种 Web 身份，登录另一种身份会撤销/替换当前 Cookie 会话，不能让平台和租户身份同时隐式存在。
- 成功登录建立新的 `jti/sessionUid`，不能沿用登录前或旧身份会话。
- 当前会话视图包含主体公开 ID、账号类型、展示名、租户公开信息、有效机构范围、用于界面展示的当前能力和过期时间；工作人员视图同时返回当前账号 `version + authVersion`，供不具备 `staff.read` 的本人执行资料修改和改密并发校验。平台视图同样返回平台账号的两个版本；两者都不包含 Cookie/JWT 正文、密码摘要、完整手机号、OpenID 或内部数据库主键。
- `DELETE .../sessions/current` 撤销当前服务端会话、清除 Cookie 并返回 `204`；P0 不通过该端点撤销同账号的其他会话。

## I-007 小程序统一登录、注册归因与自动入口

**已确认：一个精确匿名微信登录入口完成 AppID 校验、机构用户首次注册、可选设备归因和工作人员绑定识别；一次登录只返回一种受众会话，并由服务端决定默认页面。**

```http
POST /api/v1/miniapp/auth/sessions
```

```json
{
  "appId": "wx...",
  "wxLoginCode": "...",
  "registrationSource": {
    "deploymentCode": "设备部署公开码"
  }
}
```

1. `registrationSource` 整体可空。接口不接收 OpenID、租户 ID/编码或机构 ID；后端先根据已启用 AppID 配置定位机构和秘密引用，再使用该 AppID 调用微信 `code2session`。
2. 提供部署公开码时，后端必须解析有效部署并校验其与 AppID 的租户、机构一致。首次注册来源无效时返回 `422 IDENTITY.REGISTRATION_SOURCE_INVALID`，且不能先创建一个“直接注册”用户导致归因永久丢失。
3. 首次成功解析 OpenID 时创建机构用户，由后端同一事务写入 `registeredAt` 和可空注册部署；未绑定手机号也完成注册并进入地推统计。直接进入小程序时来源为空。
4. 既有用户再次登录、以后扫码或手机号绑定都不能补填、覆盖注册时间和注册部署；提供的设备上下文仍可供后续设备用例重新校验，但不再改变注册归因。
5. 微信登录不要求 `Idempotency-Key`。`wxLoginCode` 是一次性凭据；结果不明确时客户端重新执行 `wx.login`，后端依靠 AppID+OpenID、钱包唯一约束和事务保证不会重复注册。
6. 响应只返回一种短期 Bearer 会话：
   - 有有效工作人员绑定及当前机构管理访问路径：`audience=miniapp-staff`、`entryMode=MANAGEMENT`；
   - 否则机构用户有清运能力：`audience=miniapp`、`entryMode=CLEANING`；
   - 否则普通机构用户有效：`audience=miniapp`、`entryMode=USER`。
7. P0 不同时返回普通和工作人员两套 Token，也不提供手工模式切换。工作人员自动进入管理页，清运员自动进入清运页。
8. 普通用户被冻结、钱包状态或清运能力不会让独立有效的工作人员身份失效；工作人员账号/绑定失效也不自动冻结普通用户。若管理入口无效且普通用户也不可用，登录被拒绝。
9. 响应包含 `accessToken`、`tokenType=Bearer`、`audience`、`entryMode`、`expiresAt`、机构公开视图、对应主体的安全展示信息和 `isNewRegistration`；不返回 OpenID、完整手机号、AppSecret 或内部主键。
10. 小程序不保存 Refresh Token。Bearer Token 只能进入小程序运行内存和 Storage，不得写入 URL、日志或业务数据。

## I-008 微信手机号首次绑定

**已确认：手机号由当前普通小程序会话和微信一次性手机号动态码共同证明；P0 只允许首次绑定，不允许客户端明文号码直接成为可信身份。**

```http
POST /api/v1/miniapp/me/phone-bindings
Authorization: Bearer <aud=miniapp>
Idempotency-Key: <UUIDv4>
```

```json
{
  "wechatPhoneCode": "getPhoneNumber 返回的动态码"
}
```

1. 只接受微信 `getPhoneNumber` 授权产生的动态码。后端按当前会话的 AppID 调用微信换取号码并规范化；不接收客户端自报明文手机号，也不采用旧 `encryptedData/iv` 作为目标协议。
2. 手机号动态码与 `wx.login` code 是不同的一次性凭据，不能混用。
3. P0 仅支持首次绑定，不提供换绑或解绑。已经绑定相同号码时幂等返回当前结果；已经绑定其他号码时返回 `409 IDENTITY.PHONE_ALREADY_BOUND`。
4. 同一机构其他用户已经占用该号码时返回 `409 IDENTITY.PHONE_ALREADY_USED`，不自动合并机构用户、工作人员绑定、钱包或历史事实；同一号码仍可在其他机构独立绑定。
5. 成功创建手机号绑定返回 `201`，只包含 `phoneBound=true`、脱敏号码和 `phoneBoundAt`；不返回完整号码。
6. 绑定成功不改变注册时间和注册设备来源，也不要求重新登录；后续业务用例实时读取新状态。
7. 未绑定手机号仍可登录和浏览允许页面。投递、提现等明确要求手机号的操作返回 `422 IDENTITY.PHONE_BINDING_REQUIRED`，不能伪装成未认证。
8. 微信动态码无效使用 `422 WECHAT.PHONE_CODE_INVALID`；微信服务暂不可用使用 `503 WECHAT.PHONE_SERVICE_UNAVAILABLE` 且 `retryable=true`。若一次性码已经消费但本地操作未成功，客户端重新取得手机号动态码，并使用原 `Idempotency-Key` 重试同一次绑定意图；新的动态码只是替换已经失效的传输凭据，不进入稳定业务请求摘要。若用户改为绑定另一个号码，则属于新的业务意图，必须生成新的 `Idempotency-Key`。

## I-009 Web 人工设置工作人员小程序绑定与公开身份

**已确认：有权限人员在 Web 中按手机号准确找到机构用户后，显式把工作人员账号设置到该微信身份；设置操作原子替换冲突绑定、即时切换小程序入口并完整审计。所有被客户端寻址的身份使用独立公开 UUID。**

### 1. 隐私敏感用户查找

```http
POST /api/v1/web/organizations/{organizationCode}/organization-user-lookups
```

```json
{
  "phoneNumber": "13812345678"
}
```

- 这是 I-001“查询使用 GET”的明确隐私例外，避免完整手机号进入 URL、浏览器历史、代理访问日志和通用查询指标。
- 请求体中的手机号只用于当前授权机构内的精确查找，由后端规范化且不得记录明文；普通工作人员必须具有当前机构 `user.read`，租户级该能力可以覆盖本租户机构，租户主体和机构负责人按天然权限满足。
- 响应只返回 `organizationUserUid`、昵称、脱敏手机号、注册时间、普通用户状态和 `currentMiniappBinding`；后者必须明确为 `null`，或包含当前 `bindingUid + staffAccountUid + version`，供换绑前校验机构用户这一侧的当前绑定。响应不返回完整手机号或 OpenID，并设置 `Cache-Control: no-store`。

### 2. 设置、换绑与撤销

```http
GET /api/v1/web/organizations/{organizationCode}/staff-accounts/{staffAccountUid}/miniapp-binding
```

`GET` 返回 `currentMiniappBinding`：没有活动绑定时必须明确返回 `null`；存在时返回 `bindingUid + organizationUserUid + version`，只有另有 `user.read` 时才附带昵称和脱敏手机号。该查询需要当前范围的 `staff.bind`，响应设置 `Cache-Control: no-store`。

```http
PUT /api/v1/web/organizations/{organizationCode}/staff-accounts/{staffAccountUid}/miniapp-binding
Idempotency-Key: <UUIDv4>
```

```json
{
  "organizationUserUid": "...",
  "expectedStaffBinding": null,
  "expectedOrganizationUserBinding": null,
  "reason": null
}
```

- `expectedStaffBinding` 与 `expectedOrganizationUserBinding` 都是必填可空字段：显式 `null` 表示操作者观察到该侧没有活动绑定；非空时必须提交观察到的 `bindingUid + version`。省略字段不是“忽略并发”，而是 `400`。Web 先分别读取工作人员当前绑定和手机号查找结果，再构造这两个快照。
- `PUT` 表示操作者已经明确确认“该工作人员在本机构 AppID 下当前绑定到该机构用户”。服务端按稳定顺序锁定工作人员与目标机构用户并重新读取两侧活动绑定；任一侧与预期快照不一致时返回 `409 IDENTITY.MINIAPP_BINDING_VERSION_CONFLICT`，不能静默覆盖。完全相同的当前绑定且两侧预期都匹配时返回已有资源。
- 预期快照匹配后，若工作人员或机构用户存在其他活动绑定，服务端在同一事务内把冲突绑定版本各递增一次并撤销其管理会话，再创建初始 `version=0` 的新绑定；不要求前端先撤销再创建，也不留下无绑定中间窗口。唯一活动槽冲突同样转换为上述 `409`，不能返回未预期的数据库错误。
- 设置或换绑成功后，同时撤销目标机构用户的全部活动 `aud=miniapp` 会话，使其下一次请求重新执行 `wx.login` 并立即进入管理页，而不是等待任一普通 Token 到期；被替换或撤销绑定所引用的全部 `aud=miniapp-staff` 会话也一并撤销。
- 绑定前校验机构用户已验证手机号、用户/工作人员/AppID 属于同一租户和机构、工作人员具有当前机构管理访问路径，且操作者具有相应范围的 `staff.bind`；查找用户仍按上节独立要求 `user.read`，不能以绑定能力绕过用户读取边界。
- 不按目标账号权限等级限制绑定，租户主体账号也不例外。普通用户被冻结不阻止其作为已经人工核验的微信身份进行工作人员绑定。
- 操作使用 I-004 的 `Idempotency-Key`，原因可空；成功响应返回当前 `bindingUid + status=ACTIVE + version`，创建、幂等命中、换绑和冲突结果都记录安全审计。

撤销接口：

```http
POST /api/v1/web/staff-miniapp-bindings/{bindingUid}/revocations
Idempotency-Key: <UUIDv4>
```

```json
{
  "expectedVersion": 3,
  "reason": null
}
```

- 撤销只推进绑定为已撤销，不物理删除；同一事务撤销引用它的全部工作人员小程序会话。单纯撤销绑定不额外撤销该机构用户已有的普通 `aud=miniapp` 会话。
- 撤销后下一次 `wx.login` 在普通用户仍有效时回到普通/清运入口。
- 平台支持人员调用相同特权用例时使用以下完整独立入口，不能把平台身份塞入普通租户接口：

```http
GET  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-accounts/{staffAccountUid}/miniapp-binding
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/organization-users/phone-lookups
PUT  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-accounts/{staffAccountUid}/miniapp-binding
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/staff-miniapp-bindings/{bindingUid}/revocations
```

平台入口的请求体、幂等、并发和响应契约与对应租户入口相同；平台身份另行鉴权并自动记录目标租户、机构和操作人。

### 3. 接口反馈到数据库的公开身份

I-002 与本项共同确认以下 UUIDv4 公开身份，均为不可变、全局唯一 ASCII 字符串；数据库内部主键和外键继续使用 `BIGINT`：

| 对象 | 对外字段 | 数据库落实 |
|---|---|---|
| 平台管理员 | `platformAdminUid` | `iam_platform_admin.platform_admin_uid` |
| 工作人员账号/租户主体账号 | `staffAccountUid` | `iam_staff_account.staff_account_uid` |
| 机构用户 | `organizationUserUid` | `iam_organization_user.organization_user_uid` |
| 工作人员小程序绑定 | `bindingUid` | `iam_staff_miniapp_binding.binding_uid` |

后续若任职、授权等对象需要被客户端单独寻址，同样必须在对应接口批次补充公开身份，不能暴露内部主键。

## I-010 会话期限、实时授权与认证错误

**已确认：P0 使用短期、不可刷新、可立即撤销的强类型会话；Token 只标识会话，当前能力与作用域由服务端实时计算。**

### 1. 有效期与会话操作

- Web 会话使用 8 小时绝对有效期，不滑动续期；小程序 `miniapp` 与 `miniapp-staff` Token 使用 2 小时绝对有效期。
- P0 不使用 Refresh Token。Web 过期后重新输入密码；小程序过期后重新执行 `wx.login`。
- P0 允许同一账号存在多个独立会话，不设固定设备数量上限；登出只撤销当前会话，不提供会话列表或“一键退出全部设备”。
- 普通小程序当前会话/登出使用 `/api/v1/miniapp/auth/sessions/current`；工作人员小程序使用 `/api/v1/miniapp-staff/auth/sessions/current`。`GET` 返回当前安全视图，`DELETE` 撤销当前 `jti` 并返回 `204`。

### 2. Token、能力和实时校验

- JWT 只保存 `iss`、稳定公开 `sub`、`jti`、`aud`、`iat`、`exp` 等必要标识；不得保存数字角色、可长期信任的能力列表、余额或敏感身份资料。
- Web 工作人员、Web 平台、普通小程序和工作人员小程序使用不同受众。普通 Token 只能访问 `/api/v1/miniapp/**`，工作人员 Token 只能访问 `/api/v1/miniapp-staff/**`，Web 身份也不能跨普通/平台路径。
- 能力码使用稳定的 `domain.action` 字符串，例如 `delivery.review`；客户端展示的能力只控制菜单和按钮，不能作为服务端授权凭证。
- Web 当前会话返回实时租户/机构能力投影；工作人员小程序能力还必须与该客户端当前开放的渠道白名单求交。P0 开放当前机构精简统计、设备容量/满溢和告警只读视图；I-030 的检测、基准和安全恢复写命令仍保持 Web-only。
- 每次受保护请求依次校验签名及 `iss/aud/exp/jti`、强类型会话、会话 `authVersion` 与当前主体、账号/租户/机构/AppID/绑定状态，以及本次用例需要的当前能力和资源作用域。
- 禁用、改密、任职或权限变化按影响范围递增授权版本并撤销工作人员会话；普通用户冻结或清运能力变化只撤销 `aud=miniapp` 会话。建立或换入工作人员绑定时，撤销新目标机构用户的全部活动 `aud=miniapp` 会话；换绑涉及的旧绑定和新绑定、纯撤销涉及的被撤销绑定，其全部 `aud=miniapp-staff` 会话都被撤销。纯撤销不额外撤销该机构用户已有的普通会话。机构或 AppID 停用使该机构小程序会话失效并让相关 Web 授权立即不可用。

### 3. 稳定错误与客户端恢复

| 场景 | HTTP / 错误码 |
|---|---|
| 登录名不存在或密码错误 | `401 AUTH.INVALID_CREDENTIALS`，使用统一文案防止账号枚举 |
| 正确凭据但账号、租户或登录入口不可用 | `403 AUTH.ACCOUNT_UNAVAILABLE` |
| 会话缺失、过期、撤销、伪造或授权版本失效 | `401 AUTH.SESSION_INVALID` |
| Token/会话受众不匹配 | `401 AUTH.TOKEN_AUDIENCE_MISMATCH` |
| Web CSRF 缺失或不匹配 | `403 SECURITY.CSRF_INVALID` |
| 已认证但缺少业务能力 | `403 AUTH.CAPABILITY_REQUIRED` |
| 目标资源不在授权机构范围 | `404 RESOURCE.NOT_FOUND` |
| 登录或接口限流 | `429 AUTH.LOGIN_THROTTLED` 或对应限流码，并返回 `Retry-After` |

- 小程序收到会话类 `401` 后清除对应受众 Token，并且最多自动执行一次 `wx.login`，避免无限登录循环。
- 重新登录后只有安全查询，或携带原 `Idempotency-Key` 的同一业务命令，才允许客户端自动重试；开门、提现、审核等命令不能生成新键后静默重放。

## 后续细化边界

本章不定义工作人员、任职和权限资源的完整管理接口，也不列出全部业务能力码；它们在下一身份目录章节确认。本章已经冻结的登录路径、单 Token 自动入口、首次手机号绑定、原子换绑、公开身份和会话有效期不得由后续资源接口改变。
