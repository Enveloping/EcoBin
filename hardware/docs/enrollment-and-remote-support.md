# 香橙派自动注册与按需远程维护

本文描述硬件端已经实现的首次注册、维护 CA 和 OneNet 按需反向 SSH 边界。生产部署不再把
OneNet 密钥或管理员个人公钥逐台写入 `.env`。

## 首次启动顺序

1. 镜像把 `enrollment_bootstrap.py`、`device_enrollment.py`、
   `device_credentials.py`、`secure_files.py`、`maintenance_ssh_setup.py` 和独立虚拟环境安装到
   `/opt/ecobin-enrollment/`，把 `ecobin-enrollment.service` 安装到 systemd。
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

注册请求的密码学 transcript 与后端 `DeviceEnrollmentCrypto` 完全一致：

- canonical JSON 字段顺序固定为 `schemaVersion`、`enrollmentUid`、`challengeUid`、
  `enrollmentKeyId`、`enrollmentMode`、`hardwareSn`、`identityPublicKey`、
  `responseWrapPublicKey`、`tunnelPublicKey`、`sshHostPublicKey`；
- 签名内容为 `ecobin-device-enrollment-v1\0 || challengeNonce || canonicalRequest`；
- `registrationMac` 和 `signature` 分别是 K1 HMAC-SHA256 与设备 Ed25519 签名；
- LEGACY_ADOPTION 额外提供当前 OneNet 密钥的域分离 HMAC，SELF_ENROLLMENT 的
  `legacyProof` 为 null；
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

服务受理仍遵循现有边缘可靠性规则：先把 v2 envelope 写入 command inbox，再回复 OneNet
receipt。隧道状态使用独立 SQLite 单行槽，不占用投递/清运 work slot；状态更新与可靠事件
在同一事务提交。同一 `sessionUid` 是永久幂等身份，进入 CLOSED、FAILED 或 EXPIRED 后，
重复的 open 只能返回重复结果而不能复活；真正的新租约必须使用新的 `sessionUid`。
EdgeStore v10 只对现行 v9 做加法迁移，旧业务时代数据库仍拒绝读取。

## OpenSSH 进程边界

`RemoteSupportManager` 只用 argv 启动 OpenSSH，明确 `shell=False`。它把凭证中的隧道私钥
和服务器 Host Key 以 `0600` 临时物化到 `/run/ecobin/remote-support`，并强制：

- `StrictHostKeyChecking=yes`、`IdentitiesOnly=yes`、关闭密码/键盘交互；
- `ExitOnForwardFailure=yes` 和 ServerAlive；
- 只建立 `127.0.0.1:<租约端口> -> 127.0.0.1:22`；
- 不使用 `-N`，固定执行无参数的远端 `ecobin-lease-guard`，使服务器撤销租约时能主动结束
  已存在连接；
- 子进程 stderr 始终被读取但内存最多保留 4096 字节，日志不打印正文；
- 启动失败采用 1、2、4、8、15 秒封顶退避，连续六次失败进入 FAILED；正常 OPEN 行在进程或
  设备重启后恢复为 CONNECTING 并重连，到期或 close 后绝不重连。

## 部署校验

安装后依次执行：

```text
systemctl daemon-reload
systemctl enable ecobin-enrollment.service ecobin-hardware.service
systemctl start ecobin-enrollment.service
systemctl status ecobin-enrollment.service
sshd -t
systemctl start ecobin-hardware.service
journalctl -u ecobin-hardware.service -n 200 --no-pager
```

OneNet 端必须先导入包含以上两项服务和一项事件的候选物模型。服务器租约和
AuthorizedKeysCommand 不是设备侧职责；在服务器闭环未部署前不得开启后端远程维护功能开关。
