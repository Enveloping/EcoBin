# HIL 工厂母镜像 v11 完整重建离线预检证据

## 结论

`hil-factory-login-20260830-11` 已从锁定的 Orange Pi Debian 1.0.4 原始镜像、当前包锁、
当前软件负载锁和干净提交 `1cf3b4aeb572281e1f2de7acf281aacaab61ada0` 完整重建。
无密钥候选和含 K1 的单卡开发/HIL 成品都已通过离线只读预检；指定 TF 卡的写入和完整
回读也已通过。当前仍没有形成 v11 空白冷启动、自动注册或业务流程证据。

本版包含提交 `58c885dc` 的 Chrony 权限与可观测性修复。镜像内四个首次启动 Python
文件、两个门户静态文件以及 `ecobin-cellular-uplink.service` 均与构建提交逐字节一致。
蜂窝服务精确保留 `CAP_DAC_OVERRIDE + CAP_NET_ADMIN`，镜像中其他服务没有获得
`CAP_DAC_OVERRIDE`。

## 构建身份

- ARM64 运行时 `hardware-runtime-20260830-11` 为 `58275221` 字节，SHA-256 为
  `7eda57a66f65e005425aba932821dd6df1e90323ed6b3cfebd3453d6056a115a`，Ed25519
  签名验证通过，归档身份指向提交 `1cf3b4ae`；
- 软件负载 `software-payload-20260830-11` 的锁文件 SHA-256 为
  `667eefa887221c6c5f3192563358494519d35b19b5c1fc362ab43392bace3cd0`，逐文件清单
  共 5019 项，五个组件均为 v11；
- 无密钥候选大小为 `2571108352` 字节，SHA-256 为
  `e4bb77dbc5bc6b5dcfd17bdf76049af9603aecc6d822e5f29dfce7c4616de119`；
- 单卡开发/HIL 成品大小为 `2571108352` 字节，SHA-256 为
  `ba40b1e3157f95f204462ae42148450d3d4f5f81dfdaac58edaef8b063c01684`；
- 成品只从已资格化 v10 镜像逐字节继承 K1、设置热点密钥和 `orangepi` 开发密码散列。
  v10 来源在操作前后均保持 SHA-256
  `62cd40004f3cc5667f7350b04deace958bc755c8f34e7a98b7f98a5d49c9ff57`。

镜像二进制包含共享秘密和默认开发密码，只保存在 Git 忽略的
`hardware/image-artifacts/local/factory-secret/builds/hil-factory-login-20260830-11-chrony-sandbox/`
中，不纳入版本库。公开证据不记录受保护值或它们的单独摘要。

## 离线验证结果

- 锁定输入、分区结构、确定性 ext4 配置和只读 `e2fsck` 通过；构建归一化 53012 个
  候选 inode，受保护输入覆盖后归一化 53014 个 inode；
- 无密钥候选的独立 `--candidate` 验证通过，候选保持 K1、热点密钥和设备凭据缺失；
- 专用 HIL 验证以退出码 0 通过：K1、热点密钥和开发密码散列与 v10 来源逐字节一致，
  root 密码保持锁定，`orangepi` 默认开发登录启用；
- machine-id、SSH 主机密钥、设备凭据、注册状态、NetworkManager、Chrony、首次启动、
  硬件数据库和远程维护状态均为空；HSK/UNIQUESKY 外摄和 icSpring 内摄配置不变；
- 成品 sidecar 与实际 SHA-256 一致，候选和 v10 受保护输入来源在构建及复核后均未变化；
- 针对本次修复的内容审计确认六个源码文件和一个 systemd 单元逐字节一致，且
  `CAP_DAC_OVERRIDE` 未扩散给其他服务。

设备侧 Python 3.11 全量回归为 `1018 passed, 46 skipped, 5 subtests passed`；Windows
镜像工具回归为 `60 tests, 8 skipped`；Linux/root 镜像工具回归为
`60 tests, 1 skipped`，确定性 ext4 测试通过。这些测试在代码提交 `58c885dc` 后完成；
当前镜像提交 `1cf3b4ae` 在其上只增加脱敏证据和文档。

## 构建编排注意项

Windows PowerShell 把内存中的 Bash 脚本送入 WSL 时，在文本末尾追加了一个独立回车。
第一次覆盖和第一次专用复核都已经输出各自最终 `PASS`，随后才因该 `\r` 得到退出码 127；
循环设备和临时挂载由 trap 正常清理。成品随后从头完成摘要、sidecar、循环设备、只读
文件系统、受保护输入、空白状态和源码内容复核。最后使用 `tr -d '\r'` 清洗标准输入后，
同一专用 HIL 验证以退出码 0 完成。以后从 PowerShell 流式执行 Bash 时必须在执行前统一
换行，不能只根据最后一个包装退出码判断前面的镜像检查是否执行。

通用正式 `--sealed` 验证器会要求 `root` 和 `orangepi` 都禁止密码登录，因此按设计拒绝
本开发/HIL 镜像。这里使用的是更严格匹配当前制品类别的 HIL 规则：root 必须锁定，
`orangepi` 散列必须与批准来源一致。该镜像不得因此标记为正式封存或量产发布。

## 指定单卡写入结果

项目负责人明确确认用 v11 覆盖磁盘 1、介质序列号 `121220160204`。受控程序在写入前再次
确认目标为 USB、非系统盘、非启动盘、容量 `31268536320` 字节且可写，并重新计算源镜像
SHA-256。2026-08-30 10:55:32 UTC 开始写入，11:00:35 UTC 完成；实际写入和完整回读均为
`2571108352` 字节，回读 SHA-256 为
`ba40b1e3157f95f204462ae42148450d3d4f5f81dfdaac58edaef8b063c01684`，与源镜像一致，
结果为 `PASS`。结构化结果见
[flash-result-disk1-121220160204-v11.json](flash-result-disk1-121220160204-v11.json)；逐块进度日志
只保存在 Git 忽略的机密制品目录。

该结果只证明指定 TF 卡的已写入范围与 v11 镜像逐字节一致，不代替启动行为验证。

## 剩余门禁

下一步把卡装回香橙派并通电，从空白状态独立验证：P7 网页验收后无需人工系统命令即可取得
可信时间、完成 HTTPS 注册、注册 OneNet 并建立 MQTT；随后再走机器验收/封存、租户与机构
分配和业务准入。正式发布签名与量产信任策略仍是独立门禁。

结构化脱敏事实见
[hil-factory-login-chrony-sandbox-preflight-evidence.json](hil-factory-login-chrony-sandbox-preflight-evidence.json)。
