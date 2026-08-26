# 香橙派可重复量产镜像与首次启动编排计划

> 状态：已于 2026-08-22 获得整体实施授权。P1～P10 的仓库内软件、测试入口和量产手册
> 已实现并完成首轮 Windows/Linux/Java/契约集成回归。32 GB 实卡布局、
> Orange Pi/STM32/Air780E/双摄硬件在环验收和首台整机签认尚未完成，因此 layout、目标介质
> 资格和 formal-release policy（正式发布策略）继续保持 `UNLOCKED`/`UNQUALIFIED`；真实 K1、
> 热点密码、签名私钥、正式签名、真机写卡和设备放行仍禁止执行。
>
> 2026-08-26 增补：BOOT0/NRST 远程升级线改为逐台显式选择的可选
> 能力。未安装的设备仍可完成离线验收、注册、业务和封存，但必须在报告、
> 能力文件和平台资产上记录为不具备远程升级，并拒绝所有自动升级命令。
> 新增独立 `factory-sim-1.0.0` MCU 固件用于缺少传感器/屏幕/执行器时的流程
> 联调；人工验收操作仍然正常进行，模拟来源必须显著告警、人工再确认并永久入报告。
>
> 目标基线：Orange Pi Zero 3 v1.2（H618）+ 官方 Debian 12 Server / Linux 6.1 +
> Python 3.11 + 标称 32 GB TF 卡 + Air780E + EcoBin 主板 `ECOBIN_MAINBOARD_V1.1` +
> STM32F103C8T6。

## 1. 背景、目标与非目标

当前香橙派代码已经具备一次性设备注册、OneNet 正式凭证落盘、独立反向 SSH、固定帧 MCU
通信和 MCU 远程升级能力，但部署仍以代码目录、手工 `.env`、现场安装依赖和手工启用
UART5 为主。该方式适合单机联调，不足以证明几十张 TF 卡来自同一套输入，也不能保证
第一次启动在物联网卡尚未联网、硬件异常或掉电后自动收敛。

本计划的目标是形成以下闭环：

1. 从锁定的 Orange Pi 基础镜像、仓库提交、依赖锁和受控秘密，重复生成同一量产镜像；
2. 在镜像中固化 UART5、普通硬件程序、远程维护程序、首次注册程序和统一配置；
3. 尚未结束出厂模式的新设备通电后立即开启隔离热点和本地网页，不依赖物联网卡、后端或
   可信校时即可执行离线硬件验收；
4. 在网页完成真实执行器参与的“离线投递硬件测试”和“离线清运硬件测试”，但不创建订单、
   清运记录、袋关系、钱包变动、生产 EdgeStore 事实，也不访问 OneNet、COS 或业务后端；
5. 用可断电恢复的状态机依次完成安全启动、离线硬件验收、物联网卡上行验证、校时、注册、
   K1 清理、正式运行时启动、后端机器验收授权和出厂模式封存；
6. 设备离厂前证明 K1 已删除、MCU 为 revision 2、两个摄像头和本轮声明的
   硬件证据模式已完成；已安装升级线的设备还必须证明该线路可用，未安装则
   必须明确记录为不具备远程升级能力；
7. 输出可验签、可追溯、可重新烧录的正式镜像发布物。

本文同时记录目标设计、已落地的软件入口和仍未取得的真机证据。软件单测、虚拟机或模拟器
结果不能替代 Orange Pi、STM32、摄像头、Air780E 和真实 TF 卡的硬件在环验收。

## 2. 已确认决策与需求映射

| 编号 | 已确认需求 | 本计划的落点 |
|---|---|---|
| 1 | 使用标称 32 GB TF 卡制作可重复构建的香橙派镜像 | 新增锁定输入、Linux 构建器、紧凑固定镜像、首次启动扩容、两次构建一致性验证和只读镜像审计 |
| 2 | UART5 固化进镜像 | 基础镜像固定启用 `ph-uart5`，启动后必须出现 `/dev/ttyS5` |
| 3 | 统一普通硬件配置安装方式 | 使用 `/opt/ecobin/hardware/releases/<release-id>`、原子 `current` 链接和 `/etc/ecobin/hardware.env` |
| 4 | Air780E 物联网卡联网，通电验收时直接开启热点和网页 | 现有 Air780E 载板插卡后提供的 USB RNDIS 作为正式上行；不要求人工选择或升级模组固件；未封存设备每次启动固定开启隔离验收热点 |
| 5 | 可靠首次启动编排 | 新增按事实推进、原子持久化、可重复执行的 first-boot 状态机 |
| 6 | 洁净封存但不加首启防克隆 | 构建阶段清除设备唯一状态并只读审计；运行时不新增克隆侦测 |
| 7 | K1 放在镜像中并保证离厂删除 | 最终封存阶段注入 K1；正式凭证持久化后沿用现有可靠逻辑删除；离厂门禁核验路径不存在。TF 闪存不可证明的物理残留和制品持有者可读取 K1 的风险单独列为正式发布决策 |
| 8 | 安装远程升级能力时，BOOT0 接物理 7 号针；NRST 经 2N7002 开漏级接物理 11 号针 | 固化为 PC9/wPi 2 和 PC6/wPi 5；香橙派两路动作均为高有效，MOS 只负责拉低 NRST；未安装时验收跳过 F2 mode 02/ROM 探测并锁死云端升级 |
| 9 | 新 MCU 预装 revision 2 | 本地预检严格查询 F3；不是 revision 2 时禁止正式运行时启动和离厂 |
| 10 | 已确定继续使用当前稳定的两款摄像头，但保留路径可配置 | 固定现有两个型号及 `/dev/v4l/by-id` 默认路径，放入统一配置并由逐台功能预检验证，不再作为待选项，也不自动互换内外摄像头 |
| 11 | 无需 SSH 的出厂预检 | 手机网页发起 revision 2、双摄、条件化升级线路检查以及投递/清运流程；专用 MCU 模拟固件可替代缺少的 MCU 外设，但不替代人工安全确认 |
| 12 | 建立镜像发布物 | 输出压缩镜像、摘要、签名、清单、SBOM、烧录工具和验收记录模板 |

## 3. 实施前基线与差距

以下十项是 2026-08-22 开始实施前的基线，用于说明本轮改动为什么存在，不代表当前工作树
仍缺少这些软件入口。对应实现现已落在第 14 节列出的目录；其中必须由真机证明的部分仍按
`UNLOCKED`/`UNQUALIFIED` 失败关闭。

1. `hardware/ecobin-hardware.service` 把运行目录和解释器固定在
   `/root/EcoBin/hardware`，不具备版本化安装、原子激活或统一回退入口。
2. `hardware/config.py` 使用 `load_dotenv(override=True)` 隐式读取代码目录 `.env`；普通
   systemd 服务没有显式 `EnvironmentFile=`。这会让代码发布目录同时承担配置职责。
3. `hardware/.env.example` 中 MCU 升级默认关闭，BOOT0/NRST 只有示例编号；升级公钥、
   WiringOP 和 `stm32flash` 也依赖现场安装。
4. Orange Pi Linux 默认关闭 UART5，现有仓库没有把 `ph-uart5` 写入镜像的构建步骤。
5. 一次性注册服务只等待通用 `network-online.target`，没有明确 Air780E 的 USB 数据模式、
   RNDIS 接口、APN、DNS、可信校时和后端 HTTPS 可达之间的事实边界，也没有与本地验收入口协同。
6. 仓库已有安全的注册重试和 K1 清理逻辑，但没有覆盖“先离线验收、再验证物联网卡、校时、
   注册和启动正式运行时”的总编排状态机。
7. 摄像头虽然使用稳定路径，但路径仍由代码目录 `.env` 承载，且没有量产阶段逐台验证
   “路径存在、不是 metadata 节点、可以拍照、内外没有接反”。
8. 当前只有操作手册，没有量产镜像构建脚本、镜像输入锁、洁净审计、发布清单和烧录后
   的无需 SSH 预检入口。
9. 现有本地命令工具复用正式 EdgeStore、命令处理器和待上报链路，不能直接作为“零业务
   数据、零云请求”的出厂动作测试入口；AA/EE 又没有确认帧，必须另建断电安全的验收执行器。
10. 当前设备只向平台提交机器验收证据，不会收到“平台当前代次已 PASSED”的可靠终态；如果
    没有窄封存授权，设备无法同时做到“云端通过前保留本地入口”和“通过后单向关闭热点”。

## 4. 目标整体结构

目标分为“构建环境”“离线出厂验收”“注册编排”“注册后正式运行时”四层：

```text
锁定基础镜像 + Git 提交 + apt/uv 锁 + 公共配置
                         │
                         ▼
                可重复镜像构建器
                         │
       K1/AP 密码受控注入 + 洁净审计 + 签名
                         │
                         ▼
                  量产 .img.zst
                         │ 写卡
                         ▼
┌──────────────────────── 首次启动 ─────────────────────────┐
│ 安全 GPIO → 固定开启验收热点/网页 → 离线硬件与动作验收   │
│ → 物联网卡上行/可信校时 → HTTPS 自注册 → K1 清理          │
│ → UART 安全交接 → 正式运行时                               │
└────────────────────────────────────────────────────────────┘
                         │
                         ▼
 OneNet 在线 → 厂家袋扫码 → 后端机器验收 PASSED → 封存授权
                         │
                         ▼
          操作员确认 → SEALED → 关闭验收热点
```

目标文件布局遵循“程序、配置、秘密、状态、运行时文件分离”：

```text
/opt/ecobin/hardware/releases/<release-id>/   普通硬件程序和专用 venv
/opt/ecobin/hardware/current                  当前普通硬件版本原子链接
/opt/ecobin/enrollment/                       一次性注册监督程序和专用 venv
/opt/ecobin/factory-test/                     与生产命令链隔离的本地验收程序和网页
/opt/ecobin/remote-support/releases/<id>/     独立远程维护程序和专用 venv
/opt/ecobin/remote-support/current            当前远程维护版本原子链接

/etc/ecobin/hardware.env                      非秘密、同硬件版统一配置
/etc/ecobin/cellular.env                      Air780E USB/RNDIS/APN 批次配置，权限 0600
/etc/ecobin/enrollment.env                    注册地址、K1 编号和模式
/etc/ecobin/enrollment.key                    K1，注册成功后删除
/etc/ecobin/setup-ap.key                      全批次共用热点密码，设备封存后删除
/etc/ecobin/device-credentials.json           注册后每台唯一正式凭证
/etc/ecobin/image-release.json                不含秘密的镜像发布身份
/etc/ecobin/image-release.pub                 镜像发布验签公钥
/etc/ecobin/mcu-release-keys/                 MCU 固件验签公钥

/var/lib/ecobin/first-boot/state.json         首启进度与可公开错误，不含秘密
/var/lib/ecobin/first-boot/sealed.json        单向本地封存事实；一旦存在永不重新开启验收 AP
/var/lib/ecobin/device-capabilities.json      验收报告绑定的 MCU 远程升级布尔能力事实
/var/lib/ecobin/hardware/edge.db               正式边缘 SQLite
/var/lib/ecobin/hardware/photos/               待传照片
/var/lib/ecobin/mcu-firmware/                  已验证固件缓存
/var/lib/ecobin/factory-test/state.json        最小安全恢复状态，不含订单或清运业务事实
/var/lib/ecobin/factory-test/report.json       本地最终验收报告，不含照片原图
/run/ecobin/factory-test/photos/               验收网页临时缩略图，结束或重启即删除
/run/ecobin/...                                Socket、PID 和临时运行文件
```

普通硬件服务继续暂以 root 运行，以保持当前 UART/GPIO 边界；验收网页和状态展示服务使用
独立低权限账号，只有最小验收代理可以操作 UART/GPIO/摄像头。验收程序与普通硬件服务通过
systemd `Conflicts=` 和设备所有权锁保证绝不并发。以后若降低硬件服务权限，另开任务只授予
串口和 GPIO 所需最小能力。

同一发布物中的代码、公共配置、K1 和已确认采用的全批次共用热点密码相同；首次启动时每台设备分别生成
machine-id、注册 Ed25519 身份、响应解密 X25519 密钥、反向隧道 Ed25519 密钥和 SSH Host
Key，并由注册公钥派生唯一 `ECM0-...` hardwareSn。后端再为该身份创建资产和 OneNet 设备，
返回每台唯一正式凭证。上述任何设备唯一值都不得提前存在于母镜像。

## 5. 可重复镜像构建与洁净封存

### 5.1 锁定输入

新增 `tools/orangepi-image/` 作为唯一构建入口，至少包含：

- `source.lock.json`：Orange Pi Zero 3 Debian 12 Server / Linux 6.1 基础镜像下载地址、文件名、
  SHA-256、板型和内核系列；
- `apt-packages.lock`：镜像内新增 Debian 包的精确版本；
- `builder.lock`：构建容器镜像 digest、`uv`、`zstd`、`qemu-user-static` 和镜像工具版本；
- `image-layout.json`：标称 32 GB 目标介质、固定原始镜像大小、分区、文件系统 UUID 和首次
  启动扩容策略；
- `build-image.sh`：Linux 上的规范构建入口；
- `New-EcobinOrangePiImage.ps1`：Windows 开发机调用 WSL2/Linux 构建器的薄包装；
- `verify-image.sh`：只读挂载最终镜像并执行洁净、权限和内容检查；
- `flash-and-verify.*`：写卡后复读关键扇区或整盘摘要的操作入口。

基础镜像选择 Orange Pi Zero 3 官方 Debian 12 Server、Linux 6.1 分支，原因是 Python 3.11、
systemd 252、NetworkManager 和当前 `/dev/ttyS5` 路径能够对齐。确切版本不能使用“latest”，
实施时必须写入锁文件并先在一台可返工设备验证。

量产介质类型已确定为标称 32 GB TF 卡，但具体批次的最小实际容量仍须用实卡形成资格证据。
发布的原始 `.img` 保持为只容纳锁定系统内容的固定紧凑
大小，不生成接近 32 GB 的空白镜像；其最后分区必须小于试点卡实际可用容量并保留余量。首次
启动在 `SYSTEM_PREPARED` 阶段幂等扩展根分区和文件系统到本卡剩余空间，扩容中断后可继续。
因此不同厂家对“32 GB”的实际字节差异不会要求重新构建镜像，确切原始镜像大小由实现时的
基础镜像内容计算并写入 `image-layout.json`，不再需要用户提供。

### 5.2 构建步骤

1. 校验基础镜像摘要，不匹配立即停止；
2. 在固定 digest 的 Linux 构建容器中解包并挂载镜像；
3. 使用固定版本包源或本地 `.deb` 缓存安装 Python 3.11、NetworkManager、hostapd、dnsmasq、
   nftables、OpenSSH、sudo、v4l-utils、stm32flash、WiringOP，以及 Air780E AT 串口探测和
   USB RNDIS 网卡所需工具；首版不引入 QMI、MBIM 或逐次 PPP 拨号数据面；
4. 根据仓库提交和 `hardware/uv.lock` 创建普通硬件、一次性注册、远程维护和出厂验收四个相互
   隔离的运行目录/虚拟环境；
5. 安装 systemd 单元、统一配置、出厂验收网页、隔离验收执行器、MCU 发布公钥和镜像发布身份；
   阶段服务保持 static，物联网卡/以太网 profile 默认不 autoconnect，早期整机出站锁和首启
   编排是唯一自动入口；
6. 修改 boot 配置启用 UART5，并校验没有同时启用冲突的 `ph-pwm12`；
7. 清理 apt 缓存、临时文件、构建日志、持久 swapfile/交换分区启用配置、zram 生成器和源码中
   不应进入设备的开发测试资产；量产验收执行器
   作为正式镜像组件保留，但模拟 MCU、测试 API 和开发模式入口不得自启动；
8. 执行一次无秘密候选审计；
9. 两个由正式策略分别固定身份、构建域和 Ed25519 公钥的受信构建者，为各自候选生成并签署
   build receipt（单次构建回执）；回执绑定不同 invocation UID、构建容器、原始镜像、候选
   manifest、软件负载锁、Git 提交、release ID 和全部输入锁摘要；
10. 验签两个不同构建者的回执，再比较两次独立无秘密候选；只有原始镜像和被认证输入都一致，
    才由第三把构建证明密钥签署聚合 build attestation；重复使用一个回执或伪造签名均停止；
11. 在受控步骤只对选定候选注入 K1 和全批次共用热点密码，二者都不进入 Git、命令行明文
   参数或普通日志；只读复核后由独立封存证据密钥签署文件级 seal evidence（封存证据）；
12. 以正式发布策略中固定的外部公钥指纹验证构建证明、封存证据和发布签名，再生成发布物。

根文件系统不能直接复制一个已经被构建过程写入随机时间的 ext4。构建器必须从锁定源树以固定
UUID、目录哈希种子、inode/块布局和 journal 参数重新创建 ext4，并归一化全部 inode 的
`atime/mtime/ctime/crtime` 及扩展时间位；重建前后还要比较普通文件、目录、符号链接、硬链接、
特殊文件、稀疏属性和扩展属性的语义清单，再执行只读 `e2fsck` 和精确 profile 校验。

“可重复”在本计划中明确指：相同基础镜像、Git 提交、锁文件、构建器 digest 和发布纪元，
必须得到相同的**无秘密候选** `.img`、候选 manifest 和软件清单，且该事实由两个独立签名
build receipt 和镜像外聚合 build attestation 共同绑定。K1/热点密码通过读写挂载写入 ext4
会改变 inode、时间、journal 和超级块，
现有实现不得宣称两张含秘密 raw 镜像逐字节可重复；其真实性改由候选构建证明、单张封存
镜像摘要和签名的文件级封存证据共同证明。不能只修改未签名 manifest 的布尔字段后放行。

### 5.3 洁净封存检查

封存脚本必须证明以下设备唯一状态不存在：

```text
/etc/ecobin/device-credentials.json
/etc/ecobin/remote-support-credentials.json
/var/lib/ecobin/enrollment-state.json
/var/lib/ecobin/first-boot/state.json
/var/lib/ecobin/first-boot/sealed.json
/var/lib/ecobin/remote-support/state.db
/var/lib/ecobin/hardware/edge.db
/var/lib/ecobin/factory-test/*
/etc/ssh/ssh_host_*
/root/EcoBin/hardware/data/*
```

同时要求 `/etc/machine-id` 为空或处于发行版规定的首次启动生成状态，镜像中没有已保存的
客户 Wi-Fi Station 配置、OneNet 设备 Key、反向隧道私钥、MCU 签名私钥、Git 元数据、测试
照片或模拟 MCU 自启动单元。审计还必须拒绝 enrollment、cellular、factory-test、hardware
等阶段服务的独立 enable 链接和任何预先启用的蜂窝/以太网 autoconnect profile。

封存时预期存在 `/etc/ecobin/enrollment.key` 和 `/etc/ecobin/setup-ap.key`，均为
`root:root 0600`。这两个文件存在不算“不洁净”；它们是尚未注册设备的受控引导输入。
候选和封存检查还必须证明没有持久交换配置；任何读取 K1、镜像签名私钥或封存私钥的入口都要
在操作前确认 `/proc/swaps` 没有活动项。注册服务额外使用 `MemorySwapMax=0`，因此检查后再被
启用的交换空间也不能成为该进程的后备存储。

按项目负责人决定，不增加运行时“已注册凭证与 K1 同时存在就判克隆”的新机制。防止把已
启动卡当母卡复制，依靠构建器只从锁定基础镜像生成、封存审计和写卡工位流程保证。

## 6. 普通硬件程序的统一安装与配置

### 6.1 安装方式

普通硬件发布先在构建环境生成 Linux ARM64 运行包，设备上不执行 `git pull`、Maven、npm
或临时 `uv sync`。安装器执行以下原子步骤：

1. 验证运行包摘要和专用镜像/设备软件发布签名；
2. 解压到新的 `/opt/ecobin/hardware/releases/<release-id>`；
3. 校验 Python 版本、依赖清单、入口文件和只读权限；
4. 原子切换 `/opt/ecobin/hardware/current`；
5. 重启普通硬件服务并验证 READY；
6. 激活失败时恢复旧 `current` 链接，但不回退或删除已经前向迁移的设备 SQLite。

一次性注册包和独立远程维护代理保留各自目录与虚拟环境。普通硬件更新不得覆盖远程维护
代理，以继续满足“重启普通硬件服务不切断现有反向 SSH”的既有边界。

普通运行包不再包含 `device_enrollment.py`。该文件只存在于一次性注册目录，注册成功后由
现有幂等清理逻辑删除，避免不可变普通运行包与“一次性删除注册实现”相互冲突。

### 6.2 配置来源

生产服务显式读取 `/etc/ecobin/hardware.env`。`config.py` 只在明确开发模式下加载项目
`.env`，生产模式发现代码目录 `.env` 时应拒绝启动或至少由镜像预检判失败。配置优先级为：

1. `/etc/ecobin/device-credentials.json`：每台唯一 OneNet、设备入口和远程维护凭证；
2. `/etc/ecobin/hardware.env`：同一主板/摄像头组合的非秘密固定配置；
3. EdgeStore 中由后端 `applyConfiguration` 下发的版本化业务配置；
4. 代码内只保留不影响身份、安全和硬件寻址的安全默认值。

量产 `hardware.env` 至少固定：

```dotenv
ECOBIN_DEVICE_CREDENTIALS_PATH=/etc/ecobin/device-credentials.json
ECOBIN_SERIAL_PORT=/dev/ttyS5
ECOBIN_SERIAL_BAUDRATE=115200
ECOBIN_MCU_PROTOCOL=fixed-frame
ECOBIN_MCU_SIMULATED=false
ECOBIN_UART_PORT_COUNT=1

ECOBIN_DEVICE_CAPABILITIES_PATH=/var/lib/ecobin/device-capabilities.json
ECOBIN_MCU_BOOT0_WPI=2
ECOBIN_MCU_RESET_WPI=5
ECOBIN_MCU_BOOT0_ACTIVE_LEVEL=1
ECOBIN_MCU_RESET_ACTIVE_LEVEL=1
ECOBIN_MCU_HARDWARE_COMPATIBILITY=ECOBIN_MAINBOARD_V1.1
ECOBIN_MCU_SIGNING_PUBLIC_KEYS_DIR=/etc/ecobin/mcu-release-keys
ECOBIN_MCU_FIRMWARE_CACHE_DIR=/var/lib/ecobin/mcu-firmware
ECOBIN_STM32FLASH_PATH=/usr/bin/stm32flash
ECOBIN_GPIO_PATH=/usr/bin/gpio

ECOBIN_CAMERA_OUTSIDE=/dev/v4l/by-id/usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0
ECOBIN_CAMERA_INSIDE=/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0

ECOBIN_DATA_DIR=/var/lib/ecobin/hardware
ECOBIN_EDGE_STORE_PATH=/var/lib/ecobin/hardware/edge.db
ECOBIN_EDGE_BOOT_ID_PATH=/var/lib/ecobin/hardware/edge-boot-id

ECOBIN_FACTORY_AP_INTERFACE=wlan0
ECOBIN_FACTORY_AP_ADDRESS=10.42.0.1/24
ECOBIN_FACTORY_TEST_STATE_PATH=/var/lib/ecobin/factory-test/state.json
ECOBIN_FACTORY_TEST_REPORT_PATH=/var/lib/ecobin/factory-test/report.json
ECOBIN_FACTORY_TEST_PHOTO_DIR=/run/ecobin/factory-test/photos
ECOBIN_FACTORY_SEALED_PATH=/var/lib/ecobin/first-boot/sealed.json
ECOBIN_FACTORY_TEST_WEIGHT_GRAMS=500
ECOBIN_FACTORY_TEST_WEIGHT_TOLERANCE_GRAMS=10
ECOBIN_CELLULAR_CONFIG_PATH=/etc/ecobin/cellular.env
```

COS 地域、桶名和 HTTPS Base URL 继续作为环境级非秘密配置写入同一文件；设备永久密钥、
STS、K1、热点密码和任何私钥不得写入该文件。`ECOBIN_EDGE_VERSION` 由构建清单生成，禁止
长期停留在手工默认 `0.1.0`。

物联网模组已经锁定为现有 Air780E 载板。首版直接使用它插卡后提供的 USB RNDIS，不要求用户
选择、锁定或升级 Air780E 固件，也不在现场于 QMI、MBIM、ECM、RNDIS 和 PPP 之间猜测。
`/etc/ecobin/cellular.env` 只保存 NetworkManager 连接名称、RNDIS 网卡稳定匹配规则、自动 APN
模式和断线恢复参数；USB VID/PID、接口布局和模组版本由程序从真机读取并作为诊断事实记录，
不是用户输入或量产准入版本号，也不硬编码接口一定叫 `usb0`。现有 SIM 默认按插卡自动 APN
处理；只有实际联网验收失败且供应商确认属于专网 APN、带用户名/密码、SIM PIN 或目标白名单
时，才增加相应批次配置。秘密配置不进入 Git、manifest、网页或日志。

## 7. UART5 与 MCU 升级硬件参数

### 7.1 UART5 固化

镜像构建器以结构化方式修改 `/boot/orangepiEnv.txt`，在既有 overlay 列表中加入
`ph-uart5`，不得用整文件覆盖方式丢失其他启动参数。构建期和启动期分别校验：

- UART5 overlay 文件存在；
- `ph-uart5` 恰好启用一次；
- `ph-pwm12` 未启用，因为它与 UART5 的 PH2/PH3 复用；
- Linux 6.1 启动后 `/dev/ttyS5` 存在；
- 没有串口控制台、getty、模拟 MCU 或其他进程占用 `/dev/ttyS5`；
- 应用模式能够以 115200/8N1 完成 F3 和 F1 查询。

本镜像不兼容 Linux 5.4 的 `/dev/ttyAS5`。将来更换内核必须生成新的板级镜像变体并重新做
UART、GPIO 和 MCU 升级硬件在环验收，不能在同一镜像中自动猜测设备节点。

### 7.2 固定引脚

| 用途 | 26 针物理针脚 | H618 GPIO | WiringOP wPi | 正常态 | 动作态 |
|---|---:|---|---:|---|---|
| MCU BOOT0 | 7 | PC9 / GPIO 73 | 2 | 低 | 高电平进入系统 Bootloader |
| MCU NRST（经 2N7002 开漏） | 11 | PC6 / GPIO 70 | 5 | PC6 低、MOS 关断、NRST 高 | PC6 高、MOS 导通、NRST 低 |
| UART5 TX | 8 | PH2 / GPIO 226 | 3 | 115200/8N1 | ROM 阶段 115200/8E1 |
| UART5 RX | 10 | PH3 / GPIO 227 | 4 | 115200/8N1 | ROM 阶段 115200/8E1 |

量产接线采用 2N7002 开漏隔离：PC6 经 1 kΩ 接 Gate，Gate 以 100 kΩ 下拉到地，Source 接
公共地，Drain 接 STM32 NRST；NRST 以 10 kΩ 上拉到 MCU 3.3 V，并按 STM32F103 硬件设计要求
保留 10～100 nF 对地电容。禁止使用普通推挽反相器硬推 NRST 高电平。由此配置固定为
`BOOT0_ACTIVE_LEVEL=1`、`RESET_ACTIVE_LEVEL=1`：BOOT0 高电平进入系统 Bootloader；PC6 高
电平使 MOS 导通并把 NRST 拉低，PC6 低电平或香橙派掉电时 MOS 关断，由 MCU 侧上拉释放复位。
真机发布仍用万用表和真实复位行为复核接线，实测不符应判硬件装配失败，不能让工位临时翻转
软件配置掩盖批次差异。

增加早期 `ecobin-mcu-safe-gpio.service`，在验收执行器、普通硬件、注册和网络服务前先把
BOOT0/wPi 2 输出低，再把 PC6/wPi 5 输出低，使 MOS 关断并由 MCU 侧上拉把 NRST 保持为高。
这里不得把“NRST 为高”误写成“PC6 输出高”；PC6 高会导通 MOS 并触发复位。服务失败时
不得继续 MCU 预检或正式业务。

声明“已安装远程升级线”的硬件必须具备 BOOT0 约 10 kΩ 下拉、BOOT1/PB2 固定下拉、
NRST 上拉以及上述可靠开漏控制，确保香橙派尚未启动或进程崩溃时 MCU 默认进入应用。
未安装设备不能通过软件声明获得升级能力；已安装设备也不能用软件设置替代失效安全电阻。

## 8. 物联网卡上行、出厂验收热点与本地网页

### 8.1 两条网络链路的固定职责

量产设备正式上行以 Air780E 物联网卡为主，由现有 Air780E 载板、SIM、USB RNDIS 和
NetworkManager DHCP 连接提供默认路由、DNS、可信校时、HTTPS、OneNet MQTT 和 COS 访问。
板载 Wi-Fi 不作为
量产设备的 Station 上行，不让工位或现场人员逐台录入 Wi-Fi；它在出厂阶段固定作为本地 AP
（Access Point，接入点）提供验收入口。以太网可以作为工厂返工或开发诊断的备用上行，但
不能成为离线硬件验收的前置条件，也不能改变物联网卡是正式量产链路的默认选择。

正式注册和生产流量不得仅凭“存在默认路由”判定上行成功。Air780E 必须固定 RNDIS profile
和路由优先级，禁用以太网自动取得生产默认路由；DNS、NTP、HTTPS、MQTT 和 COS 探针均绑定
蜂窝接口或其策略路由表，并用 `ip route get <目标地址>` 复核实际出口、源地址和接口。开发/返工
临时启用以太网时也不能让它悄悄替代蜂窝验收。`ecobin-cellular-uplink.service` 在首次注册后
继续作为正式运行时的网络协调服务，负责 NetworkManager 自动连接、断线重拨和接口事实投影，
不是只在首启执行一次的探针。

尚未结束出厂模式的设备每次通电都先建立安全 GPIO，然后立即启动 AP 和本地网页。这个入口
不等待 SIM 就绪、不等待后端可达、不要求可信时间，也不因物联网卡已经联网而省略。这样即使
未插 SIM、APN 配错或工厂完全断网，操作员仍能判断 MCU、门锁、电机、称重、红外、烟感和
摄像头是否完好。

目标 Debian 12/Linux 6.1 镜像必须在真实 Zero 3 上用 `iw list` 和反复冷启动证明板载无线
芯片、固件和驱动支持稳定 AP 模式；该事实是镜像发布门禁，不能只依据芯片资料推定。

### 8.2 Air780E“插卡即用”边界与能力检查

本计划假设设备使用的是已经完成供电、天线、SIM 卡座、PWRKEY 和 USB 数据连接的 Air780E
载板，而不是把裸 LGA 模组直接连接香橙派。裸模组仍需要合规的载板电路，不能仅靠镜像配置
变成可插拔 USB 网卡。量产目标中的“插卡即用”具体表示：当前稳定 Air780E 载板插入现有
物联网卡后，普通启动不要求操作员登录 SSH、输入 APN、选择固件或执行拨号命令。

Air780E 固件属于模组自身的软件，不是 Debian 镜像的构建输入。本计划不刷写 Air780E 固件，
也不把某个精确版本设为设备准入条件。不同采购批次只要表现出相同的 USB RNDIS 能力并通过
真实联网测试即可使用；程序可以读取模组版本写入脱敏诊断报告，便于故障追溯，但不要求用户
事先确定版本。

这也保持了现有应用边界：注册、OneNet MQTT、COS 上传和反向 SSH 都只使用 Linux 已经建立的
IP 网络，不读取 Air780E 固件版本、APN 或网卡名称。新镜像负责复现 DHCP、默认路由、DNS、
可信时间和目标地址可达这些操作系统事实，不把模组管理耦合进普通硬件业务进程。

首版固定以下单一使用方式，不在设备上动态尝试多套数据协议：

- 生产数据面只使用当前载板直接提供的 USB RNDIS，不在正常启动中进行 PPP 拨号；
- Debian 由 udev 根据同一 USB 父设备的可观察属性识别 AT 口与 RNDIS 网卡，
  再由锁定的 NetworkManager profile 获取 DHCP；不得依赖网卡刚好叫 `usb0`；
- 模组版本、USB VID/PID 和接口编号只作为自动发现、日志脱敏和售后诊断事实，不进入人工配置；
- OneNet MQTT、COS、后端 HTTPS 和 NTP 均继续由香橙派 Python/Linux 发起，不使用模组内置 MQTT
  代替现有设备链路。

镜像提供一个 Air780E 健康服务。它优先核对 USB RNDIS 网卡、DHCP 地址、默认路由、DNS 和
真实 HTTPS/OneNet/COS 探针；必要时再通过 AT 口只读查询 SIM、注册、信号和 PDP/APN 事实。
只要 RNDIS 已经正常出现并联网，服务就不执行 `AT+SETUSB`、`AT+RNDISCALL` 或固件更新，避免
对已经可用的模组做多余持久配置。RNDIS 没有出现时，首版明确报出
`CELLULAR_RNDIS_UNAVAILABLE` 并阻止注册和离厂，由工位检查 USB/供电/SIM/载板；不让用户
为了兼容异常模组手工选协议。

现有物联网卡按运营商自动提供 APN、插卡直接联网设计，不要求开工前再提供 APN；是否真正
可用由真实 DNS、HTTPS、OneNet 和 COS 探针直接证明。专网 APN、APN 用户名/密码、SIM PIN 或
定向白名单只作为实际联网失败时的故障分支：若供应商确认存在，再作为同批次受控配置写入
`/etc/ecobin/cellular.env`；不让工位人员逐台在网页录入。
网页只显示脱敏后的模组、SIM、注册、信号、RNDIS、IP、路由和探针事实，不显示 ICCID/IMSI
全文、SIM PIN 或 APN 凭证。

因此，实施前不再要求确定 Air780E 固件版本、手工读取 `SETUSB/RNDISCALL`，也不要求重新选择
模组、APN 或连接协议。P5 实施时直接用一台现有设备完成自动发现规则和联网测试；这些工作由
开发和出厂程序完成，不形成新的用户决策。若实物连 USB RNDIS 都没有出现，则属于硬件/采购
偏差而不是正常配置步骤，应在试点中明确失败，不能让同一镜像静默切换协议。

### 8.3 热点生命周期与网络隔离

使用锁定版本的 `hostapd + dnsmasq + nftables`，不依赖运行时从 GitHub 下载 `create_ap`。
热点参数为：

```text
SSID: EcoBin-Factory-<factoryId 八位>
频段: 2.4 GHz
设备地址: 10.42.0.1/24
DHCP: 10.42.0.20～10.42.0.100
网页: http://10.42.0.1/
```

`factoryId` 由首启生成的 machine-id 与 image release ID 做 SHA-256 后截取八位大写十六进制，
只用于工位区分热点，不作为设备公开码、注册身份或鉴权凭证。这里不直接依赖板载网卡 MAC，
因为量产前尚未证明该批次 MAC 一定唯一且不会随驱动配置变化。

热点采用已确认的全批次共用 WPA2 密码，通过 `/etc/ecobin/setup-ap.key` 注入镜像，不与 K1、设备
Key、SIM PIN 或管理员密码复用。密码不进入 Git、页面响应、状态报告或日志。只有本地验收、
注册和 K1 清理完成，正式运行时健康，真实初始袋及后端机器验收为 `PASSED`，设备又收到与
当前验收代次绑定的可靠“允许封存”命令，操作员才可点击“结束出厂模式”。编排器先可靠写入
单向 `SEALED` 事实，再删除热点密码、停止 AP/网页并完成幂等清理。

一旦 `SEALED` 存在，即使清理中途断电、热点密码残留或物联网卡以后离线，也绝不重新开启
热点；下次启动只继续删除秘密和停止服务。仅“尚未注册、尚未封存且不存在 AA/EE/F2 恢复锁”
的设备可以通过重写受信镜像返工。已经注册或封存的设备必须保留原正式凭证和资产身份，走
现有反向 SSH 或另行设计的物理在场维护/身份保留重装流程，不能恢复干净母镜像重新注册。

`nftables` 默认拒绝热点客户端转发到物联网卡、以太网、OneNet、COS、后端或公网，也拒绝
访问本机 SSH、MQTT、数据库和其他服务端口；客户端只能访问本机 DHCP、DNS 和验收网页。
网页只绑定 `10.42.0.1`，不绑定 `0.0.0.0`、物联网卡或以太网地址。设备自身后续注册所需的
蜂窝出站流量与 AP 客户端转发是两件事：前者只允许注册/正式运行服务使用，后者始终禁止。
出厂模式中 `wlan0` 由热点单元独占并从 NetworkManager 的 Station 管理中排除；进入
`COMPLETE` 后停止 hostapd 并关闭无线接口，不创建或保留任何 Station profile。

### 8.4 网页职责

网页按阶段提供以下能力：

- 显示镜像发布号、板型、machine-id 摘要、磁盘、系统时间和当前首启阶段；
- 显示 UART5、MCU F3 身份/revision、F1 重量/红外/烟感、BOOT0/NRST 和升级线路结果；
- 分别预览箱外 DECXIN、箱内 icspring 摄像头，由操作员确认角色没有接反；
- 发起“离线投递硬件测试”：真实驱动 MCU 屏幕、按钮、投递门/电机和称重，展示最终 DD；
- 发起“离线清运硬件测试”：真实驱动 MCU 屏幕、按钮、清运锁和称重，展示最终 EF；
- 只读显示 Air780E 识别信息、SIM、信号、RNDIS 接口、IP、默认路由、DNS、校时和后端探针结果；
- 显示注册、K1 清理和正式运行时状态，并在满足全部条件时提供“结束出厂模式”。

网页不得提供 shell、任意文件读取、日志全文下载、服务任意启停、固件上传、Wi-Fi Station
配置或 APN 任意编辑，不展示 K1、OneNet deviceKey、SIM PIN、隧道私钥、热点明文密码或完整
设备凭证。首版物联网卡信息只读；不同 APN 必须在镜像批次配置中预先锁定，不在网页提供
通用 NetworkManager 或 AT 配置器。

实现上拆为低权限网页进程和最小 root 验收代理。两者只通过有界 Unix Socket 消息通信；
代理只接受固定的查询、开始动作、恢复和封存命令，不拼 shell 字符串。加入请求体上限、CSRF、
防重复提交、单会话互斥、速率限制和动作前安全确认。操作员关闭页面不会取消已经发给 MCU 的
动作，重新打开页面必须看到同一个本地测试状态。

### 8.5 与生产数据、生产进程和云端的隔离

“不产生数据”在本计划中严格解释为“不产生业务或云端事实”，具体保证：

- `ecobin-factory-egress-lock.service` 在 `network-pre.target` 前加载整机 nftables `OUTPUT`
  锁，除 loopback、AP 子网内的 DHCP/DNS/网页响应和必要的已建立连接外拒绝所有出站；同时
  禁用蜂窝/以太网自动连接和 NTP，防止 NetworkManager、Air780E 上行服务、校时器或遗留服务
  在验收进程之外自行联网；
- 离线动作测试期间不启动 `ecobin-hardware.service`、MQTT、OneNet 命令、COS 上传或后端客户端；
- 验收程序不打开 `/var/lib/ecobin/hardware/edge.db`，不复用正式命令 inbox/outbox、订单、
  清运、袋、皮重、钱包、照片上传或配置应用代码路径；
- 测试 UID 只属于验收进程，不得与后端 sessionUid、orderNo 或 cleanOperationUid 互认；
- 测试照片只放在 `/run/ecobin/factory-test/photos/`，仅供当前局域网页展示，结束动作、退出
  验收或重启后删除，不上传 COS；
- 验收执行器自身再叠加无网络命名空间和 systemd 地址族限制；只有 `FACTORY_TEST_PASSED`
  已可靠落盘且不存在恢复锁时，首启网络代理才原子切换全机防火墙、启用锁定蜂窝 profile 和
  校时服务，任何普通验收进程都没有 `CAP_NET_ADMIN`；
- 用带假蜂窝/以太网网关和出站抓包器的集成测试证明动作测试不会访问物联网卡、以太网或公网。

但为了物理安全，不能承诺“完全不落任何本地字节”。AA/EE 没有接收确认帧：在写串口前必须
原子保存“该动作可能已经发给 MCU”的最小恢复标记；否则写串口后断电会让香橙派误以为从未
开门并允许再次执行。该独立状态只包含测试类型、随机本地 UID、阶段、MCU 原身份摘要和稳定
错误码，不包含订单、用户、袋、价格结算或云端凭证。操作员的门体安全确认也必须与同一测试
UID 一起原子保存，不能只留在浏览器内存。

`state.json` 同时保存唯一 `currentAction` 和已完成动作摘要；完成时用一次临时文件写入、文件
`fsync`、原子替换和目录 `fsync`，把当前动作从 `RECOVERY_REQUIRED/WAITING` 单调转换为已完成
`SAFE_VERIFIED`，不能先删除活动标记再另写报告。任何时刻要么仍有活动/恢复锁，要么已经有
同一 UID 的可靠终态，不能两者都不存在。最终 `report.json` 是该权威状态的可重建投影；先可靠
生成报告，封存时才允许删除非终态工作状态。报告可保留到离厂审计，但不含照片原图和业务记录。

注册和正式运行阶段当然需要访问后端、OneNet 和 COS，但它们发生在离线硬件验收通过之后，
不是“模拟投递/清运”的一部分。页面和报告必须把二者分栏，不能用注册请求冒充硬件测试流量。

### 8.6 现有 revision 2 协议能提供的验收能力

首版不扩展 MCU 协议，按完整固定帧 revision 2 使用现有能力：

- F2 身份查询/F3 和 F0 快照查询/F1 用于只读身份、重量、红外、烟感检查；
- 离线投递测试按需要发送一次 BB 显示测试单价，再且仅再发送一次 AA，等待一个最终 DD；
- 离线清运测试只发送一次 EE，等待一个最终 EF；
- F2 的升级准备模式只用于受控 Bootloader 线路探测，不参与普通投递/清运动作测试。

AA、BB、EE 没有 ACK（接收确认）、CRC 或流程编号，因此按钮在本地安全标记落盘后立即禁用，
串口超时、页面刷新或进程重启都不得自动重发。网页只能诚实显示“指令可能已发送，请按 MCU
屏幕完成现场操作，正在等待最终结果”，不能伪造“门已开”“按钮已按”等中间状态。若以后
要求网页逐步显示门锁、按钮和屏幕状态，必须另行升级 MCU 协议修订号并增加过程事件帧。

超时、断电或串口异常统一进入 `RECOVERY_REQUIRED`：先让 BOOT0 回应用态并复位 MCU，重新
验证原 F3 身份和 F1，再由操作员现场确认投递口安全；清运门没有门磁，必须由操作员确认门扇
已经关闭，不能从电磁阀断电推断物理门位。恢复完成前禁止开始另一项测试、注册切换或正式业务。

验收 UART 向正式运行时交接时还要防止内核缓冲或迟到的 DD/EF 越界：所有动作先达到
`SAFE_VERIFIED`，然后在 BOOT0 为低时受控复位 MCU；验收代理独占重开串口、执行输入缓冲
清理和静默窗口、丢弃该窗口内所有业务结果帧，再发起全新的 F2/F3 与 F0/F1 查询。只有身份和
传感器复验成功并可靠记录 `HANDOFF_SAFE`，才释放串口给正式进程。正式适配器启动时仍要把
“没有活动生产工作可绑定”的 DD/EF 隔离，不写 EdgeStore/outbox；不能只依赖交接时清过一次
缓冲。该边界必须覆盖跨进程交接的迟到帧测试。

## 9. 可靠首次启动状态机

### 9.1 状态和权威事实

新增 `ecobin-first-boot.service` 作为编排者，状态以 0600 JSON 原子写入
`/var/lib/ecobin/first-boot/state.json`，但状态文件只是进度索引；每次启动都重新核对文件、
NetworkManager、Air780E AT/RNDIS 状态、systemd、热点防火墙、串口、验收恢复锁和凭证等事实。

| 阶段 | 进入依据 | 完成事实 | 失败后的业务影响 |
|---|---|---|---|
| `SYSTEM_PREPARED` | 本地文件系统可写 | 32 GB TF 根分区/文件系统已幂等扩容、machine-id 已生成、发布清单有效、安全 GPIO 已建立 | 不允许操作执行器、注册或业务 |
| `FACTORY_PORTAL_READY` | 安全 GPIO 已建立 | AP、DHCP/DNS、局域网页和隔离防火墙均健康 | 即使物联网卡离线也保持本地入口 |
| `FACTORY_TEST_REQUIRED` | 网页可访问且正式硬件进程未运行 | 等待操作员开始或继续同一验收 | 不启动注册或正式业务 |
| `FACTORY_TEST_RUNNING` | 已原子建立唯一测试运行 | revision 2、F1、升级线安装选择/条件检查、双摄、投递和清运均产生本地结果 | 异常进入失败或恢复锁，不自动重发 AA/EE |
| `FACTORY_TEST_PASSED` | 全部强制本地项目通过 | 报告绑定当前镜像、MCU 身份和硬件配置，且没有恢复锁 | 允许继续物联网卡和注册阶段 |
| `UPLINK_REQUIRED` | 本地硬件验收已通过 | 按锁定 Air780E RNDIS profile 尝试连接 | 保留 K1 和热点，等待 SIM/APN/信号修复 |
| `UPLINK_READY` | Air780E、SIM、PDP 和 RNDIS 链路可用 | 默认路由、DNS、后端 HTTPS 探针均从正式上行成功 | 不把 AP 客户端连接当成上行成功 |
| `TIME_READY` | 上行网络已验证 | 系统时钟同步且偏差满足注册/证书要求 | 不发送注册请求 |
| `ENROLLING` | K1、注册地址和校时均有效 | 沿用现有断电安全注册状态 | 失败自动重试，K1 不删除 |
| `ENROLLED` | 正式凭证完整复读校验成功 | K1、注册临时状态和一次性注册实现已可靠删除 | 未清理完成前不启动正式业务 |
| `HANDOFF_SAFE` | 已注册且所有本地动作安全结束 | MCU 应用复位、串口清缓冲/静默窗口、新 F3/F1 通过，交接事实落盘 | 失败回到恢复，不启动正式硬件进程 |
| `RUNTIME_READY` | `HANDOFF_SAFE` 与正式凭证有效 | 蜂窝协调、远程维护代理和普通硬件 MQTT 进程健康 | 页面进入只读，禁止再次动作测试 |
| `CLOUD_ACCEPTANCE_REQUIRED` | 正式运行时和 OneNet 健康 | 等待真实初始袋、后端挑战、COS/机器证据和平台裁决 | AP 保留为只读诊断入口，不允许本地动作重测 |
| `CLOUD_ACCEPTANCE_AUTHORIZED` | 收到目标设备的 `AUTHORIZE_FACTORY_SEAL` | 命令事务已保存且验收代次/袋摘要与最近挑战匹配 | 旧代次或重复命令不改变当前事实 |
| `SEAL_READY` | 云端封存授权、本地报告、K1 清理和运行时均健康 | 没有 MCU/动作恢复锁，等待操作员明确结束出厂模式 | AP 继续存在，但不能再操作执行器 |
| `SEALED` | 操作员确认且所有事实再次核验 | `sealed.json` 已原子写入并完成文件/目录 fsync | 从此永不启动验收 AP，只继续幂等清理 |
| `COMPLETE` | 已存在有效 `SEALED`，且本地授权终态与唯一 `FACTORY_SEAL_COMPLETED` 可靠事件已在同一 EdgeStore 事务提交 | 删除热点密码/临时照片，停止 AP/网页，正式 target 可重启恢复 | 后续离线不自动重开出厂热点；平台尚未可信应用完成事件时仍禁止资产分配和离厂 |

`COMPLETE` 仍不单独等于允许装箱离厂；第 16.3 节还要求封存后的冷启动、正式服务健康和工位
审计记录均通过。但它必须晚于当前代次的后端机器验收 `PASSED`，不能在云端验收前抢先关闭
唯一的本地返工入口。

状态文件中的历史阶段不能替代本次启动事实。安全 GPIO（通用输入输出引脚）的安全电平、根分区
扩容和 EdgeStore 结构准备必须在当前启动重新成功建立；任一当前事实缺失时，所有后续阶段 gate
（启动门禁）都返回拒绝，即使 `state.json` 记录曾经到达更晚阶段也不能启动验收动作、注册或正式
运行时。

### 9.2 systemd 顺序

```text
local-fs.target
  ├─ ecobin-expand-rootfs.service
  │    └─ ecobin-edge-store-prepare.service（仅迁移/quick_check，无网络和事件恢复）
  ├─ ecobin-factory-egress-lock.service ──Before──> network-pre.target
  └─ ecobin-mcu-safe-gpio.service
       └─ ecobin-first-boot.service（Requires=expand/EdgeStore/GPIO/egress-lock）
            ├─ ecobin-factory.target
            │    ├─ ecobin-factory-ap.service
            │    ├─ ecobin-factory-portal.service
            │    └─ ecobin-factory-test.service
            ├─ ecobin-cellular-uplink.service （FACTORY_TEST_PASSED 后启动并持续运行）
            ├─ ecobin-enrollment.service      （UPLINK_READY + TIME_READY 后）
            ├─ ecobin-factory-handoff.service （ENROLLED 后、正式 UART 接管前）
            └─ ecobin-runtime.target           （HANDOFF_SAFE 后）
                 ├─ ecobin-cellular-uplink.service
                 ├─ ecobin-remote-support.service
                 └─ ecobin-hardware.service
```

验收执行器运行时普通硬件进程尚未启动，因此它是 UART/GPIO/摄像头唯一所有者。
`ecobin-factory-test.service` 与 `ecobin-hardware.service` 双向声明 `Conflicts=`，另使用独占锁
防止手工启动绕过 systemd。验收通过后先停止执行器并释放全部设备节点，再启动正式运行时；
低权限网页进入只读状态，直到操作员封存。后续正常开机看到 `COMPLETE` 与有效正式凭证后，
直接启动 `ecobin-runtime.target`，不启动 AP，不重复执行动作、F2、复位或拍照。

镜像中只有早期出站锁和 `ecobin-first-boot.service` 进入启动依赖；cellular、enrollment、factory
test、handoff、runtime 等阶段单元均为 static unit，不允许各自 `enable`。首启编排器是唯一
启动者，每个单元还用只读 gate helper 重新验证所需持久事实，例如 enrollment 必须看到
`FACTORY_TEST_PASSED + UPLINK_READY + TIME_READY`，runtime 必须看到 `HANDOFF_SAFE + ENROLLED`。
镜像审计拒绝旧 `ecobin-hardware.service`、enrollment 或 NetworkManager 蜂窝 profile 留下的
独立 enable/autoconnect。由此即使旧 unit 文件残留，也不能越过“先本地验收、后联网注册”。

首次注册后的 `ecobin-cellular-uplink.service` 不退出；`ecobin-runtime.target` 始终 Wants/After
它，正常启动时也重新启用锁定 profile 和断线重拨。正常运行中的暂时蜂窝掉线不停止本地安全
处理或硬件服务，但会显示离线并持续受控重连；第一次进入 runtime 前则必须已经取得
`UPLINK_READY`。

任何阶段只能在完成事实可靠落盘后推进。服务退出码、重试次数和最近错误码可展示，但不把
日志文本或秘密复制进状态文件。物联网卡和注册的暂时失败采用有上限退避；等待操作员修正
SIM/APN、硬件或后端配置不记为永久完成。`FACTORY_TEST_RUNNING` 发现“AA/EE 可能已发送”
标记时必须进入恢复路径，不能因进度 JSON 较旧就创建新测试。离线验收通过只对绑定的镜像
release ID、硬件配置摘要和 MCU 身份有效；其中任何一项改变都使报告失效并回到验收阶段。

### 9.3 后端验收授权与断电安全封存

后端机器验收仍是平台权威事实。为避免本地网页在不知道平台裁决时提前永久关闭，本计划需要
新增一个窄的可靠 OneNet 命令 `AUTHORIZE_FACTORY_SEAL`：后端只能在当前设备验收状态已经为
`PASSED` 时创建，载荷绑定 deviceName/hardwareSn、验收代次、厂家袋修订号及有序袋集合摘要。
后端可靠任务每次实际下发前仍须重新核对 `PASSED` 和同一代次/摘要，状态或袋集合改变就取消
旧任务。设备只接受目标身份完全匹配、代次和摘要等于本地最近挑战的命令；重复命令幂等，
旧代次或摘要不符不能形成 `CLOUD_ACCEPTANCE_AUTHORIZED`。本命令的 `expiresAt` 只限制设备
首次可靠受理：期限内完整校验并写入 `command_inbox` 后，排队、重启和完全相同的同 UID 重投
均使用数据库保存的原始受理时间继续，不能由载荷伪造该时间；其他命令的执行期、COS 凭证和
远程维护到期校验不放宽。

正式命令处理器必须在同一 EdgeStore 事务中保存封存授权事实并完成命令，再由可重试的最小
投影器通过 root 所有的 Unix Socket 交给首启编排器；不能先把命令标成成功再只做一次易丢失的
文件写入。这个授权发生在正式运行阶段，不属于离线动作测试，也不会让测试报告上传云端。

操作员点击“结束出厂模式”后按以下单向顺序执行：

1. 重新核对当前云端授权、正式凭证、K1 已删除、验收报告摘要、`HANDOFF_SAFE`、运行时健康、
   无当前动作/升级恢复锁和无待处理测试临时文件；
2. 生成 `/var/lib/ecobin/first-boot/sealed.json`，包含 image release ID、设备身份摘要、验收报告
   摘要、云端命令 UID/验收代次和封存时间，以临时文件写入、文件 `fsync`、原子替换、目录
   `fsync` 形成不可逆 `SEALED`；不包含热点密码、设备 Key 或其他秘密；
3. 从这一刻起任何启动路径都不得启动 AP，即使 `/etc/ecobin/setup-ap.key` 仍存在；
4. 幂等执行 `unlink_and_fsync` 删除热点密码，删除临时照片和非终态测试状态，停止 AP/网页，
   加载生产防火墙；随后在同一个 EdgeStore 事务中把授权置为 `SEALED`，并插入唯一的
   `FACTORY_SEAL_COMPLETED` 可靠事件，事件精确绑定授权、验收代次/证据、厂家袋集合、镜像、
   报告、操作员确认和清理完成时间；只有这个事务提交后生产命令门禁才可放行；
5. 再把首启状态投影为 `COMPLETE`；平台可信应用当前代次的完成事件前，不允许把资产分配给
   租户或机构；
6. 任一步断电后，启动程序看到 `SEALED` 只继续第 4 步清理并启动正式运行时，绝不回退到
   `FACTORY_PORTAL_READY`；`sealed.json` 在设备生命周期内不删除。

因此“先写 COMPLETE 再删密码”和“先删密码再决定是否封存”都被禁止：权威单向边界只有先
可靠落盘的 `SEALED`。`AUTHORIZE_FACTORY_SEAL` 是云端给设备的窄授权，
`FACTORY_SEAL_COMPLETED` 是设备完成物理清理后给平台的可靠事实；两者连同具体 schema、后端
调度、设备事务、平台准入和契约测试必须作为一个跨端闭环，不能藏在镜像脚本中。

## 10. K1 生命周期与安全边界

1. K1 在构建机外部的受控文件中生成和保存，镜像构建器以只读 secret mount 读取；
2. 构建日志、命令参数、manifest、SBOM、`.env` 和 Git 均不记录 K1 或其摘要；
3. 封存后 K1 位于 `/etc/ecobin/enrollment.key`，`root:root 0600`；
4. 验收网页和动作执行器使用独立账号/沙箱，并通过 systemd 文件系统隔离明确不可访问 K1、
   正式凭证和 `/etc/ssh` 私钥；
5. 只有一次性注册进程读取 K1，用于后端挑战 HMAC；
6. 后端 READY 响应解密、正式凭证原子写入、fsync、复读和完整校验成功后，沿用现有
   `unlink_and_fsync` 删除 K1；
7. 如果在凭证落盘和删除之间断电，下次启动先复读正式凭证，再继续删除，不重新注册资产；
8. 离线硬件验收允许在 K1 尚未使用时先完成，但验收代码绝不读取 K1；结束出厂模式和离厂
   门禁都要求 K1 文件路径、注册临时状态和一次性注册实现不存在；
9. 未注册或注册失败设备保留 K1，只能留在受控工厂返工区，不允许离厂；
10. 因镜像中包含 K1，正式 `.img.zst` 本身属于秘密制品，只进入受访问控制的内部制品库，
    不能上传公开 Release、普通网盘或提交 Git。

这里的“删除”只表示：注册凭证可靠落盘后，系统执行 `unlink` 并同步父目录，随后所有正常
软件路径和离厂检查都不能再读取 `/etc/ecobin/enrollment.key`。它不等于对 TF 闪存的可靠
物理擦除：明文可能残留在 ext4 空闲块、journal 或 TF 控制器旧物理页，`shred`、覆盖写和
TRIM 也不能给出可验证保证。含 K1 的发布镜像同样意味着发布员、制品库管理员和任何能读取
该镜像的写卡工位技术上都能提取 K1，不能再宣称这些角色“不接触明文 K1”。正式量产前需由
项目负责人明确接受这一威胁模型，或另行授权改成“不含 K1 的镜像 + 工装只向内存注入”。

K1、热点密码、镜像签名私钥、MCU 固件签名私钥和维护 CA 私钥保持五个相互独立的用途，
不得复用。镜像内只包含 K1、热点密码及两类验签公钥，不包含任何签名私钥或维护 CA 私钥。

## 11. MCU revision 2 与摄像头识别

### 11.1 MCU revision 2

厂家在装配前使用线下烧录器给 STM32F103C8T6 写入符合完整固定帧协议的 revision 2 固件。
香橙派首启不承担“把 revision 1 自动升级成 revision 2”的职责，避免首次生产准入依赖尚未
验收的远程升级链路。

本地预检必须取得：

- 合法 F3，`STATUS=00`；
- `mcuFixedFrameRevision=2`；
- 合法版本、单调版本码和 8 字节固件身份；
- 合法 F1，证明通信、重量、红外和烟感读取链路满足当前自检要求。

revision 缺失、等于 1、F3 内部错误或身份字段非法都使预检终态为
`MCU_REVISION_2_REQUIRED`，不启动普通硬件运行时、不删除失败报告，也不允许设备离厂。

### 11.2 两个摄像头

两款摄像头型号和以下稳定路径已经确定为当前量产基线，不再列为实施前待确认项：

```text
outside = /dev/v4l/by-id/usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0
inside  = /dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0
```

路径仍保留为 `/etc/ecobin/hardware.env` 配置项，方便以后更换硬件版本时调整，不在 Python
业务代码中继续写死，也不允许回退到会漂移的 `/dev/videoN`。逐台预检只是证明本机摄像头
没有漏装、损坏或内外接反，不重新选择型号和路径。

预检逐台验证：

- 两个配置字符串非空且不同；
- 两个 symlink 均存在并解析到不同设备节点；
- 节点具备视频采集能力，不是 metadata 节点；
- 能按正式拍照参数完成预热和保存图片；
- 图片可解码、尺寸和最小字节数满足阈值；
- 网页分别标注“箱外 DECXIN”和“箱内 icspring”显示缩略图，由操作员确认没有物理接反。

系统不根据枚举顺序、画面内容或失败回退自动交换内外摄像头。任一摄像头缺失只会产生明确
预检失败，避免把箱内证据当作箱外证据。

## 12. 无需 SSH 的离线出厂验收

### 12.1 操作入口和命名

操作员给设备通电，手机连接 `EcoBin-Factory-<factoryId 八位>`，打开 `http://10.42.0.1/`。整个本地
硬件验收不要求物联网卡在线、可信时间、SSH、显示器、键盘或在香橙派执行命令；网页也不把
Wi-Fi 切换为 Station。即使物联网卡已经联网，工位仍通过同一个 AP 地址完成验收。

页面使用“离线投递硬件测试”和“离线清运硬件测试”，不使用容易让人误解的“模拟投递/清运”。
它们不会产生业务或云端事实。默认生产验收中门锁、电机、屏幕、按钮、称重、传感器和
摄像头都是真实动作，操作员必须按夹伤、坠落和误开门风险对待。当 MCU 刷入保留身份
`factory-sim-1.0.0 / ECOSIM01` 时，UART 和香橙派状态机仍真实执行，但 MCU 侧外设证据
明确标记为 `SIMULATED_PERIPHERALS`；网页必须显示告警，操作员仍逐步操作并在终结时再确认。

“开始验收”只要求镜像身份和权限有效、安全 GPIO 已建立、普通硬件进程没有运行且不存在未
恢复的 MCU/动作锁；不要求正式凭证已经落盘，也不要求 K1 已删除。重复点击返回同一运行，
不能并发创建第二次验收或同时占用 UART/GPIO/摄像头。存在恢复锁时页面只开放“查看原因”和
“执行受控恢复”，不开放新的投递、清运、升级线路或封存操作。

### 12.2 分阶段强制检查项

| 阶段/类别 | 检查内容 | 是否依赖上行网络 | 失败影响 |
|---|---|---:|---|
| 镜像 | manifest 签名、关键文件摘要、配置权限 | 否 | 镜像不可用，重新写卡 |
| 本地系统 | machine-id、磁盘可写/剩余空间、systemd 单元、安全 GPIO | 否 | 保持验收热点，禁止动作和正式运行时 |
| 热点隔离 | AP/DHCP/DNS、网页只绑定 `10.42.0.1`、无 WAN 转发、SSH 不可达 | 否 | 禁止开始验收 |
| UART5 | `/dev/ttyS5`、无其他占用、115200/8N1 F3/F1 | 否 | 禁止正式运行时 |
| MCU 固件 | F3 `STATUS=00`、revision 2、版本和身份 | 否 | 禁止动作、注册推进和离厂 |
| 称重精度 | 空载稳定值、放置 500 g 砝码后的稳定值及 490～510 g 增量 | 否 | 禁止本地验收通过 |
| 升级线路 | 操作员先声明是否安装；已安装时执行 F2 准备、BOOT0/NRST、ROM Bootloader 非写入探测、复位回原身份和 F1 | 否 | 已安装但失败时进入可恢复失败；未安装记录 `NOT_APPLICABLE` 并禁止远程升级，不阻止其他验收与业务 |
| 摄像头 | 两个稳定路径、采集能力、各拍一张、人工确认内外 | 否 | 禁止本地验收通过 |
| 投递动作 | BB/AA 单次下发、MCU 屏幕/按钮/投递机构、最终 DD 和临时双摄画面 | 否 | 失败或恢复锁，禁止本地验收通过 |
| 清运动作 | EE 单次下发、MCU 屏幕/按钮/清运锁、最终 EF 和临时双摄画面 | 否 | 失败或恢复锁，禁止本地验收通过 |
| 本地恢复 | 最小状态原子写/fsync、强杀后复读、MCU 复位及人工门体确认 | 否 | 禁止开始下一动作或正式业务 |
| 蜂窝上行 | Air780E、SIM、信号、PDP、RNDIS 接口/IP、默认路由、DNS、HTTPS | 是 | 不影响已完成硬件结果；阻止注册和离厂 |
| 校时/注册 | 可信时间、正式凭证、K1/临时状态/一次性实现可靠删除 | 是 | 保留热点和 K1，禁止正式业务和离厂 |
| 正式服务 | 维护 SSH 配置、远程代理凭证投影、MQTT/普通硬件 target 健康 | 是 | 禁止封存和后端机器验收 |
| 云端验收/封存 | 初始袋、OneNet/COS 机器证据、后端 `PASSED` 和当前代次封存授权 | 是 | AP 保持只读；禁止写入 `SEALED` 和离厂 |

系统时间未同步时可以执行纯本地硬件动作。报告先保存单调时钟、boot ID 和“墙上时间不可信”
标记；可信校时完成后再补充审计时间，不反向篡改动作结果。物联网卡失败也不要求重做已经绑定
同一镜像、MCU 身份和硬件配置的本地验收。

称重精度暂按已确认的 500 g 砝码、±10 g 误差执行，并通过上述两个非秘密配置项固化。操作员
先清空承重面，网页取得空载稳定值 `W0`；按提示放置 500 g 砝码并取得加载稳定值 `W1`；只有
`490 <= W1 - W0 <= 510`（单位克，边界包含）才通过。报告保存 `W0`、`W1`、增量、目标重量和
容差，不保存为订单、清运或生产重量事实。判定后必须提示操作员取下砝码，并在读数重新稳定
接近 `W0` 后才允许继续投递/清运动作测试。读数不稳定、超时、无法形成有效增量或砝码未取下
都明确失败，不能通过扩大容差自动放行。以后改变砝码或容差只生成新的硬件配置/镜像发布版本，
不修改历史验收报告。

操作员在开始本轮验收时必须选择升级线是否安装，选择与本轮状态和最终报告绑定。
未安装时不得发送 `F2 02 F2`、不切换 GPIO、不运行 `stm32flash`；报告写入
`MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED`，首启安全交接再原子写入能力文件。已安装时，
“升级线路”只探测 STM32 ROM Bootloader 和复位恢复，不擦除、不写入 MCU Flash。流程在发送
F2 前持久化原 F3 身份和恢复要求；任何掉电或异常都必须先 BOOT0 回低、应用复位、核对原
身份并通过 F1，之后才能解除本地预检维护锁。恢复失败进入 `RECOVERY_REQUIRED`。

### 12.3 投递和清运动作状态机

两种动作共用一套只有一个活动槽位的本地状态机：

```text
IDLE → ARMED → COMMAND_MAY_HAVE_BEEN_SENT → WAITING_FINAL_RESULT
                                                ├→ RESULT_RECORDED → SAFE_VERIFIED
                                                └→ RECOVERY_REQUIRED
```

进入 `ARMED` 前，页面要求操作员确认设备周围无人、门体无阻挡、测试物和清运操作安全。发送
AA 或 EE 前先以临时文件、`fsync`、原子替换和目录 `fsync` 落盘
`COMMAND_MAY_HAVE_BEEN_SENT`，然后最多执行一次串口写入；不能用“串口 write 返回成功”推断
MCU 已执行，也不能因没有 ACK 重发。收到最终 DD/EF 后先原子保存结果，再执行 F3/F1 和现场
安全确认，最后进入 `SAFE_VERIFIED`。

投递动作使用固定、明显的验收单价测试 BB 显示，不参与金额计算；随后发送一次 AA，由 MCU
负责屏幕和按钮状态机。BB+AA 预先组成一个输出缓冲，只做一次完整写尝试；部分写入或异常也
按“可能执行”进入恢复，不能补发任一帧。网页等待 DD，并展示投递前/后重量、差值和红外结果。
清运动作同样只对 EE 做一次完整写尝试，由 MCU 负责屏幕、按钮和清运锁，网页等待 EF，并展示
清运前/后重量、清出重量和红外结果。
两个流程都可以显示本地临时前后画面，但不上传、不进入生产照片目录，测试结果也不更新皮重、
当前袋或满溢状态。

协议没有过程事件，网页不得把电磁阀通电解释为门已打开，也不得自行显示按钮或关门已完成。
清运门没有门磁，最终必须由操作员确认门扇关闭。网页上的“停止/恢复”不是清除状态：它只能
停止继续下发、复位 MCU、验证原 F3/F1 并引导人工检查；任一事实不满足就保持
`RECOVERY_REQUIRED`。

### 12.4 报告和本地/云端边界

报告状态只有 `NOT_RUN / RUNNING / PASSED / FAILED / RECOVERY_REQUIRED`。最终 JSON 以
0600 原子保存，包含镜像 release ID、boot ID、可信/非可信测试时间、MCU 身份、摄像头配置
摘要、投递/清运最终数值和每项稳定结果码；不包含用户、订单、袋码、热点密码、K1、OneNet
Key、照片原图、完整串口载荷或云端命令标识。报告不是生产 EdgeStore 事件，不进入 MQTT
outbox，也不触发后端接口。

本地 `PASSED` 只证明镜像和本机硬件；它不替代后端机器验收。正式运行时启动后，厂家仍要
通过小程序真实扫描每个初始 EB1 袋，并等待后端依据 OneNet、COS、设备自检等事实把机器
验收推进为 `PASSED`。离线动作中的测试物不具有业务归属，也不能产生返现、钱包或清运记录。

设备内公钥和被校验文件位于同一张未启用安全启动的 TF 卡，因此本机 manifest 校验只能发现
意外损坏或配置漂移，不能抵抗能够同时改写根文件系统和公钥的攻击者。镜像来源真实性的
权威门禁是写卡工位在写入前使用工位外可信公钥验证 detached signature。

## 13. 镜像发布物与版本追溯

每次量产发布生成独立目录：

```text
dist/orangepi/<image-release-id>/
  ecobin-orangepi-zero3-<version>.img.zst
  image-manifest.json
  sbom.spdx.json
  apt-packages.txt
  python-packages.txt
  build-attestation.json
  build-attestation.sig
  target-media-qualification-evidence.json
  rootfs-qualification-evidence.json
  seal-evidence.json
  seal-evidence.sig
  schemas/release-manifest.schema.json
  schemas/build-attestation.schema.json
  schemas/target-media-qualification-evidence.schema.json
  schemas/rootfs-qualification-evidence.schema.json
  schemas/seal-evidence.schema.json
  release-checksums.txt
  release-checksums.sig
  flash-and-verify.ps1
  flash-and-verify.sh
  factory-checklist.md
```

`release-checksums.txt` 固定覆盖上面除自身和签名外的 19 项成员；`release-checksums.sig` 再由
外部发布密钥认证这份精确清单。多文件、少文件、改名或任何成员摘要不一致都不能进入可信写卡。

`image-manifest.json` 至少记录：

- image release ID、语义版本和 Git 提交；
- 基础镜像 URL/文件名/SHA-256；
- 构建器 digest、锁文件摘要和发布纪元；
- 板型、内核、标称 32 GB TF 目标、固定原始镜像大小、扩容 schema、UART overlay、串口设备和
  GPIO 映射；
- 普通硬件、注册、远程维护、验收执行器和验收网页的版本；
- 板载 Wi-Fi 驱动/固件及 AP 能力验证基线，Air780E USB RNDIS 能力检查和配置 schema；
  模组实际版本、USB VID/PID 和接口布局写入逐台验收报告，不作为共享镜像的锁定输入；
- first-boot/factory-test/SEALED schema 版本和 `AUTHORIZE_FACTORY_SEAL` 契约版本；
- MCU 主板兼容标识和已安装验签公钥指纹；
- 配置 schema、文件清单摘要和洁净审计结果；
- `enrollmentKeyId=K1`、`k1Injected=true`，但不记录 K1 摘要或值。

镜像使用独立的 Ed25519 镜像发布密钥生成 detached signature；该密钥不与 MCU 发布签名、
维护 CA 或 K1 复用。烧录工具先验签、再校验 SHA-256、写卡、复读校验，任何一步失败都不
把卡标记为可装机。

发布目录不提交 Git。无秘密的 manifest 模板、schema、构建脚本和测试可以纳入仓库；包含
K1 的镜像、真实 manifest 实例和签名进入受控内部制品库，并设置最小访问权限和保留策略。

## 14. 实施切片与文件影响范围

实施已获得整体授权，仓库内软件按以下切片落地；“完成证据”列同时包含自动化证据和仍需真机
取得的证据，因此软件存在不等于该切片已经获得量产放行：

| 切片 | 内容 | 主要文件/目录 | 完成证据 |
|---|---|---|---|
| P1 输入锁与镜像骨架 | 锁基础镜像、构建容器、32 GB TF 布局/扩容和依赖 | `tools/orangepi-image/` | 能构建未注入秘密的紧凑候选镜像，并在 32 GB 卡上幂等扩容 |
| P2 统一运行包 | 版本化 `/opt` 安装、显式 `hardware.env`、FHS 数据路径 | `hardware/config.py`、安装器、service | 无代码目录 `.env` 也能启动测试运行时 |
| P3 UART/GPIO 固化 | `ph-uart5`、冲突检查、安全 GPIO、wPi 2/5、NRST 开漏高有效控制 | 镜像脚本、新 oneshot unit | 真机出现 ttyS5，GPIO/MOS/NRST 电平实测正确 |
| P4 验收热点与网页 | 固定 AP、hostapd/dnsmasq/nftables 隔离、低权限移动端页面 | `hardware/factory/`、systemd units | 无 SIM 也能访问；热点客户端不能到 WAN/SSH |
| P5 首启与物联网卡编排 | 持久状态机、Air780E RNDIS 自动发现/健康查询、锁定 NetworkManager profile、systemd target、断电恢复 | `hardware/first_boot/`、网络单元 | 先验收后注册；插卡自动联网；每阶段强杀后按权威事实续跑 |
| P6 K1 封存与清理 | secret 注入、权限、日志防泄漏、离厂检查 | 镜像 seal/verify、注册单元 | READY 后 K1 路径和注册实现可靠逻辑删除；物理残留风险有书面决策 |
| P7 隔离本地验收 | revision 2、F1、Bootloader、双摄、AA/DD 投递、EE/EF 清运、最小恢复状态和报告 | `hardware/factory/`、网页代理 | 无 SSH/后端/生产 DB 完成真实动作并可恢复失败 |
| P8 云端封存闭环 | `AUTHORIZE_FACTORY_SEAL` 可靠授权、设备清理后原子生成 `FACTORY_SEAL_COMPLETED`、平台当前代次准入 | `contracts/onenet/`、device/operations/integration 模块、`hardware/` | 旧代次不能封存；完成事实不丢不重；平台收到前不能分配租户/机构 |
| P9 发布物 | 外部构建证明、封存证据、确定性压缩、签名、SBOM、可信烧录复读 | `tools/orangepi-image/release*` | 两次无秘密候选 SHA 一致且证明可验签，单张封存镜像和证据闭包完整，试点卡验收通过 |
| P10 文档与试点 | 量产手册、返工、回退、真实一机试点 | `hardware/docs/`、`docs/deployment/` | 项目负责人签认后才扩大写卡 |

预计需要新增或调整的 systemd 单元：

```text
ecobin-mcu-safe-gpio.service
ecobin-expand-rootfs.service
ecobin-edge-store-prepare.service
ecobin-factory-egress-lock.service
ecobin-first-boot.service
ecobin-factory.target
ecobin-factory-ap.service
ecobin-factory-portal.service
ecobin-factory-test.service
ecobin-factory-handoff.service
ecobin-cellular-uplink.service
ecobin-runtime.target
ecobin-enrollment.service
ecobin-remote-support.service
ecobin-hardware.service
```

除 P8 的窄封存授权、封存完成事实和资产分配门禁外，不修改后端业务、OneNet 验收证据、小程序
或 MCU 固定帧协议。离线验收
只使用当前 F1/F3 和最终 DD/EF，本地报告不上传；后端继续使用已有运行快照、F3 revision
登记、厂家袋扫码和机器验收，只有在权威状态为 `PASSED` 后追加可靠封存命令。如果以后要求
网页展示门锁/按钮/屏幕的逐步状态，必须先提交独立 MCU 协议修订设计，不在镜像任务中用推测
状态或自动重发弥补协议缺口。

## 15. 测试、故障注入与验收门槛

### 15.1 自动测试

- Python 3.11 单元测试：首启/动作状态转换、重复点击、单槽互斥、称重增量 489/490/510/511 g
  边界、输入校验、权限、脱敏和恢复门禁；
- Linux 集成测试：systemd 依赖/`Conflicts=`、Unix Socket 权限、NetworkManager、Air780E AT/RNDIS、
  hostapd/dnsmasq/nftables 模拟、阶段 unit 无独立 enable、持久 gate 不可绕过，以及验收执行器
  与正式硬件进程不可并发；
- 镜像静态测试：只读挂载、32 GB 布局余量/首次扩容门禁、文件权限、service enable 状态、
  UART overlay 和禁止文件清单；
- 可重复构建测试：同一输入由两个策略固定的独立构建者分别构建，比较无秘密候选 `.img`、
  manifest 和软件清单，验签两个不同 invocation UID/身份/构建域的 build receipt，再验证聚合
  build attestation；不要求两次读写注入秘密后的 ext4 raw 字节相同；
- 模拟 MCU/摄像头测试：revision 1、F3 内部错误、F1 无效、摄像头缺失/互换、Bootloader
  探测中断、AA/EE 无 ACK、正常/迟到/重复/损坏 DD/EF、串口断开和 MCU 复位；
- 数据隔离测试：动作前后生产 `edge.db` 不存在或摘要不变，生产照片目录、command inbox/outbox、
  订单/清运/袋/皮重记录均无新增；
- 网络隔离测试：由假蜂窝网关和出站抓包器证明动作测试期间没有 DNS、HTTP(S)、MQTT、COS
  或任意 WAN 包，系统 NTP/NetworkManager/Air780E 上行服务也不能越过整机 OUTPUT 锁，AP 客户端
  不能转发到蜂窝/以太网；
- 交接测试：复位/清缓冲/静默窗口各阶段注入迟到 DD/EF，正式进程没有活动生产槽位时只能
  隔离，不能生成 EdgeStore/outbox 事实；
- 跨端封存测试：后端只在机器验收 `PASSED` 后生成当前代次 `AUTHORIZE_FACTORY_SEAL`，覆盖
  任务丢失、重复、旧代次、袋摘要改变、设备事务后崩溃和投影重试；设备完成清理时必须在同一
  事务写 `SEALED + FACTORY_SEAL_COMPLETED`，覆盖事件先于 `ACCEPTED`、重复完成、冲突载荷、
  时间跨毫秒、平台入站重试，以及完成事实到达前租户/机构分配被拒绝；
- 安全测试：K1/密码不出现在日志、进程 argv、网页、报告、manifest 和 SBOM；网页无法
  访问公网接口、SSH 或任意文件；
- 现有硬件 Python 回归、协议契约回归和 MCU 升级模拟套件全部继续通过。

### 15.2 故障注入

至少覆盖：

1. 32 GB TF 根分区/文件系统扩容、早期整机出站锁、安全 GPIO、AP、DHCP/网页和局域网防火墙
   加载前后断电；
2. 手机重复点击开始、页面刷新/断开、两个客户端同时请求投递或清运；
3. AA/EE 的 `COMMAND_MAY_HAVE_BEEN_SENT` 落盘前、落盘后、串口写入中和写入后强杀；
4. AA/EE 无 ACK、最终 DD/EF 超时、迟到、重复、字段非法或到达错误本地运行；
5. DD/EF 已保存但 F3/F1 复验、人工门体确认或清理活动标记前断电；
6. `RECOVERY_REQUIRED` 中 MCU 无响应、身份改变、F1 失败或清运门尚未人工确认关闭；
7. F3 查询、F2 发送、BOOT0 拉高、ROM 探测、BOOT0 回低、应用复位和 F1 验证各阶段中断；
8. 第一或第二摄像头打开、预热、写临时图或删除临时图时失败；
9. 空载或加载读数不稳定/超时、增量为 489/511 g、砝码未取下，以及称重结果写报告时中断；
10. 验收状态/报告写入前后磁盘满、文件系统只读或进程退出；
11. Air780E 不响应、USB RNDIS 未出现或枚举不完整、未插 SIM、SIM 被锁、无信号、PDP 已激活
    但 RNDIS 无 DHCP/默认路由/DNS，以及实际遇到专网卡时缺少 APN/白名单；
12. DNS 成功但后端 HTTPS 不可达，或网络正常但系统时间未同步；
13. 注册请求发送前、PENDING、READY 响应解密后、凭证落盘后、K1 删除前断电；
14. 验收执行器复位 MCU、清串口、静默等待、F3/F1 复验、释放节点与正式进程接管之间断电，
    并在每个边界注入迟到 DD/EF；
15. 后端验收未通过、封存授权丢失/重复、旧验收代次、厂家袋摘要变化和设备投影前后断电；
16. 原子写入/fsync `SEALED`、删除热点密码/K1 残留、停止 AP/网页、加载生产防火墙、原子写入
    本地 `SEALED + FACTORY_SEAL_COMPLETED`、提交后上报和写入 `COMPLETE` 各步骤之间断电；
17. 正式设备后续物联网卡离线重启，确认不会重新开放出厂热点或重复执行验收。

### 15.3 真机发布门槛

模拟和离线镜像测试不能单独批准量产。至少一套真实 Orange Pi Zero 3 + STM32F103C8T6 +
DECXIN + icspring 必须证明：

- UART5、wPi 2/5、电平和 ROM Bootloader 非写入探测均通过；
- MCU revision 2、F3/F1 和应用复位后原身份一致；
- 两个摄像头连续多次冷启动路径稳定、画面角色正确；
- 板载 Wi-Fi AP 在未插 SIM、蜂窝离线和蜂窝在线三种情况下都能从手机稳定访问；热点客户端
  不能访问设备 SSH 或经物联网卡上网；
- 使用现有 Air780E 载板和 RNDIS 完成 SIM/APN、PDP、DHCP IP、默认路由、DNS、校时和 HTTPS
  验证，并证明正常冷启动不需要确定固件版本、人工 AT 命令或 SSH；
- 以太网同时接通时，注册、DNS/NTP、OneNet、COS 和后端探针仍经锁定蜂窝接口，正常重启后
  蜂窝协调服务持续存在并能断线重拨；
- 空载与 500 g 砝码测试的稳定增量位于 490～510 g，并把原始读数和判定写入本地验收报告；
- 离线投递测试真实完成 BB/AA→DD，离线清运测试真实完成 EE→EF，屏幕、按钮、门锁/电机、
  称重和红外行为与操作说明一致；
- 动作测试期间抓包确认没有后端、OneNet、COS 或其他 WAN 请求，生产 EdgeStore 和生产照片
  目录保持未创建或字节级不变；
- 在受控工装和安全监护下，于 AA/EE 关键窗口断电，证明只会进入原测试的安全恢复，不会
  恢复或重放物理流程，也不会自动重发或解除恢复锁；
- OneNet 在线、厂家初始袋扫码、COS 上传和后端机器验收最终 `PASSED`；
- 正式 UART 接管边界注入迟到 DD/EF 时没有生产业务事实；后端当前代次封存授权到达前 AP
  始终保持只读可访问，授权到达并由操作员确认后才能形成 `SEALED`；
- 在 `SEALED` 到热点密码删除/服务停止的各窗口断电，重启均不重开 AP，最终 `COMPLETE` 后
  K1/热点密码文件路径、临时验收照片和注册实现均不可由正常文件系统路径访问；
- 已完成设备重启不会重新进入出厂模式。

## 16. 发布、回退与设备离厂边界

### 16.1 发布顺序

1. 用实测最小 32 GB TF 卡形成介质资格证据，完成目标板启动/扩容和确定性 ext4 独立资格审查，
   锁定 layout 与仓库外正式发布策略；
2. 由两个策略固定的独立构建者分别构建无秘密候选、运行静态测试并签署各自 build receipt；
3. 独立验签回执、比较两份候选、复算 target-media/rootfs 资格证据并签署聚合 build attestation；
4. 在无活动 swap/zram（交换空间）的隔离环境挂载 K1、热点密码和封存证据私钥，生成一张封存
   镜像及签名证据，再生成完整签名发布目录；
5. 写入一张标称 32 GB 的可返工 TF 卡，验证根分区/文件系统扩容；不插网络也能通过热点完成
   全部离线硬件和真实动作验收；
6. 接入量产物联网卡，完成校时、注册、K1 清理、UART 安全交接和正式运行时接管；
7. 完成 OneNet、厂家袋扫码、COS 和后端机器验收；
8. 设备收到当前代次 `AUTHORIZE_FACTORY_SEAL` 后，由操作员在本地网页确认封存并等待
   `SEALED → COMPLETE` 清理完成；后端必须可信应用同一代次 `FACTORY_SEAL_COMPLETED`，在此
   之前不能分配租户或机构；
9. 断电冷启动复验一次，确认只进入正式运行时且不重开热点；
10. 项目负责人确认试点证据后才扩大批量写卡。

### 16.2 回退边界

- 尚未注册、尚未封存且没有 AA/EE/F2 活动或恢复锁的卡，可以直接重写上一个已签名镜像；
- 一旦本地状态表明 AA、EE 或 F2 可能已发送，禁止直接拔卡重写以“清除错误”；必须先按原
  流程复位 MCU、验证 F3/F1 并完成人工门体安全确认，避免抹掉唯一恢复事实；
- 注册尚未完成但已有 `/var/lib/ecobin/enrollment-state.json` 时优先修复并继续原注册，不能
  通过删除状态反复创建新资产；
- 已注册设备无论是否已经 `SEALED`，都不得通过复制另一台卡或恢复“干净母镜像”冒充原设备。
  云端验收前失败时保留当前 AP 只读诊断入口和正式身份继续返工；需要重装时必须先安全备份该设备
  正式凭证和权威状态，另行制定身份保留恢复步骤；
- 普通硬件代码版本回退只切换 `/opt/ecobin/hardware/current`，保留 `/etc/ecobin` 和
  `/var/lib/ecobin`；如果 SQLite 已发生不兼容前向迁移，则禁止盲目回退旧代码；
- 远程维护代理独立发布，普通硬件回退不得中断正在进行的维护租约。

### 16.3 设备离厂门禁

每台设备只有同时满足以下事实才能离厂：

- `/etc/ecobin/device-credentials.json` 完整有效；
- K1/热点密码文件路径、注册临时状态和一次性注册实现均不存在；
- `sealed.json` 绑定当前镜像、本地报告和当前后端验收代次，首启状态为 `COMPLETE`；
- 本地出厂预检 `PASSED`，没有 MCU/预检恢复锁；
- 500 g 砝码的空载差分结果位于 490～510 g；
- MCU F3 为 revision 2 且 F1 健康；若声明已安装升级线，BOOT0/NRST 探测后必须回到原应用身份；
  若未安装，能力文件与平台资产必须明确为 `false`；
- 两个摄像头路径和人工内外确认通过；
- 离线投递和离线清运动作测试均为 `SAFE_VERIFIED`，生产 EdgeStore/照片目录未被测试污染；
- 验收临时照片和活动恢复状态均已清理，最终报告不含业务身份或照片原图；
- 正式运行时、OneNet、远程维护本地代理均健康；
- 所有初始 EB1 袋已真实扫描；
- 后端机器验收为 `PASSED`；
- 当前后端验收代次的 `AUTHORIZE_FACTORY_SEAL` 已可靠处理，后端已可信应用同一授权的
  `FACTORY_SEAL_COMPLETED`，不存在旧代次封存；
- 临时热点和验收网页已停止，设备重启不会重新开放。

## 17. 明确不做的事项与仍需现场确认项

本计划明确不做：

- 不增加首启防克隆检查或基于 K1 缺失推断克隆；
- 不把 K1 改成逐台人工录入，也不在本阶段引入 TPM/安全芯片；
- 不让香橙派自动给全新 MCU 首刷 revision 2，厂家仍用线下烧录器；
- 不支持 Linux 5.4 `/dev/ttyAS5` 自动兼容；
- 不按 `/dev/videoN` 自动探测或互换内外摄像头；
- 不把板载 Wi-Fi 用作量产 Station 上行，也不在验收网页提供通用 Wi-Fi/APN 配置器；
- 不在已注册设备每次掉线时自动重开热点；
- 不给本地网页提供 shell、固件上传或永久运维入口；
- 不把离线投递/清运接入正式 EdgeStore、MQTT、OneNet、COS、后端、订单、袋或钱包链路；
- 不根据串口写入、电磁阀通电或页面计时推断门位/按钮等 MCU 未上报的过程状态；
- 不用本地预检替代后端机器验收和真实初始袋扫码；
- 不把含 K1 的镜像放入 Git 或公开制品库。

软件实现完成后仍须现场确认以下硬件/环境事实，不重新讨论已确定方向：

1. 锁定的 Debian 12 / Linux 6.1 官方基础镜像在目标 Orange Pi Zero 3 批次和标称 32 GB TF
   卡上可启动、可扩容；
2. 对声明已安装远程升级线的机型，BOOT0/BOOT1 下拉、2N7002 Gate 下拉、NRST 上拉和电容
   已经落板，物理 11 号针经开漏级控制 NRST 并通过仪表复核；未安装机型的平台升级准入为关闭；
3. 真实板载 Wi-Fi 驱动/固件在目标内核支持稳定 AP，并确认工厂手机兼容 WPA2 和 2.4 GHz；
4. 工厂明确 K1、已接受的全批次共用热点密码和镜像签名私钥的受控输入路径及制品库权限；
5. 准备带安全监护和断电开关的一台可返工设备、已确定的 DECXIN/icspring 摄像头、当前
   Air780E 载板和物联网卡、500 g 砝码、清运工装及一组真实 EB1 袋，
   用于首个完整试点。

项目负责人已授权进入 P1～P10 实施。该授权允许修改仓库内代码、测试、无秘密镜像构建工具和
文档，但不等于允许把真实 K1/密码/私钥写入仓库、生成并外发含 K1 的正式镜像，或在没有逐项
安全核对时操作真实生产设备。
