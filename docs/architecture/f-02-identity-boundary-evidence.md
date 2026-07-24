# F-02｜identity 边界与可信上下文实施证据

> 验证日期：2026-07-24  
> 任务：[F-02 identity 边界、可信上下文与公开端口](../planning/tasks/p0-controlled-loop/f-02-identity-boundary-and-trusted-context.md)

## 1. 实施结果

- 旧 `ecobin-module-system` 已从 Maven reactor 和生产源码退出，其既有行为迁入
  `ecobin-module-identity`；当前是九个目标模块加一个 legacy business 模块的十模块过渡
  reactor。
- 其他生产模块只通过 `identity.api`（含明确标记的迁移期 `api.legacy`）使用身份能力，
  不导入 identity 的 application、infrastructure、web、Entity、Mapper 或内部 Service。
- framework 已建立不可变 `TrustedExecutionContext`。签名 JWT 只作为候选凭据，identity
  仍从数据库重读主体、租户、角色和启停状态后才建立可信上下文。
- legacy 角色使用严格白名单：平台 `9/8`、租户 `7`、机构用户 `3/2/1`；未知角色直接
  拒绝，不能默认为机构用户。
- JWT 过滤器以方法级 `finally` 清理租户与可信上下文；正常处理、认证拒绝、提前返回和
  下游异常都不能把 ThreadLocal 留给下一请求。
- 微信 `code2session` 在本地数据库事务外完成；首次用户创建、同步 funds 参与和本地
  JWT 响应在同一事务内成败。
- `OrganizationUserRegistrationParticipant` 只有一份 production 实现，并以
  `Propagation.REQUIRED` 参加首次注册；重复登录不会重复参与。
- 关系专用 `OrganizationUserWalletOwnerRef` 没有 public 构造器或 public 静态发行器，
  不实现 `Serializable`，`toString()` 不泄露内部键，并限制为签发事务中的同线程单次
  消费。唯一实现桥为 package-private，唯一发行调用位于 identity 首次注册用例。

## 2. 自动门禁

源码和事务测试固定以下边界：

1. 非 identity 模块不能导入 identity internals，也不能重新引入旧 system 包；
2. FK 构造引用不能跨线程、跨 `REQUIRES_NEW`、跨事务完成边界或重复消费；
3. Jackson 不能从引用输出内部键；
4. 首次注册参与失败或本地 JWT 构造失败时，身份创建整体回滚；
5. `code2session` 调用时没有活动数据库事务；
6. 未知角色的真实签名 JWT 返回 `401`，拒绝路径和下游异常路径均清空上下文。

## 3. 复验证据

使用 Java 21 执行：

```powershell
mvn.cmd test
mvn.cmd install -DskipTests
git diff --check
```

结果：

- reactor：根项目加 10 个子模块，共 11 个 reactor project，全部成功；
- Surefire：22 份报告、77 项测试，`failures=0`、`errors=0`、`skipped=0`；
- 制品安装：11 个 reactor project 全部成功；
- diff 检查：无空白错误，仅工作区既有 LF/CRLF 提示。

## 4. 迁移期边界

F-02 只建立身份边界并保持旧行为，不等于 V-01/V-02 已完成。F-03/F-06 前，余额仍内嵌
在 legacy `sys_user`，funds 参与者只消费强类型引用并证明同事务扩展点；目标独立钱包、
三类目标会话、手机号业务闭环和新数据库切换仍由后续任务完成。
