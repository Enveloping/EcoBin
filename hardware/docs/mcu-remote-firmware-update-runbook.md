# STM32F103C8T6 远程固件升级实施与操作手册

> 适用范围：EcoBin 主板 `ECOBIN_MAINBOARD_V1.1`、MCU `STM32F103C8T6`、
> 香橙派 Zero 3、固定帧协议修订号 2、OneNet 正式链路。
>
> 结论：香橙派可以通过现有 UART 烧录 MCU，但并非“只接 TX/RX 后适配软件”即可。
> 除交叉连接的 UART 和共地外，香橙派必须可靠控制 `BOOT0` 与 `NRST`；MCU 必须先运行
> 支持 F2 安全准备和 F3 固件身份查询的修订号 2 固件。远程发布还依赖签名包、私有 COS、
> OneNet 合约、后端 V55 和人工灰度流程。

## 1. 已实现的完整链路

正常远程升级由平台管理员发起，具体顺序如下：

```text
离线构建并签名 .efw
  → 上传私有 COS 并在 Web 登记不可变发布
  → 创建灰度计划，指定一台验证机和后续批次
  → 人工启动单机验证
  → 后端登记可靠 OneNet 命令（不保存 COS 临时密钥）
  → 实际发送前生成最长 15 分钟的只读 COS 凭证
  → 香橙派先建立升级日志、取得本地维护锁并可靠上报已排队
  → 香橙派下载固件包，验签并校验主板兼容标识、版本和摘要
  → MCU F2 确认当前没有作业且执行器处于安全状态
  → 香橙派关闭应用 UART，拉高 BOOT0 并脉冲 NRST
  → stm32flash 以 115200/8E1 写入 0x08000000 并回读校验
  → BOOT0 恢复低电平，复位进入应用
  → F3 核对版本、版本码、身份和协议修订号，F1 完成传感器自检
  → 成功后解除维护锁；失败则最多三次并自动刷回上一份稳定包
  → 进度事实经 OneNet 回传，后端更新设备、部署和灰度状态
```

灰度不会自动扩散。验证机成功后必须人工“确认推广”，每一批也必须人工“下发下一批”。
当前批次只要存在运行中、回滚或失败锁定设备，后端就会阻止下一批。

## 2. 为什么需要四类信号

ST 官方 AN2606 规定 STM32F10xxx 的系统存储器 Bootloader 使用 USART1：MCU 的 PA10
为接收，PA9 为发送；初始化后为 8 数据位、偶校验、1 停止位。AN3155 规定主机首先发送
带偶校验的 `0x7F` 完成同步。官方依据：

- [AN2606：STM32 系统存储器启动模式](https://www.st.com/resource/en/application_note/an2606-stm32microcontroller-system-memory-boot-mode-stmicroelectronics.pdf)
- [AN3155：STM32 Bootloader USART 协议](https://www.st.com/resource/en/application_note/an3155-usart-protocol-used-in-the-stm32-bootloader-stmicroelectronics.pdf)

接线关系如下。表中的“香橙派引脚”必须根据本机 `gpio readall` 选择，环境变量使用的是
WiringOP 的 `wPi` 编号，不是 26 针排针号，也不是 H618 的 GPIO 名称。

| 香橙派侧 | STM32F103C8T6 侧 | 用途与要求 |
|---|---|---|
| UART TX | PA10 / USART1 RX | 交叉连接，3.3 V TTL |
| UART RX | PA9 / USART1 TX | 交叉连接，3.3 V TTL |
| GND | GND | 必须共地 |
| 一个输出 GPIO | BOOT0 | 高电平选择系统 Bootloader；默认必须有约 10 kΩ 下拉 |
| 一个输出 GPIO | NRST | 低电平复位；建议通过开漏缓冲/三极管控制并保留上拉 |
| 固定下拉 | BOOT1 / PB2 | 约 10 kΩ 下拉，保证 BOOT0=1 时进入系统存储器 |

不要把 5 V UART 直接接到 MCU 或香橙派。BOOT0 和 NRST 在香橙派启动、崩溃或 GPIO
尚未初始化时也必须由硬件电阻保持“BOOT0=0、NRST=1”的正常运行状态。若现有主板没有
引出 BOOT0/NRST，应先改板或增加可靠控制电路；仅靠应用固件主动跳转 Bootloader 不能覆盖
固件损坏或卡死场景，不作为本方案的恢复边界。

应用运行时 UART 为 115200/8N1；进入 ROM Bootloader 后同一串口临时切为
115200/8E1。设备程序会先关闭应用串口，`stm32flash` 退出后再恢复应用串口，禁止其他
进程同时打开该设备节点。

## 3. MCU 固件前置条件

当前本地 MCU 工程的必要改动位于 `hardware_mcu/USER/`：

- `firmware_identity.h`：版本、单调版本码和 8 字节发布身份；
- `usart1.c/.h`：F2“准备升级”和 F3“查询固件身份”固定帧；
- `main.c`：只有无投递/清运、输出已安全、门状态允许时才接受 F2，并锁住新业务；
- `STM32-DEMO.uvprojx`：STM32F103C8 使用 `STM32F10X_MD` 和 medium-density 启动文件，
  构建完成后用 `fromelf` 生成 `Output/STM32-DEMO.bin`。

固定帧字节定义见
[mcu-fixed-frame-v2.md](../../contracts/mcu-fixed-frame-v2.md)。F2/F3 是应用层预检，真正擦写
仍由芯片出厂 ROM Bootloader 完成。

当前根 `.gitignore` 有意排除了整个 `hardware_mcu/`，因此该目录的源代码不属于主仓库
发布包。正式量产前必须把这五个文件迁入受控 MCU 仓库，或由制品库同时保存源提交、Keil
工具链版本、`.bin` 和签名包；不能只保留某台开发机上的未跟踪工程。

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

还必须安装并实测 WiringOP 的 `/usr/local/bin/gpio`。当前
`ecobin-hardware.service` 以 root 运行，能访问 GPIO 和串口；若以后降权，必须只追加串口和
GPIO 所需的最小设备权限，不能通过开放所有设备节点解决。

### 5.2 先确认 wPi 编号

在没有连接 BOOT0/NRST 或 MCU 断电时执行：

```bash
gpio readall
ls -l /dev/ttyS5 /usr/bin/stm32flash /usr/local/bin/gpio
```

记录所选排针对应的两个不同 wPi 编号，并用万用表确认软件写 0/1 后电平正确。NRST 如果
通过反相三极管控制，环境变量中的 active level 必须按“让 MCU 进入复位”的实际 GPIO
电平填写，不能照抄示例。

### 5.3 修改设备环境

从 [`.env.example`](../.env.example) 复制以下配置到设备的受限环境文件：

```dotenv
ECOBIN_MCU_PROTOCOL=fixed-frame
ECOBIN_MCU_SIMULATED=false
ECOBIN_SERIAL_PORT=/dev/ttyS5
ECOBIN_SERIAL_BAUDRATE=115200
ECOBIN_MCU_UPDATE_ENABLED=true
ECOBIN_MCU_BOOT0_WPI=<boot0-wpi>
ECOBIN_MCU_RESET_WPI=<nrst-wpi>
ECOBIN_MCU_BOOT0_ACTIVE_LEVEL=1
ECOBIN_MCU_RESET_ACTIVE_LEVEL=<实际复位有效电平>
ECOBIN_MCU_HARDWARE_COMPATIBILITY=ECOBIN_MAINBOARD_V1.1
ECOBIN_MCU_SIGNING_PUBLIC_KEYS_DIR=/etc/ecobin/mcu-release-keys
ECOBIN_MCU_FIRMWARE_CACHE_DIR=/var/lib/ecobin/mcu-firmware
ECOBIN_STM32FLASH_PATH=/usr/bin/stm32flash
ECOBIN_GPIO_PATH=/usr/local/bin/gpio
```

保持开关为 `false` 时，设备仍按原逻辑运行，但会明确拒绝云端升级命令。只有接线、电平、
公钥、工具路径和真实 UART 都验收后才改为 `true`。重启服务并检查启动日志：

```bash
sudo systemctl restart ecobin-hardware.service
sudo systemctl status ecobin-hardware.service --no-pager
sudo journalctl -u ecobin-hardware.service -n 200 --no-pager
```

日志和本地 SQLite 不会保存 COS 临时 Secret。不要在诊断时输出命令原始载荷或设备凭证。

## 6. 从旧 MCU 固件迁移到修订号 2

旧固件没有 F2/F3，不能直接接受云端升级，因为设备无法证明当前版本和安全准备结果。每台
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

1. 先用独立迁移作业把目标数据库推进到 V55。V55 新增 3 个设备固件字段和 5 张固件
   发布/灰度表；应用运行制品本身不会执行迁移。
2. 把 [OneNet 候选物模型](../../contracts/onenet/generated/onenet-thing-model.candidate.json)
   中的 `startMcuFirmwareUpdate` 服务、`mcuFirmwareUpdateProgress` 事件，以及
   `deviceRuntimeSnapshot.mcuFirmwareIdentity` 字段更新到 OneNet，再按控制台实际结果复核
   映射。
3. 将 `.efw` 上传到私有 COS，键必须精确为：

   ```text
   ecobin/mcu-firmware/<releaseUid>/<packageSha256>.efw
   ```

   Web 当前只登记元数据，不代替上传。对象不得公开读。
4. 部署理解 V55 的后端和新 Web。平台管理员需要 `device.manage` 权限，进入
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
| `QUEUED/PREFLIGHT/PREPARED` | 已取得维护锁，正在取包或执行擦写前检查 | 是 |
| `PACKAGE_FETCH_FAILED` | 仅表示 COS 超时、临时凭证失效或临时响应不可读；平台会用同一命令编号和新凭证重试，设备总共最多尝试 3 次 | 是 |
| `FLASHING_TARGET/VERIFYING_TARGET` | 正在刷目标或核验 F3/F1 | 是 |
| `ROLLING_BACK/VERIFYING_ROLLBACK` | 目标失败，正在恢复上一稳定版本 | 是 |
| `SUCCEEDED` | 目标身份和自检均通过 | 否 |
| `ROLLED_BACK` | 上一稳定版本恢复成功 | 否，但后端阻止继续灰度 |
| `REJECTED` | 擦写前因投递/清运或其他维护占用、升级开关关闭、签名/板型/摘要/发布身份错误，或 COS 临时失败达到 3 次而终止；设备释放本次升级维护锁 | 否 |
| `FAILED_LOCKED` | 目标和回滚都无法证明安全 | 是，重启后仍保持 |

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
- [ ] BOOT0 默认硬件下拉，BOOT1/PB2 固定下拉，NRST 默认上拉；
- [ ] 香橙派掉电或未启动时 MCU 能稳定进入原应用；
- [ ] 两个 wPi 编号经 `gpio readall` 和万用表确认，复位有效电平实测；
- [ ] `/dev/ttyS5` 没有被其他服务占用，应用模式 115200/8N1 正常；
- [ ] `stm32flash`、WiringOP 和签名公钥路径均存在，公钥目录不可被普通用户写入；
- [ ] MCU 修订号 2 的 F2/F3 和 F1 自检在真实板上通过；
- [ ] 首次迁移在有人值守且可用 SWD/J-Link 恢复的设备上完成；
- [ ] V55 迁移、V55 epoch 门禁和最小运行权限已先部署；
- [ ] OneNet 服务/事件与生成映射一致，私有 COS 对象键和摘要一致；
- [ ] 先用一台非关键设备验证成功，人工核验后才推广；
- [ ] 人为制造一次目标自检失败，确认自动回滚成功且业务锁解除；
- [ ] 人为制造目标与回滚均失败，确认 `FAILED_LOCKED` 跨进程重启仍阻止业务；
- [ ] Web 在当前批运行中或出现回滚时确实不能下发下一批。

只有上述清单在真实 STM32F103C8T6、真实主板电路和真实 OneNet 环境中通过，才能把“软件
实现完成”提升为“可用于无人值守远程升级”。
