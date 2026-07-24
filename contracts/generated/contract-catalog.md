# EcoBin F-10 机器契约目录（生成）

> 本文件由 `contracts/tools/generate_contracts.py` 生成，请勿直接编辑。

- UART Registry：`1.0.0-rc.2`
- UART Registry SHA-256：`1c8f97160b2b8a202e8a6298f1bbe554f9311a6962244f1786e16cf5a2101705`
- UART 状态：`MCU_REVIEW_REQUIRED`
- UART 物理链路：`115200 baud / 8N1 / no flow control`
- OneNet Mapping：`1.0.0-rc.2` / `IMPLEMENTATION_CANDIDATE`

## UART 消息

| ID | 消息 | 方向 | ACK | payload 字节 |
|---:|---|---|---|---:|
| `0x01` | `HELLO` | `BIDIRECTIONAL` | 否 | `27..91` |
| `0x02` | `HELLO_ACK` | `BIDIRECTIONAL` | 否 | `32` |
| `0x03` | `ACK` | `BIDIRECTIONAL` | 否 | `22` |
| `0x04` | `NACK` | `BIDIRECTIONAL` | 否 | `23` |
| `0x05` | `QUERY_STATE` | `EDGE_TO_MCU` | 是 | `64` |
| `0x06` | `SAFE_CLOSE` | `EDGE_TO_MCU` | 是 | `54` |
| `0x10` | `CONFIG_BEGIN` | `EDGE_TO_MCU` | 是 | `139` |
| `0x11` | `CONFIG_DEVICE_BLOCK` | `EDGE_TO_MCU` | 是 | `159` |
| `0x12` | `CONFIG_PORT_BLOCK` | `EDGE_TO_MCU` | 是 | `191` |
| `0x13` | `CONFIG_COMMIT` | `EDGE_TO_MCU` | 是 | `138` |
| `0x14` | `CONFIG_APPLY_RESULT` | `MCU_TO_EDGE` | 是 | `127` |
| `0x20` | `START_DELIVERY_SESSION` | `EDGE_TO_MCU` | 是 | `125` |
| `0x21` | `START_CLEAN_OPERATION` | `EDGE_TO_MCU` | 是 | `113` |
| `0x22` | `UNLOCK_CLEAN_DOOR` | `EDGE_TO_MCU` | 是 | `95` |
| `0x23` | `RESUME_CLEAN_OPERATION` | `EDGE_TO_MCU` | 是 | `117` |
| `0x24` | `END_CLEAN_BEFORE_UNLOCK` | `EDGE_TO_MCU` | 是 | `90` |
| `0x25` | `SAMPLE_FULLNESS` | `EDGE_TO_MCU` | 是 | `118` |
| `0x26` | `MEASURE_BASELINE` | `EDGE_TO_MCU` | 是 | `113` |
| `0x27` | `AUTHORIZE_DELIVERY_FIRST_OPEN` | `EDGE_TO_MCU` | 是 | `101` |
| `0x30` | `WORK_PREOPEN_WEIGHT_READY` | `MCU_TO_EDGE` | 是 | `95` |
| `0x31` | `DELIVERY_DOOR_STATE_CHANGED` | `MCU_TO_EDGE` | 是 | `57` |
| `0x32` | `WORK_POSTCLOSE_WEIGHT_READY` | `MCU_TO_EDGE` | 是 | `95` |
| `0x33` | `DELIVERY_SELECTION` | `MCU_TO_EDGE` | 是 | `56` |
| `0x34` | `WORK_PREUNLOCK_WEIGHT_READY` | `MCU_TO_EDGE` | 是 | `93` |
| `0x35` | `CLEAN_LOCK_POWER_CHANGED` | `MCU_TO_EDGE` | 是 | `55` |
| `0x36` | `CLEAN_UNLOCK_REQUESTED` | `MCU_TO_EDGE` | 是 | `39` |
| `0x37` | `CLEAN_FINISH_REQUESTED` | `MCU_TO_EDGE` | 是 | `39` |
| `0x38` | `CLEAN_FINAL_WEIGHT_READY` | `MCU_TO_EDGE` | 是 | `79` |
| `0x39` | `FULLNESS_SAMPLE_RESULT` | `MCU_TO_EDGE` | 是 | `96` |
| `0x3A` | `BASELINE_MEASUREMENT_RESULT` | `MCU_TO_EDGE` | 是 | `93` |
| `0x3B` | `FAULT_OBSERVED` | `MCU_TO_EDGE` | 是 | `59` |
| `0x3C` | `SAFETY_SENSOR_EVENT` | `MCU_TO_EDGE` | 是 | `42` |
| `0x3D` | `SAFE_CLOSE_RESULT` | `MCU_TO_EDGE` | 是 | `42` |
| `0x3E` | `CLEAN_COMPLETION_CONFIRMED` | `MCU_TO_EDGE` | 是 | `60` |
| `0x50` | `STATE_SNAPSHOT_BEGIN` | `MCU_TO_EDGE` | 是 | `229` |
| `0x51` | `STATE_SNAPSHOT_PORT` | `MCU_TO_EDGE` | 是 | `78` |
| `0x52` | `STATE_SNAPSHOT_END` | `MCU_TO_EDGE` | 是 | `96` |

## OneNet 下行

| OneNet identifier | commandType | 目标 | 类型 |
|---|---|---|---|
| `applyConfiguration` | `APPLY_CONFIGURATION` | `CONFIGURATION_APPLICATION` | `DOMAIN_COMMAND` |
| `startDeliverySession` | `START_DELIVERY_SESSION` | `DELIVERY_SESSION` | `DOMAIN_COMMAND` |
| `startCleanOperation` | `START_CLEAN_OPERATION` | `CLEAN_OPERATION` | `DOMAIN_COMMAND` |
| `endCleanBeforeUnlock` | `END_CLEAN_BEFORE_UNLOCK` | `CLEAN_OPERATION` | `DOMAIN_COMMAND` |
| `resumeCleanOperation` | `RESUME_CLEAN_OPERATION` | `CLEAN_OPERATION` | `DOMAIN_COMMAND` |
| `sampleFullness` | `SAMPLE_FULLNESS` | `FULLNESS_DETECTION` | `DOMAIN_COMMAND` |
| `measureEmptyBagBaseline` | `MEASURE_EMPTY_BAG_BASELINE` | `BASELINE_MEASUREMENT` | `DOMAIN_COMMAND` |
| `confirmEdgeEvent` | `CONFIRM_EDGE_EVENT` | `EDGE_EVENT` | `CONTROL_COMMAND` |
| `providePhotoUploadGrant` | `PROVIDE_PHOTO_UPLOAD_GRANT` | `PHOTO_GRANT_REQUEST` | `CONTROL_COMMAND` |

## OneNet 上行

| OneNet identifier | eventType | deliveryClass | 目标 |
|---|---|---|---|
| `deviceCommandObserved` | `DEVICE_COMMAND_OBSERVED` | `RELIABLE_FACT` | `DEVICE_COMMAND` |
| `configurationProgress` | `CONFIGURATION_PROGRESS` | `RELIABLE_FACT` | `CONFIGURATION_APPLICATION` |
| `deliveryComplete` | `DELIVERY_COMPLETE` | `RELIABLE_FACT` | `DELIVERY_SESSION` |
| `cleanComplete` | `CLEAN_COMPLETE` | `RELIABLE_FACT` | `CLEAN_OPERATION` |
| `fullnessSampleComplete` | `FULLNESS_SAMPLE_COMPLETE` | `RELIABLE_FACT` | `FULLNESS_DETECTION` |
| `baselineMeasurementComplete` | `BASELINE_MEASUREMENT_COMPLETE` | `RELIABLE_FACT` | `BASELINE_MEASUREMENT` |
| `deviceFaultObserved` | `DEVICE_FAULT_OBSERVED` | `RELIABLE_FACT` | `DEVICE_DEPLOYMENT` |
| `deviceFaultRecovered` | `DEVICE_FAULT_RECOVERED` | `RELIABLE_FACT` | `DEVICE_DEPLOYMENT` |
| `photoStatusReported` | `PHOTO_STATUS_REPORTED` | `RELIABLE_FACT` | `DELIVERY_SESSION / CLEAN_OPERATION` |
| `photoUploadGrantRequested` | `PHOTO_UPLOAD_GRANT_REQUESTED` | `RELIABLE_FACT` | `DELIVERY_SESSION / CLEAN_OPERATION` |
| `businessConfirmationReceipt` | `BUSINESS_CONFIRMATION_RECEIPT` | `CONTROL_RECEIPT` | `BUSINESS_CONFIRMATION` |
| `deviceRuntimeSnapshot` | `DEVICE_RUNTIME_SNAPSHOT` | `TELEMETRY_SNAPSHOT` | `DEVICE_DEPLOYMENT` |

## MCU 人工确认清单

- [ ] 消息号、方向、逐字段顺序和固定/最大 payload 长度。
- [ ] capability bit 与当前 MCU 硬件能力一致。
- [ ] `CLEAN_FINAL_WEIGHT_READY` 作为人工完成请求后的独立称重结果可实现。
- [ ] 清运只存在电磁阀通断；没有门磁、自动关门或清运 `SAFE_CLOSE`。
- [ ] 不可逆动作的命令去重、关键事件队列和 boot/event 序号可掉电保存。
- [ ] 配置 staging/COMMIT 可原子切换并跨重启报告进度。
- [ ] C 工具链编译并通过同一份 `ecobin_uart_golden_test.c`。
- [ ] 真机对 CRC、ACK 丢失、重发、重启和投递门独立超时关门留存证据。
