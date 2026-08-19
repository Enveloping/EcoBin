# 设备自注册、厂家验收与按需反向 SSH 部署手册

> 对应 V52。本文给出生产控制面的安装和启用顺序，不包含任何真实密钥。架构和状态语义见
> [V52 设计说明](../architecture/device-enrollment-factory-acceptance-remote-support-v52.md)。

## 1. 启用前检查

必须同时满足：

- 目标数据库由受控 schema owner 前向迁移到 V52，结果为 52 条成功迁移、112 张领域表、
  76 条权限定义；
- 后端镜像包含 OpenSSH 客户端，用于短期证书签发；
- OneNet 真实模式的产品 ID、Access Key、MQTT 地址及北向订阅已经验证；
- 跳板服务器可运行 systemd、Python 3.11+ 和支持 `UnusedConnectionTimeout` 的 OpenSSH；
- 公网只保留原 SSH 管理入口，`22011～22014` 不对公网监听；
- 服务器、后端和香橙派时间同步；
- 已安排一台可返工的香橙派和一组真实 EB1 标签做试点。

默认保持：

```text
deviceEnrollmentEnabled=false
remoteSupportEnabled=false
```

先安装全部控制面和秘密，再逐项开关。不要一边迁移数据库一边允许设备注册。

## 2. 生成和保管两类全局秘密

### 2.1 厂家注册 K1

在受控服务器或密码库导出目录生成至少 32 个随机字节的 Base64 值，命令不要把结果打印到
终端或日志：

```bash
sudo install -d -o root -g root -m 0700 /etc/ecobin/secrets
sudo sh -c 'umask 077; openssl rand -base64 32 > /etc/ecobin/secrets/device-enrollment-key-k1'
```

后端源文件固定为 `/etc/ecobin/secrets/device-enrollment-key-k1`，`root:root 0600`。同一个值
经受控写卡/镜像工具写入未验收设备的 `/etc/ecobin/enrollment.key`，设备端同样为
`root:root 0600`。不得加入 Git、发布包、普通 `.env`、工单截图或日志。

### 2.2 维护用户 CA

```bash
sudo bash tools/remote-support/bin/generate-maintenance-ca.sh \
  --output-directory /root/ecobin-maintenance-ca \
  --acknowledge-unencrypted-private-key
sudo install -o root -g root -m 0600 \
  /root/ecobin-maintenance-ca/maintenance-user-ca \
  /etc/ecobin/secrets/remote-support-maintenance-user-ca
```

私钥供后端自动签发短期证书，不能复制到香橙派或管理员电脑。`.pub` 公钥交给跳板安装器，
也通过设备注册的加密响应下发给香橙派。

## 3. 安装跳板 SSH 边界

先核对当前 SSH 配置和防火墙，避免锁死远程服务器，再执行：

```bash
sudo bash tools/remote-support/bin/install-server.sh \
  --maintenance-ca-public-key /root/ecobin-maintenance-ca/maintenance-user-ca.pub \
  --backend-uid 10001 --backend-gid 10001
sudo ecobin-remote-support-verify --skip-listener-check
```

安装器创建 `ecobin-tunnel`、`ecobin-jump` 和租约读取账号，安装动态授权/强制命令，创建：

- `/var/lib/ecobin/remote-support/desired`：后端 UID 10001 可发布租约；
- `/run/ecobin/remote-support/actual`：隧道强制命令写入，后端只读；
- `/etc/ecobin/remote-support/server-policy.json`：固定四端口和权限策略。

防火墙必须显式拒绝非 loopback 到 `22011:22014`。旧 autossh 仍可能占用 22011，切换前用
`ss -ltnp` 查清并先以其他槽验证；不要粗暴结束名称不明的 SSH 进程。

## 4. 安装生产 Compose 控制文件

除既有文件外，新增安装：

```bash
sudo install -o root -g root -m 0644 \
  deploy/production/docker-compose.remote-support.yml \
  /etc/ecobin/compose/docker-compose.remote-support.yml
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-target-app-compose.sh \
  /usr/local/sbin/ecobin-target-app-compose
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-stage-runtime-secrets.sh \
  /usr/local/sbin/ecobin-stage-runtime-secrets
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-production-preflight.sh \
  /usr/local/sbin/ecobin-production-preflight
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-runtime-secret-probe.sh \
  /usr/local/sbin/ecobin-runtime-secret-probe
sudo install -o root -g root -m 0644 \
  tools/deployment/systemd/ecobin-target-app.service \
  /etc/systemd/system/ecobin-target-app.service
sudo systemctl daemon-reload
```

`ecobin-target-app-compose` 读取 `remoteSupportEnabled`。为 `false` 时只使用主 Compose；为
`true` 时自动叠加 remote-support 文件，把 desired 读写挂载、actual 只读挂载给后端。
不要绕过该入口手工启动另一套容器。

## 5. 后端非秘密配置

在 `/etc/ecobin/runtime.env` 的真实外联配置中补齐：

```text
deviceEnrollmentEnabled=true
deviceEnrollmentKeyId=K1
deviceEnrollmentModelCode=EC-M0
deviceEnrollmentExpectedPortCount=1

remoteSupportEnabled=true
remoteSupportTunnelHost=跳板服务器域名或固定地址
remoteSupportTunnelSshPort=22
remoteSupportTunnelUser=ecobin-tunnel
remoteSupportJumpUser=ecobin-jump
remoteSupportTunnelServerHostPublicKey=跳板服务器精确的 ssh-ed25519 Host Key
remoteSupportMaintenanceCaPublicKey=维护 CA 的精确 ssh-ed25519 公钥
remoteSupportSignerCaPrivateKeyPath=/run/secrets/remote-support/maintenance-user-ca
remoteSupportLeaseDesiredDirectory=/var/lib/ecobin/remote-support/desired
remoteSupportLeaseActualDirectory=/run/ecobin/remote-support/actual
```

先按实际值写好但保持两个功能开关为 `false`。`remoteSupportTunnelServerHostPublicKey` 必须
从受控渠道取得并核对指纹，不能在首次连接时使用 `accept-new` 或关闭 Host Key 校验。
两个 SSH 公钥配置都必须只保留 `ssh-ed25519` 和 Base64 公钥体这两段，不能保留
`root@host` 等末尾注释；生产预检会按后端使用的规范格式拒绝带注释的值。

## 6. 暂存秘密、预检和启动

开关仍为 `false` 时，秘密暂存器会确认运行目录中不残留对应秘密。打开开关后执行：

```bash
sudo systemctl restart ecobin-stage-runtime-secrets.service
sudo /usr/local/sbin/ecobin-production-preflight
sudo systemctl reload ecobin-target-app.service
sudo /usr/local/sbin/ecobin-runtime-secret-probe
```

暂存结果为：

- `/run/ecobin-secrets/backend/deviceEnrollmentKeyK1`，`root:10001 0440`；
- `/run/ecobin-secrets/backend/remote-support/maintenance-user-ca`，`root:10001 0440`。

预检会验证开关、秘密存在/不存在条件、CA 私钥可由 `ssh-keygen` 解析、租约目录、Compose
叠加文件及非秘密配置。`0440` 中的组读权限是后端 UID 10001 读取 CA 私钥的预期方式；
应用签名器允许组只读，但仍拒绝组写、组执行和其他用户的任何权限。运行探针确认后端容器
能读所需文件、不能看到 root/数据库 owner 等其他秘密。

## 7. 制作香橙派注册包

将 `hardware/` 代码部署到三个职责目录，三个目录不能通过同一个“当前版本”软链接一起
切换：

- 普通硬件程序保持 `/root/EcoBin/hardware`；
- 一次性注册程序安装到 `/opt/ecobin-enrollment`，其中增加
  `remote_support_credentials.py`；
- 独立远程维护程序安装到 `/opt/ecobin-remote-support`，至少包含
  `remote_support_agent.py`、`remote_support_control.py`、`remote_support_store.py`、
  `remote_support.py`、`device_credentials.py` 和 `secure_files.py`。该目录和 Python 3.11
  虚拟环境由 root 管理，运行账号 `ecobin-remote` 只有读取代码的权限。它只需要锁定版本的
  `cryptography` 及其传递依赖，不安装 MQTT、串口、相机或 COS 组件。

安装 systemd 单元：

- `hardware/ecobin-enrollment.service` → `/etc/systemd/system/`；
- `hardware/ecobin-remote-support.service` → `/etc/systemd/system/`；
- 更新后的 `hardware/ecobin-hardware.service` → `/etc/systemd/system/`；
- `hardware/enrollment.env.example` 的现场副本 → `/etc/ecobin/enrollment.env`；
- 厂家 `K1` → `/etc/ecobin/enrollment.key`，`0600`。

`ecobin-remote-support.service` 使用 systemd `LoadCredential=` 隔离隧道私钥，目标系统的
systemd 必须支持该指令（建议 248 或更高；Debian 12/Ubuntu 22.04 及更新版本满足）。版本
过旧时应先升级系统，不能通过放宽 `/etc/ecobin` 或凭证文件权限来绕过。

现场配置至少指定 HTTPS 后端地址：

```text
ECOBIN_ENROLLMENT_BACKEND_URL=https://生产域名
ECOBIN_ENROLLMENT_KEY_ID=K1
ECOBIN_ENROLLMENT_MODE=SELF_ENROLLMENT
```

接管已存在于平台和 OneNet 的设备时改用 `LEGACY_ADOPTION`，并同时指定该资产当前的
`hardwareSn`（即现有 OneNet 设备名）及只含旧 OneNet 设备密钥的 `0600` 文件：

```text
ECOBIN_ENROLLMENT_MODE=LEGACY_ADOPTION
ECOBIN_LEGACY_HARDWARE_SN=现有设备名
ECOBIN_LEGACY_ONENET_SECRET_FILE=/etc/ecobin/legacy-onenet-secret
```

注册成功后，设备会删除旧密钥文件；部署脚本还应从普通硬件环境中删除旧的 product ID、
device name 和 device key 三项，避免正式凭证与旧环境变量并存。

然后执行：

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/ecobin-enrollment.service \
  /etc/systemd/system/ecobin-remote-support.service \
  /etc/systemd/system/ecobin-hardware.service
sudo systemctl enable ecobin-enrollment.service \
  ecobin-remote-support.service ecobin-hardware.service
sudo systemctl start ecobin-hardware.service
```

硬件服务会等待一次性注册成功以及远程维护代理的本地控制 Socket 就绪。注册服务为
`Restart=on-failure`，断网或突然断电后自动使用已保存状态重试；正式凭证存在时只验证并
继续清理，不会再向后端创建资产。注册后的 `ExecStartPost` 会创建 `ecobin-remote` 账号，
并生成只含隧道身份、不含 OneNet 密钥的 root-only 凭证投影。

注册成功后逐项检查：

```bash
sudo test -s /etc/ecobin/device-credentials.json
sudo test ! -e /etc/ecobin/enrollment.key
sudo test ! -e /var/lib/ecobin/enrollment-state.json
sudo test "$(stat -c '%U:%G %a' /etc/ecobin/remote-support-credentials.json)" \
  = "root:root 600"
sudo systemctl is-active ecobin-remote-support.service
sudo systemctl is-active ecobin-hardware.service
sudo test -S /run/ecobin/remote-support/control.sock
```

还要核对注册实现的两个生产副本已经删除。仓库开发副本仍可存在于开发机；离厂设备不能
保留可直接配合 K1 批量注册的生产副本。

### 7.1 存量设备首次切换

旧版本把 OpenSSH 子进程和隧道状态放在普通硬件进程中。每台存量设备只执行一次以下切换：

1. 先部署并校验 `/opt/ecobin-remote-support`、凭证投影脚本和三个新 unit 文件，但不要同时
   运行新旧两个隧道管理器；
2. 停止旧 `ecobin-hardware.service`，确认其 Python 和 OpenSSH 子进程已经退出；
3. 以 root 运行一次 `remote_support_credentials.py`，再执行
   `remote_support_agent.py --migrate-only --legacy-edge-store
   /root/EcoBin/hardware/data/edge.db`。同时显式指定正式凭证、
   `/var/lib/ecobin/remote-support/state.db`，并在命令结束后把该状态目录递归设为
   `ecobin-remote:ecobin-remote`、移除组和其他用户权限；
4. 启动 `ecobin-remote-support.service`，确认 READY 和 Socket 后，再启动新版
   `ecobin-hardware.service`；
5. 若旧库中有尚未到期的活动会话，迁移会把它作为 CONNECTING 接管并使用原端口、原期限
   重连；终态或已过期会话不会复活。迁移命令可重复执行，代理库一旦初始化便不会覆盖。

默认路径下第 3 步的完整命令是：

```bash
sudo /opt/ecobin-enrollment/.venv/bin/python \
  /opt/ecobin-enrollment/remote_support_credentials.py
sudo /opt/ecobin-remote-support/.venv/bin/python \
  /opt/ecobin-remote-support/remote_support_agent.py \
  --credentials /etc/ecobin/remote-support-credentials.json \
  --state /var/lib/ecobin/remote-support/state.db \
  --legacy-edge-store /root/EcoBin/hardware/data/edge.db \
  --migrate-only
sudo chown -R ecobin-remote:ecobin-remote \
  /var/lib/ecobin/remote-support
sudo chmod -R go-rwx /var/lib/ecobin/remote-support
```

若现场 `.env` 改过 `ECOBIN_EDGE_STORE_PATH`，必须把上述 legacy 路径替换成设备实际路径；
迁移前先确认目标是单台设备的 `edge.db`，不能指向仓库根目录或其他设备的数据目录。

停止旧硬件服务会切断它持有的旧隧道。如果部署人员正是通过该隧道操作，必须改用现场串口、
同局域网 SSH，或者先把完整且已校验的幂等切换脚本交给 `systemd-run` 脱离当前终端执行；
不能在交互会话中先停止服务、再指望同一连接继续输入后续命令。切换完成后，仅更新普通硬件
代码和重启 `ecobin-hardware.service` 不再影响现有隧道。升级代理自身、重启整机或停止
`ecobin-enrollment.service` 仍可能短断并在原会话期限内重连。

回退到旧架构前必须先关闭平台活动会话、确认服务器 desired/actual 均已撤销，再停止独立
代理；不得让旧主进程和独立代理同时争用同一个反向端口。代理私有库产生过新会话后，也不能
把未同步的旧 EdgeStore 隧道行当作回退事实源。

## 8. 厂家验收操作

1. 平台管理员在 Web“厂家操作员”中新建独立工号；这个工号没有 Web 登录权限；
2. 为该工号生成有效期 5 分钟的一次性官方微信小程序码，让对应厂家人员用本人微信扫描；
3. 绑定成功后，同一个小程序会自动进入隐藏的“设备出厂端”，用户端和清运端没有入口；
4. 扫描设备永久二维码；若厂家微信从外部直接扫描设备码，也应进入出厂端并加载该设备；
5. 按页面投口顺序逐个扫描已经实际安装的 EB1 袋码；如果页面显示“待实体补扫”，同码扫描
   执行核验，实体袋与旧记录不同时走更正并填写原因；
6. 若扫错，立即使用更正并填写具体原因；
7. 所有投口完成后观察自动验收，不手工修改 `PENDING`；
8. 只有自动证据变为 `PASSED` 后才允许进入永久租户/机构分配流程。

如果同一微信还具备普通用户或清运身份，可从出厂端点“切换身份”；普通端不会显示返回
出厂端的入口，明确退出普通身份后才恢复厂家身份优先。停用厂家操作员会立即解绑微信并
撤销其厂家会话，重新启用后必须生成新的绑定码。

## 9. 远程维护试点

管理员本地生成一把 Ed25519 密钥，私钥留在本机：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ecobin-maintenance
```

在 Web 只粘贴 `.pub`，随后：

1. 选择一台 OneNet 在线的试点设备，开启 5 分钟会话；
2. 确认 Web 状态按 `CONNECTING → OPEN` 收敛；
3. 保存页面给出的短期证书和 known_hosts 行，并使用自己的私钥连接；
4. 核对目标 Host Key 指纹、设备 `hardwareSn`、服务器 actual 标记和后端会话一致；
5. 点击关闭，确认五秒内 actual 消失、回环监听不可再连接、端口槽释放；
6. 保持终端连接时只重启 `ecobin-hardware.service`，确认同一 SSH 会话和反向监听均未断开，
   普通硬件进程恢复后还能继续转发代理队列中的状态事实；
7. 香橙派断电后确认 Web 进入 `RECONNECTING` 且端口和原到期时间不变；重新通电后应回到
   `OPEN`，不能变成新的 30 分钟会话；
8. 再测试独立代理重启、服务器重启、证书过期、错误 principal、错误 Host Key、撤销管理员公钥和第五个
   并发会话；关闭离线设备时也应在 actual 消失后及时释放端口。

真实验收详细安全项见 `tools/remote-support/README.md`。

## 10. 回退和故障处理

V52 是前向数据库迁移，不提供 down migration。出现问题时：

1. 先把 `deviceEnrollmentEnabled=false`，阻止新挑战和注册续作；已注册设备继续使用自己的
   OneNet 凭证，不需要恢复 K1；
2. 把 `remoteSupportEnabled=false` 前先关闭活动会话并确认 desired/actual 清空，再重启
   秘密暂存和应用服务；
3. 禁用后暂存器会删除运行时 K1/CA 私钥挂载，Compose 入口不再叠加租约目录；
4. 厂家验收异常时保留设备、标签占用和更正审计，不直接改数据库状态；排除问题后按同一
   接口重试或用更正操作；
5. 注册处于 `PENDING` 时允许同一设备状态重试；`FAILED` 是终态，需要依据安全失败码处理，
   不要删除数据库行后重新冒充新设备；
6. 绝不通过手工把远程会话改为 `OPEN`、手工签长期证书、开放公网端口或关闭 SSH Host Key
   校验来绕过故障。

回退旧应用前仍要确认旧应用能容忍 V52 前向结构；数据库版本不能因为关闭功能而降回 V51。
