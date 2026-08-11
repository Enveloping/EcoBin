# 香橙派反向 SSH 隧道

`reverse_ssh_tunnel.sh` 让香橙派主动连接公网跳板服务器，并在服务器回环地址上建立一个
通往香橙派本机 SSH 的端口。现场网络不需要公网 IP，也不需要在路由器上做端口映射。

脚本只使用 Linux 当前默认路由，不绑定 `wlan0` 或物联网卡网卡。因此从 Wi-Fi 切到物联网卡
时，只需修改系统默认出口；旧连接断开后，脚本会按默认 10 秒间隔自动重连。

## 三个端口不要混淆

| 名称 | 示例 | 谁使用 | 含义 |
|---|---:|---|---|
| 服务器 SSH 入口端口 | `22` 或 `2222` | 香橙派主动连接 | 必须能从现场网络访问 |
| 服务器反向端口 | `22023` | 管理电脑经跳板访问 | 只监听服务器 `127.0.0.1` |
| 香橙派 SSH 端口 | `22` | 隧道最终目标 | 固定为香橙派 `127.0.0.1:22` |

服务器同一个反向端口同时只能属于一台香橙派。旧设备仍在使用 `22023` 时，新设备必须分配
另一个端口；旧设备永久退役后，可以撤销旧密钥并把 `22023` 重新分配给新设备。

## 脚本配置

默认值与当前调试服务器一致：

```text
ECOBIN_REVERSE_SSH_SERVER_HOST=115.159.67.35
ECOBIN_REVERSE_SSH_SERVER_PORT=22
ECOBIN_REVERSE_SSH_SERVER_USER=ecobin-tunnel
ECOBIN_REVERSE_SSH_REMOTE_PORT=22023
ECOBIN_REVERSE_SSH_IDENTITY_FILE=/etc/ecobin/reverse-ssh/id_ed25519
ECOBIN_REVERSE_SSH_KNOWN_HOSTS_FILE=/etc/ecobin/reverse-ssh/known_hosts
```

当前 Wi-Fi 实测可以访问服务器 HTTPS，但无法访问服务器 `22`。正式启用隧道前，需要在
服务器、云安全组和主机防火墙同时开放一个可从现场网络访问的 SSH 入口，例如 `2222`，然后
通过 systemd 环境文件覆盖 `ECOBIN_REVERSE_SSH_SERVER_PORT=2222`。不要让 SSH 与当前 Nginx
直接争用 `443`。

## 首次供应密钥

隧道使用独立密钥。它只用于“香橙派连接跳板并创建指定反向端口”，不能复用管理员登录
香橙派的私钥，也不能提交到 Git：

```bash
install -d -m 700 /etc/ecobin/reverse-ssh
ssh-keygen -t ed25519 -N '' \
  -f /etc/ecobin/reverse-ssh/id_ed25519 \
  -C 'ecobin-reverse-tunnel'
chmod 600 /etc/ecobin/reverse-ssh/id_ed25519
```

只把生成的 `.pub` 公钥交给服务器管理员。服务器的 `authorized_keys` 应使用
`restrict,port-forwarding,permitlisten="127.0.0.1:22023"` 限制该密钥，并且
`sshd_config` 中保持：

```text
Match User ecobin-tunnel
    AuthenticationMethods publickey
    AllowTcpForwarding remote
    GatewayPorts no
    PermitListen 127.0.0.1:22023
    PermitTTY no
    MaxSessions 0
```

`known_hosts` 必须通过可信管理连接核对服务器 ED25519 指纹后再写入。脚本使用
`StrictHostKeyChecking=yes`，不会在无人值守时自动信任一个未知服务器。

## 本地检查和运行

```bash
# 不访问网络，只检查参数、私钥权限、固定指纹和 OpenSSH 参数
hardware/tools/reverse_ssh_tunnel.sh --check

# 只打印命令；不会读取私钥内容，也不会连接网络
hardware/tools/reverse_ssh_tunnel.sh --dry-run

# 建立一段连接，连接断开后退出，适合首次联调
hardware/tools/reverse_ssh_tunnel.sh --once

# 持续运行，断线后自动重连
hardware/tools/reverse_ssh_tunnel.sh --run
```

脚本使用单实例锁。同一台香橙派重复启动同一反向端口时，第二个进程会直接失败，避免两个
调试进程互相争抢。

## systemd 示例

生产式常驻运行应由 systemd 启动，而不是使用 `nohup`。下面是示例；环境文件只保存地址、
端口和密钥路径，不保存私钥正文：

```ini
[Unit]
Description=EcoBin reverse SSH debug tunnel
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=root
EnvironmentFile=-/etc/ecobin/reverse-ssh.env
ExecStart=/root/EcoBin/hardware/tools/reverse_ssh_tunnel.sh --run
Restart=on-failure
RestartSec=10
UMask=0077

[Install]
WantedBy=multi-user.target
```

管理电脑最终通过服务器访问香橙派时，管理员私钥仍保留在管理电脑上，不复制到服务器：

```sshconfig
Host ecobin-bastion
    HostName 115.159.67.35
    User ubuntu

Host ecobin-orangepi
    HostName 127.0.0.1
    Port 22023
    User root
    ProxyJump ecobin-bastion
    HostKeyAlias ecobin-orangepi-via-bastion
```

然后执行：

```bash
ssh ecobin-orangepi
```
