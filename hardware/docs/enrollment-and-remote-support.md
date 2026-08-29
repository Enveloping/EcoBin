# 香橙派自动注册与按需远程维护

本文描述硬件端已经实现的首次注册、维护 CA 和 OneNet 按需反向 SSH 边界。生产部署不再把
OneNet 密钥或管理员个人公钥逐台写入 `.env`。

## 首次启动顺序

1. 镜像把 `enrollment_bootstrap.py`、`device_enrollment.py`、
   `device_credentials.py`、`secure_files.py`、`maintenance_ssh_setup.py`、
   `remote_support_credentials.py` 和独立虚拟环境安装到 `/opt/ecobin-enrollment/`。另把
   `remote_support_agent.py`、`remote_support_control.py`、`remote_support_store.py`、
   `remote_support.py`、`device_credentials.py`、`secure_files.py` 及其独立虚拟环境安装到
   `/opt/ecobin-remote-support/`。三个 systemd 单元分别是注册、远程维护和普通硬件服务；
   更新 `/root/EcoBin/hardware` 不会覆盖远程维护代理的运行副本。
2. 生产环境初始化时只随机生成一次至少 32 字节的全局 K1；各台尚未注册的设备写入同一个
   Base64 值，并以 `0600` 保存为 `/etc/ecobin/enrollment.key`，后端为 key ID `K1` 配置同一
   原始密钥。K1 不是逐台生成的注册码，也不依赖工厂注册名额或生产时限；短期 challenge
   只用于阻止网络重放。工厂可由受控量产镜像或烧录后的 provisioning 脚本自动写入，无需
   操作员逐台录入；仓库、对外镜像和构建日志不得保存真实 K1。
3. `/etc/ecobin/enrollment.env` 只设置 HTTPS 后端地址和
   `SELF_ENROLLMENT`。systemd 在网络和系统校时目标就绪后启动一次性注册服务，硬件主服务通过
   `Requires/After` 等待它成功。
4. 注册进程先将 enrollment UID、Ed25519 设备身份、X25519 响应包装密钥、Ed25519
   隧道密钥、SSH Host Key 和完整已签名请求原子写入
   `/var/lib/ecobin/enrollment-state.json`。断电后继续使用完全相同的身份和请求。
5. READY 响应经 X25519 + HKDF-SHA256 + AES-256-GCM 验证后，加入仅存在于本机的
   `tunnelIdentityPrivateKey`，以 `0600` 原子写入
   `/etc/ecobin/device-credentials.json`，fsync、复读并完整校验。
6. 只有上一步成功后才依次删除 K1、临时状态，以及 `/opt/ecobin-enrollment/` 和运行目录中
   可能存在的两份 `device_enrollment.py`；LEGACY_ADOPTION 还会删除一次性读取的旧 OneNet
   proof 密钥文件。永久保留的
   bootstrap supervisor 每次开机都会验证正式凭证并补做未完成的清理，因此在“凭证已落盘、
   K1 尚未删除”之间断电也能收敛。
7. `maintenance_ssh_setup.py` 创建 `ecobin-maintenance`，安装统一维护 CA、设备唯一
   principal、sudoers 和 sshd drop-in，先执行 `visudo -cf`、`sshd -t` 再
   `reload-or-restart ssh.service`。该账号不接受密码或长期
   `authorized_keys`，不允许 SSH 转发，只允许短期 CA 证书登录和受审计的免密 sudo。
8. `remote_support_credentials.py` 创建无登录权限的 `ecobin-remote` 系统账号，从正式凭证
   投影出只含 `hardwareSn` 和隧道配置的 `/etc/ecobin/remote-support-credentials.json`。
   投影文件保持 `root:root 0600`；systemd 的 `LoadCredential=` 在启动时把副本交给低权限
   代理，因此代理既不需要穿越 `/etc/ecobin`，也看不到 OneNet 设备密钥。

注册请求的密码学 transcript 与后端 `DeviceEnrollmentCrypto` 完全一致：

- canonical JSON 字段顺序固定为 `schemaVersion`、`enrollmentUid`、`challengeUid`、
  `enrollmentKeyId`、`enrollmentMode`、`hardwareSn`、`identityPublicKey`、
  `responseWrapPublicKey`、`tunnelPublicKey`、`sshHostPublicKey`；
- 签名内容为 `ecobin-device-enrollment-v1\0 || challengeNonce || canonicalRequest`；
- `registrationMac` 和 `signature` 分别是 K1 HMAC-SHA256 与设备 Ed25519 签名；
- LEGACY_ADOPTION 额外提供当前 OneNet 密钥的域分离 HMAC，SELF_ENROLLMENT 的
  `legacyProof` 为 null；
- LEGACY_ADOPTION 必须通过 `ECOBIN_LEGACY_HARDWARE_SN` 明确沿用存量资产的
  `hardwareSn`（也就是现有 OneNet 设备名）；只有 SELF_ENROLLMENT 才从新注册公钥派生
  `ECM0-...` 编号；
- 202/PENDING 只重试已持久化的原请求，200/READY 才解密，422/FAILED 不删除 K1。

正式凭证包含后端下发的 `assetUid`、`hardwareSn`、`modelCode=EC-M0`、
`expectedPortCount=1`、完整 OneNet/MQTT 配置、公开设备入口 URL，以及隧道服务器固定
Host Key、维护 CA 和设备 principal。旧设备迁移期间仍允许完整的三项 OneNet 环境变量；
禁止只混用其中一部分。迁移完成后删除三项旧变量。

## OneNet 远程维护契约

- `openRemoteSupportTunnel`：v2 command type 为 `OPEN_REMOTE_SUPPORT_TUNNEL`，target 是
  `REMOTE_SUPPORT_SESSION/sessionUid`，payload 固定为 `sessionUid`、`remotePort` 和
  `expiresAt`。端口只能是 22011～22014，payload 到期时间必须等于命令 envelope 到期时间。
- `closeRemoteSupportTunnel`：type 为 `CLOSE_REMOTE_SUPPORT_TUNNEL`，payload 只有同一
  `sessionUid`。
- `remoteSupportTunnelStatus`：可靠事件 type 为 `REMOTE_SUPPORT_TUNNEL_STATUS`，payload
  为 `sessionUid`、`state`、`remotePort`、可空 `failureCode`；状态只有 CONNECTING、OPEN、
  CLOSED、FAILED、EXPIRED，target 固定为 `DEVICE_ASSET/hardwareSn`，失败码只能取硬件代码
  中的封闭集合且最长 64 字符。

服务受理仍遵循现有边缘可靠性规则：普通硬件进程先把 v2 envelope 写入 command inbox，
再回复 OneNet receipt；随后通过只允许 root 客户端的 Unix Domain Socket（本机进程间通信
文件）把 OPEN/CLOSE 意图交给独立代理。代理是隧道单行槽和 OpenSSH 子进程的唯一所有者，
使用 `/var/lib/ecobin/remote-support/state.db`，不读取也不写入业务 `edge.db`，并且不建立
第二条 OneNet MQTT 连接。

代理每次改变 CONNECTING、OPEN、CLOSED、FAILED 或 EXPIRED 状态时，会在自己的 SQLite
事务中同时写入状态事实队列。普通硬件进程恢复运行后，先把该事实按同一个 `eventUid`
幂等导入 EdgeStore 的可靠 event outbox，再通知代理删除队列记录。因此普通硬件进程停止、
更新或崩溃期间，既不会结束已建立的 SSH 隧道，也不会丢失尚未上报 OneNet 的状态。同一
`sessionUid` 是永久幂等身份，进入 CLOSED、FAILED 或 EXPIRED 后，重复 open 只能返回
重复结果而不能复活；真正的新租约必须使用新的 `sessionUid`。EdgeStore v10 中原有隧道槽
只保留给首次升级迁移和旧数据库兼容，新的运行链路不再写它。

## OpenSSH 进程边界

`ecobin-remote-support.service` 以 `ecobin-remote` 低权限账号运行。其内部
`RemoteSupportManager` 只用 argv 启动 OpenSSH，明确 `shell=False`。它把 systemd 临时
凭证中的隧道私钥和服务器 Host Key 以 `0600` 物化到
`/run/ecobin/remote-support`，并强制：

- `StrictHostKeyChecking=yes`、`IdentitiesOnly=yes`、关闭密码/键盘交互；
- `ExitOnForwardFailure=yes` 和 ServerAlive；
- 只建立 `127.0.0.1:<租约端口> -> 127.0.0.1:22`；
- 不使用 `-N`，固定执行无参数的远端 `ecobin-lease-guard`，使服务器撤销租约时能主动结束
  已存在连接；
- 子进程 stderr 始终被读取但内存最多保留 4096 字节，日志不打印正文；
- 启动失败采用 1、2、4、8、15 秒封顶退避，连续六次失败进入 FAILED；代理进程或整机重启
  后，未到期的 OPEN 行恢复为 CONNECTING 并重连，到期或 close 后绝不重连。

封版后的 nftables 输入、输出链都保持默认拒绝。只有远程维护凭据和 `ecobin-remote`
系统账号同时存在时，蜂窝协调器才允许该账号 UID 向凭据解析出的跳板 IPv4 和 SSH 端口
发起连接；返回方向也只允许所选 RNDIS 接口上来自同一 IPv4、以该 SSH 端口为源端口的
数据。这条例外不适用于 `orangepi`、普通硬件进程或其他本机账号，也不允许以设备 22
端口为目标的新入站连接。服务器地址和 Host Key 仍由受保护凭据固定，因此防火墙限制
不能替代 OpenSSH 的主机身份校验。H616/Air780E 真机链路会把部分 SSH SYN 和返回数据误判
为 conntrack `invalid`，上述两个窄例外必须位于各自方向的 `invalid` 丢弃规则之前。

普通硬件进程是 OneNet 命令和状态上报的唯一入口，但不再持有 OpenSSH 子进程。只更新代码并
执行 `systemctl restart ecobin-hardware.service` 时，现有 SSH 连接和反向监听保持不变，因而
可以继续通过该连接完成检查或回滚；重启 `ecobin-remote-support.service`、停止注册依赖、
重启整机或修改代理自身代码仍会造成一次短断，代理随后只在原期限内重连。

如果更新的是 `first_boot/cellular_firewall.py`，部署事务必须重启
`ecobin-cellular-uplink.service` 和 `ecobin-first-boot.service`，然后等待至少一个 15 秒
蜂窝协调周期并复核规则标记。首次启动协调器每 3 秒执行封存清理，也会加载并重放生产
防火墙；只替换文件或只重启蜂窝服务，会让仍持有旧模块的常驻进程覆盖新规则。该重启会
使运行目标及 MQTT 短暂恢复一次，不能在重启窗口内把离线显示当作稳态结果。

这里保证的是“已经建立的隧道”不随普通硬件进程重启。普通硬件进程停止期间没有 OneNet
命令消费者，平台不能让设备新开一条会话，代理的新状态也只会留在本机队列中等待转发；但
管理员关闭或会话到期时，服务器撤销 desired 租约后，远端 lease guard 仍能独立结束旧
监听，不能因为设备主进程离线而延长维护授权。

后端看到已开放会话的服务器 actual 标记因设备或服务器重启而暂时消失时，会把平台会话
置为 `RECONNECTING`，但保留原 desired、端口、证书和到期时间。硬件端不需要识别这个
后端内部状态；它只需按照 SQLite 中尚未到期的 open 意图重连并重新上报 `OPEN`。管理员
关闭、管理员公钥撤销或到期仍会撤销 desired，硬件端不得用旧 open 意图复活会话。

## 部署校验

安装后依次执行：

```text
systemctl daemon-reload
systemctl enable ecobin-enrollment.service ecobin-remote-support.service ecobin-hardware.service
systemctl start ecobin-enrollment.service
systemctl status ecobin-enrollment.service
sshd -t
systemctl start ecobin-remote-support.service
systemctl status ecobin-remote-support.service
systemctl start ecobin-hardware.service
journalctl -u ecobin-remote-support.service -n 200 --no-pager
journalctl -u ecobin-hardware.service -n 200 --no-pager
```

远程维护单元使用 `Type=notify`：只有私有 Socket 已监听后才向 systemd 报告 READY，硬件服务
才会继续启动。首次从“OpenSSH 子进程属于硬件主进程”的旧版本切换时，先停止旧硬件服务，
把 `edge.db` 中尚未到期的会话一次性导入代理私有库，再启动代理和新版硬件服务；这一刀会
让活动隧道短断并在原期限内重连。完成切换后，日常硬件代码更新不再需要重复迁移。

OneNet 端必须先导入包含以上两项服务和一项事件的候选物模型。服务器租约和
AuthorizedKeysCommand 不是设备侧职责；在服务器闭环未部署前不得开启后端远程维护功能开关。
