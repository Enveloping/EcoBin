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
  `ecobin-factory-dnsmasq.service` 只持有提供非 root DHCP 所需的
  `CAP_NET_BIND_SERVICE/CAP_NET_ADMIN`；长驻就绪监控无任何能力。
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

首启状态还包含经过净化的蜂窝重试进度，热点页在联网失败时显示连续失败次数和下一次自动
检测倒计时。封存控制器用一次有界查询检查云端通信、设备更新、两项升级授权、设备管理自检、
日常业务、蜂窝联网和远程维护共八项常驻服务；网页使用中文业务名称逐项展示，并与“允许结束
出厂模式”的门禁复用同一份检查结果。查询超时、返回不完整或格式异常时全部显示为无法确认，
同时阻止封存，不能把未知状态当成正常。

热点监控进程也不读取 `/var/lib/ecobin`。root 首启编排器另行原子生成
`/run/ecobin/factory-network/ap-allowed.json`，其中只有 schema、是否允许和稳定状态码三个
字段。低权限监控只读该投影；root 的 AP prepare 则在自己的 mount namespace 中只读绑定
EdgeStore 目录，使用与 P8 相同的严格 sealed + SQLite authorization 校验。即使
`sealed.json` 丢失，只要数据库已经是 `SEALING/SEALED`，手工启动热点也会失败关闭；EdgeStore
从未暴露给 web 账号。

## 离线验收状态机

验收报告同时绑定当前`releaseId`、普通硬件配置SHA-256摘要和UART v2 MCU固件身份/能力。
顺序为原生身份、配置及设备事实、三阶段称重、双摄像头拍摄后人工确认、条件化升级线记录、
投递、清运和最终报告。MCU身份必须来自`QUERY_DEVICE_IDENTITY`，能力位包含必要掩码
`0x8100`；设备事实必须来自同一当前启动和已应用配置。开始时操作员仍须如实选择BOOT0/NRST
线是否安装；当前ECOBIN_UART固件不支持通过rc.26远程更新，未形成另行批准的升级链路时
报告`mcuRemoteUpdateCapable=false`，不得借历史F2或ROM探测把它标为可用。

### 称重标准与逐步测量展示

热点网页可在采集空载前填写测试物的**已知重量，单位为整数克**，默认 500 g，支持例如
400 g、1000 g。软件允许 11～350000 g：下限须大于固定的 10 g 容差，避免“不增加重量”也
能通过；上限来自协议范围，不是设备的额定承重，操作员不得据此超载。输入值只用于验收比较，
不写称重模块、MCU 校准系数或生产业务计价参数。

执行器将参考重量与本次空载基准一起持久化，此后加载和取下步骤不能改变标准。验收比较
`加载稳定值 − 空载稳定值` 与 `参考重量 ±10 g`；取下后还必须回到空载值 ±10 g。任一步失败
均阻止后续动作；重新采集空载会清除上一组称重数据并开始新一组，不能混用新旧读数。
默认 500 g 的旧页面确认字段及已有结果码保持兼容，自定义重量使用通用确认字段及结果码；
不同参考重量的重复请求不能被当成同一操作的幂等成功。

报告生成、首次启动、UART交接及封存校验共用本轮参考值规则，不能在下一步重新限定为
500g。安装配置中的500g保留为兼容默认值及硬件配置摘要的一部分；改本轮参考值应在网页
采集空载前填写，不改`hardware.env`。操作员填写的数值就是本轮声明的比较标准，允许为了
流程验收按当前读数填写；报告必须原样保留该标准和样本，使审核者能够区分“流程通过”和
“称重准确度已验证”。这个输入不会校准硬件，不能据此宣称称重模块准确。后端只校验报告
摘要与授权绑定，没有另一个500g门禁。

网页显示空载、加载、取下的稳定值、中位数采样规则、参考范围、重量差、偏差、各阶段最后
最多32次有效原生设备事实重量回复及有效回复总数。连续不稳定或后续查询超时也保留已获得的数值；
没有读数显示“尚未采集”，不是 0。这里的数值是 MCU 自检回复中的克数，**不是称重模块原始
寄存器或 ADC 数据，也不能证明每个回复对应一轮新的传感器转换**。当前 MCU 存在多次平均，
放置或取下物体后应等待至少 3 秒再点采集；实际准确度与模块校准仍需实物复测。

同页还展示 MCU 自检重量/红外/烟雾、相机图像是否非空和角色人工确认、升级线只读探测结果、
动作发送次数、投递/清运前后重量和安全确认。页面只读取白名单投影，不增加轮询串口指令，
不会显示任意日志、设备凭证、原始文件路径。最终报告保存所选参考重量和有界采样记录。

双摄角色使用稳定的 `/dev/v4l/by-id` 候选：外摄首选 HSK/UNIQUESKY、缺失时回退到旧
DECXIN，内摄首选 Generic USB Camera、缺失时回退到旧 icSpring。新旧型号同时存在时始终
使用首选；任一角色的候选都不存在时，拍摄失败并阻止验收继续，不能借用另一角色的摄像头。

历史`factory-sim-1.0.0 / 45434f53494d3031`、F3/F1、AA/DD和EE/EF仅用于兼容测试，不能
生成rc.26的P7通过报告或证明真实外设质量。

投递/清运各自只写出一次原生START。MCU自主完成称重、屏幕按钮和本地控制；验收器只查询
同一作业的完整`WORK_RESULT`，可靠保存后发送身份绑定的`RESULT_SAVED`并取得回复。任何“可能
已经写出START”的断电或超时都进入`RECOVERY_REQUIRED`，不会构造另一条START；重启后只查询
原命令和原结果，不用按钮、过程重量或动作片段重建成功。投递完成后操作员独立确认机构和区域
安全，清运完成后独立确认门已关闭。事后确认或当前身份/设备事实复查失败会阻止后续动作、最终
报告和封存。只有持久事实证明START零字节写出时，才允许同轮执行原动作。

串口屏采用统一的简化可信边界：完整页面、初始化、实时重量或二维码指令组原子进入UART3
软件发送队列即视为已显示；队列不足时零字节发布、不推进页面状态并重试。不等待USART完成、
串口屏回执或屏幕读回；现场未显示归为HMI工程、接线或屏幕硬件故障并返工。

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
6. 实测UART5原生身份/`0x8100`能力、配置/设备事实、本轮参考重量及全部样本、双摄像头角色、
   一次投递/清运START、完整结果保存确认、HMI和机构；动作后分别确认投递区域安全及清运门
   关闭；在START写出和结果保存确认边界断电，确认不构造第二条START且恢复锁不提前解除；
7. 验证完整序列：未封存启动热点/网页/验收；验收通过后蜂窝、注册和正式业务启动而热点仍在；
   P8 授权封存后热点关闭；已封存冷启动只进入蜂窝和正式业务。

上述 HIL 全部通过前，只能把 P4 标记为“软件实现和软件回归测试完成”，不能批准量产镜像。
