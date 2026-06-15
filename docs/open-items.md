# EcoBin 未解决项清单

> 本文是项目所有**未解决 / 待办**事项的单一汇总，取代原先散落在多份审查/笔记中的待办条目。
> 已解决的历史审查与逐次 review 见 `docs/archive/`。
> 末次同步：2026-06-11。

分三类：**技术债**（生产前需处理）、**功能缺口**（设计在册未实现）、**提现收尾**（依赖真实商户号）。

---

## 1. 技术债

> 来源：原 `three-end-review.md` §4 + `review-notes.md` 待考虑项。

### 1.1 生产前必处理

| 项 | 现状 | 修法 |
|----|------|------|
| `TokenInvalidationRegistry` 内存态 | 单实例 `ConcurrentHashMap`，**应用重启即失忆**——被禁用/降权账号的旧 token 在重启后复活，直到自然过期 | 失效记录持久化（Redis），或显著缩短 JWT 有效期 |
| ~~分页 `pageSize` 无上限~~ ✅ 已完成（2026-06-07） | 原可传 `pageSize=100000` 触发大查询 | `PaginationInnerInterceptor.setMaxLimit(200)` 全局兜底所有走 IPage 的分页端点；裸 SQL 的 `StatisticsServiceImpl.deviceRanking` 服务层另行 clamp 至 200 |
| `AesCryptoUtil` 加密强度 | `AES/ECB/PKCS5Padding`，无 IV、相同明文同密文；默认密钥硬编码 | 升 `AES/GCM/NoPadding`（随机 IV + 认证标签）；生产用 `app.crypto.aes-key` 覆盖 |
| `updateById` 置 null 依赖默认策略 | `TenantServiceImpl`/`AdminServiceImpl` 在密码/secret 为空时 `setPassword(null)`，依赖 MyBatis-Plus `FieldStrategy=NOT_NULL` 保持原值；全局策略一改即清空密码（高危） | 核对/显式注释锁定该假设 |

### 1.2 低优先

- 强制失效触发过宽：三处 `updateById` 失效条件为 `role != null || status != null`，前端回传字段（哪怕值未变）就强制下线 → 比对旧值变化后再登记。
- 秒级失效有 1 秒窗口：`after()` 用 `tokenIatSec < invalidated`，同一秒「改角色」与旧 token 签发时间相等时判未失效。低危可接受。
- `JwtAuthenticationFilter.writeUnauthorized` 手工拼 JSON → 改走 `ObjectMapper`/`Result` 序列化。
- `DEFAULT_TENANT_ID` 与 `PLATFORM_POOL_TENANT_ID` 同值=1，语义重叠（既「平台池」又「默认/未分配」）→ 将来真正分配 id 时注意区分。
- `DeliveryOrder.updateTime` 用 `@TableField(exist=false)` shadow 屏蔽 `BaseEntity.updateTime`：当前务实，但日后给基类加自动填充会静默失效 → 长期拆分「可变/不可变」基类。

---

## 2. 功能缺口（设计在册，未实现）

> 来源：原 `three-end-review.md` §2.1 / §3。

| 项 | 现状 | 依赖 / 备注 |
|----|------|------------|
| 设备状态 / 重量上报入口 | `biz_device_status` 有实体（`device/entity/DeviceStatus.java`）但**无 Service/Controller**；`biz_weight_record` **连实体都没有**（V1 建表后无业务代码） | 与投递两阶段同源（缺设备侧上报）。补齐后，统计接口当前返回 0 的预留字段（online/spill/smoke、`storageWeights` 等）方可接入真实数据 |
| ~~C 端清运员作业接口~~ ✅ 已完成（2026-06-07，V9 重做 2026-06-09） | 原「手填重量 + 后台审核」已**替换**为设备自动称重 + 垃圾袋去皮链：App `POST /api/app/clean/open`（扫新空袋→下发开清运门），设备经 `/api/iot/clean/gross`（毛重建单，net=毛重−去皮）与 `/tare`（换袋去皮 upsert `biz_clean_bag`）上报；审核流程取消 | 见 `database-design.md` §7/§7b、`api-frontend.md` §4.4 |
| 投递「上传后建单 + 设备端继续投递」✅ 已实现（2026-06-11，V13） | 投递从「开门即建单」改为「设备上传称重后建单」：`/api/app/delivery/open` 改为只记「设备当前活跃用户」会话（`biz_device_session`）+ 下发开门（不建单）；设备可在设备屏「继续投递」（每袋独立成单）；`deliveryComplete{doorIndex,weight,photo*4}` 到达时建单，归属取活跃会话，无活跃会话→无主单不返现。**2026-06-12 去设备 `deliveryToken`**：改按 **OneNet 消息 id**（分发器取报文 id，缺失回退 MQ messageId）幂等去重（落 `delivery_token` 列、唯一索引）；照片位置改设备决定 + 4 个 URL 随上报回传 | 见 `onenet-thing-model.md` §8/§4.1、`database-design.md` §8c、`delivery-flow.html`。固件需上报 doorIndex + 4 个照片 URL，待硬件方对齐 |
| OneNet 北向 MQ 上行（设备→后端） | ✅ **已实现，待联调**：`framework/onenet/OneNetMqConsumer`（Pulsar 消费者，连 `pulsar+ssl://iot-north-mq.heclouds.com:6651/`、订阅 `{accessId}/iot/event`）解第一层报文→AES 解密（`OneNetCipher`，key=secretKey.substring(8,24)）→`OneNetEventDispatcher`（business）按 `msgType=thingEvent` 的 `params` identifier 路由到 `reportGross/reportTare/completeDelivery`。消费组凭证由 `.env`（`iotAccessId/iotSecretKey/iotSubscriptionName`）注入。**联调步骤见 `docs/onenet-device-simulation.md`（MQTTX 模拟设备走完整上行）** | 凭证齐全 + `onenet.subscription.enabled=true` + 非测试环境才启动。`thingEvent` 事件输出的精确结构（是否 `value` 包裹）以联调时 consumer 打印的真实明文为准校正；属性/告警/上下线/服务回执的 Service 待补（照片已无 `photoReport` 事件，改设备直传，见 §8） |
| OneNet 设备下行真实接入 | **实现完成、本地未测**：`OneNetClient.openCleanDoor`/`openDeliveryDoor` 经 `invokeService` 调「物模型服务调用」API（`OneNetTokenGenerator` 标准 OneNET token 鉴权），`cosToken` 搭车下发（`sessionToken` 按 512 拆 `sessionToken1/2`）。`product-id/access-key` 未到位（需设备端配合）→ `isConfigured()` 为假时仍记占位日志、不真发 | 拿到 OneNet `product_id/access_key` 后填 `application.yml` 的 `onenet` 段（`onenetProductId/onenetAccessKey` 经 `.env`）；token 版本/`res` 资源串/调用路径（`onenet.invoke-service-path`）按官方「平台 API 安全鉴权 / 设备服务调用」文档联调校验 |
| 设备清运上报联调 | **开门即建单**：设备只回传 `{sn, cleanOrderId, weight}`，`cleanOrderId` 即幂等键；`gross`/`tare` 已按此重构（设备不传 doorIndex/userId/bagNo/reportSn）。固件实际上报字段/触发时机、`cleanOrderId` 透传未与硬件方对齐 | 依赖设备固件；与「设备状态/重量上报入口」同源 |
| 小程序清运页 ✅ 已完成（2026-06-09） | `pages/clean` 改为扫袋开门 + 净重展示（`frontend/miniprogram` 不纳入 git） | — |
| COS STS 真实临时凭证接入 | 开门时 `CosTokenClient.getTempCredentials` 取凭证随 OneNet `cosToken` 下发（**仅凭证，不含 key/expire**；清运 `buildPhotoKeys`+`toUrl` 开门预存订单 URL、设备据 cleanOrderId 自拼 key；投递 URL 由设备随上报回传）。`cos.*` 凭证已填 `.env`（2026-06-12，桶 `ecobin-1258140596`），并加 HTTP 拉取端点 `GET /api/iot/cos/temp-credentials` 供设备联调 | OneNet 下行 `cosToken` 真实下发仍待 `product-id/access-key` 到位后联调 |
| 设备拍照联调 | 开门前 + 关门后各拍箱内/箱外 4 张，设备直传 COS。**cosToken 不再下发 key**（也不带 expire）：**清运**设备据下发的 `cleanOrderId` 按 `{sn}/{doorIndex}/{cleanOrderId}/<slot>.jpg` 自拼 key（后端开门已按同公式预存 URL，不回传）；**投递**照片位置设备自定、4 个 URL 随 `deliveryComplete` 回传后端原样存。固件抓拍时机/上传策略待与硬件方对齐 | 待联调；见 `onenet-thing-model.md` §1.1/§3.4/§8 |
| 设备管理员专属作业接口 | 设备管理员可清运（已随上一行放行），但无「设备维护」等专属端点 | 待设备状态上报落地后一并补维护/告警处理端点 |
| HTTP 层越权读测试 | `AppDeliveryQueryTest` 在 service 层覆盖了「读他人订单抛异常」，但 `AppApiSecurityTest` 无「持 token 读他人 id → body code=404」端到端断言 | 归属过滤是核心安全边界，建议补一条 HTTP 用例 |

---

## 3. 提现收尾（真实微信转账）

> 来源：原 `withdraw-design-notes.md` 仍待办。提现核心流程（投递入账 + 申请/审核/查询）已实现并归档；以下依赖真实商户号配置，本地无法联调。

- `sys_tenant` 补支付凭证字段：`mch_apiv3_key` / `mch_cert_serial_no` / `mch_private_key`（AES 加密 + `@JsonProperty(WRITE_ONLY)`）。
- `WalletService.auditWithdraw` 通过分支接入微信「商家转账到零钱」API，成功后回填 `biz_withdraw_order.transfer_no`。
- 转账异步结果回查与失败补偿（当前提现状态机仅 3 态：0-待审核/1-已通过/2-已驳回，真实转账中间态待此处扩展）。
