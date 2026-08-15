# V52 设备出厂与远程维护代码审查

> 审查日期：2026-08-15
> 审查基线：`d7ab24a feat(device): add enrollment factory acceptance and remote support`
> 当前状态：9 项问题均已修复并完成自动化回归；真实 OneNet、微信、OpenSSH 和断电试点仍按部署手册执行
> 权威设计：[V52 设备自注册、厂家初始袋与按需远程维护](../architecture/device-enrollment-factory-acceptance-remote-support-v52.md)

## 1. 结论与已确认取舍

本轮及后续复审共发现 4 项 P1 和 5 项 P2。P1 会使生产证书签发、厂家实体扫码门槛、设备
重启重连或四端口容量在正常场景下失效；P2 会破坏验收证据代次、遗留无主远程入口、让
正常迟到消息进入人工阻塞、破坏全局操作幂等语义，或使设计文档与既有初始皮重状态机矛盾。

修复采用以下已确认边界：

- 已经通过验收或已投入使用的存量设备保留旧袋历史，不要求返厂；尚未验收且未分配的
  `PLATFORM_CREATE` 旧记录必须由厂家逐投口补扫后才能验收。
- 已打开的远程维护会话在香橙派重启或网络短断后保留到原到期时间，期间不延长授权，
  管理员关闭、维护公钥撤销和会话到期始终优先。
- 按 V36 既有边界，厂家机器验收不等待初始皮重；皮重在验收通过并分配租户、机构以后测量。

## 2. 审查发现

### V52-R01 · P1 · 生产环境无法签发远程维护证书

**状态：已修复。** 部署脚本把证书颁发机构（CA）私钥安装为 `root:10001 0440`，后端
以 `10001:10001` 运行，只能使用组读权限；签名器却拒绝任何组读权限。隧道实际监听建立
以后，签名事务会抛异常并回滚，会话不能进入 `OPEN`，管理员拿不到短期证书。

证据：

- `tools/deployment/ecobin-stage-runtime-secrets.sh:228`
- `deploy/production/docker-compose.target-app.yml:8`
- `OpenSshMaintenanceCertificateSigner.java:145`

修复标准：保留生产 `0440` 和 root 所有权，签名器允许组只读但继续拒绝组写、执行及其他
用户权限，并用自动测试证明生产权限组合可签发。

实现结果：签名器现在接受“所有者只读 + 运行组只读”，仍拒绝组写、组执行和其他用户的
读写执行权限；部署权限不需要扩大，也不需要把 CA 私钥改成后端用户所有。

### V52-R02 · P1 · 存量平台录入袋绕过厂家实体扫码且无法转换

**状态：已修复。** V52 把既有记录设为 `PLATFORM_CREATE`，操作员和标签主键为空；验收
只统计行数，因此旧 Web 记录可以启动机器验收。小程序把这些投口当作已登记，只提供更正，
更正又会尝试释放空标签对应的占用而产生 500；即使越过该步骤，更新没有切换为
`FACTORY_MINIAPP`，会违反数据库约束。

证据：

- `V52__device_enrollment_factory_support_and_remote_access.sql:544`
- `AutomaticDeviceAcceptanceChallengeService.java:114`
- `FactoryAcceptanceService.java:207`

修复标准：只有厂家扫码并存在匹配活动标签占用的记录，或迁移时明确标记的存量豁免记录，
才算验收袋齐全；待验收旧记录在小程序中有同码补扫和异码更正两条可审计转换路径。

实现结果：已验收或已分配设备迁移为 `LEGACY_GRANDFATHERED`；其余旧记录保持
`PLATFORM_CREATE` 并显示“待实体补扫”。同码补扫新增 `VERIFIED` 审计事实，异码走更正；
两者都会写入厂家操作者、标签占用并转换成 `FACTORY_MINIAPP`。调度器、证据服务和页面
完成条件都只统计有效厂家扫码或明确历史豁免，旧 Web 行不能直接启动验收。

### V52-R03 · P1 · 香橙派正常重启导致未到期会话失败

**状态：已修复。** 香橙派有意保留 SQLite 会话并在启动后重连，但停止服务会先断开 SSH，
systemd 又等待 5 秒。后端每 2 秒检查一次，只要 `OPEN` 会话的 actual 实际监听标记短暂
消失，就撤销 desired 期望租约并写成 `FAILED/SERVER_LEASE_LOST`，设备随后失去重连资格。

证据：

- `RemoteSupportSessionService.java:591`
- `hardware/remote_support.py:127`
- `hardware/ecobin-hardware.service:14`

修复标准：actual 暂时不存在时进入 `RECONNECTING`，保留原租约、证书、端口和到期时间；
重新匹配后回到 `OPEN`，明确关闭、撤销或到期时不得重连。

实现结果：后端新增 `RECONNECTING`。`OPEN` 会话只因 actual 暂时消失时保留 desired 和
数据库端口占用；actual 恢复精确匹配后回到 `OPEN`，不重新签证书、不延长期限。关闭、
公钥撤销和到期分支会先撤销 desired，不会进入重连。

### V52-R04 · P1 · 关闭离线设备不能及时释放四端口槽位

**状态：已修复。** 管理员关闭后，会话停在 `CLOSING`；协调任务只重复撤销 desired，既不
根据 actual 已消失写成 `CLOSED`，也不释放端口。离线设备收不到关闭命令时，槽位会一直
占到最长 30 分钟期限，四台离线设备即可耗尽容量。

证据：

- `RemoteSupportSessionService.java:572`
- `V52__device_enrollment_factory_support_and_remote_access.sql:753`

修复标准：撤销 desired 后，以服务端 actual 已不存在作为关闭和释放的充分事实；actual
仍存在或冲突时继续保留端口，避免旧监听与新会话重叠。

实现结果：会话新增 `lease_released_at`，数据库唯一占用由该字段控制。`CLOSING` 在 actual
不存在时收敛为 `CLOSED` 并释放；终态但 actual 尚存时继续占槽，Web 显示“入口清理中”并
轮询。actual 冲突会失败关闭且保留槽位，直到旧入口确实消失。

### V52-R05 · P2 · 袋码更正没有使旧验收挑战失效

**状态：已修复。** 更正会重置资产验收结果，但不取消等待中的 `REQUEST_DEVICE_ACCEPTANCE`
任务，也没有袋记录代次或摘要。更正前采集、更正后迟到的证据仍可消费旧挑战，再根据当前
袋记录把资产改成 `PASSED`，无法证明证据属于哪一代初始袋。

证据：

- `FactoryAcceptanceService.java:256`
- `TrustedDeviceAcceptanceEvidenceService.java:126`

修复标准：每次袋变更递增代次、重算有序摘要并取消未完成挑战；挑战和证据都携带该快照，
后端同时校验任务快照与资产当前代次，旧证据不得改变状态。

实现结果：资产新增 `factory_bag_revision` 和 `factory_bag_set_sha256`，安装、补扫、更正均在
同一事务内取消旧挑战、递增代次并按 `投口号:袋码` 重算摘要。OneNet 下行、香橙派证据、
北向解码和数据库证据行全部携带该快照；消费挑战前同时核对任务快照与资产当前快照。

### V52-R06 · P2 · 数据库提交前发布 desired 会遗留无主入口

**状态：已修复。** 开启会话先写 desired 文件，再写会话、可靠命令和审计。若 JVM 在文件
发布后、数据库提交前被强杀，数据库回滚但事务回调不会运行。启动协调只枚举数据库会话，
因此无主 desired 仍可能授权设备连接，而 Web 中没有可查询、可关闭的会话。

证据：

- `RemoteSupportSessionService.java:175`
- `RemoteSupportSessionService.java:979`

修复标准：数据库先提交，desired 作为可重建投影在提交后发布；协调器扫描固定四端口并
删除无数据库会话的 desired，端口分配前还必须确认 desired 和 actual 都不存在。

实现结果：开启事务只写会话、端口占用、可靠命令和审计，提交后回调才发布 desired；会话
保存隧道公钥、指纹和 Host Key 快照，协调器可在重启后重建文件。每轮协调清除没有数据库
占用的四端口 desired；分配前同时确认 desired/actual 为空。迟到的旧会话状态使用会话编号
感知的撤销，不能删除已经复用该端口的新 desired。

### V52-R07 · P2 · V52 文档与初始皮重状态机矛盾

**状态：已修复。** V52 文档把初始皮重列为机器验收前提；实际新增初始袋固定为
`tare_status=PENDING`，机器验收不读取该字段，而皮重测量只会在资产已分配租户、机构且
验收已经 `PASSED` 后启动。若按文档实现会形成循环依赖。

证据：

- `device-enrollment-factory-acceptance-remote-support-v52.md:139`
- `AutomaticDeviceActivationScheduler.java:29`

修复标准：文档回归 V36 边界，不修改既有皮重状态机；厂家验收通过并分配以后再测量真实
空袋皮重。

实现结果：权威 V52 文档和部署手册已明确厂家验收不等待初始皮重；新袋保持
`tare_status=PENDING`，由 V36 既有激活调度在验收通过并完成租户、机构分配后测量。

### V52-R08 · P2 · 更正袋码后的旧证据被当作可重试故障

**状态：已修复。** R05 已阻止旧袋代次证据改变验收状态，但消费服务仍把这类正常迟到
消息抛成异常。可靠收件任务因此会自动重试最多 10 次，最后进入人工阻塞；香橙派也收不到
业务终态确认，会继续保留或重传该事件。

证据：

- `TrustedDeviceAcceptanceEvidenceService.java:128`
- `ReliableInboxTaskRunner.java:102`
- `ReliableTaskFailureService.java:59`

修复标准：只有证据袋代次严格小于资产当前代次时，按终态“无需动作”收敛；不得消费当前
挑战、写验收评价或改变资产状态。相同代次但摘要不同以及未来代次仍是非法证据，不能借此
放宽完整性校验。

实现结果：严格旧代次现在登记 `BUSINESS_APPLIED / NO_ACTION_REQUIRED` 业务确认并返回
`changed=false`。可靠收件任务可正常完成，设备收到确认后可清理本地事件；当前验收状态、
挑战和评价事实均不变。重复迟到只复用同一条可靠确认任务。

### V52-R09 · P2 · V52 人机写操作没有占用全局幂等身份

**状态：已修复。** 复审最初描述为“同一个厂家操作员或平台管理员并发重试会返回业务
冲突”。代码核对后确认，同一操作者的写事务在授权阶段已经锁定同一身份行，因此该窄场景
本身会串行并能重放。真实缺口更宽：不同厂家操作员或不同平台管理员锁定的是不同身份行，
它们可同时查不到领域历史；同一个 `operationUid` 跨设备、跨动作复用时，也要到领域唯一
约束或成功审计唯一约束才暴露，容易返回袋占用、会话占用甚至 500，而不是统一的
`409 COMMON.IDEMPOTENCY_KEY_CONFLICT`。

证据：

- `FactoryAcceptanceService.java:86`
- `RemoteSupportSessionService.java:132`
- `01-cross-cutting-i001-i005.md` 的 I-004
- V33 已有 `ops_governance_idempotency`

修复标准：在设备、袋、端口或会话等业务目标锁和业务效果之前，以全局唯一
`operationUid` 原子绑定稳定公开主体、平台作用域、动作、目标和规范请求摘要。同一绑定
并发重试等待首次事务提交后重放首次结果；任一绑定维度不同均精确返回公共幂等冲突；业务
前置检查或事务失败必须连同认领一起回滚，不能永久占键。

实现结果：把 V33 既有幂等表通过 framework 公共端口开放，operations 继续提供同事务
`MANDATORY` 实现，不新增迁移、表或权限。厂家安装、补扫、更正以及远程维护开启、关闭均
接入该认领；成功结果保存资源 UID、状态和版本。厂家既有领域重放同时核对成功审计中的
动作、目标和事务绑定的厂家操作者，保留安全的升级前重试；其他无法安全重建绑定的历史成功
键按公共幂等冲突拒绝，不能被新请求重新认领。远程会话插入只把真正的唯一键碰撞映射为
槽位冲突；厂家袋操作也只把预期的唯一键碰撞映射为袋占用。审计、外键或检查约束错误不再
被这些业务冲突掩盖。

## 3. 修复验证台账

| 编号 | 回归证据 | 实现结果 | 最终状态 |
|---|---|---|---|
| V52-R01 | `OpenSshMaintenanceCertificateSignerTest` 2 项通过 | 生产 `0440` 可读，宽权限仍拒绝 | 已修复 |
| V52-R02 | `FactoryAcceptancePolicyTest` 3 项、V52 空库实迁移、小程序类型检查通过 | 历史豁免与待补扫分离；同码核验/异码更正均可用 | 已修复 |
| V52-R03 | `RemoteSupportReconciliationPolicyTest` 4 项通过 | actual 短失进入 `RECONNECTING`，恢复后回 `OPEN` | 已修复 |
| V52-R04 | `RemoteSupportReconciliationPolicyTest`、`RemoteSupportLeaseStoreTest` 通过 | actual 消失才关闭并释放，清理中继续占槽 | 已修复 |
| V52-R05 | `TrustedDeviceAcceptanceEvidenceServiceTest` 4 项、`TrustedDeviceAcceptanceChallengeServiceTest` 1 项、OneNet 出入站 18 项通过 | 任务、资产、设备证据三方绑定同一袋代次和摘要 | 已修复 |
| V52-R06 | `RemoteSupportSessionTransactionTest` 2 项、`RemoteSupportLeaseStoreTest` 2 项通过 | 数据库先提交；desired 可重建、无主可清理、旧状态不可误删新租约 | 已修复 |
| V52-R07 | 文档交叉检查、全量 Java 测试通过 | 保留 V36 验收后测皮重边界 | 已修复 |
| V52-R08 | `TrustedDeviceAcceptanceEvidenceServiceTest` 7 项通过 | 旧代次终态确认；同代摘要冲突和未来代次仍拒绝 | 已修复 |
| V52-R09 | framework 4 项、device 幂等定向 6 项、真实 MySQL 并发测试通过 | 同绑定重放；跨主体/动作/目标统一冲突；失败认领回滚 | 已修复 |

## 4. 可重复验证结果

- `./mvnw test`：九个 Maven 模块全部 `SUCCESS`；包含新增回归以及既有业务测试。需要外部
  MySQL 凭证的 39 项目标环境集成测试按原设计跳过，V52 SQL 另在临时 MySQL 验证。
- 临时 `mysql:8.4.10` 从空库顺序执行 V1～V52：52 个迁移全部成功；确认四个远程端口槽、
  两个厂家袋快照列和远程租约释放列存在；真实双事务又验证同绑定重放、跨主体冲突、历史
  成功审计拒绝和认领回滚，临时容器随后删除。
- `uv run --python 3.11 --with pytest pytest -q`（`hardware/`）：325 项通过、10 项按目标
  环境跳过、5 个子测试通过。
- `uv run --python 3.11 --no-project python contracts/tools/validate_contracts.py`：23 组通过，
  1 条“本机无 C 编译器”的可选说明；80 个生成物与权威源一致。
- `npm run build`（Web）、`npm run typecheck`（小程序）、`npm run api:types:check`：全部通过。

以上证明代码、SQL 形状、契约和本地状态机修复完成；真实 OneNet 设备供应、微信绑定、Linux
OpenSSH 权限、网络短断和香橙派断电重连仍属于部署试点，不由单元测试替代。
