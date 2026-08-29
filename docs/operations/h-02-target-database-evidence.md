# H-02｜目标数据库供应证据

> 验证日期：2026-07-27
>
> 操作人：`enveloping`
>
> 当前结论：本地开发演练和服务器整改阶段 0～3 技术门均已完成；服务器目标
> MySQL 8.4.10、五类身份、最小权限、公网隔离、加密备份和一次性隔离恢复均通过。
> 项目负责人已接受当前前期受控试验把操作机 ACL 受限 `.ecobin` 作为长期凭证
> 原件位置，加密密码库和异机密码库密文副本延期到下一版本；主审无 P0/P1 问题，
> H-02 验收为 `done`。
>
> 2026-08-29 增量结论：全新腾讯云 CDB 拟生产目标空库已完成 V1～V60、最小权限账号和
> 初始超级管理员供应，旧业务数据未导入。此后现用测试后端及原单机测试库已升级到 V60，
> 但新 CDB 生产入口、旧数据、COS 和真实入口均未切换；详细状态见第 11 节，初次供应快照
> 与同日后续现场状态必须按时间区分。

## 1. 本地开发隔离对象

| 对象 | 实际值 |
|---|---|
| Compose project | `ecobin-h02` |
| 容器 | `ecobin-h02-mysql84` |
| 数据卷 | `ecobin-h02-mysql84-data` |
| 网络 | `ecobin-h02-network` |
| 宿主机绑定 | `127.0.0.1:13306` |
| 目标数据库 | `ecobin` |
| MySQL | `8.4.10` |
| 镜像 digest / ID | `sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |

容器、网络和数据卷均为 H-02 新建对象，没有复用旧栈实例、schema 或数据卷。端口只绑定
本机回环地址；未启动后端、真实外联、OneNet/MQ 消费或可靠任务 worker。

## 2. 数据库配置与迁移

| 检查 | 结果 |
|---|---|
| 全局/会话时区 | `+00:00 / +00:00` |
| 默认事务隔离 | `READ-COMMITTED` |
| SQL mode | `STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION` |
| `log_bin_trust_function_creators` | `1` |
| 字符集/排序规则 | `utf8mb4 / utf8mb4_0900_ai_ci` |
| 成功迁移 | V1～V10 共 `10` 条 |
| V1 marker | `1 / p0 epoch and iam core / V1__p0_epoch_and_iam_core.sql / 229072802 / success` |
| 领域表 | `83` |
| 权限目录 | `71` 行 |
| 其他业务数据 | `0` 行 |

首次容器使用 `internal` Docker 网络时，Docker 没有发布宿主机端口，Flyway 在执行任何
迁移前因连接拒绝停止。诊断确认目标库表数为 0 后，保留独立数据卷和秘密，重建容器/
网络并重新生成一次性 schema owner 密码；随后 V1～V10 一次安装成功。没有执行
Flyway `repair`、旧数据导入或业务 seed。

## 3. 五类身份

| 身份 | 验收结论 |
|---|---|
| 实例初始化/恢复管理员 | 只在容器内部使用 root secret；未进入应用配置 |
| `ecobin_schema_owner` | 完成一次性迁移后 `ACCOUNT LOCK`；临时 Flyway 配置已删除 |
| `ecobin_trigger_definer` | 从创建起锁定；只有 schema `TRIGGER` 和两表所需精确列读取 |
| `ecobin_app` | 当前认证主体为 `ecobin_app@%`；无 DDL/GRANT/TRIGGER/系统库权限 |
| `ecobin_backup` | 83 表及 Flyway history 显式只读；无业务 DML/DDL/TRIGGER/GRANT |

秘密只保存在仓库外 `C:\tmp\ecobin-h02-secrets`。目录 ACL 关闭继承，仅
`Enveloping\24217` 与 `NT AUTHORITY\SYSTEM` 拥有权限。目录内只有 root、app、
backup 三个密码文件和不含密码的 Compose 环境文件；不存在
`flyway-owner.conf`，仓库和普通日志中没有秘密值。

## 4. 运行账号最小权限

H-02 审查补齐 F-04/F-05 的 34 张 P/O 表列清单，与 F-06 合成完整目录：

- 83 张领域表显式 `SELECT`；
- 除 `iam_permission_definition` 外显式 `INSERT`；
- 四张当前槽位表显式 `DELETE`；
- 52 张 P/O 表只对矩阵列授予列级 `UPDATE`；
- A/R 表无 `UPDATE/DELETE`；
- 没有 schema 级 `INSERT/UPDATE/DELETE` 或整表 `UPDATE`。

自动权限探针结果：

- `ecobin_app` 读取 Flyway history 成功；
- 事务内插入租户后回滚成功；
- 52 张 P/O 表逐表执行获准列空集更新成功；
- 同 52 张表逐表更新首个未授权列均失败；
- 四张槽位表逐表空集删除成功；
- DDL、GRANT、DROP TRIGGER、删除权限目录、更新只追加袋事实、修改商户稳定身份、
  读取 `mysql.user` 均失败；
- `ecobin_backup` 读取成功、写入失败，`single-transaction` 数据读取探针通过。

备份读取探针 SHA-256：

```text
84bd9d1f0eaf7b3fe3fbd5bd5258ee7ea46d8d348552f4b3c27bb47beb354fb3
```

## 5. 脱敏证据指纹

原始脱敏证据位于操作机：

```text
C:\tmp\ecobin-h02-evidence\20260726T070417Z
```

| 文件 | 行数 | SHA-256 |
|---|---:|---|
| `summary.json` | — | `e80ba0736187bbe0a36f75f0f294207f6e4a6dc35cdfcddbb33a49a226a6c36b` |
| `grants-initializerAdmin.txt` | 3 | `492dc4e4a0d107a9c5f31272dd778a132e06269e19fa81441ec59f1cb9cf4ee9` |
| `grants-schemaOwner.txt` | 3 | `382f32f67695b20fc7509310f604deadc0908532a467146dbc1e8d4b37aa4094` |
| `grants-triggerDefiner.txt` | 4 | `a58132750f0e712d431df9b67598f1b94de2f5e7d10377522324273476ee929f` |
| `grants-app.txt` | 85 | `83af9cb44355972cbd4ed4edde66311fa712ca33fb6d7e01a069a4504d457aca` |
| `grants-backup.txt` | 85 | `e0ba0df090fff9bbbb05f8f62bd57b115d4c89befdf38235c05db874f3054a8c` |
| `pending-update-tables.txt` | 0 | 空文件，表示列级清单无待补表 |

## 6. 服务器生产验收现状

本地 `C:\tmp\ecobin-h02-secrets` 只保存开发演练密码，不上传服务器，也不作为生产
长期保管位置。生产密码必须重新生成。

服务器实施入口见
[单机试验期生产整改计划](../deployment/single-host-production-remediation-plan.md)。
服务器独立目标容器/卷、生产五类身份、公网 3306/13306 收敛、异机备份读取和隔离
恢复验证已于阶段 3 完成。生产长期凭证原件位于操作机 ACL 受限 `.ecobin`，服务器
只保留 root-only 运行副本；项目负责人已明确接受这一当前试验期例外。加密密码库和
异机密码库密文副本属于下一版本加固，不是 H-02 当前状态门。H-02 完成只解除 H-06
的数据库环境门，不构成 seed、真实入口或 M0 完成。

## 7. 服务器阶段 0/1 记录

证据包 ID：`h02-single-host-phase01-20260726T160519Z`。

2026-07-27，主审 `enveloping` 已接受前期受控试验同主机例外并授权阶段 0/1。阶段 0
已取得以下脱敏事实：

| 项目 | 结果 |
|---|---|
| CVM | `ins-9wo8lkq5`，`ap-shanghai`，Ubuntu 22.04 |
| SSH / sudo | 免密 SSH、非交互 `sudo` 通过 |
| 旧代码 commit | `1f0701e2b5ea86b8b8138abf6667da7f9b59665f` |
| Compose | project `ecobin`；`/opt/ecobin/EcoBin/docker-compose.yml` |
| Web 镜像 ID | `sha256:24c26987dfe4e9ce2df4a7eb4f08272643c6e8158cad80553f2457071dac4cb1` |
| backend 镜像 ID | `sha256:547491fa294e5ca5f22d1ef0de90dc91baeca436aeacd98d6b71eecb80a6d9e4` |
| MySQL 镜像 ID | `sha256:ddce01ed435e81eb1b75674a3f4159f306c21346f1318895ba9f4a0f6b809af7` |
| 旧数据卷 | `ecobin_ecobin-mysql-data`；仍挂载旧 MySQL，未修改 |
| 旧库只读基线 | 14 张表；13 条成功 Flyway history；未执行 DDL/DML |
| H-01 恢复包 | 仓库外可读；JAR、完整 dump、定向 dump 三项 SHA-256 均匹配 |
| 备份加密 | RSA-3072/CMS 公钥链路；服务器无私钥；跨主机往返探针通过 |
| 公钥指纹 | `33:D6:BE:CB:26:7B:FA:2D:3A:F5:C9:CF:5B:25:10:BD:29:1A:E1:61:58:A8:8B:EB:40:B8:2E:A0:68:94:B8:E3` |
| 服务器备份目录 | `/var/backups/ecobin/legacy-final`，`root:root 0700` |
| 公网 IPv4 行为基线 | 22、80、8080、3306 均可建立 TCP 连接；不等价于安全组规则快照 |

项目负责人随后确认 VNC 可用、最大停机 24 小时，并提供云防火墙截图。初始截图显示
共五条、均为全部 IPv4 来源：

| 协议/端口 | 策略 | 阶段 1 目标 |
|---|---|---|
| TCP 8080 | 允许 | 删除 |
| TCP 8642 | 允许 | 删除；目标拓扑不发布独立后端端口 |
| TCP 22 | 允许 | 管理出口后来澄清为动态 IP，试验期保持全部 IPv4 |
| TCP 80 | 允许 | 保留，用于 HTTPS 重定向 |
| ICMP ALL | 允许 | 本阶段不改变 |

截图未显示 3306 或 IPv6 入站规则。阶段 0 据此完成。

## 8. 服务器阶段 1 执行证据

维护窗口从 2026-07-27T02:33:53Z 开始，24 小时复核点为
2026-07-28T02:33:53Z。当前执行结果：

| 项目 | 结果 |
|---|---|
| 旧 Web | 2026-07-27T02:34:23Z～02:34:26Z 停止；exit 0 |
| 旧 backend | 同一窗口停止；Docker 记录 exit 143，停止后无应用活动数据库会话 |
| 最终备份 | `mysqldump → gzip → RSA-3072/CMS` 流式完成；未落明文 SQL |
| 密文 | 6,207 字节，`root:root 0600`；服务器和异机副本 SHA-256 一致 |
| 密文 SHA-256 | `7e1b7c1b5e0d928cd2058a868e099cdd218d8b6d27c737664099731ef3f20017` |
| 恢复读取探针 | 流式解密/解压成功；SQL 28,339 字节 |
| SQL 流 SHA-256 | `6de863c6198e04f2d4917d733df3399f817a2eb385acb6b294a8a4b3ca158286` |
| 旧 MySQL | 2026-07-27T02:36:32Z～02:36:34Z 正常停止；exit 0 |
| 旧数据卷 | `ecobin_ecobin-mysql-data` 仍存在；207,201,847 字节 |
| UFW | active/enabled；默认拒绝入站、允许出站、拒绝 routed |
| UFW 入站 | 公网 IPv4 的 22/80/443；IPv6 的 80/443 |
| SSH 验证 | `/32` 初始配置和改回动态 IPv4 后均通过第二条 SSH 与非交互 `sudo` |
| SSH 有效认证 | `pubkeyauthentication yes`；按负责人决定保留 `passwordauthentication yes` |
| 宿主监听 | `ss -lntp` 只显示 22；80/443/8080/3306/8642 无进程监听 |
| 旧恢复材料 | `.env`、Compose、三个 stopped 容器、旧卷和最终密文均保留 |

管理端公网出口后来澄清为动态 IP。项目负责人决定当前允许所有 IPv4 访问 22，且不
关闭 SSH 密码登录。UFW 在两分钟自动回退保护下删除 `/32` 规则并新增
`0.0.0.0/0 → 22`；2026-07-27T02:56:55Z 第二条 SSH/sudo 通过后取消回退。公网 22
与密码认证并存是已接受的试验期残余风险，不视为最终 SSH 加固完成。

最终云防火墙截图显示四条全部 IPv4 规则：TCP 443、TCP 22、TCP 80、ICMP ALL；
8080/8642 已删除，3306 未配置，截图中没有 IPv6 入站规则。

操作机网络会把 RFC 5737 的 `192.0.2.1`、`198.51.100.1`、`203.0.113.1` 也全部扫描
为开放，证明其通用 TCP/Nmap 结果受透明代理影响，未作为验收。最终使用负责人授权的
独立外部主机 `66.42.51.209` 验证：

| UTC | 目标端口 | 结果 |
|---|---|---|
| 2026-07-27T03:08:08Z | 22 | `OPEN` |
| 同上 | 80 | `CLOSED_OR_FILTERED`，当前无 Web 监听 |
| 同上 | 443 | `CLOSED_OR_FILTERED`，当前无 Web 监听 |
| 同上 | 8080 | `CLOSED_OR_FILTERED` |
| 同上 | 3306 | `CLOSED_OR_FILTERED` |
| 同上 | 8642 | `CLOSED_OR_FILTERED` |

云防火墙、UFW、`ss`、stopped 容器和独立外部探针相互一致，阶段 1 完成。

## 9. 服务器阶段 2 执行证据

项目负责人于 2026-07-27 授权阶段 2，当时指定所有待保管秘密先放入操作机受限
`.ecobin` 目录，并计划采用本地加密密码库加异机密文副本；旧栈保持离线至阶段 3
完成，SSH 密码登录暂时保留。该密码库计划随后由项目负责人延期，见 10.3 节最终
验收决定。

| 项目 | 脱敏结果 |
|---|---|
| 新长期秘密 | MySQL root、应用数据库、备份数据库、JWT、应用 AES 共五项；均为全新随机值，未输出值 |
| 操作机暂存 | 当前用户 ACL 限制的明文导入源；CMS 加密暂存包往返校验通过 |
| CMS 暂存包 SHA-256 | `86811b68475f283b5b470401983c681fc3872fe43b6f5d9f9fd2d7c1ea368622` |
| 旧环境快照 | 只盘点 16 个键名；CMS 密文快照通过内存解密/键名复核 |
| 旧环境快照 SHA-256 | `082ab30b9c3cc0f5c97ee106c55bf24b70963ad2776b300231701113d9af37be` |
| 服务器持久源 | `/etc/ecobin/secrets` 为 `root:root 0700`；五文件均为 `root:root 0600` |
| 后端运行副本 | 仅 `dbPassword`、`jwtSecret`、`appAesKey`；`root:10001 0440` |
| 暂存服务 | `ecobin-stage-runtime-secrets.service` active/exited，结果 success |
| 容器权限探针 | 断网、只读、`10001:10001`；所需三项可读不可写，root/backup/owner 三项不可见 |
| 旧镜像审计 | 镜像层无数据库秘密，但默认用户为空；旧 stopped 容器仍有环境变量秘密，阶段 3 必须整体替换 |
| 新运行配置 | Dockerfile 固定 `10001:10001`；Spring config tree 强制注入 DB/JWT/AES，无内置生产默认值 |
| 本地候选镜像 | `sha256:d0f48d613ef88080ecfb77fca4137ed09e631f63ce7b987e724d17097b128f4c`；`Config.User=10001:10001`，禁止静态秘密环境变量计数 0 |
| 候选镜像探针 | 断网、只读运行；实际 UID/GID `10001:10001`，`/app` 不可写 |
| Swap | 2 GiB `/swapfile` 已启用并写入 `fstab`；`vm.swappiness=10` |
| 时钟 | 既有 `ntpd` active；选中单一 peer，reach `377`，系统报告已同步 |
| 容量检查 | `ecobin-capacity-check.timer` enabled/active，每五分钟；首次 root 20%、可用内存约 2.58 GiB、swap I/O 0 |
| Docker 日志 | 默认 `json-file`、`10m × 3` 已写入并两次 validate；阶段 3 未重启 daemon，目标 MySQL 以 Compose 服务级同值限制立即生效，全局默认待后续获授权的主机重启生效 |
| SSH/sudo | password auth 和全 IPv4 22 按负责人决定保留；`ubuntu` 不在 docker 组 |
| 旧栈 | Web/backend/MySQL 三容器继续 stopped；旧卷未覆盖 |

仓库配置安全测试 `RuntimeSafetyConfigurationTest` 共两项通过；完整 Maven reactor
测试成功，bootstrap 96 项和其他模块测试均无失败，5 项需真实 MySQL 条件的集成测试
按既有门禁跳过。阶段 2 执行结束时，密码库导入和异机密码库副本仍记录为人工待办；
当前 CMS 暂存包没有被冒充为已完成的用户密码库。项目负责人随后在最终验收时明确
延期该计划，因此该历史待办不再阻断 H-02。

## 10. 服务器阶段 3 执行证据

项目负责人 `enveloping` 于 2026-07-27 明确授权阶段 3。执行 run ID 为
`20260727T075001Z`，恢复演练 run ID 为 `20260727T080629Z`。

### 10.1 生产目标环境

| 项目 | 脱敏结果 |
|---|---|
| Compose / 容器 | `ecobin-target` / `ecobin-target-mysql84` |
| 独立卷 / 网络 | `ecobin-target-mysql84-data` / `ecobin-target-db`；网络 `internal=true` |
| 镜像 | `mysql@sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6` |
| Linux amd64 image ID | `sha256:9cffaceb9b62d4280247acdb2324b380d2b36208ae34dfe9f0afb62eeaf70f08` |
| MySQL / 配置 | `8.4.10`；UTC、READ COMMITTED、严格 SQL mode、`utf8mb4_0900_ai_ci` |
| 资源边界 | 内存 1,152 MiB、1.5 CPU、PID 256、`restart=unless-stopped`、日志 `10m × 3` |
| 迁移和数据 | V1～V10 共 10 条；83 张领域表；71 条权限定义；其余业务数据 0 |
| 身份 | 五类身份完成；schema owner 和 trigger definer 均锁定 |
| 最小权限 | 52 张更新表列级矩阵无缺项；应用正负探针与 backup 读取探针通过 |
| 宿主端口 | Compose 无 `ports`；`docker inspect` 为 `{}`；`ss` 的 3306/13306 监听数为 0 |
| 公网复核 | 独立主机 `66.42.51.209` 对 3306、13306 均为 `CLOSED_OR_FILTERED` |
| 边界 | 未执行 seed、旧数据导入、真实入口或应用切换；旧三容器继续 `exited` |

阶段 3 迁移只通过一次性本地 SSH 隧道直接连接目标容器 IP；服务器没有创建临时或最终
宿主机 MySQL 端口映射。目标数据库使用全新生产密码，旧
`ecobin_ecobin-mysql-data` 卷未挂载、未迁移、未修改。

生产 backup 读取探针 SHA-256：

```text
b179a4a6ffd07bf96725213052f86e8d1a457c68122d5acebbf0083e2d7aa250
```

### 10.2 加密备份与隔离恢复

`ecobin_backup` 的数据流按
`mysqldump --single-transaction → gzip -9 → OpenSSL CMS AES-256-CBC` 加密，
明文 SQL 未写磁盘。服务器 root-only 密文与操作机 `.ecobin` 中的异机密文副本大小
均为 2,869 字节，SHA-256 一致：

```text
e0b157d4aad9274fa5b27bdbea000ea013d78bbddc6bf039695aa69bfc9956e9
```

本地用恢复私钥执行 CMS 解密和 gzip 解压，SQL 流 SHA-256 为：

```text
8bf2fe0354bafca88e83e9a5afb1cb5c2b11898e59ef090ba31c4540ae4160f3
```

一次性恢复环境使用完全独立的 `ecobin-h02-restore-mysql84` 容器、
`ecobin-h02-restore-data` 卷和 `ecobin-h02-restore-net` 内部网络。首次导入因备份
同时含有既存 Flyway history，在首条 INSERT 被重复主键安全拒绝；确认没有部分业务
导入后，仅在恢复库清空权限目录和 Flyway history，再以同一内存管道重放。最终验证：

- MySQL `8.4.10`，成功迁移记录 `10`；
- 领域表 `83`，权限目录 `71`，其他业务数据 `0`；
- 恢复后权限目录 dump SHA-256：
  `44b5b012578ad687ae0f988aec16c30d91f9fd897a7db0743cf9c501c8b2b442`；
- 临时恢复容器、卷、网络、远端 Compose 目录和临时上传文件均已删除；
- 生产目标容器、卷、网络和服务器备份均在清理后再次确认保留。

### 10.3 证据归档与最终验收

脱敏证据包同时保存在操作机仓库外和服务器 root-only 目录：

```text
C:\tmp\ecobin-h02-server-evidence\20260727T075001Z
/var/lib/ecobin/evidence/h02/20260727T075001Z
```

服务器目录为 `root:root 0700`，文件为 `0600`。`manifest.sha256` 的 13 个条目均经
`sha256sum -c` 通过。生产加密备份继续保存在
`/var/backups/ecobin/h02/20260727T075001Z/`，不在 Git 中。

阶段 3 技术门至此完成。随后主审再次核对生产实时状态和实现证据：

- 目标库 V1 marker 精确匹配，V1～V10 共 10 条成功记录，83 张领域表、71 条权限
  定义、其他业务数据 0；
- 五类身份、owner/definer 锁定、grants 目录一致，应用账号拥有的角色数为 0；
- 目标 Compose 无发布端口，宿主机 3306/13306 无监听，独立外部主机复核两端口均
  不可连接；
- 操作机 `.ecobin` ACL 关闭继承，仅当前用户与 `SYSTEM` 可访问；仓库秘密值扫描
  无匹配；
- 完整 Java 21 Maven reactor 通过：operations 9 项、integration 7 项、
  bootstrap 96 项；其中按既有门禁跳过 5 项真实 MySQL 条件测试；
- 8 个 Bash 脚本语法、PowerShell 解析、52 张表列级 grant 目录和 Git 差异检查
  均通过。

2026-07-27，项目负责人 `enveloping` 最终决定：本前期受控试验继续把生产长期秘密
明文保存在操作机 ACL 受限 `.ecobin`，明确接受操作机失陷、损坏或丢失带来的保密性
与可恢复性风险；本版本不建立加密密码库，加密密码库和异机密码库密文副本延期到
下一版本加固。该例外不允许把秘密写入 Git、聊天、日志或服务器普通目录。

主审未发现 P0/P1 问题。审查提出的三项非阻断脚本加固由项目负责人决定不在本轮
处理，不影响当前证据结论。H-02 据此转为 `done`。阶段 4 未授权，旧栈继续离线；
F-12 seed、应用部署、旧数据导入和真实入口切换仍须单独授权。

## 11. 腾讯云 CDB 空库供应增量证据（2026-08-29）

本节记录后续把目标数据库迁移到托管 CDB 的现场事实。它不改写第 1～10 节的历史
证据，也不表示当前测试入口已经切换到拟生产目标库。

### 11.1 目标与验收结果

| 项目 | 脱敏结果 |
|---|---|
| 托管实例入口 | `sh-cdb-mbhhxzvu.sql.tencentcdb.com:29616` |
| 数据库版本 | TencentDB `8.4.8-txsql` |
| 拟生产目标库 | `ecobin` |
| 资格验证库 | `ecobin_qualification_20260829`，仍保留 |
| 迁移来源 | commit `7f908d72758a8bfdb0b27a8d02d3d5fccc01803a` 的目标迁移目录 |
| 目标库迁移 | V1～V60 共 `60` 条成功记录 |
| 领域对象 | `119` 张领域表、`76` 条有效权限定义、`5` 个触发器 |
| 初始数据 | 唯一超级管理员 `1` 条、引导审计 `1` 条；迁移内置策略 `1` 条和端口槽位 `4` 条 |
| 非预期业务数据 | `0`；未导入旧租户、机构、设备、袋码、订单、钱包或资金数据 |

资格库和拟生产目标库均以空库新纪元执行 V1～V60。目标库只创建平台登录所需的唯一初始
超级管理员，登录名为 `enveloping`；密码来自服务器现有受限秘密文件，并以 BCrypt
校验确认与当前管理员口令一致。秘密值和密码哈希均未写入仓库或普通输出。

项目负责人明确决定不迁移旧数据库数据。这意味着切换后除初始超级管理员和迁移内置
静态配置外，租户、机构、工作人员、设备、袋码、订单、清运、钱包和渠道配置都从空状态
重新建立；当前旧库必须作为旧应用的成对恢复/归档单元保留，不能提前删除或覆盖。

### 11.2 身份和传输边界

- `ecobin_app` 使用当前权限目录生成的最小表级/列级 grants，无 DDL、GRANT、触发器
  管理或系统库权限；账号级设置为 `REQUIRE SSL`。
- `ecobin_backup` 只读并设置为 `REQUIRE SSL`。
- `ecobin_schema_owner` 在迁移后锁定；`ecobin_trigger_definer` 从供应后保持锁定。
- 应用和备份密码复用服务器 `/etc/ecobin/secrets` 下现有 root-only 秘密文件，未复制到
  Git、文档或普通日志。
- 2026-08-29 项目负责人明确决定不启用实例级 `require_secure_transport`，其值保持
  `OFF/0` 是预期状态，不再作为待办或切换阻断。补偿边界是上述账号级 `REQUIRE SSL`，
  正式应用 JDBC URL 必须显式使用 `sslMode=REQUIRED`；管理操作也应使用 TLS。
- CDB 管理员口令曾在人工协作通道中提供，正式切换前应轮换；文档不保存原值。

### 11.3 一次性启动及副作用清理

第一次候选后端启动把服务器完整秘密树挂入 Fake 环境，安全边界检测到真实 OneNet/COS
凭证后按设计拒绝启动；该次没有创建管理员或业务数据。第二次只挂载数据库密码、JWT、
袋码 K1 和默认管理员密码四个必要文件，同时关闭设备注册、远程维护及真实外联，V60
epoch/readiness 和初始管理员创建通过。

Fake 首启默认还创建了微信支付模拟商户和出款闸门各一条。确认没有商户绑定、支付、提现
或转账依赖后，这两条模拟数据已在同一事务中精确删除，目标库恢复为“迁移静态数据 + 初始
超级管理员”的预期状态。以后若再次执行仅管理员引导，必须设置
`ecobin.funds.wechat-pay.merchant-profile-registration-enabled=false`，避免重复产生模拟
支付配置；不得把启动成功直接等同于零业务副作用。

一次性引导容器和过滤后的临时秘密目录均已删除。资格验证库仍保留用于复核，未经明确的
破坏性操作授权不得删除。

### 11.4 初次供应完成时的服务器与候选制品快照

- 初次供应完成时运行中的 `ecobin-target-backend` 使用镜像
  `ecobin-local/backend:20260826042731-61894afed291`，连接原单机
  `ecobin-target-mysql84:3306/ecobin`；没有修改当前后端数据库连接。
- 当时原单机数据库为 V59，并仍承接测试写入；现场抽样时约有 `108,784` 行业务数据。
  因此不能把新 CDB 的空库验收误写成线上数据库已经迁移。
- V60 候选 JAR 已暂存在
  `/var/lib/ecobin/staging/cdb-bootstrap-7f908d72/app.jar`，SHA-256 为
  `e6b0bc50516059a1b0eddd467e39fba42867adcd9a9830edabe9558d1bc10d11`。
  它只用于本次受限引导，不是已安装的正式发布，也没有常驻运行。
- 当时 V59 后端受 epoch 门禁约束，不能直接连接 V60 新库。切换前必须从当前代码构建并
  安装正式 V60 发布制品，再显式修改数据库连接。

### 11.5 当前测试完成后的生产切换清单

2026-08-29 项目负责人明确：当时的 V59 数据库和现用 COS 继续作为测试环境，尚未完成的
设备归属、配置、皮重、投递、清运和其他业务闭环均先在当前环境跑完。下列内容是测试通过
之后的生产化清单，不是当前紧邻动作；迁移完成后才开始把新 CDB 和新 COS 当作生产环境。
测试数据仍不导入新库。同日现用测试栈后续升级到 V60 不改变这项测试/生产边界。

1. 完成 COS 对象存储迁移和生产配置核对；当前 COS 迁移尚未开始。
2. 把经过正式发布流程构建的 V60 后端安装到服务器，核对 OneNet、COS、微信支付和设备
   注册等真实配置，但在获切换授权前保持当前后端及旧库连接不变。
3. 为新 CDB 配置正式 JDBC URL，并明确 `sslMode=REQUIRED`；先做只读连接、epoch、账号
   正负权限和 readiness 预检，再改变唯一入口所有权。
4. 轮换 CDB 管理员口令，并在切换前对新 CDB 做一次可恢复备份或快照；旧数据库及旧应用
   制品继续作为成对恢复单元保留。
5. 明确决定资格库 `ecobin_qualification_20260829` 的保留期限；没有授权时保持原状。
6. 获得正式切换授权后再停止旧入口、切换后端数据库连接并执行 H-06 成对切换验证。
   新库为空是已接受的产品决定，不得在切换过程中临时导入旧业务数据。

### 11.6 同日后续现场状态

2026-08-29 后续为继续设备端到端测试，现用测试栈已经发生前向变化：运行后端镜像为
`ecobin-local/backend:20260829054214-cb9952c0ba36`，仍连接原单机
`ecobin-target-mysql84:3306/ecobin`，该现用测试库的 Flyway 版本已核对为 V60。指定设备的
测试租户和机构分配由该 Web/后端写入此库，并在同一库形成配置 v1 `APPLIED` 与初始空袋
皮重完成事实，因此第 11.4 节的 V59 描述只代表初次供应结束时快照，不再是当前运行状态。

这不是腾讯云 CDB 生产入口切换：拟生产 CDB 仍为空库，现用 COS 未迁移，旧测试业务数据
仍不导入，正式发布制品、TLS JDBC、备份/回退和 H-06 成对切换仍按第 11.5 节另行执行。
