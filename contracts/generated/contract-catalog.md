# EcoBin F-10 机器契约目录（生成）

> 本文件由 `contracts/tools/generate_contracts.py` 生成，请勿直接编辑。

- UART Registry：`2.0.0-rc.27`
- UART Registry SHA-256：`7843c34c5fe2fd3c574186fe2a4c2b7f477b7ff5d6e284b91123a3ecb6fafb05`
- UART 状态：`MCU_REVIEW_REQUIRED`
- 实施阶段：`SIMPLIFIED_BUSINESS_INTEGRATION_NOT_RELEASED`；候选不可运行，旧运行制品摘要冻结，不自动覆盖。
- 单帧预算见 `contracts/uart/generated/message-budget.json`；不代表完整结果/RAM 预算已完成。
- UART 物理链路：`115200 baud / 8N1 / no flow control`
- OneNet Mapping：`2.5.0` / `IMPLEMENTATION_CANDIDATE`

## UART 消息

| ID | 消息 | 生命周期 | 方向 | ACK | payload 字节 | 最大帧字节 | 余量 |
|---:|---|---|---|---|---:|---:|---:|
| `0x45` | `QUERY_DEVICE_FACTS` | `currentDiagnosticMessages` | `EDGE_TO_MCU` | 否 | `17` | 31 | 225 |
| `0x46` | `DEVICE_FACTS_REPLY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 否 | `226` | 240 | 16 |
| `0x47` | `QUERY_DEVICE_IDENTITY` | `currentBusinessMessages` | `EDGE_TO_MCU` | 否 | `16` | 30 | 226 |
| `0x48` | `DEVICE_IDENTITY_REPLY` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `53..85` | 99 | 157 |
| `0x60` | `ACTUATOR_EVENT_SAVED` | `currentDiagnosticMessages` | `EDGE_TO_MCU` | 否 | `45` | 59 | 197 |
| `0x61` | `ACTUATOR_EVENT_SAVED_REPLY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 否 | `54` | 68 | 188 |
| `0x5E` | `QUERY_ACTUATOR_EVENT` | `currentDiagnosticMessages` | `EDGE_TO_MCU` | 否 | `20` | 34 | 222 |
| `0x5F` | `ACTUATOR_EVENT_QUERY_REPLY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 否 | `66` | 80 | 176 |
| `0x5C` | `PROCESS_EVENT_SAVED` | `currentDiagnosticMessages` | `EDGE_TO_MCU` | 否 | `45` | 59 | 197 |
| `0x5D` | `PROCESS_EVENT_SAVED_REPLY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 否 | `54` | 68 | 188 |
| `0x5A` | `QUERY_PROCESS_EVENT` | `currentDiagnosticMessages` | `EDGE_TO_MCU` | 否 | `97` | 111 | 145 |
| `0x5B` | `PROCESS_EVENT_QUERY_REPLY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 否 | `142` | 156 | 100 |
| `0x43` | `QUERY_WORK` | `currentBusinessMessages` | `EDGE_TO_MCU` | 否 | `86` | 100 | 156 |
| `0x44` | `WORK_QUERY_REPLY` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `132` | 146 | 110 |
| `0x40` | `WORK_RESULT` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `199` | 213 | 43 |
| `0x41` | `QUERY_RESULT` | `currentBusinessMessages` | `EDGE_TO_MCU` | 否 | `68` | 82 | 174 |
| `0x42` | `RESULT_QUERY_REPLY` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `77` | 91 | 165 |
| `0x01` | `HELLO` | `currentBusinessMessages` | `BIDIRECTIONAL` | 否 | `27..91` | 105 | 151 |
| `0x02` | `HELLO_ACK` | `currentBusinessMessages` | `BIDIRECTIONAL` | 否 | `32` | 46 | 210 |
| `0x03` | `ACK` | `currentBusinessMessages` | `BIDIRECTIONAL` | 否 | `22` | 36 | 220 |
| `0x04` | `NACK` | `currentBusinessMessages` | `BIDIRECTIONAL` | 否 | `23` | 37 | 219 |
| `0x05` | `QUERY_STATE` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `76` | 90 | 166 |
| `0x06` | `SAFE_CLOSE` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `66` | 80 | 176 |
| `0x07` | `BOOT_PROBE` | `currentBusinessMessages` | `EDGE_TO_MCU` | 否 | `8` | 22 | 234 |
| `0x08` | `BOOT_PROBE_REPLY` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `16` | 30 | 226 |
| `0x09` | `BIND_BOOT` | `currentBusinessMessages` | `EDGE_TO_MCU` | 否 | `16` | 30 | 226 |
| `0x0A` | `BIND_BOOT_REPLY` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `25` | 39 | 217 |
| `0x0B` | `COMMAND_DECISION` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `71` | 85 | 171 |
| `0x0C` | `QUERY_COMMAND` | `currentBusinessMessages` | `EDGE_TO_MCU` | 否 | `68` | 82 | 174 |
| `0x0D` | `COMMAND_QUERY_RESULT` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `83` | 97 | 159 |
| `0x0E` | `RESULT_SAVED` | `currentBusinessMessages` | `EDGE_TO_MCU` | 否 | `60` | 74 | 182 |
| `0x0F` | `RESULT_SAVED_REPLY` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `69` | 83 | 173 |
| `0x10` | `CONFIG_BEGIN` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `151` | 165 | 91 |
| `0x11` | `CONFIG_DEVICE_BLOCK` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `183` | 197 | 59 |
| `0x12` | `CONFIG_PORT_BLOCK` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `207` | 221 | 35 |
| `0x13` | `CONFIG_COMMIT` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `150` | 164 | 92 |
| `0x14` | `CONFIG_APPLY_RESULT` | `currentBusinessMessages` | `MCU_TO_EDGE` | 是 | `127` | 141 | 115 |
| `0x15` | `DEVICE_ENTRY_URL_BEGIN` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `111` | 125 | 131 |
| `0x16` | `DEVICE_ENTRY_URL_PART` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `111..175` | 189 | 67 |
| `0x17` | `DEVICE_ENTRY_URL_COMMIT` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `111` | 125 | 131 |
| `0x18` | `DEVICE_ENTRY_URL_APPLY_RESULT` | `currentBusinessMessages` | `MCU_TO_EDGE` | 否 | `89` | 103 | 153 |
| `0x20` | `START_DELIVERY_SESSION` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `137` | 151 | 105 |
| `0x21` | `START_CLEAN_OPERATION` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `125` | 139 | 117 |
| `0x22` | `UNLOCK_CLEAN_DOOR` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `107` | 121 | 135 |
| `0x23` | `RESUME_CLEAN_OPERATION` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `123` | 137 | 119 |
| `0x24` | `END_CLEAN_BEFORE_UNLOCK` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `102` | 116 | 140 |
| `0x25` | `SAMPLE_FULLNESS` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `130` | 144 | 112 |
| `0x26` | `MEASURE_BASELINE` | `currentBusinessMessages` | `EDGE_TO_MCU` | 是 | `125` | 139 | 117 |
| `0x27` | `AUTHORIZE_DELIVERY_FIRST_OPEN` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `113` | 127 | 129 |
| `0x28` | `CONFIRM_NO_ACTIVE_WORK` | `deprecatedRejectedMessages` | `EDGE_TO_MCU` | 是 | `100` | 114 | 142 |
| `0x30` | `WORK_PREOPEN_WEIGHT_READY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `97` | 111 | 145 |
| `0x31` | `DELIVERY_DOOR_COMMAND_RESULT` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `60` | 74 | 182 |
| `0x62` | `DELIVERY_LOCAL_DOOR_RESULT` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `64` | 78 | 178 |
| `0x65` | `CLEAN_OPERATION_INTERRUPTED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `61` | 75 | 181 |
| `0x64` | `DELIVERY_POSTCLOSE_INTERRUPTED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `61` | 75 | 181 |
| `0x63` | `DELIVERY_CYCLE_ABORTED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `61` | 75 | 181 |
| `0x32` | `WORK_POSTCLOSE_WEIGHT_READY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `207` | 221 | 35 |
| `0x33` | `DELIVERY_SELECTION` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `80` | 94 | 162 |
| `0x34` | `WORK_PREUNLOCK_WEIGHT_READY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `95` | 109 | 147 |
| `0x35` | `CLEAN_LOCK_POWER_CHANGED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `55` | 69 | 187 |
| `0x36` | `CLEAN_UNLOCK_REQUESTED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `63` | 77 | 179 |
| `0x37` | `CLEAN_FINISH_REQUESTED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `63` | 77 | 179 |
| `0x38` | `CLEAN_FINAL_WEIGHT_READY` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `191` | 205 | 51 |
| `0x39` | `FULLNESS_SAMPLE_RESULT` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `106` | 120 | 136 |
| `0x3A` | `BASELINE_MEASUREMENT_RESULT` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `95` | 109 | 147 |
| `0x3B` | `FAULT_OBSERVED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `59` | 73 | 183 |
| `0x3C` | `SAFETY_SENSOR_EVENT` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `42` | 56 | 200 |
| `0x3D` | `SAFE_CLOSE_RESULT` | `frozenHistoryMessages` | `MCU_TO_EDGE` | 是 | `43` | 57 | 199 |
| `0x3E` | `CLEAN_COMPLETION_CONFIRMED` | `currentDiagnosticMessages` | `MCU_TO_EDGE` | 是 | `83` | 97 | 159 |
| `0x3F` | `BOOT_RECONCILIATION_RESULT` | `frozenHistoryMessages` | `MCU_TO_EDGE` | 是 | `64` | 78 | 178 |
| `0x50` | `STATE_SNAPSHOT_BEGIN` | `frozenHistoryMessages` | `MCU_TO_EDGE` | 是 | `229` | 243 | 13 |
| `0x51` | `STATE_SNAPSHOT_PORT` | `frozenHistoryMessages` | `MCU_TO_EDGE` | 是 | `81` | 95 | 161 |
| `0x52` | `STATE_SNAPSHOT_END` | `frozenHistoryMessages` | `MCU_TO_EDGE` | 是 | `96` | 110 | 146 |

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
| `quarantineDeliveryRecovery` | `QUARANTINE_DELIVERY_RECOVERY` | `DELIVERY_SESSION` | `CONTROL_COMMAND` |
| `requestDeviceAcceptance` | `REQUEST_DEVICE_ACCEPTANCE` | `DEVICE_ASSET` | `CONTROL_COMMAND` |
| `authorizeFactorySeal` | `AUTHORIZE_FACTORY_SEAL` | `DEVICE_ASSET` | `CONTROL_COMMAND` |
| `syncDeviceEntryUrl` | `SYNC_DEVICE_ENTRY_URL` | `DEVICE_ASSET` | `CONTROL_COMMAND` |
| `startMcuFirmwareUpdate` | `START_MCU_FIRMWARE_UPDATE` | `MCU_FIRMWARE_DEPLOYMENT` | `CONTROL_COMMAND` |
| `startBusinessRuntimeUpdate` | `START_BUSINESS_RUNTIME_UPDATE` | `BUSINESS_RUNTIME_DEPLOYMENT` | `CONTROL_COMMAND` |
| `cancelBusinessRuntimeUpdate` | `CANCEL_BUSINESS_RUNTIME_UPDATE` | `BUSINESS_RUNTIME_DEPLOYMENT` | `CONTROL_COMMAND` |
| `openRemoteSupportTunnel` | `OPEN_REMOTE_SUPPORT_TUNNEL` | `REMOTE_SUPPORT_SESSION` | `CONTROL_COMMAND` |
| `closeRemoteSupportTunnel` | `CLOSE_REMOTE_SUPPORT_TUNNEL` | `REMOTE_SUPPORT_SESSION` | `CONTROL_COMMAND` |

## OneNet 上行

| OneNet identifier | eventType | deliveryClass | 目标 |
|---|---|---|---|
| `deliveryIssueEvidenceAppended` | `DELIVERY_ISSUE_EVIDENCE_APPENDED` | `RELIABLE_FACT` | `DELIVERY_SESSION` |
| `deliveryIssueArchived` | `DELIVERY_ISSUE_ARCHIVED` | `RELIABLE_FACT` | `DELIVERY_SESSION` |
| `deviceCommandObserved` | `DEVICE_COMMAND_OBSERVED` | `RELIABLE_FACT` | `DEVICE_COMMAND` |
| `configurationProgress` | `CONFIGURATION_PROGRESS` | `RELIABLE_FACT` | `CONFIGURATION_APPLICATION` |
| `deviceEntryUrlApplicationResult` | `DEVICE_ENTRY_URL_APPLICATION_RESULT` | `RELIABLE_FACT` | `DEVICE_ASSET` |
| `deliveryComplete` | `DELIVERY_COMPLETE` | `RELIABLE_FACT` | `DELIVERY_SESSION` |
| `deliveryRecoveryQuarantined` | `DELIVERY_RECOVERY_QUARANTINED` | `RELIABLE_FACT` | `DELIVERY_SESSION` |
| `cleanComplete` | `CLEAN_COMPLETE` | `RELIABLE_FACT` | `CLEAN_OPERATION` |
| `fullnessStateChanged` | `FULLNESS_STATE_CHANGED` | `RELIABLE_FACT` | `PORT_FULLNESS_STATE` |
| `fullnessSampleComplete` | `FULLNESS_SAMPLE_COMPLETE` | `RELIABLE_FACT` | `FULLNESS_DETECTION` |
| `baselineMeasurementComplete` | `BASELINE_MEASUREMENT_COMPLETE` | `RELIABLE_FACT` | `BASELINE_MEASUREMENT` |
| `deviceFaultObserved` | `DEVICE_FAULT_OBSERVED` | `RELIABLE_FACT` | `DEVICE_ASSET` |
| `deviceFaultRecovered` | `DEVICE_FAULT_RECOVERED` | `RELIABLE_FACT` | `DEVICE_ASSET` |
| `safetySensorStateChanged` | `SAFETY_SENSOR_STATE_CHANGED` | `RELIABLE_FACT` | `DEVICE_ASSET` |
| `photoStatusReported` | `PHOTO_STATUS_REPORTED` | `RELIABLE_FACT` | `DELIVERY_SESSION / CLEAN_OPERATION` |
| `photoUploadGrantRequested` | `PHOTO_UPLOAD_GRANT_REQUESTED` | `RELIABLE_FACT` | `DELIVERY_SESSION / CLEAN_OPERATION` |
| `businessConfirmationReceipt` | `BUSINESS_CONFIRMATION_RECEIPT` | `CONTROL_RECEIPT` | `BUSINESS_CONFIRMATION` |
| `deviceRuntimeSnapshot` | `DEVICE_RUNTIME_SNAPSHOT` | `TELEMETRY_SNAPSHOT` | `DEVICE_ASSET` |
| `deviceSoftwareStateReported` | `DEVICE_SOFTWARE_STATE_REPORTED` | `RELIABLE_FACT` | `DEVICE_ASSET` |
| `deviceAcceptanceEvidence` | `DEVICE_ACCEPTANCE_EVIDENCE` | `RELIABLE_FACT` | `DEVICE_ASSET` |
| `remoteSupportTunnelStatus` | `REMOTE_SUPPORT_TUNNEL_STATUS` | `RELIABLE_FACT` | `DEVICE_ASSET` |
| `mcuFirmwareUpdateProgress` | `MCU_FIRMWARE_UPDATE_PROGRESS` | `RELIABLE_FACT` | `MCU_FIRMWARE_DEPLOYMENT` |
| `businessRuntimeUpdateProgress` | `BUSINESS_RUNTIME_UPDATE_PROGRESS` | `RELIABLE_FACT` | `BUSINESS_RUNTIME_DEPLOYMENT` |
| `businessRuntimeCancelResult` | `BUSINESS_RUNTIME_UPDATE_CANCEL_RESULT` | `RELIABLE_FACT` | `BUSINESS_RUNTIME_DEPLOYMENT` |
| `factorySealCompleted` | `FACTORY_SEAL_COMPLETED` | `RELIABLE_FACT` | `DEVICE_ASSET` |

## MCU 人工确认清单

- [ ] 消息号、方向、逐字段顺序和固定/最大 payload 长度。
- [ ] capability bit 与当前 MCU 硬件能力一致。
- [ ] `CLEAN_FINAL_WEIGHT_READY` 作为人工完成请求后的独立称重结果可实现。
- [ ] 清运只存在电磁阀通断；没有门磁、自动关门或清运 `SAFE_CLOSE`。
- [ ] 投递门事件只报告命令输出，物理门位始终 `NOT_OBSERVABLE`。
- [ ] `UNSTABLE` 和带数据的故障测量保留 `reportedWeightGrams` 与质量标志。
- [ ] 配置 staging/COMMIT 在 RAM 中原子切换；重启后由香橙派重新同步。
- [ ] 启动对账可显式确认无旧作业或续接原清运，且不重启清运窗口。
- [ ] 未实现时不得宣称持久命令去重、持久事件队列或门控 HIL 能力。
- [ ] C 工具链编译并通过同一份 `ecobin_uart_golden_test.c`。
- [ ] 真机对 CRC、ACK 丢失、重发、重启和投递门独立超时关门留存证据。
