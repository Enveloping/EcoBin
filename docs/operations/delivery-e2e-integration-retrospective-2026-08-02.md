# 投递全链路联调复盘与复跑手册（2026-08-02）

> 状态：已完成一次真实云链路、模拟 MCU 和模拟双摄的受控联调
>
> 对应代码：`29368af feat(fullness): trust edge-reported current-bag state`
>
> 适用数据库：P0 目标库 V25
>
> 相关裁决：[V25 设备上报当前袋满溢状态](../architecture/fullness-reporting-v25.md)

## 1. 结论和验收边界

本轮已经打通以下闭环：

```text
后端开始投递
  → OneNet 服务下发
  → 香橙派可靠受理
  → PTY 模拟固定帧 MCU 返回重量/状态
  → simulated:// 双摄生成四张照片并经真实 COS 链路上传
  → 香橙派可靠上报 DELIVERY_COMPLETE
  → OneNet 北向消息进入后端
  → 后端创建唯一订单
  → 后端下发业务确认
  → 香橙派持久化确认并上报 BUSINESS_CONFIRMATION_RECEIPT
  → 后端确认任务收敛
  → 香橙派上报 FULLNESS_STATE_CHANGED
  → 后端仅在当前袋明确 FULL 时阻止下一次投递
```

本轮得到的脱敏业务结果为：

- 唯一投递订单，净重 `2500g`；
- 返现金额 `1.13 元`；
- 投递前后、内外共四个照片槽均有可信 COS URL；
- 投递完成事件的业务确认已在香橙派落盘，确认回执使后端原可靠任务结束；
- 当前袋上报 `FULL` 后，投递选项查询和开始投递接口都返回 `PORT_FULL`；
- 后端没有主动下发或轮询 `SAMPLE_FULLNESS`。

本轮是真实 OneNet、北向 MQ、后端、MySQL 和 COS 链路，但 MCU、门、称重传感器和两路
摄像头均为模拟。因此它只能证明“跨端软件受控闭环完成”，不能证明物理串口、电气抗干扰、
屏幕按钮、门执行器、真实称重、USB 枚举和成像质量；后者仍属于 H-03 真机验收。

## 2. 本轮遇到的坑

### 2.1 旧 systemd 服务与手工进程会争用同一份设备身份和本地状态

香橙派上同时存在 `ecobin-hardware.service` 和 `ecobin-mcu-simulator.service`。如果只在
SSH 会话里手工启动新代码，却没有先停用旧服务，可能同时出现两个 MQTT 客户端、两个
SQLite 写入者或两个串口消费者。表面现象通常是重复上下线、事件来源混乱、软链接被占用，
甚至误以为新代码没有生效。

本轮处理方式是先停止并禁用两个服务，再确认没有活动 unit，最后才启动本轮 PTY 模拟器和
香橙派进程。下次联调必须先执行：

```bash
systemctl stop ecobin-hardware.service ecobin-mcu-simulator.service
systemctl disable ecobin-hardware.service ecobin-mcu-simulator.service
systemctl is-active ecobin-hardware.service ecobin-mcu-simulator.service
pgrep -af 'hardware/main.py|fixed_frame_pty_simulator.py'
```

预期两个 unit 都是 `inactive`，并且不存在上一轮遗留进程。联调完成后是否重新启用正式服务，
必须由操作者明确决定，不能让测试脚本自动恢复。

### 2.2 “全新环境”不能只清后端数据库，也不能直接删除边缘目录

后端目标库即使已经清理，香橙派旧 `edge.db`、照片队列、确认收件箱、事件序号和作业文件仍
可能在重连后继续发送。反过来，只清香橙派而保留数据库中的活动 session、当前袋或可靠任务，
也会造成新旧作业串联。

本轮先分别备份目标数据库和香橙派旧 `data/`，再清理受控测试业务数据并建立新的边缘数据
目录。正确原则是：

1. 停止所有后端和边缘写入者；
2. 核对将要操作的是试验目标库和 `/root/EcoBin/hardware/data`，不是旧生产库或仓库根目录；
3. 先生成带 UTC run ID 的数据库备份；
4. 把边缘 `data/` 移到 root-only 的 `archive/data-before-<run-id>`，不直接递归删除；
5. 使用受控 reset/seed 流程清理业务数据并重建当前部署、投口、袋、配置和测试身份；
6. 启动后检查 SQLite 从空状态初始化，数据库中不存在上一轮活动 session 和待发旧事件。

禁止凭印象执行一组 `TRUNCATE`，因为设备、部署、投口、袋、session、订单、inbox/outbox
和可靠任务之间有外键及事实所有权。若当前没有受控 reset 工具，应先写出精确清理清单并人工
复核，不能用关闭外键约束的方式绕过。

### 2.3 代码已经是 V25，不代表 Docker 中的数据库会自动升级

目标运行制品不携带 Flyway 迁移脚本，后端启动只做数据库纪元（schema 版本和身份）的只读
校验。本轮数据库起初是 V24；如果直接启动 V25 后端，会被 epoch guard 拒绝，而不是自动
执行 `V25__edge_reported_current_bag_fullness.sql`。

下次顺序必须是：

1. 备份数据库；
2. 使用一次性 schema owner/受控迁移作业执行 V25；
3. 同步 V25 新表和列需要的 `ecobin_app` 最小权限；
4. 校验 `flyway_schema_history` 为连续成功的 V1～V25；
5. 再以日常应用身份启动后端，确认 readiness 通过。

数据库身份和迁移边界见[目标数据库环境供应手册](../deployment/h02-target-database.md)。不要把
schema owner、root 或迁移密码写进 `.env`、命令历史和本文档。

### 2.4 多模块修改后直接启动 bootstrap 会加载 `.m2` 里的旧模块 jar

`spring-boot:run -pl ecobin-bootstrap` 不会重建其他模块。只执行该命令时，bootstrap 可能
读取本机 Maven 仓库中旧的 device、recycling、operations 或 integration jar，造成源码、
单元测试和实际运行行为不一致。

本轮还遇到 Windows 正在运行的后端 JVM 锁住目标 jar，使 `install` 失败。可靠顺序是：

```powershell
# 先正常停止旧后端 JVM
.\mvnw.cmd install -DskipTests
.\mvnw.cmd spring-boot:run -pl ecobin-bootstrap
```

如果 `install` 报 jar 无法替换，先定位并正常停止占用它的 JVM，再重试；不能跳过安装，也
不能把构建失败误判为业务代码失败。不要使用 `-am spring-boot:run`，它可能在父 POM 查找
不存在的启动类。

### 2.5 OneNet 物模型、Schema、Java 投影和 Python 解码必须四方同时一致

V25 新增 `FULLNESS_STATE_CHANGED` 后，首次真实确认暴露出一个契约缺口：后端业务确认的
`resultReferences` 会引用 `PORT_FULLNESS_STATE`，但最初的 OneNet 确认服务枚举、Java
投影和香橙派解码并未完整包含该类型。结果是订单和满溢事实可以在后端创建，但确认下行无法
完整投影，可靠任务被阻断。

最终统一为固定线级编号 `8 = PORT_FULLNESS_STATE`，并同步修改：

- 权威 `contracts/onenet/common.schema.json`；
- 生成的 OneNet 物模型候选；
- 后端 `OneNetClient` 投影；
- 香橙派 `onenet_wire.py` 解码；
- Java/Python 契约测试。

下次修改任何跨端枚举时必须先改权威 Schema/Mapping，再生成，不得手改候选文件：

```powershell
python contracts/tools/generate_contracts.py
python contracts/tools/generate_contracts.py --check
python contracts/tools/validate_contracts.py
python -m unittest discover -s contracts/tests -v
```

导入 OneNet 后还要在控制台或导出结果中检查：

- 存在事件 `fullnessStateChanged`；
- 存在服务 `confirmEdgeEvent`；
- `confirmEdgeEvent.resultReferences[].type` 包含编号 `8`；
- 后端、香橙派部署的代码与本次导入候选来自同一提交。

“控制台导入成功”不能替代这四项检查，也不能证明已经运行的设备进程加载了新解码表。

### 2.6 OneNet `code=0` 只表示平台受理，不表示业务确认闭环完成

本轮最容易误判的是确认链路。后端调用 `confirmEdgeEvent` 得到 OneNet 成功，只能证明
OneNet 接受了服务调用；可靠任务仍应等待：

1. 香橙派收到并校验原 `eventUid + payloadSha256`；
2. 香橙派把确认写入 SQLite；
3. 香橙派建立并发布 `BUSINESS_CONFIRMATION_RECEIPT`；
4. 北向 MQ 把回执送到后端；
5. 后端匹配原 `confirmationUid` 后将原任务置为 `DONE`。

这对应四层不同事实：OneNet 传输接受、香橙派可靠受理、物理动作/设备事实、后端业务完成。
任何一层都不能冒充下一层。联调报告必须同时给出中心可靠任务和边缘 SQLite 的收敛证据。

### 2.7 修正物模型后，已经 BLOCKED 的原任务不会凭空恢复

首次确认因缺少 `PORT_FULLNESS_STATE` 映射而进入 `BLOCKED`。重新导入正确物模型只修复
外部条件，不会自动改变数据库中原任务的状态。

本轮使用仓库定义的受控恢复语义唤醒原任务：保留同一个 `taskUid`、稳定载荷、外部业务身份
和历史 attempt，只增加唤醒版本，让 worker 重新校验并执行。禁止直接把任务 SQL 更新成
`DONE`，也禁止复制出一个新确认任务，否则会伪造回执或破坏幂等证据。

恢复后仍需证明同一确认在香橙派侧首次为 `ACCEPTED`、重复投递为 `DUPLICATE`，并且回执
最终使后端原任务结束。重复确认是可靠投递的正常现象，不是重复业务结果。

### 2.8 主机时钟偏差会放大短 TTL 命令和 STS 授权问题

本轮发现 Windows 主机与香橙派约有数秒偏差；它没有阻止最终结果，但对带 `issuedAt`、
`expiresAt` 的 OneNet 命令和短期 COS STS 授权是明确风险。Windows 非管理员执行
`w32tm /resync` 可能被拒绝，不能靠放宽业务校验或无限延长 TTL 掩盖。

每次联调前比较三端 UTC：

```powershell
Get-Date -AsUTC
```

```bash
date -u
timedatectl status
```

目标是 Windows、后端宿主机和香橙派差值不超过 2 秒。超出时先用有权限的系统时间同步
机制修复，再测试命令过期和 STS；同时记录比较时间，避免把网络耗时当成时钟偏差。

### 2.9 只看订单成功会漏掉照片、确认、满溢和旧轮询残留

“数据库中出现订单”只是中间结果。本轮直到以下事实同时成立才算投递软件闭环通过：

- 同一 session 只有一笔订单且重量、金额正确；
- 四个照片槽都对应本次 work UID，URL 可读取；
- 原 `DELIVERY_COMPLETE` 在边缘为已确认；
- 后端 `CONFIRM_EDGE_EVENT` 原任务有匹配回执并结束；
- 没有 projection、inbox 或边缘冲突错误；
- `FULLNESS_STATE_CHANGED` 只作用于当前袋和较新事件序号；
- 明确 `FULL` 时选项与开始接口都阻止，`NOT_FULL` 时两者都允许；
- 正常投递/清运没有新增 `SAMPLE_FULLNESS` 任务。

## 3. 下次复跑的标准顺序

### 阶段 A：冻结版本和拓扑

- [ ] 记录后端 commit、香橙派 commit、数据库 epoch 和 OneNet 候选文件摘要；
- [ ] 明确真实组件和模拟组件，本手册默认“真实 OneNet/COS + PTY MCU + 模拟双摄”；
- [ ] 确认 SSH、OneNet、北向 MQ、数据库和 COS 的网络可达性；
- [ ] 比较 Windows、后端宿主机、数据库和香橙派时钟；
- [ ] 不输出 `.env`、token、设备 Key、AccessKey、STS 或数据库密码。

SSH 不通时先按网络问题处理：检查设备 IP、路由、热点/Wi-Fi、端口 22 和免密身份。只有 SSH
恢复后在真实香橙派复现了相同业务错误，才把问题归到设备代码；WSL 是否参与终端环境本身
不能证明代码好坏。

### 阶段 B：建立全新且可恢复的测试环境

- [ ] 停止后端写入者、香橙派 systemd 服务和上一轮模拟器；
- [ ] 备份目标数据库和香橙派 `data/`；
- [ ] 受控清理上一轮业务数据，保留备份和审计信息；
- [ ] 迁移并验证数据库为 V25；
- [ ] 重建唯一测试租户/机构/部署/投口/当前袋和必要配置；
- [ ] 确认没有旧活动 session、未决旧边缘事件或旧 `SAMPLE_FULLNESS` 任务。

### 阶段 C：先验证契约，再部署代码

- [ ] 生成检查、契约校验和契约单测通过；
- [ ] OneNet 导入同提交生成的 `onenet-thing-model.candidate.json`；
- [ ] 检查 `fullnessStateChanged` 和确认引用编号 `8`；
- [ ] Java 21 测试通过；
- [ ] Python 3.11 硬件测试通过；
- [ ] 停止旧后端，执行全 reactor `install -DskipTests`，再启动 bootstrap。

### 阶段 D：以单实例启动模拟硬件链路

香橙派 `.env` 只把物理边界改为显式模拟，其他路径仍运行正式代码：

```dotenv
ECOBIN_MCU_PROTOCOL=fixed-frame
ECOBIN_SERIAL_PORT=/tmp/ecobin-fixed-frame-mcu
ECOBIN_UART_PORT_COUNT=1
ECOBIN_CAMERA_OUTSIDE=simulated://outside
ECOBIN_CAMERA_INSIDE=simulated://inside
```

设备、OneNet 和 COS 的真实凭证继续从香橙派受限 `.env` 读取，不复制进命令、日志或文档。
先启动 `fixed_frame_pty_simulator.py`，确认稳定软链接存在，再启动唯一 `main.py`。启动后检查：

- SQLite schema 初始化完成；
- MCU 本地握手完成；
- MQTT 连接成功且没有重复 client；
- runtime snapshot 持续更新；
- 两路模拟摄像头名称不同；
- 没有另一个 systemd 或手工进程争用资源。

模拟器和摄像头具体命令见
[显式双摄像头模拟使用说明](../../hardware/tools/SIMULATED-CAMERA.md)。

### 阶段 E：逐层执行并留证

1. 在当前袋非 `FULL` 时查询投递选项并开始 session；
2. 确认后端只创建一个稳定开始命令，OneNet 下发到正确部署和投口；
3. 确认香橙派先可靠落盘，再让 PTY MCU 返回本轮重量/状态；
4. 确认四次拍照、短期 STS 授权、四个 COS 上传和照片状态；
5. 结束投递，检查唯一 `DELIVERY_COMPLETE` 从边缘 outbox 经北向进入后端；
6. 检查唯一订单的 session、净重、锁定单价、金额和四个照片槽；
7. 检查确认下行、边缘确认落盘、回执上行和后端原任务 `DONE`；
8. 让模拟器产生当前袋 `FULL`，检查状态变化上报；
9. 同时调用选项和开始接口，二者都必须返回 `PORT_FULL`；
10. 上报同一当前袋较新的 `NOT_FULL`，二者都必须恢复允许；
11. 查询可靠任务，确认不存在本轮新建的 `SAMPLE_FULLNESS`。

### 阶段 F：结束和证据归档

- [ ] 正常停止 `main.py` 和 PTY 模拟器；
- [ ] 确认没有残留进程、租约或被占用的 PTY 软链接；
- [ ] 保存脱敏的 commit、epoch、OneNet 模型摘要、业务 UID、行数和状态结论；
- [ ] 不把数据库 dump、完整日志、照片原件或任何秘密提交到 Git；
- [ ] 明确测试结束后 systemd 服务保持禁用还是恢复正式运行；
- [ ] 保留本轮数据库和边缘数据备份，按约定的保留期再受控清理。

## 4. 症状到检查点

| 症状 | 优先检查 | 不应采取的做法 |
|---|---|---|
| SSH 连接失败 | IP、路由、22 端口、免密身份、设备是否重连 | 未登录设备就断言代码错误 |
| 后端拒绝启动，提示 epoch 不完整 | V25 是否迁移、history 是否连续、运行身份权限 | 让应用持有 root 并自动改库 |
| 源码已改但行为仍旧 | `.m2` jar、旧 JVM、部署 commit、双实例 | 继续打兼容补丁掩盖旧制品 |
| OneNet 返回参数/物模型错误 | 候选版本、identifier、枚举编号、控制台实际模型 | 直接手改生成候选 |
| OneNet `code=0` 但确认任务未结束 | 边缘确认落盘、回执 outbox、北向 inbox、匹配摘要 | 直接把任务改成 `DONE` |
| 修复模型后任务仍 `BLOCKED` | 用受控恢复入口唤醒同一任务 | 新建第二个确认或伪造回执 |
| 重复确认或重复事件 | 稳定 UID、摘要、`DUPLICATE` 幂等结果 | 把重复传输当成第二笔业务 |
| 有订单但没有四图 | 拍摄、STS、上传、照片状态事件分别检查 | 后端猜测 COS 对象 key/URL |
| 新袋被旧满溢结果阻止 | 当前 `bagUid`、事件序号、`STALE_BAG` 处理 | 用旧检测或未知状态继续阻止 |
| 选项可投递但开始被拒绝 | 两接口是否读取同一当前袋 `FULL` 投影 | 恢复过期检测 gate 双重口径 |
| 命令偶发过期或 STS 立即失效 | 三端 UTC、NTP、网络耗时、TTL | 关闭过期校验或无限延长授权 |

## 5. 下次验收报告的最小内容

每轮报告至少记录以下非秘密事实：

```text
runUid / UTC 起止时间
后端、边缘 commit 与制品摘要
数据库 epoch
OneNet 候选摘要和导入时间
真实/模拟组件清单
测试租户、机构、部署的公开业务标识
sessionUid / eventUid / orderNo / confirmationUid
订单重量、金额、四图状态
确认任务、边缘确认、回执和 inbox 收敛状态
FULL/NOT_FULL 下选项与开始接口的实际结果
本轮 SAMPLE_FULLNESS 新任务数
已知限制和操作者
```

日志只作为定位辅助，不是唯一证据。最终结论应由数据库权威事实、边缘 SQLite、OneNet
传输记录和可重复 API 断言共同支持。
