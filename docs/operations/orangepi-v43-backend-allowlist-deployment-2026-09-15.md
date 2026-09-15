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
