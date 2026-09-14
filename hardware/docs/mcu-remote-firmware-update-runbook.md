# STM32F103C8T6 远程固件升级实施与操作手册

> **当前仅适用于历史 `FIXED_FRAME` 固件。** UART v2（`ECOBIN_UART`）的远程
> 固件升级尚未实现；`.efw` schema 1 会拒绝 UART v2 身份。UART v2 固件当前只能按
> 已确认的人工烧录流程处理，不能使用本文流程登记或下发。

> 适用范围：EcoBin 主板 `ECOBIN_MAINBOARD_V1.1`、MCU `STM32F103C8T6`、
> 香橙派 Zero 3、固定帧协议修订号 2、OneNet 正式链路。
>
> 结论：香橙派可以通过现有 UART 烧录 MCU，但并非“只接 TX/RX 后适配软件”即可。
> 除交叉连接的 UART 和共地外，香橙派必须可靠控制 `BOOT0` 与 `NRST`；MCU 必须先运行
> 支持 F2 执行准备和 F3 固件身份查询的修订号 2 固件。远程发布还依赖签名包、私有 COS、
> OneNet 合约、后端当前 V60（固件表由 V55 引入）和人工灰度流程。
> 未安装 BOOT0/NRST 线的设备仍可运行普通 UART 业务并完成出厂封存，但验收必须
> 写入 `mcuRemoteUpdateCapable=false`，平台不得把它加入灰度计划。

## 1. 已实现的完整链路

正常远程升级由平台管理员发起，具体顺序如下：

```text
离线构建并签名 .efw
  → 上传私有 COS 并在 Web 登记不可变发布
  → 创建灰度计划，指定一台验证机和后续批次
  → 人工启动单机验证
  → 后端登记可靠 OneNet 命令（不保存 COS 临时密钥）
  → 实际发送前生成最长 15 分钟的只读 COS 凭证
  → 香橙派检查维护锁、工作槽和未决命令，空闲时原子建立升级日志与维护锁
  → 香橙派下载固件包，验签并校验主板兼容标识、版本和摘要
  → 香橙派把当前 F3 固件身份和“F2 可能已执行”标记持久化到 SQLite
  → 香橙派发送 F2，MCU 停止本地活动、关闭输出并锁存升级执行状态
  → 香橙派关闭应用 UART，拉高 BOOT0 并脉冲 NRST
  → stm32flash 以 115200/8E1 写入 0x08000000 并回读校验
  → BOOT0 恢复低电平，复位进入应用
  → F3 核对版本、版本码、身份和协议修订号，F1 完成传感器自检
  → 成功后解除维护锁；失败则最多三次并自动刷回上一份稳定包
  → 进度事实经 OneNet 回传，后端更新设备、部署和灰度状态
```

灰度不会自动扩散。验证机成功后必须人工“确认推广”，每一批也必须人工“下发下一批”。
当前批次只要存在运行中、回滚或失败锁定设备，后端就会阻止下一批。

升级是否可以开始由香橙派决定，不由 MCU 再判断一次。收到平台命令时，香橙派以本地
SQLite 为准：已有维护任务、投递/清运占用唯一工作槽或其他物理命令尚未收敛时，本次升级
直接形成可靠 `REJECTED` 事实，且不会向 MCU 发送 F2；本地空闲时，升级记录和维护锁在
同一事务写入，从而阻止后续新业务。MCU 收到 `F2 02 F2` 只负责执行停机与锁存，并通过
F3 报告执行结果。现有投递、清运和屏幕状态机边界不因此迁移到香橙派。

在香橙派把 F2 写入 UART 之前，它会先将当前 F3 身份快照和恢复标记同步写入
SQLite v14。一旦该标记存在，即使 F2 应答丢失、`PREPARED` 写库失败或进程在两步之间
重启，也不能直接解锁。香橙派必须先让 MCU 回到应用、核对该快照并通过 F1 自检；
恢复成功才记录 `REJECTED` 并解除维护锁，任一验证失败都记录 `FAILED_LOCKED` 并继续阻止业务。
v13 升级到 v14 时，旧库中尚未擦写的非 legacy `PREFLIGHT/PREPARED` 记录也按“F2 可能已执行”
迁移：能从上一稳定 Manifest 重建身份时自动恢复验证，无法重建时保持锁定等待人工处理。

## 2. 为什么需要四类信号

ST 官方 AN2606 规定 STM32F10xxx 的系统存储器 Bootloader 使用 USART1：MCU 的 PA10
为接收，PA9 为发送；初始化后为 8 数据位、偶校验、1 停止位。AN3155 规定主机首先发送
带偶校验的 `0x7F` 完成同步。官方依据：

- [AN2606：STM32 系统存储器启动模式](https://www.st.com/resource/en/application_note/an2606-stm32microcontroller-system-memory-boot-mode-stmicroelectronics.pdf)
- [AN3155：STM32 Bootloader USART 协议](https://www.st.com/resource/en/application_note/an3155-usart-protocol-used-in-the-stm32-bootloader-stmicroelectronics.pdf)

量产主板固定使用以下接线。环境变量使用 WiringOP 的 `wPi` 编号，不是 26 针排针号，也
不是 H618 的 GPIO 名称。

| 香橙派侧 | STM32F103C8T6 侧 | 用途与要求 |
|---|---|---|
| 物理 8 / UART5 TX | PA10 / USART1 RX | 交叉连接，3.3 V TTL |
| 物理 10 / UART5 RX | PA9 / USART1 TX | 交叉连接，3.3 V TTL |
| GND | GND | 必须共地 |
| 物理 7 / PC9 / wPi 2 | BOOT0 | 高电平选择系统 Bootloader；默认必须有约 10 kΩ 下拉 |
| 物理 11 / PC6 / wPi 5 | 2N7002 Gate | 经 1 kΩ 驱动；Gate 以 100 kΩ 下拉；高电平使 MOS 导通并拉低 NRST |
| 2N7002 Drain | NRST | MCU 侧以 10 kΩ 上拉到 3.3 V，并保留 10～100 nF 对地电容 |
| 2N7002 Source | GND | 与香橙派和 MCU 共地，形成开漏复位控制 |
| 固定下拉 | BOOT1 / PB2 | 约 10 kΩ 下拉，保证 BOOT0=1 时进入系统存储器 |

不要把 5 V UART 直接接到 MCU 或香橙派，也不要用普通推挽反相器直接驱动 NRST。BOOT0、
2N7002 Gate 和 NRST 在香橙派启动、崩溃或 GPIO 尚未初始化时，必须由硬件电阻分别保持
“BOOT0=0、Gate=0、NRST=1”的正常运行状态。若现有主板没有
引出 BOOT0/NRST，应先改板或增加可靠控制电路；仅靠应用固件主动跳转 Bootloader 不能覆盖
固件损坏或卡死场景，不作为本方案的恢复边界。

应用运行时 UART 为 115200/8N1；进入 ROM Bootloader 后同一串口临时切为
115200/8E1。设备程序会先关闭应用串口，`stm32flash` 退出后再恢复应用串口，禁止其他
进程同时打开该设备节点。

## 3. MCU 固件前置条件

当前本地 MCU 工程的必要改动位于 `hardware_mcu/USER/`：

- `firmware_identity.h`：每次发布生成的版本、单调版本码和 8 字节发布身份，不提交 Git；
- `usart1.c/.h`：F2“查询身份/执行升级准备”和 F3“身份/执行结果”固定帧；
- `main.c`：收到 F2 模式 02 后无条件停止本地投递、清运和称重活动，关闭输出并锁住新输入；
- `mcu_update_execution.c/.h`：不依赖 STM32 寄存器的停机与锁存转换，可由桌面 Clang 回归；
- `STM32-DEMO.uvprojx`：STM32F103C8 使用 `STM32F10X_MD` 和 medium-density 启动文件，
  构建完成后用 `fromelf` 生成 `Output/STM32-DEMO.bin`。

全部固定帧字节、字段、校验和升级切换边界都已写入
[MCU—香橙派完整单文件协议](单片机-香橙派适配通信协议详细内容.md)。F2/F3 用于应用层
固件身份和停机执行确认，真正擦写仍由芯片出厂 ROM Bootloader 完成。

`hardware_mcu/` 的 Keil 工程、MCU 业务源码、CMSIS 和 STM32F10x 标准外设库已经纳入
主仓库。`Output/`、`Listing/`、历史备份、开发机配置和发布生成的
`USER/firmware_identity.h` 仍由目录内 `.gitignore` 排除。构建方法和纳管边界见
[`hardware_mcu/README.md`](../../hardware_mcu/README.md)。发布制品仍须同时保存源提交、
Keil 工具链版本、`.bin` 和签名包，不能只保留某台开发机上的输出目录。

## 4. 首次生成发布身份与签名包

### 4.1 签名密钥边界

使用 Ed25519。私钥只存在于离线发布机或专用签名环境，不放入 Git、不上传后端、不下发
设备。香橙派只安装公钥。示例命令中的路径均为占位符：

```bash
openssl genpkey -algorithm Ed25519 -out /secure/mcu-release-private.pem
openssl pkey -in /secure/mcu-release-private.pem -pubout \
  -out /secure/RELEASE_2026_01.pem
```

设备公钥文件名的主干就是 `signingKeyId`。可同时安装多把公钥以完成轮换；移除旧公钥前，
必须确认所有可能用于回滚的稳定包仍可验证。

### 4.2 在编译前生成身份头

在仓库根目录运行，`releaseUid` 必须是本次后端发布使用的同一个 UUIDv4，
`version-code` 必须单调递增：

```bash
uv run --directory hardware --python 3.11 python mcu_firmware_package.py identity \
  --version 2.1.0 \
  --version-code 20100 \
  --application-protocol-family FIXED_FRAME \
  --release-uid <release-uuid-v4> \
  --header ../hardware_mcu/USER/firmware_identity.h \
  --metadata ./tmp/mcu-2.1.0-identity.json
```

随后在 Keil 中执行完整 Rebuild。确认 target 为 `STM32F103C8`，宏为 `STM32F10X_MD`，
启动文件为 `startup_stm32f10x_md.s`，并生成不大于 64 KiB 的
`hardware_mcu/Output/STM32-DEMO.bin`。不要复用身份头重新编译另一个版本。

### 4.3 生成并复核 `.efw`

```bash
uv run --directory hardware --python 3.11 python mcu_firmware_package.py package \
  --bin ../hardware_mcu/Output/STM32-DEMO.bin \
  --identity ./tmp/mcu-2.1.0-identity.json \
  --private-key /secure/mcu-release-private.pem \
  --key-id RELEASE_2026_01 \
  --hardware-compatibility ECOBIN_MAINBOARD_V1.1 \
  --build-commit <lowercase-git-commit> \
  --built-at <rfc3339-utc-time> \
  --output ./tmp/mcu-2.1.0.efw

uv run --directory hardware --python 3.11 python mcu_firmware_package.py verify \
  ./tmp/mcu-2.1.0.efw \
  --public-key RELEASE_2026_01=/secure/RELEASE_2026_01.pem \
  --hardware-compatibility ECOBIN_MAINBOARD_V1.1
```

命令输出的 `packageSha256`、`packageSize` 和 manifest 是后续上传与登记依据。`.efw` 内只
允许 `firmware.bin`、规范化 `manifest.json` 和 64 字节 `manifest.sig`；设备还会重新校验
包摘要、镜像摘要、64 KiB 上限、板型、固定帧修订号和签名。

## 5. 香橙派部署与配置

### 5.1 安装依赖和公钥

```bash
sudo apt-get update
sudo apt-get install -y stm32flash
sudo install -o root -g root -m 0755 -d /etc/ecobin/mcu-release-keys
sudo install -o root -g root -m 0644 \
  /secure/RELEASE_2026_01.pem \
  /etc/ecobin/mcu-release-keys/RELEASE_2026_01.pem
sudo install -o root -g root -m 0700 -d /var/lib/ecobin/mcu-firmware
```

还必须安装并实测官方基础镜像锁定的 WiringOP `/usr/bin/gpio`。当前
`ecobin-hardware.service` 以 root 运行，能访问 GPIO 和串口；若以后降权，必须只追加串口和
GPIO 所需的最小设备权限，不能通过开放所有设备节点解决。

### 5.2 核对固定 GPIO 和开漏电路

先断开 MCU 电源，确认物理 7/11 分别对应 wPi 2/5，并检查所需工具：

```bash
gpio readall
ls -l /dev/ttyS5 /usr/bin/stm32flash /usr/bin/gpio
```

接回 MCU 后用万用表确认安全态为 BOOT0 低、PC6/Gate 低、NRST 高。只有在现场安全监护下
才可短暂把 wPi 5 写为高：此时 PC6/Gate 应为高、NRST 应被 MOS 拉低；随后必须立即把 wPi 5
恢复为低并确认 NRST 回到高。这里的 `RESET_ACTIVE_LEVEL=1` 指“香橙派输出高会让 MOS 拉低
NRST”，不是把 STM32 NRST 推挽驱动为高。

### 5.3 修改设备环境

把以下固定配置安装到设备的 `/etc/ecobin/hardware.env`：

```dotenv
ECOBIN_MCU_PROTOCOL=fixed-frame
ECOBIN_MCU_SIMULATED=false
ECOBIN_SERIAL_PORT=/dev/ttyS5
ECOBIN_SERIAL_BAUDRATE=115200
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
```

生产环境不再从 `ECOBIN_MCU_UPDATE_ENABLED` 接受人工开关。离线验收中操作员声明本机是否
安装升级线；只有声明已安装且 F2、电平、ROM 只读探测、恢复回原 F3/F1 全部通过时，
首启交接才会原子写入 `mcuRemoteUpdateCapable=true`。未安装时写入 `false`，运行时在下载、
F2 和维护锁之前以 `MCU_REMOTE_UPDATE_UNAVAILABLE` 终态拒绝。重启服务并检查启动日志：

```bash
sudo systemctl restart ecobin-hardware.service
sudo systemctl status ecobin-hardware.service --no-pager
sudo journalctl -u ecobin-hardware.service -n 200 --no-pager
```

日志和本地 SQLite 不会保存 COS 临时 Secret。不要在诊断时输出命令原始载荷或设备凭证。

## 6. 从旧 MCU 固件迁移到修订号 2

旧固件没有 F2/F3，不能直接接受云端升级，因为设备无法证明当前版本和停机执行结果。每台
旧设备必须先通过维护 SSH 执行一次本地迁移；现场应保留 SWD/J-Link 恢复能力。

前置事实：设备无投递和清运、机械输出已安全、签名包已复制到设备临时目录、硬件升级开关
已开启。然后让正在运行的主进程保持在线，另开维护终端只负责写入升级队列：

```bash
cd /root/EcoBin/hardware
./.venv/bin/python tools/mcu_firmware_update.py install \
  /secure-transfer/mcu-2.1.0.efw \
  --legacy-preflight \
  --reason "first fixed-frame revision-2 installation" \
  --wait-seconds 180
```

命令不会自行打开 UART 或切 GPIO；主进程仍是唯一硬件所有者。SQLite 先取得维护锁，新的
投递、清运和物理命令会被阻止。首次迁移尚无“上一份已验证稳定包”，因此若擦写后新固件
无法通过 F3/F1，只能保持 `FAILED_LOCKED` 并用现场 SWD/J-Link 恢复。这就是首次迁移必须
有人值守的原因。

成功后检查：

```bash
./.venv/bin/python tools/mcu_firmware_update.py status
```

应看到 `state=SUCCEEDED`、`stableFirmware.current_manifest` 为目标版本、维护锁为空。此后
重启 `ecobin-hardware.service`（或等待主进程完成升级后立即触发的运行快照），香橙派会发送
`F2 01 F2`。只有 F3 返回 `STATUS=00`、revision 2，且版本码、版本文本和 8 字节身份完整，
运行快照才携带 `mcuFirmwareIdentity`。后端收到这个已认证且序列更新的事实后，才把该设备
登记为 revision 2；这一步不依赖来源为 `CLOUD` 的升级进度。登记完成前创建灰度计划会被
明确拒绝，因此首次迁移后不能只看本地 `SUCCEEDED` 就直接开始云端灰度。

## 7. 后端、OneNet、COS 和 Web 发布

1. 先用独立迁移作业把目标数据库推进到当前 V60。V55 新增 3 个设备固件字段和 5 张固件
   发布/灰度表，V56 增加封存授权与设备验收代次，V57 增加跨时钟容错，V58 补强恢复约束，
   V59 将袋标签单批生成上限统一为 500，V60 增加可空的 MCU 远程升级能力事实；
   应用运行制品本身不会执行迁移。
2. 把 [OneNet 候选物模型](../../contracts/onenet/generated/onenet-thing-model.candidate.json)
   中的 `startMcuFirmwareUpdate` 服务、`mcuFirmwareUpdateProgress` 事件，以及
   `deviceRuntimeSnapshot.mcuFirmwareIdentity` 字段更新到 OneNet，再按控制台实际结果复核
   映射。
3. 将 `.efw` 上传到私有 COS，键必须精确为：

   ```text
   ecobin/mcu-firmware/<releaseUid>/<packageSha256>.efw
   ```

   Web 当前只登记元数据，不代替上传。对象不得公开读。
4. 部署理解 V60 的后端和新 Web。平台管理员需要 `device.manage` 权限，进入
   `/mcu-firmware`。
5. “登记发布”时逐项复制 verify 输出；相同发布、包摘要、版本码和身份不可变。
6. “创建灰度”时指定一台验证机、目标设备、每批数量和原因。创建只冻结计划，不下发。
7. 点击“启动单机验证”，等待验证部署成为 `SUCCEEDED`。
8. 人工核对设备日志、屏幕、传感器和业务后点击“确认推广”。
9. 每次只点击一次“下发下一批”，等待本批全部成功再继续。

后端可靠任务中冻结的是命令身份和 COS 对象键，`cosGrant` 固定为 `null`。OneNet 适配器在
每次真实传输尝试前生成新的只读 STS，并将命令时效刷新为不超过 15 分钟；因此数据库、
任务历史和 Web 都不会持久化临时 SecretId/SecretKey/Token。

## 8. 状态、业务影响和人工恢复

| 设备状态 | 含义 | 是否阻止新业务 |
|---|---|---:|
| `QUEUED/PREFLIGHT/PREPARED` | 香橙派已取得维护锁，正在取包、检查身份或确认 MCU 停机执行 | 是 |
| `PACKAGE_FETCH_FAILED` | 仅表示 COS 超时、临时凭证失效或临时响应不可读；平台会用同一命令编号和新凭证重试，设备总共最多尝试 3 次 | 是 |
| `FLASHING_TARGET/VERIFYING_TARGET` | 正在刷目标或核验 F3/F1 | 是 |
| `ROLLING_BACK/VERIFYING_ROLLBACK` | 目标失败，正在恢复上一稳定版本 | 是 |
| `SUCCEEDED` | 目标身份和自检均通过 | 否 |
| `ROLLED_BACK` | 上一稳定版本恢复成功 | 否，但后端阻止继续灰度 |
| `REJECTED` | 擦写前因投递/清运或其他维护占用、本机未验收远程升级线、签名/板型/摘要/发布身份错误，或 COS 临时失败达到 3 次而终止；设备释放本次升级维护锁 | 否 |
| `FAILED_LOCKED` | 目标/回滚验证失败，或 F2 执行不确定后无法重新证明原应用已恢复 | 是，重启后仍保持 |

查看指定记录：

```bash
./.venv/bin/python tools/mcu_firmware_update.py status \
  --deployment-uid <deployment-uuid>
```

`FAILED_LOCKED` 不会被云端命令自动解除。排除供电、接线、串口或固件问题后，维护人员必须
给出原因，优先只重试回滚：

```bash
./.venv/bin/python tools/mcu_firmware_update.py retry-failed \
  --update-uid <update-uuid> \
  --rollback-only \
  --reason "repaired UART wiring and restoring last stable image"
```

只有明确确认目标包仍正确时才省略 `--rollback-only`。本地降级同样必须使用已签名包、
显式 `--allow-downgrade` 和原因；云端永远禁止降级。

## 9. 上线验收清单

- [ ] PA9/PA10 交叉连接、共地，电平均为 3.3 V；
- [ ] BOOT0 约 10 kΩ 下拉、BOOT1/PB2 固定下拉、2N7002 Gate 约 100 kΩ 下拉、NRST 约
      10 kΩ 上拉并有 10～100 nF 对地电容；
- [ ] 香橙派掉电或未启动时 MCU 能稳定进入原应用；
- [ ] 物理 7/PC9/wPi 2 与物理 11/PC6/wPi 5 经 `gpio readall` 和万用表确认；wPi 5 高时
      MOS 拉低 NRST，wPi 5 低或香橙派掉电时 NRST 被释放；
- [ ] `/dev/ttyS5` 没有被其他服务占用，应用模式 115200/8N1 正常；
- [ ] `stm32flash`、WiringOP 和签名公钥路径均存在，公钥目录不可被普通用户写入；
- [ ] MCU 修订号 2 的 F2/F3 和 F1 自检在真实板上通过；
- [ ] 投递或清运占用本地工作槽时下发升级，确认香橙派上报 `REJECTED` 且线路上没有 F2；
- [ ] MCU 内存中人为保留活动状态后发送 F2 模式 02，确认 MCU 不返回 BUSY/UNSAFE，而是
      停止全部活动和输出并返回 `STATUS=00 + SAFE_FLAGS=1F`；
- [ ] 人为丢弃 F2 应答，确认香橙派复位回原应用并核对原身份/F1 后才解除维护锁；恢复验证
      失败时必须保持 `FAILED_LOCKED`；
- [ ] 在 MCU 已返回 F2 成功后人为使 `PREPARED` 写库失败，确认香橙派仍先复位并验证
      原身份/F1，不得在 MCU 保持升级锁存时解除维护锁；
- [ ] 首次迁移在有人值守且可用 SWD/J-Link 恢复的设备上完成；
- [ ] V60 迁移、V60 epoch 门禁和最小运行权限已先部署；
- [ ] OneNet 服务/事件与生成映射一致，私有 COS 对象键和摘要一致；
- [ ] 先用一台非关键设备验证成功，人工核验后才推广；
- [ ] 人为制造一次目标自检失败，确认自动回滚成功且业务锁解除；
- [ ] 人为制造目标与回滚均失败，确认 `FAILED_LOCKED` 跨进程重启仍阻止业务；
- [ ] Web 在当前批运行中或出现回滚时确实不能下发下一批。

只有上述清单在真实 STM32F103C8T6、真实主板电路和真实 OneNet 环境中通过，才能把“软件
实现完成”提升为“可用于无人值守远程升级”。
