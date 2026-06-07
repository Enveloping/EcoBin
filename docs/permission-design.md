# EcoBin 权限与角色设计文档

> 状态：讨论完成，待实现  
> 日期：2026-06-04  
> 关联文档：[[database-design.md](database-design.md)]

---

## 1. 总体方案：纯角色控制

| 维度 | 机制 | 说明 |
|------|------|------|
| 角色权限 | JWT（硬编码） | 登录时将角色值打入 JWT，URL 层通过 `SecurityFilterChain` + `hasRole()` 校验 |
| 强制失效 | Token 失效登记表 | 角色/状态变更时登记标识符，JWT 校验时比对签发时间，旧 token 立即失效 |
| 数据隔离 | `tenant_id` | MyBatis-Plus 租户拦截器按上下文自动注入 `WHERE tenant_id`，平台域放行 |

> **tenant_id 约定**：`tenant_id = 1` 是保留值，表示"平台池/未分配"，**不对应任何真实租户**。真实租户的
> `tenant_id = sys_tenant.id 且恒 > 1`（`sys_tenant` 表 `AUTO_INCREMENT = 2`，id=1 永久保留）。
> 平台级主体（`sys_admin`）本身无 `tenant_id` 列，仅在 JWT 上下文中赋逻辑值 1 并被租户拦截器放行。

**核心理念**：角色即权限。不引入独立的权限码表、设备权限表或 AOP 切面。

```
能否执行某操作 = 角色满足 + tenant_id 匹配（如适用）
```

---

## 2. 三张登录主体表

三类登录主体，分开存储，职责清晰：

| 表 | 存储对象 | 角色 | 登录端 | 有无 tenant_id |
|----|----------|------|--------|---------------|
| `sys_admin`（新） | 平台管理员 | 9-超管, 8-管理员 | 网页 | ❌ 无（平台级） |
| `sys_tenant`（扩） | 租户 | 7-租户 | 网页 | ❌ 无（它自己就是租户） |
| `sys_user`（现有） | 终端用户 | 3-设备管理员, 2-清运员, 1-用户 | 小程序 | ✅ 有（归属某租户） |

### 2.1 `sys_admin` 表结构（新）

```sql
CREATE TABLE sys_admin (
    id          BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(50)  NOT NULL,
    password    VARCHAR(255) NOT NULL COMMENT 'BCrypt加密',
    real_name   VARCHAR(50)  DEFAULT NULL,
    role        TINYINT      NOT NULL COMMENT '9=SUPER_ADMIN, 8=ADMIN',
    status      TINYINT      NOT NULL DEFAULT 1 COMMENT '0=禁用, 1=启用',
    create_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_admin_username (username)
);
```

- 无 `tenant_id`、无 `openid`、无 `phone`、无 `email`
- 仅超管(9)可创建新的 sys_admin 记录
- 初始 seed data：admin / admin123（role=9）

### 2.2 `sys_tenant` 扩展

在现有 `sys_tenant` 基础上新增：

```sql
ALTER TABLE sys_tenant
    ADD COLUMN username       VARCHAR(50)  DEFAULT NULL COMMENT '租户登录用户名',
    ADD COLUMN password       VARCHAR(255) DEFAULT NULL COMMENT 'BCrypt加密密码',
    ADD COLUMN miniapp_appid  VARCHAR(32)  DEFAULT NULL COMMENT '小程序AppID（未配置存NULL）',
    ADD COLUMN miniapp_secret VARCHAR(256) DEFAULT NULL COMMENT '小程序Secret（AES加密）',
    ADD COLUMN merchant_no    VARCHAR(64)  DEFAULT NULL COMMENT '微信商户号',
    ADD UNIQUE KEY uk_tenant_miniapp_appid (miniapp_appid);
-- 同时设置自增起点，保留 id=1 给平台池
ALTER TABLE sys_tenant AUTO_INCREMENT = 2;
```

- 现有 `code` 字段保留（内部可读标识符）
- `miniapp_secret` AES 加密存储，密钥在配置文件中
- `miniapp_appid` 须加**唯一约束**（`uk_tenant_miniapp_appid`），保证 wx-login 按 appid 反查租户唯一命中；
  未配置时存 `NULL` 而非 `''`（MySQL 唯一索引允许多个 NULL），避免多个空值冲突
- `sys_tenant` 无 `tenant_id` 列（当前 Java 实体 `Tenant extends BaseEntity` 有不一致，实现时修复）

### 2.3 `sys_user` 表（现有，角色值调整）

`sys_user` 专用于小程序用户，仅包含 role=1/2/3：

```
role = 3 → DEVICE_ADMIN  设备管理员
role = 2 → CLEANER       清运员
role = 1 → USER          普通用户（默认）
```

- 只能通过小程序微信扫码自动注册，**不允许手动创建**
- `username` 和 `password` 在 V2 迁移中已改为 nullable

---

## 3. 角色体系（硬编码）

```
 9 = SUPER_ADMIN   超级管理员（sys_admin）
 8 = ADMIN         管理员（sys_admin）
 7 = TENANT        租户（sys_tenant）
 6 = (预留)
 5 = (预留)
 4 = (预留)
 3 = DEVICE_ADMIN  设备管理员（sys_user）
 2 = CLEANER       清运员（sys_user）
 1 = USER          普通用户（sys_user）
```

**作用域，而非线性高低**：角色不是一条"值越大权限越高"的链，而是分属三个独立作用域：

```
平台域：9 超管、8 管理员   —— 管平台（租户/设备分配），不进入任何租户业务数据
租户域：7 租户             —— 管自己租户内的用户与业务数据
终端域：3 设备管理员、2 清运员、1 用户  —— 在某租户下操作设备 / 投递
```

仅在**终端域内部**存在包含关系：设备管理员(3) ⊃ 清运员(2) ⊃ 用户(1)。跨域不存在"高值自动覆盖低值"，
例如租户(7) 能看业务数据而管理员(8) 不能（见 §4 矩阵）。

---

## 4. 角色权限矩阵

> ✅ 可操作  ❌ 不可操作

### 4.1 管理操作

| 操作 | 超管(9) | 管理员(8) | 租户(7) | 设备管理员(3) | 清运员(2) | 用户(1) |
|------|---------|-----------|---------|--------------|-----------|---------|
| 创建/禁用管理员(sys_admin) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 创建/禁用租户(sys_tenant) | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| 创建/管理设备 | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 提升/降低用户角色(sys_user) | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 查看租户设备清单（不含状态） | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 查看业务数据 | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |

> 投递物品见 §4.2 设备操作（投递是终端域动作）。

### 4.2 设备操作

| 操作 | 超管(9) | 管理员(8) | 租户(7) | 设备管理员(3) | 清运员(2) | 用户(1) |
|------|---------|-----------|---------|--------------|-----------|---------|
| 操作设备 | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ |
| 设备清运 | ❌ | ❌ | ✅ | ✅ | ✅ | ❌ |
| 设备维护 | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| 投递物品 | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ |

> 投递依赖小程序 `openid` 身份（终端域）。平台域的超管(9)/管理员(8) 无 `openid`，无法投递，故为 ❌。
> 租户(7) 走网页端但归属自身租户，保留投递能力（如租户自营测试）。

### 4.3 数据可见性

| 数据类型 | 超管(9) | 管理员(8) | 租户(7) | 设备管理员(3) | 清运员(2) | 用户(1) |
|----------|---------|-----------|---------|--------------|-----------|---------|
| 管理员列表(sys_admin) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 租户列表(sys_tenant) | ✅ | ✅ | ❌(仅自己) | ❌ | ❌ | ❌ |
| 用户列表(sys_user) | ✅ | ❌ | ✅(自己租户下) | ❌ | ❌ | ❌ |
| 设备列表（含设备名） | ✅ | ✅ | ✅(自己租户下) | ❌ | ❌ | ❌ |
| 设备状态（实时数据） | ✅ | ❌ | ✅(自己租户下) | ❌ | ❌ | ❌ |
| 投递订单 | ✅ | ❌ | ✅(自己租户下) | ❌ | ❌ | ❌ |
| 清运订单 | ✅ | ❌ | ✅(自己租户下) | ❌ | ❌ | ❌ |
| 提现订单 / 审核 | ✅ | ❌ | ✅(自己租户下) | ❌ | ❌ | ❌ |
| 用户钱包余额 | ✅ | ❌ | ✅(自己租户下) | ❌ | ❌ | ❌ |
| 自己的投递记录 | — | — | — | — | — | ✅ |
| 自己的钱包信息 | — | — | — | — | — | ✅ |

---

## 5. 登录设计

### 5.1 网页端登录（管理员 + 租户）

前端提供登录方式选择，传 `userType` 区分：

```
POST /api/system/auth/login
{ "userType": "admin",  "username": "...", "password": "..." }  → 查 sys_admin
{ "userType": "tenant", "username": "...", "password": "..." }  → 查 sys_tenant
```

登录成功返回 JWT：
```json
{
  "token": "...",
  "role": 9,
  "realName": "..."
}
```

### 5.2 小程序微信登录

```
POST /api/system/auth/wx-login
{ "code": "微信code", "appid": "租户A的小程序appid" }

后端流程：
1. 根据 appid 查 sys_tenant → 获取 tenant_id 和 secret（AES 解密）
2. 用 secret 调微信 code2session → 获取 openid
3. 查 sys_user WHERE tenant_id=X AND openid=Y
   → 找到：校验 status==1，生成 JWT
   → 未找到：注册新用户 {tenant_id=X, role=1, openid=...}
4. 返回 JWT
```

### 5.3 JWT 结构（按登录来源区分）

三种登录主体，JWT claims 不同：

```json
// 平台管理员（sys_admin）
{ "sub": "admin", "adminId": 1, "role": 9 }

// 租户（sys_tenant）
{ "sub": "tenant_username", "tenantId": 5, "role": 7 }

// 小程序用户（sys_user）
{ "sub": "openid", "userId": 10, "tenantId": 5, "role": 2 }
```

`JwtAuthenticationFilter` 根据 `role` 构造 `ROLE_SUPER_ADMIN` / `ROLE_ADMIN` / `ROLE_TENANT` / `ROLE_DEVICE_ADMIN` / `ROLE_CLEANER` / `ROLE_USER`。

### 5.4 强制失效（Token 失效登记表）

角色/状态变更后旧 JWT 需立即失效，采用**登记 + 比对**（而非每次操作查库）：

实现：`framework/security/TokenInvalidationRegistry`（当前为单实例内存 `ConcurrentHashMap`，
多实例部署应换 Redis）。

1. **登记**（变更发生时，由对应 Service 调用）：
   - 管理员被改角色/状态（超管禁用其他管理员）→ `invalidateAdmin(adminId)` → `admin:{id}`
   - 租户被改状态 → `invalidateTenant(tenantId)` → `tenant:{id}`
   - 禁用租户（status=0）→ 额外 `invalidateTenantUsers(tenantId)` → `tenantUsers:{tenantId}`（连带名下用户）
   - 终端用户被改角色/状态 → `invalidateUser(userId)` → `user:{id}`
   - 每条记录值为变更时刻（epoch 秒）。

2. **比对**（`JwtAuthenticationFilter` 校验 token 时）：按 `role` 选 key，若
   `token.iat < 登记的失效时刻` → 立即返回 401 `{"code":401,"message":"权限已变更，请重新登录"}`。
   - 平台域(9/8)：查 `admin:{userId}`
   - 租户(7)：查 `tenant:{tenantId}`
   - 终端用户(1/2/3)：查 `user:{userId}` 或 `tenantUsers:{tenantId}`（任一命中即失效）

3. **自动恢复**：重新登录签发的新 token `iat` 晚于失效时刻，自动有效；旧 token 永久失效。
   （被禁用账号即使重登，也会在登录阶段被 `status==0` 校验拦截。）

> 状态禁用同样通过本机制即时生效——不再使用"每次写操作查源表"的方案。

---

## 6. 数据隔离模型

### 6.1 三层归属

| 层级 | 表 | tenant_id | 隔离方式 |
|------|-----|-----------|----------|
| 平台级 | `sys_admin` | 无此列；JWT 上下文赋逻辑值 1 | 可跨租户管理，租户拦截器对其放行（ignore） |
| 租户级 | `sys_tenant` | 无此列；`id` 本身即数据空间（恒 >1） | 自包含；拦截器对该表放行 |
| 用户级 | `sys_user` | 绑定到所属租户（>1） | 查询自动过滤 `WHERE tenant_id = ?` |
| 业务表 | `biz_*` | 绑定到租户（>1）；`1` = 平台池/未分配 | 查询自动过滤、INSERT 自动回填 |

> `sys_admin`/`sys_tenant` 表本身无 `tenant_id` 列（见 §2.1/§2.2），"固定=1"指的是 `JwtAuthenticationFilter`
> 写入 `TenantContextHolder` 的逻辑值，不是数据库字段。

### 6.2 TenantContextHolder

承载当前线程的 `tenantId` 与 `ignore`（是否放行租户过滤）两个 ThreadLocal，由 `JwtAuthenticationFilter` 设置：

```java
// JwtAuthenticationFilter 中
平台域(9/8) → setTenantId(1L);            setIgnore(true);   // 全量视图
租户(7)     → setTenantId(jwt.tenantId);  setIgnore(false);  // = sys_tenant.id
用户(1/2/3) → setTenantId(jwt.tenantId);  setIgnore(false);  // = 用户所属租户
无 token    → setTenantId(1L);            setIgnore(true);   // 登录前流程，由业务显式指定 tenant_id
```

### 6.3 租户行级隔离（MyBatis-Plus TenantLineInnerInterceptor）

实现：`framework/tenant/EcoBinTenantLineHandler` + `MybatisPlusConfig` 注册 `TenantLineInnerInterceptor`
（在分页拦截器之前）。SQL 自动改写规则：

```text
ignoreTable(table) 返回 true（放行，不注入条件）当：
  - TenantContextHolder.isIgnore() == true   // 平台域 / 登录前
  - 当前无租户上下文
  - 表为平台级无 tenant_id 列：sys_admin / sys_tenant
否则：SELECT/UPDATE/DELETE 自动追加 WHERE tenant_id = ctx；INSERT 自动回填 tenant_id = ctx
```

- 设备由平台域（管理员）创建时 ignore=true 不回填，`DeviceServiceImpl` 兜底设为 `tenant_id=1`（平台池，待分配）。
- 租户/用户创建的业务数据（订单、投口、wx 注册用户）由拦截器按上下文自动回填或代码显式指定。

### 6.4 关键规则

- 创建 `sys_tenant` 记录 = 创建新数据隔离空间（新 `tenant_id` = `sys_tenant.id`，恒 >1）
- `sys_tenant` 表 `AUTO_INCREMENT = 2`，**id=1 永久保留给"平台池"，绝不分配给真实租户**，避免与未分配资源撞号
- 跨租户严格隔离：无任何跨租户数据访问或身份共享
- 同一微信 openid 在不同租户下为独立 `sys_user` 记录
- 管理员(8) tenant_id=1 但租户拦截器对其放行（ignore=true），实际 SQL 不加过滤

---

## 7. 设备归属与分配

### 7.1 设备生命周期

```
1. 管理员创建设备
   → tenant_id = 1（平台池，未分配）

2. 管理员分配给租户
   → tenant_id = 目标租户ID（>1）

3. 租户操作设备
   → 检查 jwt.tenantId == biz_device.tenant_id

4. 管理员收回设备
   → tenant_id = 1（回平台池）
```

### 7.2 分配权限

| 操作 | 超管(9) | 管理员(8) | 租户(7) |
|------|---------|-----------|---------|
| 创建设备（tenant_id=1） | ✅ | ✅ | ❌ |
| 分配设备给租户 | ✅ | ✅ | ❌ |
| 收回设备 | ✅ | ✅ | ❌ |

- 设备分配唯一入口是管理员（超管9 + 管理员8）
- 租户只能操作已分配到自己名下的设备，不能修改设备归属

---

## 8. 关键流程

### 8.1 创建租户（超管9 或 管理员8）

```
POST /api/system/tenant
{ username, password, name, code, miniapp_appid, miniapp_secret, merchant_no, ... }

后端事务：
1. sys_tenant 插入 → 获得 tenant_id（= id）
2. miniapp_secret AES 加密后存储
3. username + BCrypt(password) 直接存 sys_tenant
4. 无需创建 sys_user 记录
```

### 8.2 小程序用户注册

完全自动，无需管理员干预：
1. 用户打开租户A的小程序 → 微信静默授权
2. 后端根据 `appid` 确定 `tenant_id`
3. 自动在 `sys_user` 插入 `{tenant_id, openid, role=1}`

### 8.3 用户角色管理（租户7）

- 租户登录网页端 → 查看自己租户下的 `sys_user` 列表
- 单方面提升/降低角色（1↔2↔3），无需用户同意
- 仅可管理自己 `tenant_id` 下的用户

---

## 9. API 路径与权限配置

### 9.1 URL 拦截规则（SecurityFilterChain）

> 设计要点：
> 1. 无角色继承（不配置 `RoleHierarchy`），因此凡是超管(9) 需要的"全量视图"，都必须在规则里**显式列出 `SUPER_ADMIN`**。
> 2. URL 级 `hasRole` 无法区分读/写。对"能写不能读"的资源（如清运订单），按 **HTTP 方法拆 URL**。
> 3. **以下为当前已实现的真实路径**（控制器仍用 `/api/device`、`/api/business`、`/api/statistics`）。
>    将控制器统一对齐到 `/api/biz/...`、并新增设备分配/收回与独立投递端点，为后续可选优化项。

```java
.requestMatchers("/api/system/auth/**").permitAll()

// 平台管理：管理员账号（仅超管）
.requestMatchers("/api/system/admin/**").hasRole("SUPER_ADMIN")
// 平台管理：租户 CRUD（超管 + 管理员）
.requestMatchers("/api/system/tenant/**").hasAnyRole("SUPER_ADMIN", "ADMIN")
// 用户管理：租户管自己租户用户；超管全量查看
.requestMatchers("/api/system/user/**").hasAnyRole("SUPER_ADMIN", "TENANT")

// 设备 / 投口（含 /api/device/door）：超管 + 管理员 + 租户；租户仅能动自己设备（数据隔离兜底）
.requestMatchers("/api/device/**").hasAnyRole("SUPER_ADMIN", "ADMIN", "TENANT")

// 投递订单：超管 + 租户
.requestMatchers("/api/business/delivery/**").hasAnyRole("SUPER_ADMIN", "TENANT")

// 清运订单——读（超管 + 租户）；清运员/设备管理员只写不读
.requestMatchers(HttpMethod.GET, "/api/business/clean/**").hasAnyRole("SUPER_ADMIN", "TENANT")
// 清运订单——写（创建清运单）：租户 + 设备管理员 + 清运员
.requestMatchers("/api/business/clean/**").hasAnyRole("TENANT", "CLEANER", "DEVICE_ADMIN")

// 统计：业务数据视图，超管 + 租户
.requestMatchers("/api/statistics/**").hasAnyRole("SUPER_ADMIN", "TENANT")

// 小程序终端用户 C 端接口：仅访问属于自己的数据（投递记录 / 个人信息）
.requestMatchers("/api/app/**").hasAnyRole("USER", "CLEANER", "DEVICE_ADMIN")

.anyRequest().authenticated()
```

> 注：Spring Security 规则**自上而下匹配**，更具体（带 `HttpMethod`）的规则必须排在通配规则之前。
> 终端域（设备管理员/清运员/用户）的 C 端只读接口已落地于 `/api/app/**`（见 §9.3）：投递记录查询 + 个人信息，
> 按 `user_id` 归属过滤（叠加租户拦截器的 `tenant_id` 隔离）。终端域的**设备操作/清运/投递提交**端点仍为后续新增项。

### 9.2 GrantedAuthority 映射

```java
case 9 -> "ROLE_SUPER_ADMIN"
case 8 -> "ROLE_ADMIN"
case 7 -> "ROLE_TENANT"
case 3 -> "ROLE_DEVICE_ADMIN"
case 2 -> "ROLE_CLEANER"
case 1 -> "ROLE_USER"
```

### 9.3 API 路径一览（当前真实路径，2026-06-06）

```
网页登录:
  POST /api/system/auth/login         → 管理员/租户登录（传 userType）

小程序:
  POST /api/system/auth/wx-login      → 微信登录

管理端（仅超管）:
  /api/system/admin/**                → 管理员 CRUD

管理端（超管+管理员）:
  /api/system/tenant/**               → 租户 CRUD（*）

租户自查（仅租户本人，比上方通配规则先匹配）:
  GET /api/system/tenant/me           → 租户查看自身资料（脱敏）

超管+租户:
  /api/system/user/**                 → 用户角色管理（租户管自己；超管全量查看）
  /api/system/withdraw/**             → 提现单列表 + 审核
  /api/business/delivery/**           → 投递订单数据（管理端全租户视图）
  GET /api/business/clean/**          → 清运订单查看
  /api/statistics/**                  → 业务统计

超管+管理员+租户:
  /api/device/**                      → 设备 CRUD（含 /api/device/door 投口；租户仅限自己设备）

写操作（创建清运单）:
  /api/business/clean/**（POST/PUT/DELETE）→ 租户/设备管理员/清运员

IoT 设备上报（放行，无用户登录态）:
  POST /api/iot/delivery/complete     → 设备 IoT 投递完成上报（SN 反查鉴权）

终端域（小程序用户，USER/CLEANER/DEVICE_ADMIN）:
  GET  /api/app/profile               → 我的个人信息（脱敏 VO）
  GET  /api/app/device                → 本租户设备列表（含投口信息）
  POST /api/app/delivery/open         → 扫码开投口（建进行中投递记录）
  GET  /api/app/delivery/my           → 我的投递记录分页（按 user_id 过滤）
  GET  /api/app/delivery/my/{id}      → 我的单条投递详情（归属校验）
  GET  /api/app/wallet                → 我的钱包余额（balance + pendingBalance）
  POST /api/app/wallet/withdraw       → 发起提现申请
  GET  /api/app/wallet/withdraw       → 我的提现记录分页
```

> （*）租户 CRUD 通配 `/api/system/tenant/**` 覆盖 `GET /api/system/tenant` 全量列表等，仅超管/管理员。
> 租户自查 `GET /api/system/tenant/me` 是独立规则，在通配之前声明，仅 `TENANT` 角色。

> 设备创建默认 `tenant_id=1`（平台池）；分配/收回当前通过 `PUT /api/device/{id}` 修改 `tenant_id`
> （仅平台域可改，租户被数据隔离限制）。独立的分配/收回端点为后续新增项。

---

## 10. 实现状态（相对最初代码的变更，均已落地）

| 项目 | 最初 | 现状（已实现） |
|------|------|--------|
| `sys_admin` 表 | 不存在 | 新增，存储平台管理员（V3 迁移） |
| `sys_user.role` | 旧枚举 1-5 | 仅存 1/2/3，管理员迁到 `sys_admin`，租户迁到 `sys_tenant` |
| `sys_tenant` | 无登录字段 | 加 username/password/miniapp_appid/miniapp_secret/merchant_no + appid 唯一约束 |
| seed data admin | sys_user (role=1) | sys_admin (role=9) |
| `UserRole` 枚举 | 旧 5 种 | 新 6 种有效值 (1/2/3/7/8/9) + `authorityOf/isPlatform` |
| `SecurityConfig` | `.permitAll()` | 按角色拦截（匹配真实路径，见 §9.1） |
| `JwtAuthenticationFilter` | `emptyList()` | 按 role 构造 `GrantedAuthority` + 设租户上下文 + 强制失效校验 |
| `Tenant` 实体 | extends BaseEntity | 改 extends `PlatformBaseEntity`（无 tenant_id） |
| 租户数据隔离 | 仅日志占位 | MyBatis-Plus `TenantLineInnerInterceptor` + `EcoBinTenantLineHandler` 自动隔离 |
| 强制失效 | 无 | `TokenInvalidationRegistry`（登记+比对，见 §5.4） |
| miniapp_secret | 无 | `AesCryptoUtil` AES 加密存储 |
| `biz_device` | 现有字段 | 无变更（创建默认 `tenant_id=1`） |
| openid 唯一约束 | 全局唯一 `uk_openid` | 改建租户内复合唯一 `uk_tenant_openid (tenant_id, openid)`（V6） |
| 投递闭环 | 无数据入口 | 两阶段流程：C端开投口 + IoT 设备上报回填（V7），`/api/iot/**` 放行 |
| 租户自查 | 无端点 | `GET /api/system/tenant/me`（仅 TENANT 角色） |
| 投递流水删除 | 可删除 | 移除删除接口，投递流水不可变 |
| 用户余额 | 无 | `sys_user.balance` + `sys_user.pending_balance`（V8），原子 SQL 防并发 |
| 投口单价 | 无 | `biz_door.price`（元/kg），投递完成时 `price × weight` 入账 |
| 提现流程 | 无 | `biz_withdraw_order` + 申请/租户审核/记录查询（V8） |
| C 端钱包 | 无 | `GET/POST /api/app/wallet` + `/api/app/wallet/withdraw` |

### 新增关键组件
- `common/base/PlatformBaseEntity`：无 `tenant_id` 的平台级实体基类（Admin/Tenant 继承）。
- `framework/tenant/EcoBinTenantLineHandler`、`framework/security/TokenInvalidationRegistry`、`framework/crypto/AesCryptoUtil`。
- `system`：`Admin` 实体/Mapper/Service/Controller、`AuthService`（按 userType/appid 分派登录）。

### 旧枚举值迁移

| 旧 `UserRole` | 旧值 | 新值 | 迁移目标 |
|---------------|------|------|----------|
| SUPER_ADMIN | 1 | 9 | `sys_admin` |
| DEVICE_ADMIN | 2 | 3 | `sys_user` |
| OPERATOR | 3 | — | 废弃（租户代替） |
| CLEANER | 4 | 2 | `sys_user` |
| NORMAL_USER | 5 | 1 | `sys_user` |

---

## 11. 已确认决策清单

- [x] 三张登录主体表：sys_admin / sys_tenant / sys_user
- [x] 纯角色控制，无设备权限表、无 AOP、无审计日志
- [x] 强制失效用 Token 失效登记表（登记+比对签发时间），覆盖：超管禁用管理员、租户改用户、禁用租户连带名下用户下线
- [x] 租户数据隔离用 MyBatis-Plus 租户拦截器自动注入 `tenant_id`，平台域放行
- [x] 角色体系：9-超管, 8-管理员, 7-租户, 6/5/4-预留, 3-设备管理员, 2-清运员, 1-用户
- [x] JWT 只放 role，硬编码映射权限
- [x] 租户下角色(2-7)自动拥有该租户所有设备对应操作权限
- [x] 跨租户严格隔离
- [x] 用户仅通过小程序自动注册，不允许手动创建
- [x] 角色提升/降低唯一入口是租户(7)，单方面操作
- [x] 管理员(8)：可创建/禁用租户、创建设备、查看设备清单（不含状态），不可查看业务数据
- [x] 设备管理员(3)和清运员(2)：无数据查看权
- [x] 每租户独立小程序，通过 `sys_tenant.miniapp_appid` 绑定
- [x] 同一 openid 在不同租户下独立注册
- [x] `miniapp_secret` AES 加密，密钥放配置文件
- [x] 网页登录前端传 `userType` 区分管理员/租户
- [x] API 路径：URL 级 `SecurityFilterChain` + `hasRole/hasAnyRole`（按真实控制器路径，§9.1）
