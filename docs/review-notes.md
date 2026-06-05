# 代码 Review 待办记录

本文件按 git 提交 ID 隔离，记录每次 review 发现的、**暂不立即修复但推进项目时需要考虑**的点。
每节标题为被 review 的提交短 ID + 主题，便于回溯。不在此处记录已修复内容。

---

## 2b0e755 — 多租户隔离 + 角色体系重构 + JWT 强制失效

Review 时间：2026-06-05

### 已确认良好（无需处理，仅备注）

- `EcoBinTenantLineHandler.ignoreTable()` 先判空再取租户，避免 `getTenantId()` 为 null 时 NPE（依赖 MyBatis-Plus 先调 `ignoreTable` 的契约）。
- `JwtAuthenticationFilter` 用 `try/finally` 清理 ThreadLocal，避免异常路径下租户上下文串线。
- 登录失败统一返回"用户名或密码错误"，不泄露账号是否存在。

### 待考虑项

> 已处理（按本文件约定不再展开）：原「终端用户登录后无业务接口可用」设计缺口已落地 C 端只读接口
> （`/api/app/**`：我的投递记录 + 个人信息，按 `user_id` 归属过滤），并补齐 `SecurityConfig` 终端域放行。
> 详见 `docs/permission-design.md` §9。

#### 1.【中·运维隐患】TokenInvalidationRegistry 重启即失忆
内存 `ConcurrentHashMap` 实现。除注释已提的"多实例需换 Redis"外，更隐蔽的问题是：
**应用重启后失效记录全部丢失**，被禁用/降权账号的旧 token 在重启后会重新生效，直到自然过期。
- **生产前必须解决**：失效记录持久化（Redis 等），或显著缩短 JWT 有效期。

#### 2.【中·加密强度】AES/ECB 模式
`AesCryptoUtil` 使用 `AES/ECB/PKCS5Padding`，无 IV、相同明文产生相同密文。
当前仅加密 `miniapp_secret` 单值字段，危害有限。
- 建议改用 `AES/GCM/NoPadding`（随机 IV + 认证标签）。
- 默认密钥硬编码在代码中，**生产环境必须通过 `app.crypto.aes-key` 覆盖**。

#### 3.【中·需确认】updateById 置 null 依赖默认字段策略
`TenantServiceImpl` / `AdminServiceImpl.updateById` 在密码/secret 为空时 `setPassword(null)`，
依赖 MyBatis-Plus 默认 `FieldStrategy=NOT_NULL`（null 字段不参与 update）来"保持原值"。
- **风险**：若全局 `update-strategy` 被改动，会把密码字段直接清空（高危）。
- **待确认**：核对 MyBatis-Plus 全局策略，或显式注释锁定该假设。

### 小问题（低优先级）

#### 4.【低·体验】失效触发过宽
三处 `updateById` 失效条件为 `role != null || status != null`，只要前端回传这两个字段（哪怕值未变）就强制下线。
- 建议比对旧值变化后再登记，避免无谓重登。

#### 5.【低·精度】秒级失效有 1 秒窗口
`after()` 用 `tokenIatSec < invalidated`，iat 与失效时刻均为秒级。同一秒内"改角色"与旧 token 签发时间相等时判定未失效，存在最多 1 秒有效窗口。低危，可接受。

#### 6.【低·健壮】writeUnauthorized 手工拼 JSON
`JwtAuthenticationFilter.writeUnauthorized` 直接字符串拼接 JSON。当前 message 为固定常量无注入风险，但建议改走 `ObjectMapper` / `Result` 序列化。

#### 7.【低·命名】DEFAULT_TENANT_ID 与 PLATFORM_POOL_TENANT_ID 同值=1
两常量均为 1，语义重叠（既是"平台池"又是"默认/未分配"）。
- 将来真正给租户分配 id 时注意区分，避免混淆。
