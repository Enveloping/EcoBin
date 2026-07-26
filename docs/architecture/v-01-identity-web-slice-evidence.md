# V-01｜身份与 Web 管理纵切实施证据

> 验证日期：2026-07-27
> 任务：[V-01 租户、机构和工作人员可以安全登录管理](../planning/tasks/p0-controlled-loop/v-01-tenant-organization-staff-login.md)

## 1. 实施结果

- 平台管理员与工作人员使用独立登录入口。登录请求只接收 `loginName/password`，工作人员
  登录名由目标表的全局唯一约束保证，不接收租户、角色或权限作为认证依据。
- Web 凭据是持久化 `jti` 会话对应的目标 JWT，只通过
  `__Host-ecobin-web-session` 下发；Cookie 固定为
  `Secure + HttpOnly + SameSite=Lax + Path=/`，不设置 Domain，响应正文和
  JavaScript 状态中都没有会话 Token。
- `/api/v1/web/**` 使用独立 Spring Security 链。登录与全部非安全方法均校验
  `X-CSRF-TOKEN`；匿名 CSRF bootstrap、登录、登出和前端安全重试遵守 F-09 的轮换边界。
- 每个业务请求先从 Cookie 解析最小 `sub/jti/aud/iat/exp`，再查询 MySQL 会话、账号、
  租户、任职和权限当前事实。账号状态、租户状态、`authVersion`、撤销时间和 audience
  任一不匹配都会拒绝会话，客户端提交的 tenant、organization、accountKind 或 role
  不能扩大范围。
- identity 模块增加平台及工作人员两套明确控制器，完成租户、主体账号、机构、普通员工、
  机构员工原子开通、任职、租户权限、机构权限、个人资料和改密闭环。平台跨租户操作只能
  进入 `/api/v1/web/platform/tenants/{tenantCode}/...` 命名空间。
- 所有目录命令使用 `READ_COMMITTED` 事务、资源行锁、`version + authVersion` 和
  `Idempotency-Key`。幂等摘要绑定操作者、动作、规范化目标资源和请求体；同一操作者的
  并发同键请求在操作者行锁处收敛，重试从成功操作槽校验摘要并通过权威资源读取重建响应，
  同键异摘要或异目标返回 `409 COMMON.IDEMPOTENCY_KEY_CONFLICT`。
- 登录账号行以 `FOR UPDATE` 串行校验。失败计数即使返回 401 也提交；连续五次失败锁号
  15 分钟，错误响应不泄露账号是否存在。
- 账号禁用、改密、租户停用、机构停用、任职或授权变化会递增必要版本并撤销受影响会话。
  机构停用同时撤销租户主体和该机构工作人员的 Web/工作人员小程序会话。
- operations 模块提供 `ops_audit_log` 适配器。成功的目录、安全和授权变化与业务修改在
  同一事务提交；拒绝/失败请求和平台跨租户读取在请求结束后以独立事务留痕。安全摘要不
  承担响应缓存职责，只记录操作者、作用域、动作、目标、状态及必要前后值；完整响应不入
  审计，密码、电话、地址、OpenID、Token、密钥、凭据和 URL 等敏感值统一脱敏。
- Web 管理端新增目标租户、当前租户、机构、员工、任职/授权和账号设置页面。前端统一
  使用 Cookie+CSRF 与幂等键，隐藏仍依赖旧 Bearer/旧接口的下游页面，等待 V-02 以后纵切。
- `contracts/http/openapi.yaml` 同步增加全部 V-01 控制器操作和 DTO Schema。当前机器源
  共 73 个唯一 operation、596 个可解析本地引用；自动对照确认每个 V-01 目录控制器方法
  都存在于 OpenAPI。

## 2. 真实 MySQL 验收

`TargetWebIdentityMysqlIntegrationTest` 在 Flyway V1～V10 的 MySQL 目标库、最小
`ecobin_app` 运行身份和真实目标 epoch guard 下验证：

1. Cookie 冻结属性、正文无 Token、CSRF、独立登录入口、两租户/三机构隔离、实时会话
   撤销、天然/显式权限及客户端伪造身份无效；
2. 同一操作者两个并发请求复用同一 `Idempotency-Key` 时只创建一个租户、只提交一条
   成功审计；
3. 连续五次错误密码的失败计数真实提交并锁号，之后正确密码仍被拒绝；
4. 必填 `permissionCodes`、`expectedVersion`、`manager` 缺失或为 null 时返回 400，
   不会被解释为清空权限、版本 0 或非负责人；
5. 幂等摘要包含路径目标，同键同正文改投另一租户、机构或工作人员时返回冲突且不修改
   第二个目标；
6. 审计摘要脱敏工作人员电话，平台跨租户 GET 和登录拒绝均产生相应审计；
7. 普通工作人员不能通过新建自己的任职把自己提升为机构负责人；
8. 仅有机构级读取权限的管理员查看共享任职员工时，只能看到共享机构的权限，不会得到
   该员工的租户级或其他机构授权。

测试类默认按环境变量选择性启用，避免普通 H2 回归误把 MySQL 专项当作通过；本次验收
显式设置目标 JDBC URL 后八项测试全部执行。

## 3. 复验证据

```powershell
.\mvnw.cmd install -DskipTests
.\mvnw.cmd test

$env:ECOBIN_V01_MYSQL_URL = 'jdbc:mysql://127.0.0.1:33316/ecobin_v01_test?...'
$env:ECOBIN_V01_MYSQL_USERNAME = 'ecobin_app'
$env:ECOBIN_V01_MYSQL_PASSWORD = '<ephemeral-test-secret>'
.\mvnw.cmd -pl ecobin-bootstrap -Dtest=TargetWebIdentityMysqlIntegrationTest test

python contracts/tools/http_contract.py
npx --yes @redocly/cli@2.20.3 lint contracts/http/openapi.yaml

Set-Location frontend/web
npm run test:http-foundation
npm run build
```

结果：

- Maven reactor 安装成功，Java 21 release 编译通过；
- 全仓回归发现 123 项测试，执行 118 项，5 项可选可靠收件箱 MySQL 专项按设计跳过；
  `failures=0`、`errors=0`；
- V-01 真实 MySQL 专项 8 项全部执行并通过；
- HTTP 契约专项 5 项通过，73 个 operation、596 个本地引用和 7 份样例有效；
- Redocly 判定 API description 有效，仅报告既有许可证缺失及预留公共组件未引用警告；
- Web 3 项传输基础测试通过，TypeScript project build 与 Vite production build 通过；
- `git diff --check` 无空白错误，仅有 Windows 工作区 LF/CRLF 提示。

Vite 仍报告既有单包体积超过 500 kB 的提示；代码分包属于独立前端性能任务，不影响本
身份纵切的功能和安全验收。

## 4. 范围边界

本切片不实现机构普通用户微信注册、工作人员小程序免密入口、设备/投递/清运/资金业务、
Refresh Token 或平台用户跨租户成员关系。旧业务控制器和旧页面仍供未纵切功能保留，但
V-01 新路由和 `/api/v1/web/**` 安全链不会把旧 Bearer 身份当作目标会话。
