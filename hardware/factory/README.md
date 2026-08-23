# EcoBin 出厂热点、本地门户与离线整机验收（P4/P7）

本目录实现 P4 的整机网络隔离与出厂热点，以及 P7 的离线整机验收。它不是生产设备程序：
验收不建立投递/清运订单，不写 EdgeStore，不启动 MQTT/COS，也不访问后端。root 验收执行器
独占 UART5、BOOT0/NRST 和两个摄像头；低权限网页只能通过固定 AF_UNIX 本地接口请求一组
白名单动作，不能传入设备路径、串口帧或 shell 参数。

## 网络安全边界

- `ecobin-factory-egress-lock.service` 是唯一随系统早期启动的 P4 单元。它没有
  `ConditionPathExists=`：无论设备是否已有封存事实，网络启动前都必须先建立可验证的紧急
  全拒绝规则；规则建立失败会使 `network-pre.target` 失败，不会被 systemd 当作“条件不满足而
  成功跳过”。封存后的正常生产联网必须由 P5 编排器先校验权威持久事实，再原子切换到生产
  网络策略，不能靠跳过本服务获得一个无防火墙窗口。
- Debian 12 的 nftables 1.0.6 没有 `destroy table`。`firewall.py` 先用 JSON 查询私有表是否
  存在，再在一个 `nft -f` 事务中按需 `delete table` 并完整 `add table`，不会保留旧链、旧规则
  或旧 flowtable。三个 `inet` 基础链都位于优先级 `-150`（在 conntrack 之后）且默认拒绝，
  因而 IPv4、IPv6 和转发均失败关闭。
- 正式出厂规则只允许 `wlan0` 上的 DHCP、`10.42.0.1:53` 本地 DNS、
  `10.42.0.1:80` 门户以及 loopback。每条规则都有固定唯一标记；加载后再通过 `nft -j` 验证
  表、三条基础链、hook、优先级、默认策略和全部规则标记。正式规则检查、加载或事后验证任意
  一步失败，都会重建并重新证明紧急全拒绝规则。
- `network.py` 只接受 `wlan0 / 10.42.0.1/24 / 10.42.0.20～100`。dnsmasq 不使用上游
  DNS、不监听 loopback、不提供 IPv6 DHCP/路由通告，所有 DHCP 回复均广播且不做 ICMP
  探测，因此专用非 root 进程只需要绑定低端口的能力。
- root 的 `ap_supervisor prepare` 是短时、可重复的准备步骤：明确关闭 IPv4/IPv6 转发，
  禁用 `wlan0` IPv6，先 `down`，再执行 `nomaster` 脱离可能存在的网桥，清空地址并设置固定
  IPv4 地址。长驻的 hostapd、dnsmasq 和就绪监控分别以三个非登录账号运行。

## 热点、权限与就绪语义

- 热点密码只从 `/etc/ecobin/setup-ap.key` 的单行、root 私有且禁止跟随符号链接的文件读取。
  运行时 hostapd 配置位于 `/run/ecobin/factory-network/hostapd/hostapd.conf`，只允许
  hostapd 账号读取；dnsmasq 的配置目录由 root 控制且只读，租约写入另一个专用 `0700`
  状态目录，长驻进程既不能修改启动配置，也不能读取热点密码。
- `ecobin-factory-ap-prepare.service` 只持有 `CAP_CHOWN/CAP_NET_ADMIN`：准备进程先临时
  持有 dnsmasq 的 `0700` 状态目录并原子写入租约文件，落盘后才把目录移交给专用账号，
  因此不需要 `CAP_DAC_OVERRIDE` 或 `CAP_FOWNER`；
  `ecobin-factory-hostapd.service` 只持有 `CAP_NET_ADMIN/CAP_NET_RAW`；
  `ecobin-factory-dnsmasq.service` 只持有 `CAP_NET_BIND_SERVICE`；长驻就绪监控无任何能力。
  dnsmasq 已由 systemd 直接以目标账号启动，因此不需要 `CAP_SETUID/CAP_SETGID`。
- `ap_supervisor monitor` 只有在 `wlan0` 同时为 UP、具有精确的 `10.42.0.1/24`、无线类型为
  AP，且 `10.42.0.1:53` 的 TCP DNS 连续通过三次探测后，才向 systemd 上报 `READY=1`。
  门户依赖这个就绪事实；后续健康探测失败会使监控退出并触发失败重启，而不是继续展示一个
  实际不可用的验收入口。
- `config/sysusers.d` 声明 web、hostapd、dnsmasq 三个专用非登录账号，`config/tmpfiles.d`
  声明 root 控制的运行目录。`config/NetworkManager/conf.d` 禁止自动生成默认连接，并把
  `wlan0` 固定标记为 unmanaged。镜像集成还必须 mask 发行版自带的 `hostapd.service`、
  `dnsmasq.service` 和 `wpa_supplicant@wlan0.service`；P4 单元中的 `Conflicts=` 是运行时第二道
  防线，不代替镜像安装动作。

## 门户与验收权限边界

`portal.py` 只绑定 `10.42.0.1:80`。静态页面、状态和当前 nonce 对应的两张临时验收照片使用
`GET/HEAD`；唯一动作入口是 `POST /api/v1/acceptance/action`。动作要求同源 `Origin`、固定
自定义请求头、`application/json`、4 KiB 请求体上限、无重复 JSON 键和精确字段集合。网页只能
调用 root 服务的 `/run/ecobin/factory-test/control.sock`；套接字为
`root:ecobin-factory-web 0660`，每次物理动作还受 revision（状态修订号）、非阻塞互斥锁、显式
人工确认和已完成幂等判断保护。并发动作返回忙，不会排队后偷偷执行；旧 revision 只有在目标
动作已由持久事实证明完成时才返回幂等成功；当前 revision 的重复完成动作同样只返回既有事实。
服务端强制校验当前 `allowedActions`，核心执行器还会按已持久化的 `sendAttempts` 二次阻止重复
写帧，不能通过手工构造请求绕过网页步骤。

门户不能读取 EdgeStore、正式凭证、K1、热点密码或日志。Host、Origin、请求方法和路径均为
白名单；请求行、请求头、请求体、并发数、连接超时和客户端速率有固定上限。所有资源位于镜像
本地，无 CDN、外链字体、分析脚本或公网请求。

`status.json` 是 P5 首启状态投影；`acceptance.json` 是 P7 验收投影。二者均由 root 使用临时
文件 + `fsync` + 原子替换生成 `root:ecobin-factory-web 0640` 文件。P7 只投影步骤状态、稳定
结果码、必要测量值、MCU 固件身份白名单和当前相机 nonce，不复制完整 `state.json`、
`report.json`、日志、环境变量、任意 MCU 字段或凭证。临时照片放在 tmpfs 的
`/run/ecobin/factory-test/photos`；确认、超时或执行器重启即作废，不能成为生产照片。

热点监控进程也不读取 `/var/lib/ecobin`。root 首启编排器另行原子生成
`/run/ecobin/factory-network/ap-allowed.json`，其中只有 schema、是否允许和稳定状态码三个
字段。低权限监控只读该投影；root 的 AP prepare 则在自己的 mount namespace 中只读绑定
EdgeStore 目录，使用与 P8 相同的严格 sealed + SQLite authorization 校验。即使
`sealed.json` 丢失，只要数据库已经是 `SEALING/SEALED`，手工启动热点也会失败关闭；EdgeStore
从未暴露给 web 账号。

## 离线验收状态机

验收报告同时绑定当前 `releaseId`、普通硬件配置 SHA-256 摘要和 MCU revision 2 固件身份。
顺序为 F3/F1、三阶段稳定称重、双摄像头拍摄后人工确认、F2 后 STM32 ROM 只读探测、模拟投递、
模拟清运和最终报告。ROM 探测必须得到 STM32F103C8 的 device ID `0x0410`，且永不写 Flash。

投递/清运的动作帧只发送一次，任何“可能已经发送”的断电或超时都进入
`RECOVERY_REQUIRED`，不会自动重发。投递收到 DD 并完成 F3/F1/串口静默复查后仍保留锁，操作员
必须重新观察投口机械结构、周围人员和阻挡物，并通过独立的“投递区域恢复安全”动作确认；发送
BB+AA 前的安全勾选不能复用。清运收到 EF 后同样先复位、验证原 F3 身份、F1 自检和串口静默，
再由操作员独立确认舱门已关闭。两种动作的事后确认未完成或硬件复查失败时，均阻止后续清运、
最终报告和封存；不确定发送经安全恢复并确认后，本轮验收终止为 `FAILED`，只能显式开始新一轮。
只有 `ARMED + sendAttempts=0` 已持久化证明命令未发送的崩溃，才允许在同一轮重新测试。

## 安装与启动顺序

镜像集成器需把代码安装到 `/opt/ecobin/factory-test/current/app/factory`，安装 sysusers、
tmpfiles、NetworkManager 配置和全部 static 单元，只 enable 早期 egress lock。首次启动编排器
只能在确认尚未封存后启动 `ecobin-factory.target`；即使错误请求在封存后发生，root 准备步骤
也会明确失败，热点不会被 systemd 记成一次成功的条件跳过。

```text
ecobin-factory-egress-lock.service
  -> ecobin-mcu-safe-gpio.service
  -> ecobin-factory-ap-prepare.service
       -> ecobin-factory-hostapd.service
       -> ecobin-factory-dnsmasq.service
  -> ecobin-factory-ap.service（无能力监控，READY 后继续）
  -> ecobin-factory-portal.service
  -> ecobin-factory-test.service（root，AF_UNIX 命令端，独占 UART/GPIO）
```

P7 通过后可启动 Air780E、注册和正式业务；正式运行只与
`ecobin-factory-test.service` 冲突，因此热点/网页仍保留以展示封存授权。只有 P8 在后端授权且
操作员明确确认后才能停止整个 `ecobin-factory.target` 并清理热点密钥。已封存冷启动由 P5
直接恢复蜂窝与正式业务，不重新创建热点。

## 自动测试与真机门禁

当前自动测试覆盖配置生成、无条件失败关闭、nft 调用顺序与 JSON 事后证明、紧急规则恢复、接口
准备命令、热点就绪判定、HTTP/AF_UNIX 权限边界、验收状态机、断电恢复、动作幂等和 systemd
权限声明。开发环境是 Windows，WSL 中没有
`nft`、hostapd、dnsmasq 或真实 `wlan0`，因此本次没有声称完成真实内核 network namespace
集成。`systemd-analyze verify` 已用于检查单元语法，但开发机缺少目标镜像中的账号、可执行文件
和挂载权限，不能替代 Debian 12 镜像启动验证。

发布前必须在 Orange Pi Zero 3 硬件在环（HIL）环境完成以下门禁：

1. 使用 Debian 12 镜像实际执行 `nft -c`、规则加载和 JSON 验证，并故障注入旧表、语法错误、
   加载失败和事后规则篡改；确认失败后只有 loopback 可通信；
2. 验证 hostapd/dnsmasq 以声明账号和最小能力启动，wlan0 不属于任何 bridge，IPv4/IPv6
   forwarding 均为 0，且发行版通用网络服务全部处于 masked/inactive；
3. 未插 SIM、蜂窝离线、蜂窝在线三种状态下，手机均可连接 WPA2 热点并访问
   `http://10.42.0.1/`，DHCP 续租、DNS 和门户长连接/慢请求均符合限额；
4. 热点客户端不能访问设备 22/1883/其他端口，不能经蜂窝或以太网转发上网；设备自身不能
   发出 DNS、HTTP(S)、MQTT、NTP 或其他 WAN 流量；
5. 反复冷启动并注入 hostapd、dnsmasq、wlan0 和门户故障，确认门户只在真实 READY 后出现；
6. 实测 UART5 revision 2、500g±10g、双摄像头角色、STM32 `0x0410` 只读 ROM 探测、投递和
   清运；DD 后重新确认投递机构和区域安全，EF 后重新确认清运门关闭；在所有破坏性帧边界断电，
   确认不自动重发、恢复锁不提前解除且不确定动作只能在新验收轮次重测；
7. 验证完整序列：未封存启动热点/网页/验收；验收通过后蜂窝、注册和正式业务启动而热点仍在；
   P8 授权封存后热点关闭；已封存冷启动只进入蜂窝和正式业务。

上述 HIL 全部通过前，只能把 P4 标记为“软件实现和软件回归测试完成”，不能批准量产镜像。
