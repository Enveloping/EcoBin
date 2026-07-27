# F-10 后续契约工具硬化清单

本文件记录 2026-07-24 为尽快交付 F-10 权威契约而明确延后的工具增强。这里的项目不改变
`onenet/*.schema.json`、`thing-model.mapping.yaml` 或 `uart-registry.yaml` 的权威语义，
也不允许运行时忽略这些语义。

## 进入 F-11 / 原生 UART 1.0 路线的项目

1. **三端完整 payload 校验器**
   让 generated Python、Java 和 MCU C 对 39 种消息执行与 Registry 相同的精确长度、
   UTF-8、bool、enum、范围、常量、零 UUID、保留位、`invalidWhen` 和命令摘要校验。
   当前 Python `contractlib.py` 是参考校验器；生成的 C/Java 主要锁定帧、CRC、编号和摘要
   原语，不能直接当成完整生产 payload 防线。
2. **跨消息集合与状态轨迹验证器**
   为 CONFIG、SNAPSHOT、CLEAN 和 DELIVERY 分别实现集合/轨迹校验，覆盖分段完整性、摘要
   反向重建、ACK 前置、代次作废和最终身份绑定。生产状态机仍必须实现 Registry
   `semanticRules`，不能因为参考工具尚未自动化而跳过。
3. **黄金向量覆盖扩充**
   从当前代表性向量扩充为 39/39 消息正例及字段级负例；负例应重算 CRC，避免只在帧层
   提前失败。配置和快照摘要测试应由结构化字段重建 preimage，而不是只对现成 preimage
   求 SHA-256。
4. **超长粘包的增量排空**
   当前参考/generated stream parser 会先把一次 `feed` 的全部字节放入 512 字节缓冲，
   再执行解析；单次输入超过上限且全部由完整合法帧组成时可能丢弃前部合法帧。F-11/H-03
   必须改为边接收边排空完整帧，再对真正无法排空的数据执行溢出策略，并增加
   `>512 bytes` 全合法粘包轨迹。
5. **共同版本形成后的门禁**
   在真实 Python 3.11、Java 21、通用 C11 和 OneNet 测试产品上固化 CI；只有选择
   `uart-v1` 原生 MCU 时才增加目标工具链/HIL。这对应 I-055 的后续升级，不影响
   已完成的 F-10。

## 使用限制

- `contracts/**/generated/` 仍只能由生成器重建，不能手改。
- F-11 运行时接入时，必须以权威 Schema/Registry 和参考校验器补齐上述能力后再切换正式
  链路。
- `uart-v1` 原生 MCU 必须在目标工具链和真机上验证；当前 H-03 则验证固定帧线路、
  屏幕状态机和真实物理行为。本地 Java/Python 通过都不代表门锁或断电恢复已经验收。
