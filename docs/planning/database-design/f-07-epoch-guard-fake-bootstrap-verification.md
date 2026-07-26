# F-07｜epoch guard 与 Fake bootstrap 验证矩阵

> 验证日期：2026-07-26
> 状态：实现、自动验收与 P1 复审补强均通过，任务为 `done`
> 目标运行版本：Java 21、MySQL 8.4.x、目标迁移 V1～V10

## 1. 验证结论

F-07 已建立“迁移作业”和“运行应用”的硬边界：

- 运行制品不包含 Flyway 运行库，也不包含目标或旧纪元迁移脚本；
- 目标 `db/p0-migration` V1～V10 只供独立 Flyway Maven 作业从文件系统读取；
- 运行应用关闭 SQL init 和自动建库；
- `ecobin_app` 启动时只读校验 MySQL 8.4、固定 V1 marker 和完整 V1～V10；
- 同一判定加入 readiness；
- 默认只装配 Fake OneNet、COS、微信，不装配真实网络客户端；
- Fake 模式阻断真实设备/渠道入站，并拒绝任何真实渠道凭证混入；
- 启动不创建业务实例，也不修改 schema 或 Flyway history。

这只证明目标空业务库的安全 bootstrap，不表示后续纵向业务已经适配 83 张目标表，
也不包含 F-12 正式试点 seed。

## 2. 固定 epoch 身份

运行 guard 的不可配置常量为：

| 项目 | 固定值 |
|---|---|
| 数据库 | MySQL 8.4.x |
| 运行身份 | `ecobin_app` |
| V1 version | `1` |
| V1 description | `p0 epoch and iam core` |
| V1 script | `V1__p0_epoch_and_iam_core.sql` |
| V1 checksum | `229072802` |
| 最低完整版本 | V10 |

Guard 要求 V1～V10 每个版本恰有一条成功记录，并拒绝任一版本化失败记录。V1 的
description、script 或 checksum 任一漂移都视为错误纪元。

实现位置：

- `ecobin-bootstrap/.../database/epoch/P0DatabaseEpochPolicy.java`；
- `DatabaseEpochVerifier.java`；
- `DatabaseEpochGuard.java`；
- `DatabaseEpochGuardProperties.java`。

## 3. 启动拒绝矩阵

[`verify-f07-bootstrap.ps1`](../../../tools/database/verify-f07-bootstrap.ps1)
在随机命名的临时 MySQL 容器内构造以下数据库，并使用实际打包 JAR 启动：

| 数据库状态 | 期望 | 结果 |
|---|---|---|
| 正确 V1～V10 | readiness `UP` | 通过 |
| schema 存在但无 Flyway history | 拒绝启动 | 通过 |
| 旧 V1 marker + V1～V14 history | 拒绝启动 | 通过 |
| 目标 V1 checksum 错误 | 拒绝启动 | 通过 |
| V5 `success=0` | 拒绝启动 | 通过 |
| 正确 V1～V9、缺 V10 | 拒绝启动 | 通过 |
| 数据库不存在 | 拒绝且不得自动建库 | 通过 |
| 过度授权运行账号 + 外部开启 Flyway + 空库 | 拒绝且 schema 零变更 | 通过 |

所有存在的测试 schema 在应用启动前后分别计算 DDL dump SHA-256 和 Flyway history
SHA-256；正向与负向启动后的指纹全部不变。

## 4. 正向空业务库

真实 Flyway 11.14.1 使用一次性 `ecobin_schema_owner` 安装目标迁移。迁移后：

| 检查 | 结果 |
|---|---:|
| MySQL | 8.4.10 |
| 领域表 | 83 |
| Flyway history 表 | 1 |
| V1～V10 history | 10 |
| 权限参考数据 | 71 行 |
| 其他业务实例数据 | 0 行 |
| readiness | `UP` |

迁移完成后删除 schema owner 账号，再以 `ecobin_app@%` 启动 JAR。运行身份只授予
本次 bootstrap 所需的读取权限；`CREATE TABLE` 和删除权限目录事实均被 MySQL 拒绝。
`ecobin_trigger_definer` 保留为锁定账号，任何 owner/definer 密码都未进入应用参数。

MySQL 开启 binary log 时，V9 固定 definer 触发器还要求环境显式启用
`log_bin_trust_function_creators=ON`。迁移 owner 仅有 `SET_ANY_DEFINER`，不授予
`SUPER`；该要求已同步给 H-02 权限矩阵。

## 5. Fake 外联边界

`ecobin.external.mode` 默认 `fake`。此模式：

- `DeviceCommandGateway` 为无网络 Fake，只记录被阻断的物理动作；
- COS 凭证只返回保留域名 `https://cos.invalid`；
- 微信适配器只接受 `fake:` 前缀 code，生成确定性 fake openid；
- `/api/iot/**` 及预留 OneNet/微信/资金通知入口返回 HTTP 503；
- servlet context path 非空时仍按应用内路径阻断上述入口；
- `OneNetClient`、`OneNetMqConsumer`、`OneNetEventDispatcher`、
  `CosTokenClient`、`WechatMiniappClient` 和 `RestTemplate` 均不装配。

以下错误配置均在应用承接入口前失败：

- Fake 模式开启 OneNet 订阅；
- Fake 模式注入任一 OneNet、COS 或微信真实配置；
- 非测试运行关闭 Fake 入站闩锁；
- 打包 JAR 仅通过激活 `test` profile 伪造入站 bypass；
- 打包 JAR 以 MySQL 伪造 epoch test bypass；
- Real 模式缺少任一当前声明的真实渠道配置。

旧 H2 测试旁路同时受 `test` profile、测试 classpath 和内存 H2 限制，不是可部署开关。

## 6. 自动化证据

Java 21 测试覆盖：

- `P0DatabaseEpochPolicyTest`：正确 marker、空库、旧/错 V1、checksum 漂移、
  失败/低/缺失/重复版本、错误身份和错误数据库版本；
- `ExternalBoundaryPolicyTest`：Fake/Real 配置状态机；
- `FakeExternalIngressBlockFilterTest`：根路径/context path 下真实入口 503 与普通内部 API 放行；
- `FakeExternalAdapterIsolationTest`：Spring 装配中没有真实客户端；
- `RuntimeSafetyConfigurationTest`：运行 classpath 没有 Flyway 引擎、Boot Flyway 自动配置模块
  或迁移脚本，SQL init/自动建库关闭。

F-07 独立分支的 Surefire 报告共 28 份，合计 103 个测试，failure、error、skipped
均为 0。合入包含 F-08 的 `database-refactor` 后再次执行 Java 21
`mvn clean test`，最终报告共 30 份、112 个测试，failure 和 error 均为 0；
其中 5 个 skipped 是按设计交由独立 MySQL 8.4.10 脚本执行的 F-08 数据库验收。

最终合并树生产 JAR SHA-256 为：

```text
e317da4211cae9c2b93435bcb578ade73627a32004b5ee096edabd605e82d95a
```

真实数据库验收命令：

```powershell
mvn.cmd install -DskipTests
.\tools\database\verify-f07-bootstrap.ps1
```

脚本固定并检查 MySQL 镜像 ID：

```text
sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6
```

脚本使用随机秘密、随机容器名和临时日志目录，`finally` 只清理自己创建的进程、容器和
日志，不访问现有数据库或数据卷。输出只包含版本、计数、布尔结论和制品 SHA-256。
脚本还直接检查生产 JAR 中目标迁移、旧迁移和 Flyway 运行库计数均为 0；随后给专用
空库上的 `ecobin_app` 故意授予完整 schema 权限与 `SET_ANY_DEFINER`，传入
`--spring.flyway.enabled=true`，确认应用仍因缺少 epoch history 拒绝且数据库指纹不变。
同一生产 JAR 以 `/ctx` 启动时，`/ctx/api/iot/**` 仍返回 503。

## 7. 仍待后续任务完成

- H-02：真实实例、账号、列级 DML GRANT、脱敏 `SHOW GRANTS` 和凭证保管；
- F-08：中心 inbox 与可靠任务已实施并处于 `in-review`，仍需完成复审收口；
- V-01～V-11：目标表上的纵向业务；
- F-12：完整试点 seed；
- H-06：真实入口闩锁、成对切换与回退签署。

在这些任务完成前，F-07 的 `UP` 只表示“正确目标纪元上的安全 Fake bootstrap”，
不能作为 M0、真实设备或真实资金可用的证据。
