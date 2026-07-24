# H-01｜旧栈恢复单元与所有权证据

> 状态：**已完成**
>
> 执行日期：2026-07-24（Asia/Shanghai）
>
> 授权：项目负责人已明确授权执行 H-01，并授权在其完成后实施 F-01。

本文只保存可公开复核的元数据、摘要、数量和恢复步骤，不保存密码、`.env`、设备 Key、
微信/COS/OneNet 密钥、私钥或真实用户字段。包含旧业务数据的逻辑备份只保存在版本库外的
本机恢复包中，不得提交 Git 或发送给无权限人员。

## 1. 旧代码与可运行制品

| 项目 | 证据 |
|---|---|
| Git 分支 | `database-refactor` |
| 旧代码 commit | `1c860fee034457a183c72c459c8d06191102b17e` |
| commit 时间 | `2026-07-17T19:15:29+08:00` |
| commit 主题 | `feat(miniprogram): 接入投递开门与自动登录` |
| Java | `21.0.10` |
| Maven | `3.9.11` |
| 构建 | `mvn.cmd package -DskipTests`，成功 |
| 旧行为基线 | `mvn.cmd test`，59 tests、0 failures、0 errors、0 skipped |
| 可运行制品 | `ecobin-bootstrap-0.0.1-SNAPSHOT.jar` |
| 制品 SHA-256 | `8C18F724A0CF84AD44059BD2DFF5DA3EEAF43C953E7215F7D98FFBA5053FEFBD` |

固化制品前，生产源码、POM、前端和硬件目录相对上述 commit 没有已跟踪差异；当时已有的
AGENTS、CLAUDE 和规划文档改动不进入旧可运行制品，也不影响其代码身份。

## 2. 旧数据库基线

| 项目 | 值 |
|---|---|
| 数据库产品 | MySQL `8.0.43` |
| 旧 schema | `ecobin` |
| 表数 | 14 |
| Flyway 成功记录 | 14 |
| 迁移范围 | V1～V14 |

### 2.1 关键表精确行数

| 表 | 行数 |
|---|---:|
| `biz_clean_bag` | 0 |
| `biz_clean_order` | 0 |
| `biz_delivery_order` | 5 |
| `biz_device` | 2 |
| `biz_device_session` | 2 |
| `biz_device_status` | 0 |
| `biz_door` | 3 |
| `biz_door_status` | 0 |
| `biz_weight_record` | 0 |
| `biz_withdraw_order` | 0 |
| `flyway_schema_history` | 14 |
| `sys_admin` | 3 |
| `sys_tenant` | 2 |
| `sys_user` | 1 |

### 2.2 Flyway history 与迁移摘要

安装顺序以旧库 `flyway_schema_history.installed_rank` 为准；旧库实际先安装 V11、后补装
V10，该历史事实在恢复包中原样保留。

| rank | version | description | Flyway checksum | SQL SHA-256 |
|---:|---:|---|---:|---|
| 1 | 1 | init schema | 1043660541 | `B207E3DDE4EE747A53999F3329AFE0266F2E783590AB18D547F76A6E5CA6CB88` |
| 2 | 2 | add wechat login | 445556341 | `E6F6E293E366FB9083271EB887316DB84162243DD0D816F6B92F825FA4C1EA1E` |
| 3 | 3 | add role tables | -1673118330 | `F8FD7B70758ACBB2A5AF9E8EEB47F8704BF82674021D3E2157501B152F55747F` |
| 4 | 4 | add door unique | -1259362270 | `3E9E998DF7CAEA1B91ADC4DDF7A75A0B107DE8AEBC8541CD8F03B107ADE0D830` |
| 5 | 5 | add door fk | 1642752041 | `EC9688AABF2A4FBC5908A00DB9995D8D072F66D2226B50E4DFB36159F4F820A3` |
| 6 | 6 | fix openid tenant unique | -1084563660 | `3E9F21704C1DA9392CBCEBA4F7DF19DDD10EBEB892E3E4562C3E0ABFB26AC00A` |
| 7 | 7 | add delivery two phase | -740892999 | `CEE55DE6FE076D0292A43EE45B88B76E6242B60F203FAE89C8482CC539B3EC16` |
| 8 | 8 | add wallet withdraw | 1179689677 | `CF4E4A289C236EE09B6C44AD1B6F9EE4B5B58A1BF6D21FED035766410CEE1B67` |
| 9 | 9 | add clean bag and refactor | 425754128 | `75C5FDEB9D8BF37E3841B2C0ECDCE041EB5E4DF3DB5B8241DA9673E9E21FCBA8` |
| 10 | 11 | add order photos | 782612714 | `CFC83899990B673BE6F2DEAD58A43E1DFA82693C2F492AEE6FBDBD50F0D00916` |
| 11 | 10 | refactor device door status | 309562777 | `1F4A58E5BBD8B900C5DE14701F58636BC65C61E1F9C652EA1753B7D25527DB14` |
| 12 | 12 | clean order new bag | -1293822861 | `84A20833D741AC842CBE5E82B0F900BACD7AE2E41234E8A66EC1FBE74B1CE2FB` |
| 13 | 13 | add device session | 446746393 | `7B608DCAA64A5FAF5969C57BE472545CC264CD97B507843CBEE17C736ABD9C06` |
| 14 | 14 | add delivery audit | -1093798101 | `51648C2613CF4BA4D4D3677334E4F6C371005496BA15A6E39B4A78C458F157F0` |

## 3. 版本库外恢复包

恢复包位于：

```text
C:\tmp\ecobin-h01-20260724-1c860fee-0948
```

其中包含：

- 旧可运行 JAR；
- `.env.example` 脱密模板；
- `docker-compose.yml`；
- V1～V14 SQL 副本；
- 含建库语句的完整逻辑 dump；
- 可定向导入隔离 schema 的 schema/data dump。

| 文件 | SHA-256 |
|---|---|
| `ecobin-v1-v14.sql` | `D107F10F58BBE6FA03A4B3F843EFA8521E1019E587721640F25A8AFF0B2E4E32` |
| `ecobin-schema-data.sql` | `DD6B7D10AB05CB8E4D3A2944F08127EAEB2D713F64236156C0B814C8E31B2DD6` |
| `ecobin-bootstrap-0.0.1-SNAPSHOT.jar` | `8C18F724A0CF84AD44059BD2DFF5DA3EEAF43C953E7215F7D98FFBA5053FEFBD` |

## 4. 隔离恢复验证

| 项目 | 结果 |
|---|---|
| 隔离恢复 schema | `ecobin_h01_restore_20260724_0948` |
| 恢复后表数 | 14 |
| 恢复后成功 Flyway 记录 | 14 |
| 原库/恢复库逐表行数比较 | 14/14 全部一致 |
| 旧 JAR 健康检查 | `GET /actuator/health` 返回 `UP` |
| 临时端口 | `18081` |
| 临时进程 | 验证后已停止 |

启动验证时：

- 数据源只指向隔离恢复 schema；
- Flyway 被禁用，避免验证过程修改恢复库；
- OneNet 北向订阅明确禁用；
- OneNet、微信、COS 的真实凭证均未注入；
- 未执行登录、设备下行、照片授权、支付或转账请求。

因此该证据只证明“旧应用能够装配并读取恢复库”，不会触发真实设备和资金渠道。

## 5. 旧栈入口与所有权清单

H-01 固化的是本次执行时实际可运行的本地旧栈。项目负责人没有把已部署的生产主机、
生产数据库账号、独立宿主卷、生产域名证书或独立 worker 指定为该旧栈的现存资源；
因此这些项目在当前恢复单元中为“不存在/不适用”，不是遗漏的回退依赖。当前外部平台
入口的操作责任统一归项目负责人，凭证只存在于其原受控配置位置，不复制进恢复包。

| 资源/入口 | 旧实现所有者 | 配置或运行位置 | 恢复/切换注意事项 |
|---|---|---|---|
| 后端进程 | `ecobin-bootstrap` | 根 `Dockerfile`、`docker-compose.yml`、`application.yml` | 应用和旧库必须成对恢复 |
| MySQL | 旧 `ecobin` schema | 本机 `MySQL80`；Compose 服务 `mysql` | 不与目标 V1～V10 共用迁移纪元 |
| 数据卷 | 部署操作者 | Compose 卷 `ecobin-mysql-data` | 切换前确认精确宿主卷/备份位置 |
| Web/Nginx | `frontend/web` | `frontend/web/Dockerfile`、`nginx.conf` | API 反代必须与后端成对切换 |
| 小程序 | `frontend/miniprogram`；操作人：项目负责人 | 微信开发者工具与小程序后台 | AppID/版本发布由项目负责人掌握 |
| 管理/用户 HTTP | legacy system/business/device Controller | `/api/system/**`、`/api/device/**`、`/api/business/**`、`/api/app/**` | 旧路由行为由 59 个测试形成搬迁基线 |
| OneNet 北向入口 | 旧 framework `OneNetMqConsumer`；操作人：项目负责人 | `onenet.subscription.*` | 停旧栈前必须停止消费并确认无并行消费者 |
| OneNet 下行 | 旧 framework `OneNetClient`；操作人：项目负责人 | `onenet.*` | 切换时禁止旧新应用同时控制设备 |
| COS STS | 旧 framework `CosTokenClient`；操作人：项目负责人 | `cos.*`、`/api/iot/cos/**` | 永久密钥不进入恢复证据 |
| 微信登录 | 旧 framework `WechatMiniappClient`；操作人：项目负责人 | `wechat.miniapp.*`、`/api/system/auth/**` | 验证恢复时不调用真实 code2session |
| 后台 worker | OneNet `SmartLifecycle` 消费者 | 后端进程内 | 当前没有其他 `@Scheduled`/`@Async` worker |
| 设备侧 | `hardware/` 香橙派程序 | OneNet、COS、UART 本地配置 | H-01 不修改设备或 MCU 固件 |

若 H-06 前新供应生产主机、账号、卷、域名证书、OneNet 消费组或增加小程序/商户平台
操作人，部署操作者必须把这些**新增目标环境资源**补入受控运维台账；它们不反向成为
H-01 本地旧栈恢复单元的未完成项，环境私密信息也不进入本仓库。

## 6. 恢复步骤

1. 核对旧 commit、JAR 和 dump SHA-256。
2. 在隔离 MySQL 中创建空 schema，不复用目标新库名称。
3. 使用 `ecobin-schema-data.sql` 恢复，并核对 14 张表、14 条成功 Flyway 记录和逐表行数。
4. 复制 `.env.example` 到隔离环境的秘密存储位置，填入隔离数据库凭证；不要提交 `.env`。
5. 明确关闭 OneNet 消费并不注入 OneNet、COS、微信真实凭证。
6. 使用 Java 21 启动固化 JAR，检查 `/actuator/health`。
7. 验证后停止临时进程；保留恢复 schema 和本文件中的非敏感证据供回退演练复核。
