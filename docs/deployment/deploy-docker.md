# EcoBin 后端 Docker 部署说明

> 2026-08 更新：本文保留 F-07/V10 的历史安全边界说明；当前目标数据库和应用门已推进
> 到 V31。服务器实际部署请以
> [`target-single-host-deployment.md`](target-single-host-deployment.md) 为准，不再照本文
> 的 V10 数量或旧启动示例执行。

> 当前适用阶段：F-07 目标 V10 空业务库 Fake bootstrap。
>
> 本文替代 F-07 之前“应用用 root 启动并自动建库/迁移”的开发说明。当前运行制品不含
> Flyway 运行库或迁移脚本，不能 baseline 或 migrate；数据库未预装到正确 V10、运行身份不是
> `ecobin_app`、或 Fake 环境混入真实渠道配置时，应用必须拒绝启动。

## 1. 先理解当前部署边界

数据库和应用分成两个生命周期：

1. 环境供应者创建 MySQL 8.4 实例、目标数据库及独立身份；
2. 一次性迁移作业用 `ecobin_schema_owner` 执行目标 V1～V10；
3. 迁移完成后销毁 owner 凭证上下文；
4. 后端容器只拿 `ecobin_app`，启动时只读检查 epoch；
5. F-07 默认以 Fake OneNet/COS/微信运行，不接真实入口、不做真实出站。

这意味着单独执行 `docker compose up` 不会替你迁移数据库或创建最小授权。H-02
尚未执行时，backend 未就绪是正确的安全行为。

## 2. 固定版本

- Java 21；
- Spring Boot 4.0.6；
- MySQL 8.4.x；
- 目标迁移目录：
  `ecobin-bootstrap/src/main/resources/db/p0-migration`；
- 运行制品不包含 Flyway 运行库，也不包含目标 V1～V10 或旧 V1～V14 迁移脚本；
  目标迁移源码只供独立 Maven 作业从文件系统读取。

自动验收锁定的本地 MySQL 镜像 ID 和实际版本见
[F-07 验证矩阵](../planning/database-design/f-07-epoch-guard-fake-bootstrap-verification.md)。

## 3. 数据库身份

| 身份 | 用途 | 禁止进入 |
|---|---|---|
| 实例初始化管理员 | 建库、创建/锁定账号、初始授权 | 应用配置、日常运行 |
| `ecobin_schema_owner` | 一次性执行 V1～V10 和维护 Flyway history | 后端容器、长期会话 |
| `ecobin_trigger_definer` | 固定触发器 definer；账号锁定 | 交互登录、应用配置 |
| `ecobin_app` | 后端唯一日常身份；按 H-02 矩阵授予 DML，读取 Flyway history | DDL、GRANT、TRIGGER、owner 权限 |

V9 使用固定 trigger definer。开启 binary log 的 MySQL 8.4 必须由环境供应者处理
trigger creator 策略；F-07 自动验收使用
`log_bin_trust_function_creators=ON`，schema owner 只额外拥有
`SET_ANY_DEFINER`，不授予 `SUPER`。

真实账号、密码和 GRANT 由 H-02 供应，不写入仓库或普通日志。

## 4. 构建

多模块修改后先安装 reactor，避免 bootstrap 读取本地仓库中的旧模块 JAR：

```powershell
$env:JAVA_HOME = '你的 JDK 21 路径'
mvn.cmd install -DskipTests
```

生成的运行制品位于：

```text
ecobin-bootstrap/target/ecobin-bootstrap-0.0.1-SNAPSHOT.jar
```

Dockerfile 使用 Java 21 多阶段构建，运行阶段切换到非 root UID `10001`。

## 5. 迁移不是应用启动步骤

目标数据库必须先由受控迁移作业创建并升级。Flyway Maven 插件只扫描
`db/p0-migration`，没有内置 URL、用户名或密码；连接信息必须由迁移作业的秘密注入。

迁移作业完成后至少核对：

- `flyway_schema_history` 有且仅有成功的 V1～V10；
- V1 description 为 `p0 epoch and iam core`；
- V1 script 为 `V1__p0_epoch_and_iam_core.sql`；
- V1 checksum 为 `229072802`；
- 共有 83 张领域表和 71 行权限参考数据；
- 除权限目录外没有租户、机构、账号、设备、袋、钱包或订单实例数据；
- schema owner 凭证已从运行环境移除。

不要对失败半库执行自动 `repair` 后继续。首次空库迁移失败时应保存证据、丢弃半库并
重新从空目标库安装。

## 6. Fake backend 配置

复制 `tools/development/application-local-secrets.example.yml` 到
`.ecobin/application-local-secrets.yml` 后填写本机配置。`.ecobin/` 已被 Git
忽略。已有旧 `.env` 时可以执行一次：

```powershell
.\tools\development\migrate-dotenv-to-local-yaml.ps1
```

根 Compose 通过包装脚本读取同一份 YAML。至少填写：

```yaml
localMysqlDatabase: 'ecobin'
localMysqlRootPassword: '由本机环境供应者保管的随机值'
dbUrl: 'jdbc:mysql://127.0.0.1:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=Asia/Shanghai'
dbUsername: 'ecobin_app'
dbPassword: '独立随机运行密码'
jwtSecret: '至少 32 字节的随机值'
appAesKey: '有效 AES 密钥'
```

`local-fake` 会在最终属性层屏蔽 YAML 中已有的 OneNet、COS 和微信值，因此切换
Fake/Real 时不用清空或恢复字段。生产不使用该文件，仍通过容器 secret 注入。

## 7. 启动与探针

数据库已经迁移、`ecobin_app` 已按 H-02 授权后，才启动：

```powershell
.\tools\development\run-local-stack.ps1
.\tools\development\run-local-stack.ps1 -Action Logs
```

只暴露 health endpoint。就绪探针：

```text
GET /actuator/health/readiness
```

返回 HTTP 200 和 `{"status":"UP"}` 才可承接内部 Fake 流量。readiness 同时包含：

- Spring readiness state；
- `dbEpoch`；
- `externalBoundary`。

Fake 环境访问 `/api/iot/**` 等真实设备/渠道入站路径会返回 HTTP 503。真实 OneNet、
COS、微信客户端在 Fake 模式根本不装配。

## 8. 自动验收

开发机安装 Docker、Java 21 和 Maven 后执行：

```powershell
mvn.cmd install -DskipTests
.\tools\database\verify-f07-bootstrap.ps1
```

脚本会创建随机命名的临时 MySQL 8.4 容器，并验证：

- 正确 V10 空业务库可就绪；
- 空 schema、旧 V1～V14、错误 V1、失败迁移、V9 和不存在的数据库全部拒绝；
- 应用启动前后 schema 与 Flyway history 指纹不变；
- 即使给运行账号过度授予迁移权限并传入 `spring.flyway.enabled=true`，空库也保持
  零表且启动被 epoch guard 拒绝；
- 没有自动 seed；
- `ecobin_app` 不能 DDL 或删除事实；
- Fake 入站（含非空 servlet context path）、真实凭证混入以及打包制品伪造 test
  bypass 全部失败。

脚本结束会删除自己创建的临时容器和临时日志，不接触现有数据库或数据卷。

## 9. 真实渠道与正式部署

`externalMode=real` 不属于 F-07 的 Fake 验收。切换真实 OneNet、COS、微信和资金渠道前，
必须完成对应纵向任务、H-02/H-04/H-05 供应以及 H-06 成对切换审核。不要为了让服务
“先起来”而在 Fake 模式塞入真实凭证或关闭入站闩锁。

旧应用、旧数据库和旧 V1～V14 只按 H-01 恢复单元成对保留；不能让新应用连接旧库，
也不能把旧迁移重新放回新运行制品。
