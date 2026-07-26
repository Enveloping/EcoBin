# F-08｜inbox 与可靠任务 tracer 实施证据

> 验证日期：2026-07-26
>
> 任务：[F-08 inbox 与可靠任务 tracer](../planning/tasks/p0-controlled-loop/f-08-inbox-reliable-task-tracer.md)

## 1. 实施结果

- operations 新增可信收件公开端口。F-08 Fake 只接受已经完成认证、解密和白名单规范化的
  平台作用域消息；真实 OneNet/微信作用域解析继续由后续纵向任务负责，不能信任外部自报
  `tenant_id` 或 `organization_id`。
- 规范摘要同时覆盖消息类型、Schema 版本和按属性名排序的规范 JSON。解析阶段强制浮点
  使用 `BigDecimal`、整数使用 `BigInteger`，避免高精度、极端指数或 64 位边界数字先
  折叠为 `double/long`；数字 `1`、`1.0` 和 `1e0` 等价，原始传输正文及其摘要只在
  首次收件保存，重复投递不能覆盖原证据。
- 收件以独立 `READ COMMITTED` 短事务建立 inbox 和唯一
  `PROCESS_INBOX:<inboxUid>` 任务。方法只有在该短事务提交后才返回可 ACK receipt；
  调用方已有事务即使随后回滚，也不能撤销已经允许 ACK 的收件事实。
- 同稳定外部 ID、同语义摘要复用并唤醒原任务；同 ID、异摘要只追加或聚合
  `IDENTITY_CONTENT_CONFLICT` 隔离事实，不覆盖原 inbox、任务或正文。
- framework 新增 inbox 完成和可靠任务唤醒两个稳定技术端口。完成端口必须加入调用方
  已有业务事务，业务事实、attempt 成功、inbox `PROCESSED` 和 task `DONE` 共同提交。
- runner 先获取通道级、进程内共享的 `maximumInFlight` 许可，再使用数据库 UTC 时间、
  `FOR UPDATE SKIP LOCKED` 和独立 `REQUIRES_NEW` 短事务即时领取一个任务；每轮仍受
  `batchSize` 限制，但不再预领一批任务放在内存中等待租约消耗。领取提交后立即执行
  handler。operations 尾部固定按 `inbox → task → attempt` 加锁，不让 task 行锁跨
  领域处理或外部调用。
- 过期租约复用同一 task，新增 attempt 并标记旧 attempt `reclaimed_at`。旧 worker
  迟到时可保存自己的真实技术结果，但不能完成 inbox、覆盖新租约或创建第二业务意图；
  它只递增原任务 `wakeVersion` 触发重新核对。
- 业务处理失败先整体回滚，再由独立短事务记录脱敏的 `RETRYABLE_FAILURE` 和指数退避；
  自动重试耗尽只把原任务置为 `BLOCKED/AUTO_RETRY_EXHAUSTED`，inbox 保持 `RECEIVED`。
- `iot-device`、`funds-wechat` 和 `maintenance` 三类执行通道具有独立的 worker、批次、
  租约、外部超时、最大在途、队列容量、轮询、退避和自动尝试上限；同一 Spring Boot
  实例内所有并发 runner 共享每个通道的最大在途计数。V7 持久化 lane 仍只有
  `DEVICE/FUNDS`；maintenance 使用独立领取策略，不伪造第三种持久化枚举。

## 2. 真实 MySQL 8.4 验收

运行：

```powershell
$env:JAVA_HOME = 'C:\D\002-Tools\004-DevTool\jdk-21.0.10'
.\tools\database\verify-f08-reliable-tracer.ps1
```

验证器固定 F-06 已审镜像 ID，创建随机密码、随机宿主端口的临时 MySQL，等待 PID 1
切换为正式 `mysqld` 后安装 V1～V10。测试结束或失败时均清除环境变量和临时容器。

| 项目 | 结果 |
|---|---|
| Java | `21.0.10`，编译目标 `release 21` |
| MySQL | `8.4.10` |
| 镜像 ID | `sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |
| 迁移 | V1～V10，共 10 个文件 |
| 专项测试 | 5 tests，0 failures，0 errors，0 skipped |
| 结果 | 全部通过 |

专项测试覆盖：

1. task 插入故障时 inbox 同事务回滚，ACK 前重投可重新建立完整事实；
2. 外层事务在收件返回后强制回滚，已提交收件仍存在，ACK 后重投命中原 inbox/task；
3. JSON 属性顺序和等价数字变化仍命中同摘要，保留首次原始正文并唤醒原任务；
4. 同稳定 ID 异摘要进入隔离，原 inbox/task 不被覆盖；
5. 两个物理连接并发 `SKIP LOCKED` 领取不同 task，DEVICE/FUNDS/maintenance 互不串线；
6. 领取事务提交后可立即 `NOWAIT` 锁 task，证明行锁未跨业务处理持有；
7. 租约过期由另一 worker 接管，旧 attempt 留下 `reclaimed_at`；
8. 旧 worker 迟到完成不覆盖新租约、不误标 inbox，只递增原任务唤醒代际；
9. 业务事务在提交前注入失败时，业务副作用、成功 attempt、inbox/task 完成全部回滚，
   随后由新 attempt 复用原意图完成；
10. 迟到领域事实通过稳定唤醒端口复用原 task，幂等复检不产生第二业务副作用；
11. 队列总处理时间超过单次租约时，每个任务仍在获得执行容量后才领取，开始处理时租约
    未过期；
12. 两个并发 runner 共享通道 `maximumInFlight`，没有容量的一方不预领数据库任务。

## 3. Java 回归与制品

使用 Java 21 运行：

```powershell
.\mvnw.cmd clean test
.\mvnw.cmd install -DskipTests
git diff --check
```

结果：

- 根项目加九个目标模块，共 10 个 reactor project，测试和制品安装全部成功；
- Surefire 共 25 份报告、91 项测试，`failures=0`、`errors=0`；普通回归中 5 项
  MySQL 专项按环境门禁跳过，已由上一节脚本在 MySQL 8.4 中以 `skipped=0` 独立执行；
- `CanonicalJsonTest` 的 4 项精确数值回归覆盖高精度小数、指数与普通写法等价、
  超出 `long` 的相邻整数，以及 `1e-10000/1e10000` 极端指数不下溢或上溢；
- `ModuleBoundaryTest` 继续通过，新 framework 类型只使用 JDK API，operations
  跨模块依赖仍只经过公开 API；
- `git diff --check` 通过。

## 4. 范围边界

F-08 只交付 Fake 平台作用域 tracer、可靠执行核心和后续模块可用的稳定技术端口，不
代表真实 OneNet/微信入站、投递/清运/配置/充值/提现 handler、外部调用客户端、调度线程、
管理页面、告警或对账业务已经完成。task `DONE` 也只表示本 task 的处理条件成立，不能
替代订单、设备、支付或提现的权威业务终态。
