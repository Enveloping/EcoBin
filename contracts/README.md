# EcoBin 机器契约

本目录保存跨端协议的唯一机器来源。业务语义仍以 `docs/planning/` 下已确认的需求、接口和
详细设计为准；这里负责把字段、单位、枚举、线级编号和校验规则变成可解析、可生成、
可重复验证的制品。

## 当前状态

- OneNet：Draft 2020-12 JSON Schema 候选版，等待 F-11 后端/香橙派适配。
- UART：协议 `1.0` 候选 Registry，software 阶段已完成，最终冻结仍等待 MCU
  负责人确认消息号、字段顺序、能力位和非易失边界。
- HTTP：属于 F-09，不在 F-10 中创建。

候选版不是生产切换授权。现有 OneNet 物模型、AA/BB/CC/DD 临时帧和旧 D1 清运链只有
运行现状意义，F-11/H-03 正式切换时必须整体退出，不能建设生产双协议回退。

## 目录

```text
contracts/
├─ onenet/
│  ├─ common.schema.json
│  ├─ event-envelope.schema.json
│  ├─ command-envelope.schema.json
│  ├─ command-receipt.schema.json
│  ├─ events/events.schema.json
│  ├─ commands/commands.schema.json
│  ├─ thing-model.mapping.yaml
│  └─ generated/
│     ├─ onenet-thing-model.candidate.json
│     ├─ onenet-wire-mapping.json
│     └─ java/
├─ uart/
│  ├─ uart-registry.schema.json
│  ├─ uart-registry.yaml
│  ├─ mcu-review-checklist.md
│  └─ generated/
├─ examples/
│  ├─ onenet/
│  ├─ onenet-wire/
│  └─ uart/
├─ generated/
│  └─ contract-catalog.md
├─ tools/
└─ tests/
```

`*.yaml` 文件刻意使用 JSON 语法。JSON 是 YAML 1.2 的合法子集，这样 Python 3.11
标准库即可读取，无需在香橙派或 MCU 开发环境额外安装 YAML 解析器。

## 生成与验证

在仓库根目录运行：

```powershell
python contracts/tools/generate_contracts.py
python contracts/tools/validate_contracts.py
python -m unittest discover -s contracts/tests -v
```

生成物必须由机器源重建，不得直接编辑。检查工作区是否存在生成漂移：

```powershell
python contracts/tools/generate_contracts.py --check
```

工具只使用 Python 3.11 标准库。Java 黄金样本由校验器在存在 Java 21 工具链时编译执行；
C 头文件和黄金样本程序交给 MCU 负责人使用其固件工具链编译，人工结果是 F-10 的
integration/acceptance 完成门。

完整的本地、OneNet 控制台、Java、C 与真机人工验证顺序见
[`MANUAL-VALIDATION.md`](MANUAL-VALIDATION.md)。

为加快交付而不阻塞权威契约冻结，本轮未扩展的三端完整 payload 执行器、跨消息状态轨迹、
穷举负例和超长粘包工具修复记录在
[`DEFERRED-HARDENING.md`](DEFERRED-HARDENING.md)。这些是 F-11/H-03 的实施要求，
不是允许运行时放宽 Registry。

## 摘要与单位

- JSON 稳定载荷只允许 `null`、布尔、字符串、整数、数组和对象；禁止 IEEE 754 浮点。
- `payloadSha256` 对 RFC 8785 可规范化的 `payload` 计算。当前工具对上述整数子集生成
  UTF-8、键排序、无多余空白的规范字节，并用黄金样本锁定。
- 稳定命令摘要使用 `ECOBIN:ONENET:COMMAND:v1\0` 域分离，绑定命令身份、类型、部署、
  目标、签发/截止时间和 `payloadSha256`；`cosGrant` 刷新不改变该摘要。
- 中心事件摘要使用 `ECOBIN:ONENET:EVENT:v1\0` 域分离，并额外绑定 OneNet 已认证的
  `productId + deviceName`，不能信任设备把来源身份写进业务载荷。
- UUID 使用 RFC 4122 文本；UART 中转换为网络顺序 16 字节。
- SHA-256 在 JSON 中使用 64 位小写十六进制，在 UART 中使用原始 32 字节。
- 重量使用带符号整数克；单价使用“元/千克 × 10000”的无符号整数；相对时长使用毫秒。
- `eventUid`、`commandUid`、`mcuCommandUid`、`txSequence`、`mcuEventSequence` 和
  OneNet/Pulsar 传输 ID 是不同身份，禁止互相替代。

## 修改规则

1. 先修改权威 Schema 或 Registry，再运行生成与验证。
2. 协议 `1.0` 尚未由 MCU 负责人共同确认时，可以在同一候选版内协调修改；每次修改都要
   重生成摘要和黄金样本。
3. 共同基线冻结后，破坏性变化提升 major，兼容新增提升 minor，纯说明/样例修正提升 patch。
4. 不得用机器契约反向削弱已确认的门安全、资金、幂等、租户/机构或失败恢复边界。
5. `onenet-thing-model.candidate.json` 是从 JSON Schema 生成的控制台导入候选；若控制台
   拒绝，记录原始错误并修改 Schema/生成器，不能直接改生成文件制造第二套契约。
