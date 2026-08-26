# EcoBin 首次启动与 Air780E 编排（P5/P7/P8）

本目录实现失败关闭（任何关键事实缺失时都不放行业务）的首次启动编排。它每次启动都重新读取
真实系统事实，不能靠修改进度文件跳过验收、注册、串口交接或封存门禁。

## 事实与状态边界

`/var/lib/ecobin/first-boot/state.json` 只是可重建的进度索引，不是授权事实。每轮编排会重新核对：

- `/etc/ecobin/image-release.json` 的 `releaseId`、`/etc/machine-id` 和普通硬件配置摘要；
- P7 的私有状态与最终报告，并要求报告同时绑定当前发布、硬件配置、MCU revision 2 固件身份；
- Air780E 唯一 USB 父设备下的 `rndis_host` 接口、DHCP 地址、所选默认路由，以及严格绑定该
  接口的 DNS/HTTPS 探测；
- 可信时间、设备凭证、UART 安全交接事实和 P8 封存事实。

状态、报告或 seal 路径只要存在损坏、符号链接、越界大小或不一致内容，就进入明确错误或恢复
状态，不会重新开放一个已封存设备的热点。私有索引以 `0600` 原子写入；网页只读取 root 原子
生成的 `root:ecobin-factory-web 0640` 脱敏投影。热点监控只读取三字段的
`/run/ecobin/factory-network/ap-allowed.json`，不会为了判断封存状态获得 EdgeStore 权限；root
AP prepare 使用严格 sealed + SQLite authorization 复核，数据库已进入 `SEALING/SEALED` 但
marker 丢失时也永久拒绝热点。

## Air780E schema v2

`config/cellular.env.example` 使用 schema v2。Air780E 按 RNDIS 网卡接入，普通设备启用自动 APN；
USB VID/PID 只作为现场诊断信息，不是准入配置，因此同一合格采购批次不需要逐台填写 VID/PID。
生产准入实际要求：

- `ECOBIN_CELLULAR_USB_DRIVER=rndis_host`、`ECOBIN_CELLULAR_USB_PROFILE=RNDIS`；
- 系统只能选出一个属于目标 USB 父设备的 RNDIS 接口，不能回退到 Wi-Fi、以太网或猜测
  `usb0`；
- DHCP、默认路由、DNS、HTTPS 均在该接口上成功，且 nftables 规则仍禁止转发；
- `ECOBIN_CELLULAR_HIL_APPROVED=true`，并把两个 `HIL_REQUIRED` 探测目标替换为量产批次已验证
  的固定公网 IPv4 和 HTTPS 健康地址。

HIL（硬件在环）批准不是逐台配置，也不要求读取模组固件版本。它表示当前 Orange Pi 镜像、
Air780E 载板/USB 枚举和运营商网络组合已经在真机上验证。模板故意保持
`ECOBIN_CELLULAR_HIL_APPROVED=false`，防止未验证的镜像误入量产。

候选蜂窝规则会在一个 nftables 事务中替换现有私有表，三个基础链均为默认拒绝；只允许所选
RNDIS 接口上的 DHCP、DNS、NTP、HTTPS、MQTT 以及已建立连接，不开放 SSH，也不接受其他
接口。规则检查、应用或事后 JSON 验证任一步失败，都会恢复并重新证明紧急全拒绝规则。

## P7 真实验收与 UART 交接

`ecobin-factory-test.service` 已不是占位服务。它以 root 运行
`factory.acceptance_service`，独占 UART5 与 GPIO，通过权限为 `0660` 的 AF_UNIX 本地套接字
只向低权限网页暴露窄命令接口。验收全程离线，不打开 EdgeStore、MQTT、COS 或后端连接：

1. 校验 MCU F3 身份为固定帧 revision 2，并用 F1 验证硬件自检；
2. 空载、500g、移除砝码三个阶段都要求连续 3 个样本、最大离散 2g，500g 误差为 ±10g；
3. 两个固定 `/dev/v4l/by-id` 摄像头先生成带 nonce 的临时照片，操作员看过后再确认角色；
4. F2 后只读探测 STM32 ROM，并明确要求 STM32F103C8 的 device ID `0x0410`，随后复位回原
   应用并再次核验 F3/F1；不写 Flash；
5. 模拟投递和清运只验证本机硬件，不建单、不写生产数据库、不上传图片、不访问后端。投递
   的 DD 结果和复位后 F3/F1/静默检查全部通过后，网页才允许操作员重新观察并确认投口驱动机构、
   投口和周围区域安全；清运的 EF 结果通过相同硬件复查后，才允许独立确认舱门已关闭。两个
   动作前的安全勾选都不能代替动作后的现场事实；
6. 所有可能已经发送的破坏性动作帧均不会自动重试。掉电或结果不确定时保持
   `RECOVERY_REQUIRED` 和维护锁，只有明确恢复动作重新复位、核验身份与 F1 后才能继续。

注册完成后，`ecobin-factory-handoff.service` 独占同一 UART 锁，将 BOOT0 恢复到应用启动位、
复位 MCU、清空残帧，再把当前 F3/F1 与 P7 报告中的身份逐项比较；只有匹配才原子写入
`handoff-safe.json`。正式硬件服务必须同时通过运行门禁，不能和验收执行器争抢 UART。

## systemd 生命周期

只有 `ecobin-first-boot.service` 需要由镜像 enable；其余单元由编排器按事实启动。未封存时的
预期序列是：

```text
紧急网络锁 -> 出厂热点/本地网页 -> P7 离线验收
P7 PASSED -> Air780E 蜂窝 -> 可信时间 -> 设备注册 -> UART 安全交接 -> 正式业务
后端 P8 授权 + 操作员确认 -> 原子写入 sealing 事实 -> 关闭出厂热点/网页 -> 完成清理与 sealed
已封存冷启动 -> 校验 sealed/清理事实 -> Air780E -> UART 交接复核 -> 正式业务
```

P7 通过后，验收执行器会因正式运行的冲突关系停止，但热点和网页仍保留，供操作员查看状态并
完成后端授权封存；只有 P8 封存控制器可以停止整个 `ecobin-factory.target`。首次启动编排器
不会提前关闭热点，也不会用本地按钮替代后端授权。已封存设备冷启动绝不重开热点。

UART 安全交接还会把本地 PASSED 报告中的 `mcuRemoteUpdateCapable` 与报告摘要原子写入
`/var/lib/ecobin/device-capabilities.json`。正式运行时只信任这个文件：值为 `false`
不阻止本地业务、OneNet 或封存，但 MCU 升级命令会在下载固件、发送 F2 或建立维护锁前
终态拒绝为 `MCU_REMOTE_UPDATE_UNAVAILABLE`。

`ecobin-cellular-uplink.service`、`ecobin-factory-test.service`、
`ecobin-factory-handoff.service`、`ecobin-runtime-gate.service` 和
`ecobin-runtime.target` 都是静态单元，没有 `[Install]`；已有注册、硬件和远程支持服务的
drop-in 会重复检查门禁。

## 发布前仍需完成的 HIL 门禁

自动测试覆盖配置解析、状态门禁、断电重入、幂等命令、报告绑定、nftables 失败恢复和 systemd
声明，但不能替代真机。量产前仍须在 Debian 12 / Orange Pi Zero 3 / Air780E / 两款指定摄像头 /
STM32F103C8T6 上验证：

- 冷启动热点、网页、UART5、BOOT0（物理 7 号脚/wPi 2）和 NRST（物理 11 号脚/wPi 5）；
- 500g 砝码、红外、舱门、投递/清运动作，DD 后投递区域安全确认、EF 后清运门关闭确认，以及
  人为断电后保持恢复锁且不重发动作的安全恢复；
- Air780E 自动 APN、唯一 RNDIS 选择、绑定接口的公网探测和所有故障分支的紧急断网；
- P7 通过至 P8 授权封存的完整生命周期，以及已封存多次冷启动只进入蜂窝/正式业务。

这些真机门禁通过前，只能说明软件实现与自动回归通过，不能据此批准量产。
