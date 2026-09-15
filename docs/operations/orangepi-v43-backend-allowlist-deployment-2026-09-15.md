# 香橙派 v43 镜像构建与后台认可版本部署记录（2026-09-15）

## 结论

UART按请求5秒超时、精确回复自动恢复及慢任务线程隔离已经提交为
`244dd4eaf72476110df322ac22017fe86336c4ad`。以该提交为唯一源码输入重新生成
`hardware-runtime-20260915-43`、`software-payload-20260915-43`和
`0.1.0-single-card.20260915.43`，没有复用v42运行时归档或软件载荷。两次独立候选构建的
运行时、载荷锁和候选整镜像均逐字节一致。

最终受控HIL镜像只从v42受保护镜像继承既定的厂家接入、热点和受控本地登录材料，其余系统
内容来自v43候选。最终镜像已导出到
`hardware/image-artifacts/local/factory-secret/builds/hil-factory-login-20260915-43-uart2-rc26/`
（该目录受Git忽略保护），大小为2,571,108,352字节，SHA-256为
`6c9a97e21dec1be8c6a5d2a96b735ab401cf44eb8393c008d3e64ef129325e2f`。

项目负责人随后先要求增加后端允许版本，并在镜像和后台步骤完成后另行插入TF卡、明确确认
覆盖Windows磁盘1。受保护写卡工具已完成整镜像写入、强制刷新和全镜像范围回读，回读摘要与
源镜像一致。生产服务器`VM-0-16-ubuntu`的
`/etc/ecobin/runtime.env`已只向
`ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS`原精确值末尾追加
`hardware-runtime-20260915-43`。v40、v41、v42及全部更早的现行认可值原样保留，没有修改
设备验收记录、业务数据、应用发布或容器镜像，也没有开启远程业务程序下发。

TF卡现已写入v43，但尚未装回香橙派冷启动，也没有执行真实HIL。后台认可和写卡回读都不能替代
热点、注册、UART、HMI、RS485、称重、摄像头、机构或业务流程的现场验证。

## 源码、构建和离线验证

提交前验证包括：请求/恢复/慢任务专项`300 passed, 29 skipped`；Python 3.11完整
`hardware/tests`为`4334 passed, 126 skipped, 1 failed, 5 subtests passed`。唯一失败仍是既有
Windows强杀进程后SQLite WAL重开触发`SQLITE_IOERR_TRUNCATE`，未改代码的单项复跑通过。

第一次构建在生成任何v43输出前因v43 Git bundle尚不存在而安全退出。随后从已提交Git对象生成并
验证`repository-v43-20260915.bundle`，确认包含上述精确提交；未跟踪的`hardware_mcu.zip`没有
进入bundle。两个全新的受控目录分别完成候选构建，结果为：

- 运行时归档SHA-256：
  `46c4be9ad891c56b46d7845346201c4e7263b4a088912bc715fd2f5bee040a0c`；
- 软件载荷锁SHA-256：
  `33c9e254be0fe393a14469650b23f6adc496c8583fe41e8ffec4ba654c2f271c`；
- 无秘密候选镜像SHA-256：
  `03240555fc824b6f53b17d64aa4b789b04a63b8fa501048c7465e391c0cb6e44`；
- 两次构建对以上三个文件执行`cmp`和SHA-256核对，均逐字节一致；每次构建退出后专用
  `/dev/sdc`交换分区均已恢复。

受控最终镜像以SHA-256为
`2f6a982b094406fde4916b2165dfa96896a34d91fa77dd1373fe0d5980931c17`的v42成品为受保护输入源。
文件树差异审计确认53,464个条目与v43无秘密候选一致，只有3个批准路径不同；受保护值及其单项
摘要没有输出，启动区来自v43候选。最终镜像在WSL私有构建区和导出目录逐字节一致，导出后再次
执行整镜像SHA-256校验通过。

断网、镜像只读、仅合成状态、无真实硬件访问的ARM64冒烟通过：镜像内8项测试通过，Python为
3.11.2；UART端口/波特率、rc.26消息注册、通信模块导入、未知物理效果隔离账本、重量报告和完整
报告消费者检查均通过。镜像工具回归共63项通过、8项按Windows/Linux环境条件跳过。

## TF卡写入和完整回读

用户插入TF卡后，系统重新枚举到唯一符合条件的外置介质。向用户展示完整目标事实后，用户本轮
明确回复“确认覆盖磁盘 1”。临写前和管理员进程内的两次检查均确认：

- Windows磁盘号：1；总线类型：USB；
- 序列号：`121220160204`；容量：31,268,536,320字节；
- 非系统盘、非启动盘、在线、健康、非只读；
- 唯一分区没有Windows盘符，未挂载Windows文件系统；
- 源镜像大小为2,571,108,352字节，SHA-256为
  `6c9a97e21dec1be8c6a5d2a96b735ab401cf44eb8393c008d3e64ef129325e2f`；
- 受保护写卡入口`Write-HilImageToDisk.ps1`的SHA-256为
  `2117d2e231ae001187e04c48c64e56b4fca5a21d99ec308777d7e015e69c9d78`；
- 固定磁盘号、二次确认磁盘号、序列号、容量和镜像摘要的一次性v43包装器SHA-256为
  `dd6af61c4e84c1cbfa6139ac661911c7e885b35d082aa43f5db059b3a52e3f8e`。

管理员写卡进程于`2026-09-15T13:32:22.1641733Z`开始，于
`2026-09-15T13:37:13.4826942Z`完成。结果为：

- 写入并强制刷新：2,571,108,352字节；
- 完整回读：2,571,108,352字节；
- 回读SHA-256：
  `6c9a97e21dec1be8c6a5d2a96b735ab401cf44eb8393c008d3e64ef129325e2f`；
- 写卡结果：`PASS`，退出证据为0，没有生成错误文件；
- 写后独立检查再次确认磁盘号、序列号、容量、总线类型和非系统/启动盘身份均未变化，且没有
  Windows盘符挂载；
- 进度日志SHA-256：
  `d7af6f3fcc2c5358adcd7fcf341bad7159a88fcbb739606e5b7f2524162e1555`；
- 结果JSON SHA-256：
  `0801776a681f8e46fc8d373ddb4a7cb5720bbe95dd3067df83c6ed49f62afd2f`。

写卡进度、结果和一次性包装器位于Git忽略的`hardware/image-artifacts/local/`，没有提交设备秘密；
`hardware_mcu.zip`仍未修改或提交。

## 后端变更前检查

Windows OpenSSH继续使用已固定主机指纹和既有身份；远端通过免交互sudo执行仅由标准输入传入的
脚本。两轮只读预检分别由独立预检脚本和正式部署脚本的加锁`preflight`模式完成，确认：

- 当前应用发布仍为`20260914064458-82e72e0dca16`；
- 原认可值共19个且唯一，v40、v41、v42存在，v43不存在；
- `runtime.env`为`root:root 0600`的普通单硬链接文件，认可键只出现一次；
- 原文件SHA-256为
  `1a3967c8b3cf5f7b963e167a84ac1e38fc914c66bd6683da1e48e030517db839`；
- systemd目标、后端/Web容器、生产预检、秘密隔离、后端健康和Web回环入口正常；
- `businessReleaseRemoteDispatchEnabled=false`。

生成后的远端部署脚本在本地再次通过语法和不变量检查：持有独占锁、复核精确旧值和应用发布，
创建root私有备份，在同目录生成候选文件，只替换一个认可列表键并原子移动；激活失败时仅在当前
文件摘要仍等于本次应用摘要的条件下回滚，避免覆盖并发修改。

## 原子追加和独立验证证据

- 修改前`runtime.env` SHA-256：
  `1a3967c8b3cf5f7b963e167a84ac1e38fc914c66bd6683da1e48e030517db839`；
- 修改后`runtime.env` SHA-256：
  `fe59137c8a5f41bb38552f9c448141895b618e17cae2c183e8716aedb7224aad`；
- root私有备份：`/etc/ecobin/runtime.env.pre-v43-20260915T131537Z.HWKWgjRh`，摘要等于修改前
  文件，元数据为`root:root 0600`、普通文件、单硬链接；
- 后端镜像保持
  `sha256:9b40cec445f90501c8306dc580db2877c6885ba8851af4442aa76e48261740d8`；
- Web镜像保持
  `sha256:d32e68aeca898979714b9560c57b7a87fb44c7873eaf120325f39097d62602da`；
- `ecobin-stage-runtime-secrets.service`与`ecobin-target-app.service`均为active；
- `ecobin-target-backend`与`ecobin-target-web`均为running/healthy；
- 服务器文件与运行中容器认可值精确一致，共20个唯一值，v40/v41/v42/v43均存在；
- `businessReleaseRemoteDispatchEnabled=false`；
- 生产预检、秘密隔离探针、后端内置健康检查和`127.0.0.1:18080` Web回环检查通过。

正式应用只执行一次。独立核验由新的SSH进程重新读取文件、备份、容器环境、镜像身份和健康
状态，结果为`v43-allowlist-independent-verification=PASS`。生成后的预检、部署和独立核验脚本
SHA-256分别为`07da26fcd3da9e7fd3db2646d1b27919cd983a6093f4ab32f5ac46609d0d36ed`、
`d7d04641d0decb0bed55d5535abcd1960a0cdf1188b4a43582de7824694cb9b0`和
`862598acd66663270d692a03244883d48595ee0c7cbd71695f6df36681cca570`。

## 尚未完成的现场阶段

当前TF卡已写入v43并通过完整回读，但尚未安全弹出并装回香橙派冷启动。写卡成功只证明介质中
镜像范围的字节与源文件一致，不证明系统能够在目标硬件上启动或外设工作。

真实热点、设备注册、UART、HMI、RS485、称重、摄像头、机构、OneNet、反向SSH、慢拍照期间
UART持续轮询、迟到回复恢复，以及投递/清运/断电HIL均未执行。尤其清运不能用投递结果替代；
后台认可和离线ARM64冒烟也不能视为这些现场项目通过。

## v43首次冷启动现场观察

项目负责人随后把v43卡装回设备并开始真实厂家验收。以下观察属于现场问题记录，不因对应步骤
最终通过而删除，也不把部分步骤通过扩大为整机HIL通过。

### 投递结束反馈延迟

投口打开后，操作员立即在串口屏点击“完成投递”，厂家网页仍需等待数十秒才进入后续步骤；
该次投递最终通过。源码和现有真实C状态机测试复核表明，开门页的“完成投递”只请求当前轮关门，
不是整场投递的最终`END`：MCU在实际下发逻辑`CLOSE`后固定等待
`deliveryDoorTravelWaitMs=30000`毫秒，再执行新的关门后称重；称重完成后才进入
`continueDeliveryWaitMs=30000`毫秒的继续/结束选择窗口。操作员若没有在该新页面再次点击
“结束投递”，窗口到期形成`DELIVERY_WINDOW_EXPIRED`；厂家验收当前把它与
`DELIVERY_END`都视为有效成功原因。香橙派只在最终`WORK_RESULT`形成后开始保存和确认，
不是先收到最终结果再固定等待几十秒。

本地按Python 3.11原样复跑提前关门、30秒行程边界、选择窗口到期和最终原因四项现有测试，结果
为`4 passed`。该观察当前记为流程/提示问题：尚未读取本次实机`report.json`，因此不能断言现场
最终原因一定是`DELIVERY_WINDOW_EXPIRED`；没有修改30秒机械行程边界、UART协议、MCU或页面。

### 云端验收信息停在设备平台已接收

投递步骤通过后，现场继续推进到“采集并上传验收信息”，页面子步骤“云端设备平台已接收”长期
显示“进行中”。这里的“进行中”不能解释为已经接收成功；设备投影只有在取得OneNet成功回复后
才会完成该子步骤，活动态的准确含义仍是等待OneNet接受。即使该子步骤以后完成，也仍不等于
后端北向消费、验收记录落库或设备收到最终确认已经完成。

现场期间对生产服务器执行只读核对：后端容器健康、重启次数为0，OneNet北向Pulsar消费者已经
成功订阅且仍在持续接收同一设备的配置进度和运行快照。该设备在UTC
`14:00:43`、`14:06:16`、`14:11:50`和`14:17:23`先后形成四个自动验收请求；前三个均在各自
五分钟有效期内没有等到验收证据而被可靠取消，第四个在UTC `14:21:04`查询时仍为
`PENDING/AWAITING_DEVICE_EVIDENCE`。最近24小时的后台可靠收件箱和
`dev_device_acceptance_evidence`均没有该设备的`DEVICE_ACCEPTANCE_EVIDENCE`，因此后台尚未
开始验收计算，也没有条件创建返回设备的`CONFIRM_EDGE_EVENT`。

同时查明，从该设备UTC `13:58:42`起每约五秒出现的北向永久拒收是另一类周期运行快照，隔离原因
统一为`weightMeasurementUid has an invalid format`；它们不是验收证据，不能拿来解释或伪装本次
验收已到后台。当前证据把验收卡点限定在“设备记为OneNet已接收”到“后台北向收到验收事件”之间，
但尚不能仅凭后台证据区分OneNet没有转发验收事件，还是设备把别的上行回复错误关联成验收事件
成功。开发机当时仍在普通WLAN地址`192.168.1.149`，不在厂家热点网段，尚未读取设备本地
`event_outbox`的事件编号、平台接收时间和MQTT回复关联。不得通过手工重复验收、补造后台记录、
清除设备待办或确认隔离记录来绕过。

### 香橙派本机只读诊断与根因

随后没有切换开发机网络，而是从正式后台创建15分钟短期远程维护会话，通过只监听服务器回环
地址的反向SSH访问该香橙派。香橙派本机请求`http://10.42.0.1/api/v1/status`成功；只读检查本地
SQLite、运行日志和实际线级编码完成后，维护会话主动关闭为`CLOSED`，端口22011租约释放，
`leaseCleanupPending=false`。没有重启服务、修改设备记录、直接占用UART或执行机构动作。

设备本机证据排除了“照片仍在采集或COS仍在上传”：热点状态中`CAMERA_CAPTURE`、
`COS_UPLOAD_READBACK`和`EVIDENCE_RECORDED`均已完成，只有`ONENET_TRANSPORT_ACCEPTED`保持
`WAITING_ONENET_ACCEPTANCE`，`PLATFORM_CONFIRMED`尚未开始。本地验收命令均为`COMPLETED /`
`EVIDENCE_RECORDED`；每次自动重发的新验收请求都会再次拍摄、上传并形成新的可靠验收事件。

截至只读维护结束，日志和数据库已看到UTC `14:00`至`14:45`至少九轮验收证据。每条
`DEVICE_ACCEPTANCE_EVIDENCE`都仍为`PENDING`，`confirmed_at`和`platform_accepted_at`均为空；
OneNet对每一条及其后续重试都返回`2308`。例如设备序号29、95、161、228、295、362、429、
500和567的验收事件均未被错误编号或其他事件回复冒充成功；同一时段远程维护状态等其他事件能
取得`200`并由后台确认，证明MQTT连接、事件回复编号关联和后端北向链路不是整体中断。

根因是设备线级物模型与生产OneNet控制台物模型没有同步：

- 9月11日最后一次生产导入和逐字段核验记录为18个服务、22个事件；当时
  `deviceAcceptanceEvidence`只有37个输出字段，`evidenceSchemaVersion`只允许v3/v4；
- v43实际携带当前OneNet 2.4投影：18个服务、25个事件；验收事件为v5、45个输出字段，线级
  `evidenceSchemaVersion=3`表示业务版本v5；
- 新增的八个输出字段为`deviceEntryUrlMcuAppliedPresent`、`deviceEntryUrlMcuApplied`、
  `deviceEntryUrlAppliedSha2Present`、`deviceEntryUrlAppliedSha2`、
  `deviceEntryUrlAppliedMcuBPresent`、`deviceEntryUrlAppliedMcuB`、
  `deviceEntryUrlDisplayBasiPresent`和`deviceEntryUrlDisplayBasi`；生产旧模型既不接受枚举值3，
  也没有这八个字段，因此OneNet在北向转发前以2308拒绝。当前一条实机线级报文为1,954字节、
  45个输出字段，与v43生成投影完全一致。

服务器现有产品级下行密钥调用OneNet只读`QueryThingModel`返回
`iot.common.authPermissionDeny`，不能用该密钥补做控制台在线导出；以上结论由9月11日已保存的
生产导入核验、当前机器生成候选、实机45字段编码及全部事件的2308回复交叉确定。修复前必须由
有控制台权限的操作者导入并保存当前
`contracts/onenet/generated/onenet-thing-model.candidate.json`，然后再逐项核对18服务、25事件、
0属性及零字段差异；仅增加后端允许版本不会改变OneNet的字段校验。

另有一个不会被物模型同步自动解决的后续门槛：实机验收证据中持久存储、可信时间、配置、MCU
通信、双摄、COS回读和二维码应用均为`true`，但`sensorsHealthy=false`。当前设备事实显示称重和
烟感可用，满溢事实为`fullnessReadStatus=UNAVAILABLE`；v43验收采集器要求满溢读数也为`VALID`，
而本地厂家报告仍允许该情形以`PASSED`完成。后端收到事件后会据此加入
`SENSOR_SELF_TEST_FAILED`，所以同步OneNet模型以后，本轮也不能直接判通过。必须先确认是满溢
传感器/接线确实不可用，还是本地厂家验收与云端验收规则口径矛盾；不能把`false`改成`true`、
手工补确认或复用旧证据绕过。

本机`report.json`同时确认前述投递延迟的现场终因确为`DELIVERY_WINDOW_EXPIRED`：操作员在开门页
请求关门后经历30秒门行程等待，进入继续/结束选择窗口后没有再点击“结束投递”，再等30秒窗口
到期才产生最终结果。因此第一项不是结果已经到达香橙派后又被固定压住，而是MCU按当前两阶段
交互尚未形成最终结果；后续应单独优化页面提示或已确认关门后的验收路径，不能删除机械行程等待。
