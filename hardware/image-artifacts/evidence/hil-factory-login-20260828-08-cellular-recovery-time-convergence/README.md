# HIL 工厂母镜像 v8 离线预检证据

本目录记录 `hil-factory-login-20260828-08` 单卡开发/HIL 热修镜像的离线构建和
预检结果。含 K1 的镜像二进制只保存在 Git 忽略的
`hardware/image-artifacts/local/factory-secret/builds/` 下，不纳入版本库。

v8 以写卡和完整回读均通过的 v7 为只读母本，只替换两个文件：蜂窝链路已经处于
NetworkManager 的“已连接”状态时直接复用，避免服务重试导致 Air780E 链路重启；
可信校时固定使用同一组 chrony 时间源，并在失败前执行最多三轮受限的
`burst + waitsync`，提高冷启动和弱信号时的收敛率。K1、设置热点密钥、环境配置和
登录信息均从 v7 逐字节继承，没有重新注入，也没有在证据中记录其内容或摘要。

离线验证确认镜像分区结构和 ext4 文件系统正常，当前首次启动源码及 systemd 单元
一致，设备身份和运行状态保持空白，开发登录策略保持为“root 密码登录锁定、
orangepi 密码登录启用”。v7 与 v8 除两个受控文件外，文件内容、权限、属主、ACL、
扩展属性、硬链接和符号链接均无漂移。

这是本地单卡 HIL 热修镜像，不是正式量产发布。它有意保留 v7 的镜像内部版本和旧包
版本，当前状态仅为“可写入指定单卡并从全新状态复测”。下一步必须完成写卡全量回读、
冷启动自动接入、Air780E 拔插恢复、两个现用摄像头和模拟 MCU 成功流程验收。

完整 SHA-256、测试统计、包版本边界和未完成闸门见
`hil-factory-login-cellular-recovery-time-convergence-preflight-evidence.json`。
