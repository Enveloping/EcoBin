# EcoBin F-10 人工验证手册

本手册用于复现 F-10 的机器契约验证，并为选择 `uart-v1` 原生 MCU 实现时提供附加
符合性步骤。验证分为四层：

1. 本地生成物和 Python 规则；
2. Java 21 / 通用 C11 跨语言黄金样本；
3. OneNet 测试产品导入与 OneJSON 收发；
4. 可选的原生 UART 1.0 MCU 逐字段确认。

F-10 已于 2026-07-27 收口。现有固定帧单片机不需要执行第 7～8 节；其线路和真实
物理行为按 F-11/H-03 的固定帧适配验收。若以后选择 `uart-v1` 原生 MCU 实现，再使用
[`uart/mcu-review-checklist.md`](uart/mcu-review-checklist.md) 验证工具链、门、锁、
断电和恢复，不能用 F-10 软件证据冒充部署验收。

## 1. 验证前准备

- 保留现有 OneNet 生产物模型的导出备份，不直接在生产产品上试导入。
- 新建或选择一个 MQTT + OneJSON 的非生产测试产品。
- 使用 Java 21；香橙派代码目标仍是 Python 3.11。
- 准备通用 C11 编译器；若部署 `uart-v1` 原生 MCU，再准备其实际固件编译器。开发机
  没有 C 编译器时，自动校验中的 `NOTE` 不否定已经归档的 F-10 证据。
- 不把真实 OneNet Key、设备 Key、COS 临时密钥或服务器凭证写入样例或验证记录。

## 2. 本地软件验证

在仓库根目录依次执行：

```powershell
python --version
python contracts/tools/generate_contracts.py --check
python contracts/tools/validate_contracts.py
python -m unittest discover -s contracts/tests -v
```

上述默认流程不需要、也不会创建 `hardware_mcu/`。若以后明确恢复 `uart-v1` MCU
原生实现，再附加 `--include-hardware-mcu` 生成并检查 MCU 工程内的 C 制品；当前
固定帧适配不执行该步骤。

香橙派或其他明确安装了目标解释器的环境使用 `python3.11` 替换上述 `python`。如果
`python --version` 不是 3.11，只能证明当前解释器下的行为；单元测试中的 3.11 grammar
检查不能替代至少一次真实 Python 3.11 执行。

预期：

- 生成检查没有 drift；
- 18 种 OneNet 命令、22 种事件/回执全部通过；
- OneNet 导入候选共 40 个功能点；
- OneNet 导入候选使用 LF、严格小于 256 KiB；枚举显示说明为 1～20 个允许字符；
- 每个服务输入/输出分别不超过 20 项，每个事件输出不超过 50 项；功能标识不超过 50
  字符，显示名不超过 30 字符；
- 40 份 OneJSON 线级样例与导入候选一致；
- Python 与 Java 的 JCS/稳定身份摘要一致；
- UART 39 个消息、11 个帧向量、10 个流式解析轨迹和 3 个摘要向量通过；
- 单元测试全部为 `OK`；
- 若机器没有 C 编译器，只允许出现“C compiler unavailable/skip”的说明。

任何失败都先修改权威 Schema、Registry 或生成器，再重新生成。不要直接编辑
`contracts/**/generated/` 或 `contracts/examples/`。

## 3. Java 21 单独验证

自动校验会在可找到 `javac/java` 时执行本节。需要单独复现时：

```powershell
New-Item -ItemType Directory -Force contracts/.tmp-javac-f10 | Out-Null
javac --release 21 -d contracts/.tmp-javac-f10 `
  contracts/uart/generated/java/EcobinUartProtocol.java `
  contracts/uart/generated/java/EcobinUartGoldenTest.java `
  contracts/onenet/generated/java/EcobinCanonicalJson.java `
  contracts/onenet/generated/java/EcobinCanonicalJsonGoldenTest.java
java -cp contracts/.tmp-javac-f10 EcobinUartGoldenTest
java -cp contracts/.tmp-javac-f10 EcobinCanonicalJsonGoldenTest
```

预期最后两条分别输出：

```text
Java UART golden vectors: 11 frames, 10 stream traces, 3 digest profiles passed
Java OneNet canonical vectors: 4 payloads, 7 stable identities passed
```

JCS 负例还必须拒绝浮点、超出 `±9007199254740991` 的整数和未配对 surrogate。
JSON 解析入口必须拒绝重复对象键，不能在进入摘要算法前静默采用“最后一个值”。

## 4. OneNet 控制台导入

导入文件：

[`onenet/generated/onenet-thing-model.candidate.json`](onenet/generated/onenet-thing-model.candidate.json)

在非生产产品中导入后核对：

| 项目 | 预期 |
|---|---:|
| 属性 | 0 |
| 同步服务 | 18 |
| 事件 | 22 |
| 总功能点 | 40（低于 OneNet 的 100 个功能点上限） |
| 导入文件 | `192207` bytes、小于 256 KiB、LF 换行 |
| 枚举显示说明 | 1～20 个中英文、数字、下划线或连字符 |
| 单服务输入/输出 | 各不超过 20 |
| 单事件输出 | 不超过 50 |

2026-09-10 当前候选的 SHA-256 为
`5f5eb993527a899eed0193dd34d7c57842ff002f676126062d5c082ca369bea1`，距离
`262144` bytes 上限还剩 `69937` bytes。导入时必须选择仓库内的生成文件，不要用编辑器
重排 JSON、转换换行或另存副本。

每个服务都应为同步调用，并具有相同的 6 个即时回复字段：

```text
schemaVersion
commandUid
receiptState
errorCodePresent
errorCode
edgeBootId
```

同步回复只允许证明香橙派已经校验并把命令可靠保存到 SQLite。OneNet 同步调用窗口为
5 秒，不能在回复前等待开门、称重、拍照、MCU 完整作业或后端业务完成。

OneNet 物模型只负责平台能表达的类型和范围。以下规则仍由香橙派和后端使用权威 JSON
Schema/语义校验器执行：

- 字符串枚举在 OneNet 线上编码为整数，接收后先按生成映射还原为 JSON 符号；
- 可空字段使用 `<field>Present + typed placeholder`，先还原为 JSON `null`；
- 还原后才计算 `payloadSha256` 并执行跨字段规则；
- `struct` 只有一层，`struct` 成员不包含数组，协议中没有浮点重量或单价。
- 数组描述使用控制台导出格式 `specs.size + specs.items`，长度值使用字符串；
- 超过服务顶层参数上限时，生成器把原始标量无损分组到
  `scalarFields`/`scalarFieldsN` 一层结构（每组不超过 20 个成员）；对应 `jsonPath`
  和还原方式以生成的 wire mapping 为准。

本候选使用的 OneNet 平台边界可对照以下官方资料：

- [物模型功能点与数据类型](https://onenet.hk.chinamobile.com/doc/v5/fuse/detail/199)：
  功能点不超过 100，`struct` 只支持一层且成员不支持数组；
- [中国移动物联网物模型标准白皮书](https://upfiles.heclouds.com/portal5-admin/portal5-admin/2022/03/14/4ed1223472bf7e25d16f021f5b839b5b.pdf)：
  服务输入/输出、事件输出、标识符和显示名数量/长度限制；
- [物模型查询返回结构](https://iot.10086.cn/doc/iot_platform/book/api/common/queryThingModel.html)：
  `functionMode`、服务/事件参数和类型描述结构；
- [OneJSON 设备服务](https://iot.10086.cn/doc/iot_platform/book/device-connect%26manager/thing-model/protocol/OneJSON/service.html)：
  同步调用超时 5 秒及 invoke/invoke_reply 格式；
- [OneJSON 设备事件](https://onenet.hk.chinamobile.com/doc/v5/fuse/detail/202)：
  event/post 与 event/post/reply 格式；
- [物模型服务调用 API](https://onenet.hk.chinamobile.com/doc/v5/fuse/detail/312)：
  `thingmodel/call-service` 的请求和响应边界。

平台文档和控制台可能演进；如果当前测试产品的真实导入或调用行为与资料不同，以原始控制台/API
结果作为阻塞证据，回到机器源调整，不在控制台中维护第二套手工定义。

完整投影规则见：

[`onenet/generated/onenet-wire-mapping.json`](onenet/generated/onenet-wire-mapping.json)

如果控制台拒绝导入：

1. 记录控制台原始错误、功能点和字段名；
2. 不手改导入 JSON；
3. 回到 Schema/生成器修正；
4. 重新执行第 2 节，并重新导入一个干净的测试产品版本。

## 5. OneNet 同步服务验证

首选先验证：

[`examples/onenet-wire/start-delivery-session.service-wire.json`](examples/onenet-wire/start-delivery-session.service-wire.json)

步骤：

1. 将 `product_id` 和 `device_name` 占位符替换为测试产品和测试设备；
2. 使用 OneNet 控制台应用模拟器或官方 `thingmodel/call-service` API 调用
   `startDeliverySession`；
3. 设备订阅样例中的 `deviceInvokeTopicTemplate`；
4. 设备完成 Schema、部署、摘要、截止时间校验和 SQLite 提交；
5. 在 5 秒内复制请求 `id`，按 `deviceAcceptedReplyTemplate` 发布同步回复；
6. 核对应用侧返回 6 个即时回执字段。

至少再验证：

- 同一个 `commandUid + stable digest` 重复下发返回 `DUPLICATE_ACCEPTED`，不重复物理动作；
- 同一个 `commandUid` 改目标、期限或稳定载荷时返回 `REJECTED`；
- 只刷新 `cosGrant` 时稳定命令摘要不变；
- `cosGrant.bucket/region/baseUrl` 必须与可信部署配置逐项相等，照片 URL 的 origin
  也必须相等；设备或载荷不能自行指定另一个 COS 环境；
- 过期命令、新部署不匹配、SQLite 无法提交时均不得返回 `ACCEPTED`；
- OneNet 调用成功或回执 `ACCEPTED` 均不得被记录为门已打开或订单已完成。

`examples/onenet-wire/` 中为全部 18 个服务提供了同格式样例。样例中的 COS 凭证是假的，
只用于类型/协议验证，不能用于真实上传。

## 6. OneNet 事件验证

首选先验证：

[`examples/onenet-wire/delivery-complete.event-wire.json`](examples/onenet-wire/delivery-complete.event-wire.json)

步骤：

1. 设备向样例中的 `$sys/{pid}/{device-name}/thing/event/post` 发布
   `oneJsonPayload`；
2. 订阅 `/thing/event/post/reply`，确认相同消息 `id` 收到 `code=200`；
3. 在 OneNet 北向 MQ 中确认事件标识符、认证 `productId/deviceName` 和参数完整；
4. 后端先还原 enum/null，再校验 JSON Schema、`payloadSha256`、部署和目标；
5. 后端权威事务完成后才下发 `CONFIRM_EDGE_EVENT`；
6. 设备持久化确认后上报 `BUSINESS_CONFIRMATION_RECEIPT`。

重点检查：

- 一个投递 `sessionUid` 无论中间继续多少次，只上报一个 `DELIVERY_COMPLETE`；
- `deliveryNetWeightGrams = 最终关门稳定重量 - 首次开门前稳定重量`；
- `negativeWeightAnomaly` 只是一项最终布尔标志，不携带中间减少值；
- 清运完成必须有电磁阀断电、健康正常及清运员人工关门/完成确认；无门磁时物理门位
  保持 `UNKNOWN`，不能由锁状态推定关闭；
- 照片不完整不阻止订单/清运，`PHOTO_STATUS_REPORTED` 只补报
  `AVAILABLE/PERMANENTLY_MISSING`；
- `AVAILABLE` URL 必须精确命中部署、作业、槽位和 `photoUid`；
- OneNet PUBACK、事件上报 `code=200` 和 Pulsar ACK 都不是后端业务确认。

`examples/onenet-wire/` 中为全部 22 个事件/回执提供了可发布样例。

## 7. 可选：原生 UART 1.0 MCU C 工具链验证

将以下两份文件放入 MCU 的实际 C11 工具链：

- [`uart/generated/c/ecobin_uart_protocol.h`](uart/generated/c/ecobin_uart_protocol.h)
- [`uart/generated/c/ecobin_uart_golden_test.c`](uart/generated/c/ecobin_uart_golden_test.c)

若工具链提供类 GCC 命令，可参考：

```text
<cc> -std=c11 -Wall -Wextra -Werror \
  -Icontracts/uart/generated/c \
  contracts/uart/generated/c/ecobin_uart_golden_test.c \
  -o ecobin_uart_golden_test
```

运行后预期：

```text
C UART golden vectors: 11 frames, 10 stream traces, 3 digests passed
```

该 C 黄金程序当前证明帧、CRC、编号、代表性 payload 和摘要原语一致，不是 39 种消息的
完整生产 payload 校验器。三端完整字段/语义执行器、跨消息轨迹和 `>512 bytes` 合法粘包
增量排空按 [`DEFERRED-HARDENING.md`](DEFERRED-HARDENING.md) 在选择 `uart-v1`
原生实现时完成；固定帧适配不宣称具备这些能力。

同时核对生成头文件中的：

```text
115200 baud / 8N1 / no flow control
magic EC 42
big-endian
maximum frame 256 bytes
maximum payload 242 bytes
receive buffer 512 bytes
frame assembly deadline 100 ms
ACK timeout 500 ms
maximum sends 3
CRC-16/CCITT-FALSE: "123456789" -> 29B1
```

## 8. 可选：原生 UART 1.0 MCU 人工 checkpoint

逐项完成：

[`uart/mcu-review-checklist.md`](uart/mcu-review-checklist.md)

必须记录：

- Registry 版本和 SHA-256；
- MCU 固件/工具链版本；
- 39 个消息号、字段偏移、方向和 ACK 规则的确认结论；
- capability bit 0～14 的真实支持情况；
- 非零 UUID 默认规则、明确的零 UUID sentinel、`PortFaultBitmap` bit 0～4 与保留位规则；
- 快照 applied/staging 全有或全无、partCount/bitmap 恢复约束；
- 危险命令去重、关键事件队列、配置和作业状态所用非易失介质、容量与擦写边界；
- C 黄金样本输出；
- 不能实现或需要调整的字段及原因。

若原生 UART 1.0 MCU 无法跨看门狗/断电保存不可逆动作的去重和关键事件，该部署不能
宣称符合相应 capability；固定帧模式同样不能让香橙派用猜测或超时成功替代缺失事实。

## 9. 验证记录

完成后建议在 MCU 确认单尾部追加：

```text
验证日期：
仓库 commit：
OneNet 测试产品：
物模型导入：通过 / 失败（原始错误）
同步服务：通过 / 失败
事件上报与北向 MQ：通过 / 失败
Python：
Java 21：
MCU C 工具链：
Registry SHA-256：
MCU 负责人结论：
遗留项：
```
