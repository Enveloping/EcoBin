# V50｜平台管理员引导与治理

> 状态：已确认并实施
> 数据库版本：V50
> 适用端：后端、Web 管理端、生产部署

## 1. 目的

系统必须在平台管理员表完全为空时建立第一个可登录账号，同时不能让之后创建的普通
平台管理员获得管理其他平台管理员的权限。V50 将平台管理员分为两类：

- `DEFAULT`：受保护的默认平台管理员，全系统最多一个；
- `STANDARD`：之后由默认管理员创建的普通平台管理员。

两类账号都可以使用平台业务功能并修改自己的密码。只有 `DEFAULT` 获得
`platform-account.manage`（平台账号治理）能力。

## 2. 升级和空库引导

V50 为 `iam_platform_admin` 增加 `admin_kind`、`deleted_at` 和唯一默认管理员约束。
升级前如果表中只有一个规范化登录名为 `enveloping` 的账号，迁移只把它标记为
`DEFAULT`，保留原 UID、密码摘要、启停状态、认证版本和并发版本。迁移不写入或修改
密码。

应用启动时启用 `defaultPlatformAdminEnabled=true` 后按以下事实处理：

1. 表完全为空：用固定登录名 `enveloping` 创建一个启用的 `DEFAULT` 账号；初始密码
   只从仓库外的 `defaultPlatformAdminPassword` 读取并立即 BCrypt 摘要化；
2. 表非空且恰有一个启用、未删除的 `DEFAULT`：不创建账号、不读取密码进行重置；
3. 表非空但没有唯一有效的 `DEFAULT`：启动失败，要求人工核对数据，不自动提升任意
   普通账号。

因此，重复启动不会改变密码；已有唯一账号升级后仍使用升级前的账号密码。生产密码以
`/etc/ecobin/secrets/default-platform-admin-password` 持久保存，运行时只读挂载为
`/run/secrets/defaultPlatformAdminPassword`，不得进入 Git、`runtime.env`、日志或审计。

## 3. 管理边界

默认管理员可以查询平台管理员、创建普通平台管理员、停用或启用普通管理员、重置其
密码，以及永久逻辑删除普通管理员。

普通平台管理员不能执行上述操作，只能在“账号设置”中提供当前密码后修改自己的密码。
默认管理员也通过同一个自助流程修改自己的密码。默认管理员不能被停用、删除或通过
“重置他人密码”入口修改。

普通账号被停用、改密、重置密码或删除时，后端增加 `auth_version` 并撤销该账号全部
活动会话；已有会话不能继续调用业务接口。

## 4. 永久逻辑删除

“删除”会在同一事务中把普通管理员改为禁用、写入 `deleted_at`、增加认证版本和并发
版本，并撤销全部会话。删除后：

- 登录查询排除该行，账号不能再次登录；
- 启用、重置密码和再次删除均被拒绝；
- 原登录名仍受唯一约束保护，不能被新账号复用；
- Web 列表仍可按 `DELETED` 查询历史账号，但只显示“不可恢复”。

这不是可恢复的“回收站”。需要新账号时必须使用新的登录名创建新记录。

## 5. HTTP 与 Web

默认管理员专属接口位于：

```text
GET  /api/v1/web/platform/admin-accounts
GET  /api/v1/web/platform/admin-accounts/{platformAdminUid}
POST /api/v1/web/platform/admin-accounts
POST /api/v1/web/platform/admin-accounts/{platformAdminUid}/activations
POST /api/v1/web/platform/admin-accounts/{platformAdminUid}/deactivations
POST /api/v1/web/platform/admin-accounts/{platformAdminUid}/password-resets
POST /api/v1/web/platform/admin-accounts/{platformAdminUid}/deletions
```

所有平台管理员共用自助改密接口：

```text
POST /api/v1/web/platform/admin-accounts/current/password-changes
```

写接口使用 UUIDv4 `Idempotency-Key`（同一人工操作重试时复用的操作标识）和
`expectedVersion` / `expectedAuthVersion` 乐观并发检查。审计只保存账号安全摘要、操作
结果和可选原因，不保存请求密码。永久删除在 Web 端要求输入目标登录名和删除原因二次
确认。

## 6. 不变量

- 全库最多一个 `DEFAULT`；它必须启用且未删除。
- `deleted_at` 非空的账号必须禁用。
- 运行账号只能修改 `deleted_at` 等生命周期投影，不能修改 `admin_kind`。
- 权限判断同时复核当前数据库账号类别与会话能力，不能仅依赖前端隐藏菜单。
- 任何管理员密码都不以明文进入数据库、运行环境变量、请求日志或审计记录。
