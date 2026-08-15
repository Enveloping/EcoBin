# EcoBin 按需反向 SSH 服务器边界

本目录提供跳板服务器的可审计安装模板和无第三方依赖的租约实现。它不会连接生产服务器，
不会修改防火墙，也不包含 CA 私钥、设备私钥、管理员私钥或真实公钥。
服务器运行时要求 systemd、OpenSSH（支持 `UnusedConnectionTimeout`）和 Python 3.11 以上。

## 安全模型

- `ecobin-tunnel` 只接受 `AuthorizedKeysCommand` 根据当前租约动态返回的设备 Ed25519
  公钥；普通 `authorized_keys` 被禁用。
- 每份租约只允许监听 `127.0.0.1` 上的一个端口，固定池为 `22011～22014`，生命周期
  最长 1800 秒。同一设备密钥出现多份有效租约时全部拒绝。
- 授权行同时使用 `restrict`、`port-forwarding`、`permitlisten`、`expiry-time` 和动态
  强制命令。设备必须执行 `ecobin-lease-guard`，不能使用 `ssh -N`。
- 强制命令确认反向监听已经出现后才原子写入 actual 标记。它每秒检查 desired 租约；
  租约删除、更换或到期时删除 actual 标记，并结束对应 sshd 会话，从而关闭已经存在的
  反向监听。
- `ecobin-jump` 只接受维护 CA 签发且包含 `ecobin-jump` principal 的短期用户证书；
  它没有 Shell/PTY，只允许本地转发到四个回环端口。
- 四个端口没有公网监听。`GatewayPorts no`、sshd 双层 `PermitListen/PermitOpen` 和防火墙
  规则共同形成纵深防御。

`UnusedConnectionTimeout 15` 会关闭没有命令通道的错误 `ssh -N` 连接。后台必须仅在
desired、actual 和设备上报三者一致后把会话置为 `OPEN` 并签发管理员证书；这样没有
守护命令的连接不会成为可用维护会话。

## 文件布局与权限

| 路径 | 所有者/模式 | 用途 |
|---|---|---|
| `/var/lib/ecobin/remote-support/desired` | backend UID:`ecobin-lease-readers` `2750` | 后端原子发布/撤销期望租约 |
| `/run/ecobin/remote-support/actual` | `ecobin-tunnel`:backend GID `2750` | 强制守护进程发布实际连接标记；重启自动清空 |
| `/etc/ecobin/remote-support/server-policy.json` | root:root `0644` | 固定 UID、路径、端口和时间上限 |
| `/etc/ecobin/remote-support/maintenance-user-ca.pub` | root:root `0644` | 仅 CA 公钥 |
| `/usr/local/libexec/ecobin-remote-support/*` | root:root，脚本 `0755` | sshd 调用的可信代码 |

desired 文件名固定为 `<port>.json`，V1 结构如下。不得增加未定义字段：

```json
{
  "schemaVersion": 1,
  "sessionUid": "11111111-1111-4111-8111-111111111111",
  "hardwareSn": "ECM0-0123456789ABCDEFGHJKMNPQ",
  "keyType": "ssh-ed25519",
  "keyBase64": "PUBLIC_KEY_BASE64_ONLY",
  "keyFingerprint": "SHA256:PUBLIC_KEY_FINGERPRINT",
  "listenHost": "127.0.0.1",
  "listenPort": 22011,
  "createdAtEpochSecond": 1786723200,
  "expiresAtEpochSecond": 1786725000
}
```

设备公钥不是秘密，但不得把私钥或 OneNet 密钥放入租约。发布算法必须是：在同一目录
创建 `0640` 临时文件，完整写入并 `fsync`，原子 `rename` 为 `<port>.json`，再 `fsync`
目录。撤销算法是核对 `sessionUid` 后 `unlink` 并 `fsync` 目录。后端容器只能读写
desired、只读 actual；不能获得服务器 root 或 `ecobin-tunnel` 权限。

`ecobin-remote-support-leasectl` 是该算法的参考实现及现场诊断工具。例如：

```bash
expires_at="$(( $(date +%s) + 1800 ))"
sudo ecobin-remote-support-leasectl publish \
  --session-uid 11111111-1111-4111-8111-111111111111 \
  --hardware-sn ECM0-0123456789ABCDEFGHJKMNPQ \
  --port 22011 \
  --public-key-file /run/ecobin/device-tunnel-key.pub \
  --expires-at-epoch "${expires_at}"

sudo ecobin-remote-support-leasectl revoke \
  --session-uid 11111111-1111-4111-8111-111111111111 \
  --port 22011 --wait-seconds 5
```

生产后端先提交数据库会话、端口槽、可靠命令和审计，再发布 desired；数据库端口槽锁仍是
并发分配和会话存在性的权威来源，文件锁只保护单机文件操作。后端协调器会从数据库重建
缺失的 desired，并删除固定四端口上没有数据库会话的无主 desired。分配一个空闲槽以前，
还必须同时确认 desired 和 actual 都不存在；actual 未消失时不能把端口交给下一台设备。

## CA 与安装

先在受控秘密目录生成一次维护 CA。命令不会打印私钥，但生成的是供自动签名使用的
无口令私钥，必须移入 `/run/secrets`、独立签名服务或 HSM，绝不能放进 Git：

```bash
sudo bash tools/remote-support/bin/generate-maintenance-ca.sh \
  --output-directory /root/ecobin-maintenance-ca \
  --acknowledge-unencrypted-private-key
```

仅把 `.pub` 交给安装器：

```bash
sudo bash tools/remote-support/bin/install-server.sh \
  --maintenance-ca-public-key /root/ecobin-maintenance-ca/maintenance-user-ca.pub \
  --backend-uid 10001 --backend-gid 10001
```

安装器会创建三个锁定密码的系统账号、安装 root-owned 辅助程序、创建 tmpfiles 规则、
安装 `sshd_config.d/60-ecobin-remote-support.conf`、执行 `sshd -t` 和有效配置检查，全部
通过后才 reload sshd。它要求发行版主配置已经包含 `sshd_config.d/*.conf`；不满足时
会停止，不会自动改写主 SSH 配置。

当前设备迁移时，旧 autossh 可能仍占用 `127.0.0.1:22011`。安装器不会停止它；启用
新调度前必须用 `ss -ltnp` 核对端口，并在数据库中暂时保留该槽，先用 `22012～22014`
验证新链路。新链路确认可用后再按旧服务的精确名称停用旧隧道，最后释放 `22011`。

签发工具用于验证后台签名参数。正式后台必须从数据库分配唯一证书 serial，并把
`valid-before` 限制为维护会话剩余时间：

```bash
now="$(date +%s)"
bash tools/remote-support/bin/sign-maintenance-certificate.sh \
  --ca-private-key /run/secrets/ecobin-maintenance-user-ca \
  --operator-public-key /tmp/operator-key.pub \
  --output-certificate /tmp/operator-key-cert.pub \
  --serial 12345 \
  --key-id platform-admin-7:session-11111111-1111-4111-8111-111111111111 \
  --hardware-sn ECM0-0123456789ABCDEFGHJKMNPQ \
  --valid-after-epoch "$((now - 30))" \
  --valid-before-epoch "$((now + 1770))"
```

证书只携带 `permit-port-forwarding` 和 `permit-pty` 扩展，principals 固定为
`ecobin-jump,ecobin-device-<hardwareSn>`。跳板 Match 块会继续拒绝 PTY；设备侧专用
`ecobin-maintenance` 账号可以使用 PTY，但应另行禁止转发并只信任同一 CA 公钥。

## 客户端连接约束

香橙派必须请求远程命令，不能用 `-N`：

```bash
ssh -T \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/etc/ecobin/remote-support/bastion-known-hosts \
  -i /etc/ecobin/remote-support/device-tunnel-key \
  -R 127.0.0.1:22011:127.0.0.1:22 \
  ecobin-tunnel@115.159.67.35 ecobin-lease-guard
```

管理员应使用本地 `~/.ssh/config` 同时给跳板和目标指定同一把私钥/短期证书，且为目标
使用后端返回的设备 Host Key 文件和稳定别名：

```sshconfig
Host ecobin-bastion
    HostName 115.159.67.35
    User ecobin-jump
    IdentityFile ~/.ssh/id_ed25519
    CertificateFile ~/.ssh/id_ed25519-ecobin-cert.pub
    IdentitiesOnly yes
    StrictHostKeyChecking yes

Host ecobin-target
    HostName 127.0.0.1
    Port 22011
    User ecobin-maintenance
    ProxyJump ecobin-bastion
    IdentityFile ~/.ssh/id_ed25519
    CertificateFile ~/.ssh/id_ed25519-ecobin-cert.pub
    IdentitiesOnly yes
    HostKeyAlias ecobin-ECM0-0123456789ABCDEFGHJKMNPQ
    UserKnownHostsFile ~/.ssh/ecobin-session-known-hosts
    StrictHostKeyChecking yes
```

随后执行 `ssh ecobin-target`。设备 Host Key 与用户证书是两套独立信任：前者确认目标
设备身份，后者确认维护人员权限，不能用 `StrictHostKeyChecking=no` 绕过。

## 防火墙与验收

监听已经被强制绑定回环，但仍建议在现有防火墙策略中显式拒绝非 loopback 流量。先核对
当前 SSH 放行规则，避免远程锁死服务器，再按所用防火墙选择一种等价规则：

```bash
# UFW 示例；先确保管理 SSH 端口已经 allow
sudo ufw allow in on lo proto tcp to any port 22011:22014
sudo ufw deny in proto tcp to any port 22011:22014

# nftables 规则含义示例，需合并进现有持久规则集
iifname != "lo" tcp dport 22011-22014 drop
```

部署后执行：

```bash
sudo ecobin-remote-support-verify
bash tools/remote-support/tests/run-tests.sh
```

验收至少包括：错误密钥无授权、错误端口失败、第五个数据库会话被拒、actual 出现后才
签证书、删除 desired 后五秒内 actual 消失且端口不可连接、证书过期/错误 principal/
错误 CA 失败、无数据库行的 desired 会被清理、旧会话的迟到状态不能删除复用该端口的新
desired，以及 `ss -ltnp` 中四个端口只可能出现 `127.0.0.1`。

本工具不会直接写防火墙，也不会替代真实 Linux 上的 sshd 集成验收；仓库测试覆盖租约
解析、授权行、权限拒绝、守护撤销和 CA 证书内容。
