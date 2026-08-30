# HIL 工厂母镜像 v10 完整重建离线预检证据

本目录记录 `hil-factory-login-20260830-10` 单卡开发/HIL 镜像的完整重建和离线预检
结果。镜像二进制包含批次共用 K1、设置热点密钥和开发登录密码，只保存在 Git 忽略的
`hardware/image-artifacts/local/factory-secret/builds/hil-factory-login-20260830-10-time-sync-convergence/`
下，不纳入版本库。

v10 从锁定的 Orange Pi Debian 1.0.4 原始镜像、当前包锁、软件负载锁和干净提交
`6c2784332b1d8c81285b1df5544afa61243bc093` 完整重建。它包含 v9 首次启动现场发现并已
提交的可信校时收敛修复：校时器区分“已同步、仍在等待、确定失败”，保留已经解析出的
时间源和进行中的 burst，不再因一次暂未同步就撤销 NTP 出口；注册和正式运行仍然必须
等待系统报告可信时间。成品镜像中的 `first_boot/time_sync.py` 和
`first_boot/cellular_service.py` 已与该提交逐字节比较通过。

无密钥候选镜像 SHA-256 为
`c84c23436d1fdba4695b97c534cbcdbb6065e54b2a01051878b53fd86263295e`。随后只从已经完成
写入回读和现场流程验证的 v9 镜像逐字节继承 K1、设置热点密钥和 `orangepi` 密码散列；
受保护值及其单独摘要没有出现在日志或本公开证据中。root 密码登录保持锁定，开发阶段的
`orangepi` 默认密码登录保持启用。

最终镜像大小为 `2571108352` 字节，SHA-256 为
`62cd40004f3cc5667f7350b04deace958bc755c8f34e7a98b7f98a5d49c9ff57`。离线只读验收确认：

- 原始分区布局、ext4 文件系统和锁定的 ext4 构建配置正确；
- 当前软件负载、版本化组件、systemd 单元和提交身份一致；
- K1、设置热点密钥和开发密码散列与 v9 受保护来源逐字节一致，权限符合要求；
- 设备凭据、注册状态、远程维护状态、业务数据库、SSH 主机密钥、machine-id、
  NetworkManager 和 chrony 运行状态均为空；
- HSK/UNIQUESKY 外摄和 icSpring 内摄配置保持不变；
- 镜像内容审计再次确认两处可信校时源码与提交逐字节一致。

签名 ARM64 运行时 `hardware-runtime-20260830-10` 为 `58275320` 字节，SHA-256 为
`7ab99f00ca2ebf270fe470c8100e826e708984e3ffa39ea46a36231a67191bc0`，Ed25519 签名验证
通过。锁定软件负载归档 SHA-256 为
`b5d03aaf32f4868192a4622d1f27bb14867b637acba1430fbc14aac9733d01e0`，其锁文件 SHA-256
为 `2c99b528ba03e0d5e41af1458a3ca27a849f34d6f12dd8b3b637cd57ea25db53`。

回归结果：设备侧 Python 3.11 测试为 `1000 passed, 46 skipped, 5 subtests passed`；
Windows 镜像工具为 `60 tests, 8 skipped`；Linux/root 镜像工具为
`60 tests, 1 skipped`，确定性 ext4 测试通过。构建后独立离线验证和源码内容核对均通过。

## 2026-08-30 指定单卡写入结果

项目负责人明确确认用 v10 覆盖磁盘 1、介质序列号 `121220160204`。受控写卡程序在写入前
再次确认目标为 USB、非系统盘、非启动盘、容量 `31268536320` 字节且可写，并重新计算源
镜像 SHA-256。实际写入和完整回读均为 `2571108352` 字节；回读 SHA-256 为
`62cd40004f3cc5667f7350b04deace958bc755c8f34e7a98b7f98a5d49c9ff57`，与源镜像一致，
结果为 `PASS`。结构化结果见 `flash-result-disk1-121220160204-v10.json`；完整进度日志只保留
在 Git 忽略的本地制品目录。

该结果证明指定 TF 卡的已写入范围与 v10 镜像逐字节一致，但不代替首次启动行为验证。
下一步须把卡装回香橙派并通电，从空白状态重走局域网页验收、自动注册、机器验收/封存、
租户与机构分配和业务准入流程。

这是单卡开发/HIL 母镜像，不是正式量产发布：它有意携带共享秘密和默认开发密码，也没有
执行正式发布签名和审批。结构化边界与验证事实见
`hil-factory-login-time-sync-convergence-preflight-evidence.json`。
