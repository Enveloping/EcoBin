# EcoBin 数据库重构字段草案

> 状态：持续讨论稿，已记录第一至三轮确认结果，不代表最终迁移方案。  
> 整理日期：2026-07-19  
> 来源：`database-refactor.png` 及当前 V1–V14 Flyway 迁移。  
> 当前阶段只记录字段与语义，不确定数据类型、默认值、索引、外键和迁移方案。

## 1. 标记说明

| 标记 | 含义 |
|---|---|
| 沿用 | 草图字段与当前数据库字段基本一致 |
| 调整 | 当前已有相近字段，但名称、归属或语义发生变化 |
| 新增 | 当前 V1–V14 中没有该字段或独立数据表 |
| 待确认 | 草图存在重复、缺少英文名或业务语义尚未完全明确 |

字段名优先按草图原文记录；草图使用驼峰或只写中文时，同时给出建议的数据库下划线命名，但暂不视为定案。

## 2. 表与领域概览

草图包含以下主体和业务记录：

1. 用户
2. 租户
3. 管理员
4. 回收袋
5. 投递订单记录
6. 提现订单记录
7. 清运订单记录
8. 设备投口
9. 设备
10. 机构
11. 机构清运配置
12. 机构投递配置
13. 机构设备配置
14. 机构自动提现配置
15. 设备状态
16. 设备投口状态
17. 设备配置
18. 设备投口配置
19. 机构余额变更记录

其中“机构”及四类“机构配置”是相对当前模型最明显的新层级。现已确认一个租户可包含多个机构；业务表同时保存
`tenant_id` 与 `organization_id`，既受租户拦截器隔离，也必须在应用层继续按当前机构隔离，详见 §20。

## 3. 用户

建议暂沿用表名：`sys_user`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 用户主键 | 沿用 |
| `tenant_id` | `tenant_id` | 所属租户，继续承担租户隔离 | 沿用 |
| `organization_id` | `organization_id` | 所属机构；一条用户记录只属于一个机构 | 新增 |
| `username` | `username` | 用户名 | 沿用；当前为微信用户历史兼容字段，可空 |
| `password` | `password` | 登录密码密文 | 沿用；当前为微信用户历史兼容字段，可空 |
| `name` | `name` | 用户姓名或昵称 | 调整；当前拆为 `real_name` 与 `nickname`，需确认是否合并 |
| `avatar` | `avatar` | 头像 URL | 沿用 |
| `phone` | `phone` | 手机号 | 沿用 |
| `openid` | `openid` | 微信小程序用户标识 | 调整；唯一范围改为 `(organization_id, openid)` |
| `unionid` | `unionid` | 微信开放平台用户标识 | 沿用 |
| `role` | `role` | 用户角色 | 沿用；当前终端角色为 1/2/3 |
| `status` | `status` | 启用/禁用状态 | 沿用 |
| `balance` | `balance` | 可用余额 | 沿用 |
| `pending_balance` | `pending_balance` | 已提交提现但尚未成功或退款的冻结金额 | 沿用；语义已明确为提现处理中余额 |
| `withdrawn_balance` | `withdrawn_balance` | 草图备注为“总提现余额” | 新增；属于可派生汇总值，是否落库待确认 |

草图未列出当前字段：`email`、`real_name`、`nickname`、`create_time`、`update_time`。其中姓名相关字段是否合并到 `name`，需要在正式设计中明确。



answer: 

- 姓名就只用name，不需要real_name 和 nickname，name代表用户昵称
- create_time和update_time 我省略了，后面的所有create_time 和 update_time 我都省略了，你自行加上便可
- email不需要

## 4. 租户

建议暂沿用表名：`sys_tenant`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 租户主键，同时是租户隔离空间标识 | 沿用 |
| `name` | `name` | 租户名称 | 沿用 |
| `username` | `username` | 租户网页登录用户名 | 沿用 |
| `password` | `password` | 租户网页登录密码密文 | 沿用 |
| `address` | `address` | 租户地址 | 沿用 |
| `status` | `status` | 启用/禁用状态 | 沿用 |

草图未列出当前字段：`code`、`contact_name`、`contact_phone`、`miniapp_appid`、`miniapp_secret`、`merchant_no`、`create_time`、`update_time`。其中小程序与微信商户配置在新草图中明显下沉到了“机构”。



answer: 

- 两个创建和更新时间自行添上

## 5. 管理员

建议暂沿用表名：`sys_admin`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 管理员主键 | 沿用 |
| `username` | `username` | 管理员登录用户名 | 沿用 |
| `password` | `password` | BCrypt 等安全算法生成的密码密文 | 沿用 |
| `name` | `name` 或 `real_name` | 管理员姓名 | 调整；当前字段为 `real_name` |
| `role` | `role` | 平台管理员角色 | 沿用；当前为 9-超管、8-管理员 |
| `status` | `status` | 启用/禁用状态 | 沿用 |

草图未列出当前的 `create_time`、`update_time`。



answer: 

- 就用名字的字段名就叫name吧

- 两个创建和更新时间自行添上

## 6. 回收袋

草图名称：回收袋。建议临时表名：`biz_recycle_bag`；是否替代当前 `biz_clean_bag` 待确认。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 回收袋主键 | 沿用 |
| `tenant_id` | `tenant_id` | 所属租户 | 沿用 |
| `bag_qr` | `bag_qr` | 回收袋二维码或唯一编号 | 沿用 |

当前 `biz_clean_bag` 不是单纯的回收袋主数据，而是“设备投口当前袋”记录，还包含 `device_id`、`door_index`、`tare_weight`、`user_id` 及时间字段。新草图更像独立的袋子主表，需要确认：

- 一个袋码是否只能使用一次；
- 是否需要 `organization_id`；
- 当前袋与设备投口的绑定放在哪张表；
- 皮重由设备本地保存后，数据库是否仍保留皮重快照。



answer: 

- 表名依旧使用 biz_clean_bag

- 一个袋子会使用多次
- 不需要organization_id
- 在设备投口表里面增加一个 bag_qr 来绑定
- 保留，实时状态会在 设备投口状态 里面保存以及会在清运订单中保留

## 7. 投递订单记录

建议暂沿用表名：`biz_delivery_order`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 投递订单主键 | 沿用 |
| `tenant_id` | `tenant_id` | 所属租户 | 沿用 |
| `organization_id` | `organization_id` | 所属机构 | 新增 |
| `order_sn` | `order_sn` | 业务订单号 | 沿用 |
| `device_id` | `device_id` | 投递设备 ID | 沿用 |
| `door_id` | `door_id` | 投口记录 ID | 沿用 |
| `door_index` | `door_index` | 物理投口编号快照 | 新增；可避免只靠 `door_id` 回查历史编号 |
| `bag_qr` | `bag_qr` | 本次投递进入的回收袋编号 | 新增 |
| `user_id` | `user_id` | 投递用户；无活跃会话时允许为空 | 沿用 |
| `weight_before` | `weight_before` | 投递前设备称重，单位 kg | 新增；由设备随完成事件上传 |
| `weight_after` | `weight_after` | 投递后设备称重，单位 kg | 新增；由设备随完成事件上传 |
| `weight` | `weight` | 投递重量，单位 kg | 沿用 |
| `unit_price` | `unit_price` | 本单单价快照 | 调整；当前字段名为 `price` |
| `amount` | `amount` | 本单返现金额快照 | 新增；建议保存实际结算值，避免以后按变化后的单价重算 |
| `hasAnomaly` | `has_anomaly` | 草图备注：重量为负时标记为 1，否则为 0 | 调整；当前使用 `status=-1` 表示异常，需决定是否拆成独立布尔字段 |
| `status` | `status` | 草图备注：暂时不使用，作为订单状态备用字段 | 沿用但重新定义语义 |
| `photo_open_outside` | `photo_open_outside` | 开门前箱外照片 URL | 沿用 |
| `photo_open_inside` | `photo_open_inside` | 开门前箱内照片 URL | 沿用 |
| `photo_close_outside` | `photo_close_outside` | 关门后箱外照片 URL | 沿用 |
| `photo_close_inside` | `photo_close_inside` | 关门后箱内照片 URL | 沿用 |
| `audit_status` | `audit_status` | 审核状态 | 沿用；当前为 0-待审核、1-通过、2-拒绝 |
| `audit_time` | `audit_time` | 审核时间 | 沿用 |
| `audit_remark` | `audit_remark` | 审核备注 | 沿用 |
| `msg_id` | `msg_id` | OneNet MQ 消息 ID，用于完成事件防重 | 新增；唯一范围为 `(device_id, msg_id)` |

当前表另有 `delivery_token`、`delivery_status`、`waste_type1`、`waste_type2`、`score`、`login_type`、`create_time`。`delivery_token` 当前实际承载 OneNet 消息幂等键，重构后由语义明确的 `msg_id` 替代；`delivery_status` 属于旧流程残留，草图未继续使用。



answer: 

- hasAnomaly 就变成布尔字段
- 现在的 delivery_token 存的是 OneNet MQ 消息 ID（msgId），作用是防重投幂等——MQ 可能 at-least-once 重复投递同一条消息，用 device_id + delivery_token 做唯一性去重。那就加一个msg_id用来去重吧
- 两个创建和更新时间添上（需要update_time）

## 8. 提现订单记录

建议暂沿用表名：`biz_withdraw_order`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 提现订单主键 | 沿用 |
| `tenant_id` | `tenant_id` | 所属租户 | 沿用 |
| `organization_id` | `organization_id` | 实际出资或处理提现的机构 | 新增 |
| `user_id` | `user_id` | 提现申请用户 | 沿用 |
| `amount` | `amount` | 提现金额 | 沿用 |
| `status` | `audit_status` | 0-待审核、1-通过、2-驳回、3-无需审核 | 调整；审核状态与微信转账状态拆开 |
| `audit_by` | `audit_by` | 审核人 | 沿用；需明确关联管理员、租户还是机构操作员 |
| `audit_time` | `audit_time` | 审核时间 | 沿用 |
| `audit_remark` | `audit_remark` | 审核备注 | 沿用 |
| `outTradeNo` | `merchant_transfer_no` | 商户侧转账单号 | 调整；具体映射到 `out_bill_no`、`out_detail_no` 等哪个微信字段，取决于最终 API |

草图未列出当前的 `create_time`、`update_time`。正式接入微信转账时，建议区分系统提现单号、商户订单号、微信转账单号及转账状态，不能只用一个字段承载全部语义。



answer: 

- 增加系统提现单号 order_sn（防止发起重复提现，自己生成）、微信转账单号（微信转账后返回，微信返回）以及转账状态
- 两个创建和更新时间添上

## 9. 清运订单记录

建议暂沿用表名：`biz_clean_order`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 清运订单主键 | 沿用 |
| `tenant_id` | `tenant_id` | 所属租户 | 沿用 |
| `organization_id` | `organization_id` | 所属机构 | 新增 |
| `device_id` | `device_id` | 清运设备 ID | 沿用 |
| `door_id` | `door_id` | 清运投口记录 ID | 沿用 |
| `door_index` | `door_index` | 物理投口编号快照 | 新增 |
| `bag_qr` | `bag_qr` | 本次清运实际拿走的旧袋编号 | 沿用；明确不保存本次换入的新袋编号 |
| `user_id` | `user_id` | 执行清运的用户 | 沿用 |
| `weight_before` | `weight_before` | 清运开门前的稳定原始毛重，单位 kg | 新增；由设备随完成事件上传 |
| `weight_after` | `weight_after` | 换入新空袋并关门后的稳定原始毛重，单位 kg | 新增；由设备随完成事件上传 |
| `weight` | `weight` | 本次实际拿走内容物的最终净重 | 沿用；由设备上传 |
| `gross_weight` | `gross_weight` | 被清走旧袋的毛重审计快照，应与 `weight_before` 一致 | 沿用；兼容/审计字段 |
| `tare_weight` | `tare_weight` | 被清走旧袋原有皮重的审计快照 | 沿用；用于说明本次净重计算依据 |
| `net_weight` | `net_weight` | 净重审计兼容字段，应与 `weight` 一致 | 沿用；是否长期保留重复列仍待确认 |
| `audit_status` | `audit_status` | 0-待审核、1-通过、2-拒绝、3-无需审核 | 沿用；是否需要审核由机构清运配置决定并在订单中留快照 |
| `photo_open_outside` | `photo_open_outside` | 开门前箱外照片 URL | 沿用 |
| `photo_open_inside` | `photo_open_inside` | 开门前箱内照片 URL | 沿用 |
| `photo_close_outside` | `photo_close_outside` | 关门后箱外照片 URL | 沿用 |
| `photo_close_inside` | `photo_close_inside` | 关门后箱内照片 URL | 沿用 |
| `msg_id` | `msg_id` | OneNet MQ 消息 ID，用于 `cleanComplete` 防重 | 新增；唯一范围为 `(device_id, msg_id)` |

继续保留 `order_sn`、`waste_type1`、`waste_type2`、`status`、`create_time`、`update_time`。当前已有的
`new_bag_qr` 不进入新模型：新袋编号只更新 `biz_door_status.bag_qr`，不保存在清运订单。

按当前确认，设备同时上传清运前重量、清运后重量、旧袋皮重和最终净重，后端只保存稳定结果，不负责称重稳定性判断。
`gross_weight = weight_before`、`net_weight = weight` 会形成两组重复值；本稿先按“新增前后重量，同时保留原审计列”记录，
正式迁移前还需决定是长期保留兼容列，还是只在迁移过渡期保留。



answer： 

- bag_qr 会保存到设备投口状态表中（新加的表 后面会提到），因为数据库中有，清运时清运员在小程序中扫新垃圾袋的二维码得到bag_qr然后上传到后端，所以我就打算用数据库中的数据，不依赖设备上传，不知道会不会有什么弊端
- weight就是最终净重
- 保留审核，但是是否需要审核应该根据数据库中的配置项进行配置
- 增加 order_sn 用于对外标识，还有两个创建和更新时间；
- gross_weight和tare_weight用来审计留快照

> 第三轮修订：上面的“清运单袋号只取数据库、不依赖设备上传”已被后续决定覆盖。
> 清运单仍从清运开始时的状态快照取得旧袋号；设备完成事件上传的是换入的新袋号，用于更新投口当前袋，详见 §25.3。

## 10. 设备投口

建议暂沿用表名：`biz_door`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 投口主键 | 沿用 |
| `tenant_id` | `tenant_id` | 所属租户 | 沿用 |
| `organization_id` | `organization_id` | 所属机构 | 新增 |
| `device_id` | `device_id` | 所属设备 ID | 沿用 |
| `door_index` | `door_index` | 设备上的物理投口编号 | 沿用 |
| `waste_type1` | `waste_type1` | 垃圾一级分类 | 沿用 |
| `waste_type2` | `waste_type2` | 垃圾二级分类 | 沿用 |
| `enabled` | `enabled` | 是否启用 | 沿用 |

草图未列出当前的 `name`、`price`、`sort_order`、`create_time`、`update_time`。按已确认结果，保留 `name` 和时间字段，
移除 `sort_order`，单价从 `biz_door` 移到 `biz_door_config`，并允许它覆盖机构默认单价。



answer:

- name保留；price和sort_order去除，price放到投口配置表中（新增）
- 单价默认由机构投递配置提供全局默认值但是可以由投口配置表进行配置
- 加上两个创建和更新时间

## 11. 设备

建议暂沿用表名：`biz_device`。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 设备主键 | 沿用 |
| `tenant_id` | `tenant_id` | 所属租户 | 沿用 |
| `organization_id` | `organization_id` | 所属机构 | 新增 |
| `sn` | `sn` | 设备序列号 | 沿用 |
| `name` | `name` | 设备名称 | 调整；移入 `biz_device_config`，设备主表不重复保存 |
| `type` | `type` | 设备类型 | 沿用 |
| `lat` | `lat` | 纬度 | 调整；移入 `biz_device_config` |
| `lng` | `lng` | 经度 | 调整；移入 `biz_device_config` |
| `poi_id` | `poi_id` | 地图 POI 标识 | 新增；与位置字段一起放入 `biz_device_config` |
| `address` | `address` | 安装地址 | 调整；移入 `biz_device_config` |
| `enabled` | `enabled` | 是否启用 | 调整；当前字段为 `status`，同时混合离线/在线/维护状态 |

重构后 `biz_device` 只保存归属、SN、类型和启停等相对稳定的设备身份字段；名称、位置和地址属于可调整配置，
统一由 `biz_device_config` 保存。在线、信号、电压、固件版本、重量和告警继续放在运行态快照表中，避免主数据、配置与实时状态混用。



answer：

- 对的，设备状态相关的都放一个设备状态表中，后面会说到

## 12. 机构

建议新表名：`sys_organization` 或 `biz_organization`，前缀归属待确认。机构归属于租户，并承载小程序、微信商户和提现资金配置。

| 草图字段 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `id` | `id` | 机构主键 | 新增 |
| `name` | `name` | 机构名称 | 新增 |
| 微信商户号 | `wechat_merchant_no` 或 `merchant_no` | 草图备注为出资商户，原图未给出明确英文列名 | 调整；当前位于 `sys_tenant.merchant_no` |
| `wx_miniprogram_appid` | `wx_miniprogram_appid` | 微信小程序 AppID；草图注明 18 位、填写后不可更改 | 调整；当前为 `sys_tenant.miniapp_appid` |
| `wx_miniprogram_appsecret` | `wx_miniprogram_appsecret` | 微信小程序 AppSecret，必须加密保存且不得输出明文 | 调整；当前为 `sys_tenant.miniapp_secret` |
| `wx_miniprogram_name` | `wx_miniprogram_name` | 微信小程序名称 | 新增 |
| `phone_number` | `phone_number` | 机构联系电话 | 调整；当前租户有 `contact_phone` |
| `balance` | `balance` | 草图备注为用于用户提现的机构余额 | 新增；资金来源、充值、冻结及对账方式待设计 |
| `tenant_id` | `tenant_id` | 机构所属租户 | 新增；确定租户与机构的一对多关系 |
| `enabled` | `enabled` | 机构是否启用 | 新增；使用布尔语义替代宽泛的 `status` |



answer：

- 表名前缀就用sys
- 微信商户号就用wechat_merchant_no 
- 机构启用 status 变成 enabled 语义更明确
- 增加一个 机构余额变更记录表 来保存 用户提现、机构充值、金额变更类型/原因/备注 等数据
- 一个租户底下可以有多个机构，一个机构只能属于一个租户

## 13. 机构清运配置

建议临时表名：`sys_organization_clean_config`。

| 草图字段/配置项 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `organization_id` | `organization_id` | 机构外键；原则上一机构一条配置 | 新增 |
| `clean_data_source` | `clean_data_source` | 清运数据来源；草图候选包括设备累加、后台累加、设备即时重量 | 新增；枚举值和三种模式的精确定义待确认 |
| `foo` | 待命名 | 草图明确标注“待后续添加” | 占位字段，不应直接进入正式迁移 |



## 14. 机构投递配置

建议临时表名：`sys_organization_delivery_config`。

| 草图字段/配置项 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `organization_id` | `organization_id` | 机构外键；原则上一机构一条配置 | 新增 |
| `audit` | `audit_mode` | 投递审核方式 | 新增；需定义人工审核、自动审核等枚举 |
| `global_default_unit_price` | `global_default_unit_price` | 全局默认投递单价，单位元/kg | 新增；当前单价位于 `biz_door.price` |
| 单次最小提现金额 | `min_withdraw_amount` | 单笔提现下限 | 新增 |
| 单次最大提现金额 | `max_withdraw_amount` | 单笔提现上限 | 新增 |
| 日上限重量 | `daily_weight_limit` | 每日累计重量上限，单位 kg | 新增 |
| 周上限重量 | `weekly_weight_limit` | 每周累计重量上限，单位 kg | 新增 |
| 月上限重量 | `monthly_weight_limit` | 每月累计重量上限，单位 kg | 新增 |
| 年上限重量 | `yearly_weight_limit` | 每年累计重量上限，单位 kg | 新增 |
| 天上限金额 | `daily_amount_limit` | 每日累计返现金额上限 | 新增 |
| 周上限金额 | `weekly_amount_limit` | 每周累计返现金额上限 | 新增 |
| 月上限金额 | `monthly_amount_limit` | 每月累计返现金额上限 | 新增 |
| 年上限金额 | `yearly_amount_limit` | 每年累计返现金额上限 | 新增 |
| 设备数据来源 | `delivery_data_source` | 草图候选包括设备累加、后台累加、设备即时重量 | 新增；名称与计算规则待确认 |

提现上下限被画在“机构投递配置”中，但它们也可能更适合归入“机构自动提现配置”或独立资金配置；当前只按草图位置记录，不提前移动。



answer:

- 表名就用 sys_organization_delivery_config

- 这里的审核是投递审核，控制是否需要审核投递订单，而提现审核控制提现订单是否需要审核
- 对，这里提现金额相关的配置应该移动到机构提现配置中
- 设备数据来源待讨论

## 15. 机构设备配置

建议临时表名：`sys_organization_device_config`。

| 草图字段/配置项 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `organization_id` | `organization_id` | 机构外键；原则上一机构一条配置 | 新增 |
| 1 号投口价格 | `door_1_unit_price` | 机构下所有设备 1 号投口的默认价格 | 新增 |
| 2 号投口价格 | `door_2_unit_price` | 机构下所有设备 2 号投口的默认价格 | 新增 |
| 3 号投口价格 | `door_3_unit_price` | 机构下所有设备 3 号投口的默认价格 | 新增 |
| 4 号投口价格 | `door_4_unit_price` | 机构下所有设备 4 号投口的默认价格 | 新增 |
| 5 号投口价格 | `door_5_unit_price` | 机构下所有设备 5 号投口的默认价格 | 新增 |
| 6 号投口价格 | `door_6_unit_price` | 机构下所有设备 6 号投口的默认价格 | 新增 |



answer：

- 加6个投口的价格配置项 也就是1号到6号投口的价格（因为之后可能需要配置多投口，并且这个配置项被配置后的优先度高于全局投口默认价格）

  

## 16. 机构自动提现配置

建议临时表名：`sys_organization_auto_withdraw_config`。

| 草图字段/配置项 | 建议数据库字段 | 备注 | 对照当前数据库 |
|---|---|---|---|
| `organization_id` | `organization_id` | 机构外键；原则上一机构一条配置 | 新增 |
| 是否投递后即时支付 | `instant_payment_enabled` | 开启后，投递完成可直接打款到用户微信零钱；草图注明开启后还需配置免审核金额 | 新增 |
| 投递免审核金额 | `no_audit_amount_threshold` | 草图范围为 0.1–200 元；小于阈值的投递可自动打款，大于阈值的订单进入人工审核 | 新增；边界是“小于”还是“小于等于”待确认 |

即时支付会改变当前“余额入账 → 用户申请提现 → 人工审核”的资金状态机。正式设计时必须同时明确微信转账结果、失败重试、幂等、退款/冲正和资金流水，不能只增加两个配置字段。



answer:

- 投递免审金额边界是小于等于
- 是的，是否需要人工审核提现订单接受配置 （都需要审核、超额需审核）
- 微信转账结果、失败重试、幂等、退款/冲正和资金流水 待讨论

## 17. 草图尚未覆盖的公共字段与约束

图片主要表达业务字段，因此以下内容统一留到下一轮设计：

- `create_time`、`update_time` 等审计字段；
- 金额与重量精度、可空性、默认值和非负约束；
- `tenant_id` 与 `organization_id` 的一致性约束；
- 主键、业务唯一键、复合索引与外键；
- 设备、投口、机构迁移或重新归属时的级联规则；
- IoT 完成事件的幂等键与失败恢复；
- 钱包流水、机构余额流水、提现转账流水和冲正记录；
- 订单状态、审核状态、支付状态是否拆分；
- 软删除、历史数据保留与敏感配置加密。



answer：

- 两个 创建和更新时间都需要加上
- 金额精度保留四位小数，最终展示和结算时四舍五入为2位

## 18. 下一轮需要确认的问题

1. 一个租户是否可以拥有多个机构，一个用户是否固定属于一个机构？
2. `tenant_id + organization_id` 是否会同时保存在所有业务表，如何保证两者匹配？
3. 租户草图中第二个 `name` 实际表示什么？
4. 用户的 `name` 是真实姓名、微信昵称，还是两者合并？
5. 回收袋是独立主数据，还是仍表示“投口当前袋”？
6. 投递单的 `has_anomaly` 与备用 `status` 如何分工？
7. 清运单在 v3 下是否仍保存 `gross_weight`、`tare_weight`，以及是否继续人工审核？
8. 机构默认单价与投口个性化单价谁优先？
9. 自动提现的金额阈值按单笔投递金额判断，还是按用户累计余额判断？
10. 机构 `balance` 是账务余额、可提现资金池快照，还是微信商户平台余额的镜像？



answer：

- 一个租户拥有多个机构，一个用户固定属于一个机构，用户表中可能会出现同两个手机号、openid等个人信息一样的用户但是他们的机构id是不一样的，也就是说机构间的用户不是共享的、
- 是的，organization_id和 tenant_id 会保存在所有的业务表，小程序中应该附带自己属于哪个机构的信息，来标识当用户进入小程序时是使用的哪个机构信息（同一租户下不同机构间用户数据不共享），如果该机构下无该用户则自动注册，不同机构之间用的小程序appid不同但页面代码和逻辑是一样的，后端区分不同的小程序就通过用户登录时传的appid来区分不同的机构
- 第二个name是重复的，忽略掉，不需要
- name可以来自微信昵称也可以用户自己设置（name和avatar），不需要存储用户真实名称（毕竟应该不会有人想实名上网）
- 回收袋是独立的主数据
- status在目前阶段先不使用，备注好就行，以防后续产生疑惑
- 保存gross_weight和tare_weight;前面回答了，根据配置决定
- 投口个性化单价优先
- 自动提现的金额阈值按单笔投递金额判断，也就是说，自动提现是指在用户投递并审核投递订单（如果需要审核）完成后如果计算得到的金额少于设置的自动提现金额阈值就直接提现到用户的微信钱包里面（不需要审核提现订单，用户也不需要到小程序中手动申请提现），如果大于的话投递并审核投递订单（如果需要审核）完成后则需要先审核提现订单，审核完成后就直接提现到用户的微信钱包里面
- 机构 balance 是可提现资金池快照

## 19. 第二轮已确认决策（2026-07-19）

本轮确认以下内容：

- 当前袋的权威位置是 `biz_door_status.bag_qr`，不再放入 `biz_door`。
- `biz_clean_order.bag_qr` 固定表示本次清走的旧袋；换入的新袋只替换 `biz_door_status.bag_qr`，不写入清运单。
- 用户唯一范围下沉到机构，`openid` 唯一约束调整为 `(organization_id, openid)`。
- 机构级 1–6 号投口价格使用六个固定列；系统最多支持六个投口。
- 自动提现仍先把投递返现记入用户余额，再按机构配置触发提现。
- 微信转账明确失败后，提现金额退回用户可用余额，并在提现订单记录失败原因。
- 投递订单增加 `weight_before`、`weight_after`；最终 `weight` 表示本次投递净增重量。
- 新增或重整：设备状态、设备投口状态、设备配置、设备投口配置、机构余额变更记录。

## 20. 机构级数据隔离设计

### 20.1 为什么只增加 `organization_id` 仍可能串数据

在表中保存 `organization_id` 是必要条件，但它本身不会自动参与 SQL。现有 MyBatis-Plus 租户拦截器只会追加
`tenant_id = 当前租户`，因此同一租户下的机构 A 与机构 B 仍在同一个租户结果集中。以下情况仍会泄漏或写错数据：

1. 列表或详情查询只按 `tenant_id`、主键查询，忘记追加 `organization_id`，会读到同租户其他机构的数据。
2. C 端请求允许客户端直接传 `organization_id`，攻击者可以替换成同租户其他机构 ID。
3. 订单虽然保存了机构 ID，但关联的 `user_id`、`device_id`、`door_id` 实际属于不同机构，形成跨机构脏关联。
4. `tenant_id` 与 `organization_id` 可以被分别写入，出现“租户 2 的业务行引用租户 3 的机构”这种不一致。
5. 唯一索引仍按租户建立时，同一个 `openid` 无法在同租户的第二个机构注册。
6. IoT 上报没有用户 JWT；如果信任消息体传来的机构 ID，设备可以把订单写入错误机构。

### 20.2 约束规则

1. 小程序登录按全局唯一的 `wx_miniprogram_appid` 查询 `sys_organization`，得到可信的
   `tenant_id + organization_id`；用户 JWT 同时携带两者。
2. C 端业务写入的机构 ID只能来自登录上下文，不接受请求体覆盖。
3. IoT 事件按可信的设备 SN 查询 `biz_device`，订单的 `tenant_id + organization_id` 从设备记录取得，不采用设备上报值。
4. 所有机构级查询、更新、删除必须同时限定 `tenant_id + organization_id`。后续实现可增加
   `OrganizationContextHolder` 与机构拦截器，避免依靠每个 Service 手写条件。
5. `sys_organization` 建立 `UNIQUE (tenant_id, id)`；业务表可用复合外键
   `(tenant_id, organization_id) → sys_organization(tenant_id, id)`，保证机构确实属于该租户。
6. 创建订单时校验用户、设备、投口与订单的 `tenant_id + organization_id` 全部一致。关键关联可进一步使用复合外键，
   或在单一事务内做强校验。
7. `sys_user` 使用 `UNIQUE (organization_id, openid)`；`openid IS NULL` 时仍允许多条历史/非微信用户记录。
8. `sys_organization.wx_miniprogram_appid` 全局唯一；同一套小程序代码可以对应多个不同 AppID。
9. 租户后台可以管理名下所有机构，但进入机构业务页时必须显式选择机构；终端用户始终只能访问自己的机构。

## 21. 设备与投口状态表设计

仓库 V10 已存在 `biz_device_status` 和 `biz_door_status`，本轮是在现有表上重整字段，不再创建第三套同义状态表。
状态表保存“最近一次快照”，允许高频覆盖更新，不承担历史时序日志职责。

### 21.1 `biz_device_status` — 设备状态快照

每台设备一行，建议唯一约束 `UNIQUE (device_id)`。

| 字段 | 建议类型 | 说明 |
|---|---|---|
| `id` | BIGINT | 主键 |
| `tenant_id` | BIGINT | 租户隔离字段 |
| `organization_id` | BIGINT | 所属机构 |
| `device_id` | BIGINT | 设备 ID，一对一唯一 |
| `online` | TINYINT | 0-离线、1-在线 |
| `work_status` | TINYINT | 建议：0-空闲、1-投递中、2-清运中、3-维护中、4-故障 |
| `voltage` | DECIMAL(8,3) | 整机供电电压 |
| `rssi` | INT | 网络信号强度 dBm |
| `app_version` | VARCHAR(32) | 香橙派设备程序版本 |
| `mcu_version` | VARCHAR(32) | MCU 固件版本 |
| `cpu_temperature` | DECIMAL(6,2) | 香橙派 CPU 温度 |
| `cpu_usage` | DECIMAL(5,2) | CPU 使用率 0–100 |
| `memory_usage` | DECIMAL(5,2) | 内存使用率 0–100 |
| `disk_usage` | DECIMAL(5,2) | 磁盘使用率 0–100 |
| `fault_code` | VARCHAR(64) | 当前故障码；无故障为空 |
| `fault_message` | VARCHAR(255) | 当前故障说明；便于运维查看 |
| `last_online_time` | DATETIME | 最近上线时间 |
| `last_offline_time` | DATETIME | 最近离线时间 |
| `last_report_time` | DATETIME | 最近状态上报时间 |
| `create_time` | DATETIME | 创建时间 |
| `update_time` | DATETIME | 最近快照更新时间 |

说明：当前的 `fw_version` 建议拆成 `app_version + mcu_version`，避免无法判断版本属于香橙派还是 MCU。

2026-07-19 对一台 Orange Pi Zero 3 做了只读核查：系统可读取 CPU、GPU、DDR、VE 四个 thermal zone，
也可从 `/proc`、`/sys` 和文件系统统计中得到 CPU、内存、磁盘指标；当时 CPU 温度约 39℃。
当前设备程序尚未采集并上报这些运行指标。本稿先只落运维最常用的 `cpu_temperature`，不为 GPU/DDR/VE
分别增加数据库列；如将来需要完整硬件遥测，更适合进入时序监控而不是不断扩宽状态快照表。

### 21.2 `biz_door_status` — 设备投口状态快照

每个物理投口一行，建议建立：

- `UNIQUE (door_id)`；
- `UNIQUE (device_id, door_index)`；
- `UNIQUE (bag_qr)`：`bag_qr` 可空，非空时保证一个回收袋同一时刻只绑定一个投口。

| 字段 | 建议类型 | 说明 |
|---|---|---|
| `id` | BIGINT | 主键 |
| `tenant_id` | BIGINT | 租户隔离字段 |
| `organization_id` | BIGINT | 所属机构 |
| `device_id` | BIGINT | 所属设备 ID |
| `door_id` | BIGINT | 对应 `biz_door.id` |
| `door_index` | TINYINT | 物理投口编号 1–6 |
| `bag_qr` | VARCHAR(64) | 当前绑定的回收袋编号，本表为权威位置；非空值唯一 |
| `current_weight` | DECIMAL(12,3) | MCU 给出的当前稳定原始毛重，单位 kg |
| `tare_weight` | DECIMAL(12,3) | 设备上报的当前袋皮重快照，单位 kg |
| `net_weight` | DECIMAL(12,3) | 设备上报的当前内容物净重快照；与前两项同批更新 |
| `fullness` | TINYINT | 满溢度 0–100 |
| `door_state` | TINYINT | 预留且可空：0-关闭、1-开启中、2-已开启、3-关闭中、4-故障 |
| `lock_state` | TINYINT | 预留且可空：0-已锁、1-已解锁、2-故障 |
| `spill_alarm` | TINYINT | 满溢告警 |
| `smoke_alarm` | TINYINT | 烟雾告警 |
| `fault_code` | VARCHAR(64) | 当前投口故障码 |
| `fault_message` | VARCHAR(255) | 当前投口故障说明 |
| `last_report_time` | DATETIME | 最近状态上报时间 |
| `create_time` | DATETIME | 创建时间 |
| `update_time` | DATETIME | 最近快照更新时间 |

当前没有门位和锁状态传感器，但后续大概率增加，因此两列保留为可空预留列。当前也没有投口温度传感器，
投口状态表不增加 `temperature`。MCU 只给出稳定重量，状态表不再保存恒为真的 `weight_stable`，
后端与香橙派也不配置负重量容差。

清运开始时必须先锁定并保存当时的旧 `bag_qr`、旧袋皮重、清运人、设备和投口；完成时再在一个数据库事务中：

1. 按 `(device_id, msg_id)` 幂等创建清运单，订单 `bag_qr` 写开始时捕获的旧袋号；
2. 保存设备上报的清运前后重量、旧袋皮重、最终净重和照片；
3. 将设备完成事件中的新袋号写入 `biz_door_status.bag_qr`，并将清运后重量作为新袋状态基线；
4. 同步更新当前毛重、皮重、净重及告警快照。

任一步失败时整组数据回滚。这样既不会把新袋误记为被清走的袋，也不会在状态覆盖后丢失旧袋号。

## 22. 设备与投口配置表设计

配置表保存低频修改、需要下发或参与业务计算的参数；状态表不保存这些配置。

### 22.1 `biz_device_config` — 单台设备配置

每台设备一行，`UNIQUE (device_id)`。

| 字段 | 建议类型 | 说明 |
|---|---|---|
| `id` | BIGINT | 主键 |
| `tenant_id` | BIGINT | 租户隔离字段 |
| `organization_id` | BIGINT | 所属机构 |
| `device_id` | BIGINT | 设备 ID，一对一唯一 |
| `name` | VARCHAR(100) | 设备展示名称；从 `biz_device` 移入本表 |
| `lat` | DECIMAL(10,7) | 安装纬度 |
| `lng` | DECIMAL(10,7) | 安装经度 |
| `poi_id` | VARCHAR(100) | 地图 POI 标识；可空 |
| `address` | VARCHAR(255) | 设备安装地址 |
| `full_weight_threshold` | DECIMAL(12,3) | 重量满溢阈值，单位 kg |
| `heartbeat_interval_seconds` | INT | 心跳上报间隔 |
| `status_report_interval_seconds` | INT | 状态快照上报间隔 |
| `offline_timeout_seconds` | INT | 超过该时间未上报则判定离线 |
| `command_timeout_seconds` | INT | 下行命令等待回执超时时间 |
| `photo_enabled` | TINYINT | 是否启用投递/清运拍照 |
| `continuous_delivery_enabled` | TINYINT | 是否允许设备端“继续投递” |
| `config_version` | BIGINT | 配置版本号，修改后递增 |
| `sync_status` | TINYINT | 建议：0-待下发、1-已同步、2-同步失败 |
| `last_sync_time` | DATETIME | 最近成功同步时间 |
| `create_time` | DATETIME | 创建时间 |
| `update_time` | DATETIME | 更新时间 |

`full_weight_threshold` 是设备级默认值。当前建议用投口 `net_weight >= full_weight_threshold` 判定重量满溢，
避免不同袋皮重影响阈值；最终比较毛重还是净重仍需用户确认。名称、位置、地址只有本表一个权威来源，
`biz_device` 不再保留同名副本。

### 22.2 `biz_door_config` — 单个实际投口配置

每个实际投口一行，`UNIQUE (door_id)`。它用于设备/投口个性化覆盖，不替代 `biz_door` 的结构信息。

| 字段 | 建议类型 | 说明 |
|---|---|---|
| `id` | BIGINT | 主键 |
| `tenant_id` | BIGINT | 租户隔离字段 |
| `organization_id` | BIGINT | 所属机构 |
| `device_id` | BIGINT | 所属设备 ID |
| `door_id` | BIGINT | 投口 ID，一对一唯一 |
| `door_index` | TINYINT | 投口编号快照 1–6 |
| `unit_price` | DECIMAL(18,4) | 该实际投口的个性化单价；可空表示不覆盖 |
| `min_delivery_weight` | DECIMAL(12,3) | 单次有效投递最小重量；可空表示不限制 |
| `max_delivery_weight` | DECIMAL(12,3) | 单次有效投递最大重量；可空表示不限制 |
| `config_version` | BIGINT | 配置版本号 |
| `sync_status` | TINYINT | 0-待下发、1-已同步、2-同步失败 |
| `last_sync_time` | DATETIME | 最近成功同步时间 |
| `create_time` | DATETIME | 创建时间 |
| `update_time` | DATETIME | 更新时间 |

### 22.3 单价优先级

按已确认的“投口个性化单价优先”规则，建议采用：

```text
biz_door_config.unit_price（某一台设备的某个实际投口）
  > sys_organization_device_config.door_N_unit_price（机构下第 N 号投口默认价）
  > sys_organization_delivery_config.global_default_unit_price（机构全局默认价）
```

该优先级已确认。三层均为空时，在下发开门命令前直接拒绝投递，不允许按 0 元继续。
如果设备在配置异常或重复消息场景下仍上报了完成事件，后端不能丢弃审计数据：可以创建不返现的异常订单，
但不能把它视为正常的 0 元投递。

## 23. 机构余额变更记录设计

建议新表：`biz_organization_balance_record`。机构余额是可提现资金池快照，本表是不可删除、不可修改业务金额的追加式流水。

| 字段 | 建议类型 | 说明 |
|---|---|---|
| `id` | BIGINT | 主键 |
| `tenant_id` | BIGINT | 租户隔离字段 |
| `organization_id` | BIGINT | 机构 ID |
| `record_sn` | VARCHAR(50) | 机构余额流水号，全局唯一 |
| `change_type` | TINYINT | 变更类型，见下方枚举 |
| `change_amount` | DECIMAL(18,4) | 带符号变更额；增加为正、扣减为负 |
| `balance_before` | DECIMAL(18,4) | 变更前机构余额 |
| `balance_after` | DECIMAL(18,4) | 变更后机构余额 |
| `source_type` | TINYINT | 来源类型：充值、提现单、人工调整、初始化等 |
| `source_id` | BIGINT | 来源记录主键；无来源时可空 |
| `source_order_sn` | VARCHAR(50) | 来源业务单号快照，便于人工对账 |
| `operator_type` | TINYINT | 操作主体类型：系统、管理员、租户等 |
| `operator_id` | BIGINT | 操作人 ID；系统自动操作可空 |
| `idempotency_key` | VARCHAR(100) | 同一资金动作的幂等键，唯一 |
| `reason` | VARCHAR(100) | 结构化变更原因 |
| `remark` | VARCHAR(500) | 人工备注或渠道返回说明 |
| `create_time` | DATETIME | 创建时间 |
| `update_time` | DATETIME | 更新时间；正常情况下与创建时间一致 |

建议的 `change_type`：

| 值 | 含义 |
|---|---|
| 1 | 机构充值 |
| 2 | 用户提现/自动提现出款 |
| 3 | 出款失败冲正 |
| 4 | 人工增加 |
| 5 | 人工扣减 |
| 6 | 初始化或数据迁移 |

每次修改 `sys_organization.balance` 时，必须在同一个数据库事务内写入本流水，并用
`idempotency_key` 防止重复扣款或重复冲正。流水行保存前后余额，便于验证：

```text
balance_after = balance_before + change_amount
```

### 23.1 配套建议：用户余额流水

只增加机构余额流水仍不足以解释“投递入账 → 提现冻结 → 成功扣减 / 失败退回”的用户资金变化。
建议同时新增 `biz_user_balance_record`，至少保存：

| 字段 | 说明 |
|---|---|
| `tenant_id` / `organization_id` / `user_id` | 用户资金归属 |
| `record_sn` | 用户余额流水号 |
| `change_type` | 投递返现、提现冻结、提现成功、提现退款、人工调整 |
| `balance_before` / `balance_after` | 可用余额变更前后值 |
| `pending_before` / `pending_after` | 冻结/处理中余额变更前后值 |
| `source_type` / `source_id` | 来源投递单或提现单 |
| `idempotency_key` | 防重复入账、冻结、扣减和退款 |
| `remark` | 说明 |
| `create_time` / `update_time` | 时间字段 |

用户已确认增加该表。它与 `sys_user.balance`、`sys_user.pending_balance` 必须在同一事务内更新；
余额流水不是可选日志，而是用户资金变动的审计依据。

### 23.2 机构资金扣减时点

机构资金在实际准备调用微信转账时才扣减，不在投递返现进入用户余额时预留。推荐资金规则：

1. 提现创建时只把用户 `balance` 冻结到 `pending_balance`，机构余额不变。
2. 审核通过或无需审核后，调用微信前使用条件更新原子扣减机构余额，并在同一事务写
   `biz_organization_balance_record`；扣减金额是两位小数的实际转账金额。
3. 若机构余额不足，条件更新失败，不调用微信；提现单记 `ORG_BALANCE_NOT_ENOUGH`，用户冻结金额立即退回可用余额。
4. 微信返回受理中、网络超时或结果未知时，机构扣减和用户冻结都保持不动，必须先查单。
5. 查单确认成功时，扣除用户 `pending_balance`，机构扣减保持有效。
6. 查单确认终态失败并决定放弃重试时，机构余额写一笔“出款失败冲正”，同时把用户冻结金额退回；两边都必须幂等。

机构余额扣减不能简单采用“先读余额再更新”，否则并发提现可能共同通过检查而透支。正式实现应采用
`balance >= amount` 的条件更新或行锁，并让余额快照、机构流水和提现单资金状态在同一事务提交。

## 24. 自动提现与提现订单状态机

### 24.1 `biz_withdraw_order` 建议字段

| 字段 | 建议类型 | 说明 |
|---|---|---|
| `id` | BIGINT | 主键 |
| `tenant_id` | BIGINT | 所属租户 |
| `organization_id` | BIGINT | 出资机构 |
| `order_sn` | VARCHAR(50) | 系统提现单号，全局唯一 |
| `user_id` | BIGINT | 收款用户 |
| `source_type` | TINYINT | 1-用户手动申请、2-投递后自动触发 |
| `source_delivery_order_id` | BIGINT | 自动提现来源投递单；手动申请为空 |
| `amount` | DECIMAL(18,2) | 四舍五入后的实际结算、冻结及微信转账金额 |
| `audit_required` | TINYINT | 本单创建时根据配置得到的审核要求快照 |
| `audit_status` | TINYINT | 0-待审核、1-通过、2-拒绝、3-无需审核 |
| `transfer_status` | TINYINT | 转账状态，见下方状态定义 |
| `refund_status` | TINYINT | 用户资金退回状态：0-无需退款、1-待退、2-已退 |
| `organization_fund_status` | TINYINT | 机构资金状态：0-未扣、1-已扣、2-已冲正 |
| `audit_by` | BIGINT | 审核人 |
| `audit_time` | DATETIME | 审核时间 |
| `audit_remark` | VARCHAR(255) | 审核备注 |
| `channel_api_type` | VARCHAR(32) | 使用的微信转账产品/API 类型快照 |
| `merchant_transfer_no` | VARCHAR(64) | 商户侧转账单号；映射到具体 API 的 `out_bill_no` / `out_detail_no` 等字段 |
| `wechat_transfer_no` | VARCHAR(64) | 微信侧转账单号；具体响应字段随 API 产品确定 |
| `channel_status` | VARCHAR(32) | 最近一次查单得到的微信原始状态 |
| `failure_code` | VARCHAR(64) | 微信或内部失败码 |
| `failure_reason` | VARCHAR(500) | 失败原因 |
| `retry_count` | INT | 已执行的自动重试次数；不含首次请求 |
| `next_retry_time` | DATETIME | 下次允许自动重试时间 |
| `last_query_time` | DATETIME | 最近一次微信查单时间 |
| `last_transfer_time` | DATETIME | 最近一次请求微信时间 |
| `transfer_success_time` | DATETIME | 查单确认转账成功时间 |
| `manual_handle_by` | BIGINT | 风控、反洗钱或超限重试后的人工处置人 |
| `manual_handle_time` | DATETIME | 人工处置时间 |
| `manual_handle_remark` | VARCHAR(500) | 人工处置决定、查单证据和备注 |
| `organization_debit_record_id` | BIGINT | 机构余额扣减流水 ID |
| `organization_reversal_record_id` | BIGINT | 机构余额冲正流水 ID；未冲正为空 |
| `user_refund_record_id` | BIGINT | 用户失败退款流水 ID；未退款为空 |
| `config_version` | BIGINT | 创建本单时使用的机构提现配置版本 |
| `create_time` | DATETIME | 创建时间 |
| `update_time` | DATETIME | 更新时间 |

建议为自动提现建立 `UNIQUE (source_delivery_order_id)`，保证一张投递单最多触发一张自动提现单；
`merchant_transfer_no`、非空的 `wechat_transfer_no` 也分别建立唯一约束。

建议的 `transfer_status`：

| 值 | 含义 | 是否可退回用户余额 |
|---|---|---|
| 0 | 未开始 | 审核拒绝或取消时可退 |
| 1 | 待发起转账 | 机构余额不足或发起前取消时可退 |
| 2 | 已受理/处理中 | 否 |
| 3 | 成功 | 否 |
| 4 | 等待自动重试 | 暂不退，等待重试策略 |
| 5 | 确定失败 | 是，只允许退回一次 |
| 6 | 结果未知/待查询 | 否，必须先向微信查单 |
| 7 | 渠道异常人工处置中 | 否，人工确认终态后再决定成功或退款 |

### 24.2 推荐流程

```text
投递订单审核通过（或配置为免审）
  → 计算返现金额并按 HALF_UP 结算到 2 位
  → 原子增加用户 balance，同时写用户余额流水
  → 机构未启用自动提现：余额留在钱包，用户以后可手动提现
  → 机构已启用自动提现：为本次投递创建唯一提现单
      → 将本次两位结算金额从 balance 冻结到 pending_balance
      → amount ≤ 自动免审阈值：audit_status=无需审核
      → amount > 自动免审阈值：audit_status=待审核
  → 审核通过或无需审核
  → 调微信前原子扣减机构 balance，并写机构余额流水
      → 机构余额不足：不调微信，机构不扣款，用户冻结金额退回
      → 扣减成功：提交本地事务后，以唯一 merchant_transfer_no 调微信
          → 查单成功：提现成功，扣减用户 pending_balance
          → 查单处理中/结果未知：用户继续冻结、机构资金保持已扣，继续查单
          → 查单终态失败且不再重试：机构余额冲正、用户冻结金额退回
          → 风控/反洗钱拦截：进入人工处置，不能自动退款或换单重付
```

无论是否启用自动提现，钱包里未被本次自动提现冻结的剩余余额都允许用户手动申请提现；手动提现与自动提现共用
同一套审核、机构扣款、微信查单、失败冲正和资金流水逻辑。

### 24.3 还必须处理的边界

1. **接口受理不等于到账成功**：同步响应成功也只能按渠道状态进入处理中，最终必须以微信查单结果为准。
2. **接口超时不等于失败**：微信可能已经成功，只是响应丢失。立即退款或换新单号重付会造成双付。
3. **本地扣款后进程崩溃**：任务恢复后先用原 `merchant_transfer_no` 查单；查不到且 API 允许时，才用同一单号、同一参数重发。
4. **退款与机构冲正必须分别幂等**：提现状态条件更新、两类余额流水唯一键共同保证用户只退一次、机构只冲正一次。
5. **配置需要快照**：提现单创建后，即使机构修改审核模式或阈值，旧单仍按创建时决策执行。
6. **自动提现需要来源唯一**：投递审核接口重试不能生成第二张自动提现单。
7. **禁用不等于取消**：用户或机构被禁用后，已经被微信受理的订单仍要继续查到终态，不能直接退款。
8. **渠道前置校验**：调用前需校验单笔限额、收款用户状态、实名要求和机构微信商户配置。
9. **人工处置不能绕过查单**：风控、反洗钱或人工补单前必须再次查单，并记录操作人、原因和渠道证据。

### 24.4 查单、重试与失败分类

统一入口规则是“失败或不确定时先查单”。如果查单返回成功，直接标记成功；返回处理中则继续保持用户冻结和机构扣减，
不得重新发起另一笔转账。只有查单确认未成功且符合重试条件时，才进入重试。

| 分类 | 代表错误 | 处理规则 |
|---|---|---|
| 可重试、状态不确定 | `SYSTEM_ERROR`、频率限制类、`BANK_ERROR`、网络超时 | 保持双方资金状态；至少等待 1 分钟后先查单；允许时使用原商户单号和原参数重试 |
| 权限/配置错误 | `NO_AUTH` | 停止自动重试并告警；修复商户权限或配置后，再根据查单结果决定是否以原单号继续，不做无条件 1–2 次盲重试 |
| 原渠道单已关闭 | `ORDER_CLOSED` | 先确认原单绝不会支付；若仍要重付，应建立新的渠道尝试并使用新商户单号，不能把已关闭单当成普通瞬时错误 |
| 当前自动流程终止 | `PARAM_ERROR`、`SIGN_ERROR`、`ACCOUNT_ABNORMAL` | 查单确认未成功后停止本轮，冲正机构资金并退回用户；修复参数/签名后由新业务决策重新发起 |
| 渠道余额不足 | `NOT_ENOUGH` | 按本项目产品规则停止并退款；它是产品选择的终止策略，不应笼统称为微信永久错误 |
| 业务风控拦截 | `RISK_LIMIT`、`AML_BLOCKED` | 不自动重试、不立即退款，转人工审核；人工决定继续或退款前仍需查单 |

自动重试最多 5 次，不含首次请求，因此最多可能调用转账接口 6 次；间隔依次为 1、2、4、8、16 分钟。
每次重试前都要先查单，查单轮询次数不计入 `retry_count`。超过 5 次仍无终态时转人工处置，不能因达到次数上限就自动退款。

`SYSTEMERROR` 在数据库中统一规范成官方常见写法 `SYSTEM_ERROR`。不同微信转账产品的字段名、状态和错误码并不完全相同，
所以上表中的 `BANK_ERROR`、`ORDER_CLOSED`、`RISK_LIMIT`、`AML_BLOCKED` 等仍需在选定具体 API 后逐项对照，
不能把跨产品搜集的错误码直接写死为同一套枚举。

### 24.5 微信官方文档核验（2026-07-19）

本轮对照了微信支付官方文档，得到三条会影响状态机的结论：

1. API 返回受理不代表最终转账成功，业务方必须查单确认最终状态。
2. `SYSTEM_ERROR` 等不确定场景应保持原商户单号和原参数重试，随意换单号存在重复转账风险。
3. 旧版批量转账文档对 `NO_AUTH` 的解释是商户权限/信息问题，并不适合定时盲重试；对 `NOT_ENOUGH` 的建议是充值后使用原单号重试。
   本项目仍可按已确认的产品规则在余额不足时直接退款，但应明确这是业务选择，而不是渠道声称永不可重试。

参考：

- [商家转账到零钱开发指引](https://pay.wechatpay.cn/doc/v3/merchant/4012065168)
- [商家转账“发起转账”API](https://pay.wechatpay.cn/doc/v3/merchant/4012716434)
- [旧版商家转账接口及错误码说明](https://pay.wechatpay.cn/doc/v3/merchant/4012458841)

## 25. 投递与清运重量、换袋语义

### 25.1 投递重量

设备 `deliveryComplete` 上传稳定称重结果：

```text
doorIndex + weightBefore + weightAfter + netWeight + bagQr + 4 photo URLs
```

订单字段含义：

- `weight_before`：本次开门前 MCU 给出的稳定原始毛重；
- `weight_after`：本次关门后 MCU 给出的稳定原始毛重；
- `weight`：设备上报的本次净增重量，正常时等于 `weight_after - weight_before`；
- `has_anomaly`：任一重量缺失、`weight < 0`，或三个值在数据库三位小数精度下不满足上述等式时为 true；
- `bag_qr`：本次投递进入的当前袋编号快照。

MCU 负责给出稳定值，香橙派与后端不再做重量稳定性滤波，也不使用负重量容差。后端仍做完整性和定点算术一致性校验，
但不因微小负数设置额外放行区间：只要最终 `weight < 0` 就标记异常。异常投递仍建单、保存原始重量和照片，
但不返现，等待人工处理。

### 25.2 清运前后重量

设备 `cleanComplete` 需要同时上传清运前后两个稳定重量，并保留以下明确语义：

- `weight_before`：清运开门前、旧满袋仍在投口中的稳定原始毛重；
- `weight_after`：换入新空袋并关门后的稳定原始毛重；
- `gross_weight`：旧袋毛重审计快照，当前定义与 `weight_before` 相同；
- `tare_weight`：被清走旧袋原有皮重的审计快照；
- `weight` / `net_weight`：本次清走内容物的最终净重，两者当前定义相同。

上述重量均由设备上传稳定结果。清运完成后，`biz_door_status` 使用设备同批上报的新袋当前毛重、皮重和净重更新；
正常的新空袋状态通常满足“当前毛重约等于新皮重、净重为 0”，但数据库不通过容差替设备修正数据。

### 25.3 清运袋号

一个清运动作会接触旧袋和新袋，但清运订单按用户确认只记录一个袋号：

- 清运开始前的 `biz_door_status.bag_qr` 是本次拿走的旧袋，写入 `biz_clean_order.bag_qr`；
- 小程序扫描的新袋号随开门命令下发，并由设备在 `cleanComplete` 原样回传；
- 新袋号只替换 `biz_door_status.bag_qr`，不写入 `biz_clean_order`。

因此不再建议清运订单保存 `old_bag_qr + new_bag_qr`，当前已有的 `new_bag_qr` 应在正式迁移方案中退出。
为避免协议字段与订单字段同名却含义相反，完成事件建议把新袋字段明确命名为 `installedBagQr` 或 `newBagQr`，
不要笼统命名为 `bagQr`；清运订单的 `bag_qr` 永远表示被拿走的旧袋。

清运 v3 不预建业务订单，但仍建议生成 `clean_operation_id` 并持久化一条短生命周期清运操作上下文，
在开门前保存旧袋号、旧皮重、清运员、设备、投口和扫描的新袋号，再下发给设备并要求 `cleanComplete` 原样回传。
完成事务消费这份上下文后才更新投口状态。否则新袋状态一旦先覆盖旧袋，后端收到延迟/重复事件时将无法可靠恢复
“本次拿走的是哪一个袋”。正式表名、字段和保留周期仍需确认。

## 26. 第三轮已确认决策（2026-07-19）

本轮确认并写入上述设计：

1. 设备可上传工作状态、程序/MCU 版本和主机资源指标。实机只读检查确认 Orange Pi Zero 3 可读取板载温度、CPU、内存和磁盘；当前设备程序尚未上报这些指标。
2. `door_state`、`lock_state` 虽然当前没有传感器，仍作为未来能力保留可空字段；没有证据支持投口温度传感器，因此删除投口 `temperature`。
3. `biz_door_status.current_weight` 是传感器稳定原始毛重；皮重、净重也由设备上报。清运单增加 `weight_before`、`weight_after`。
4. 一个回收袋同一时刻只能绑定一个投口，对非空 `biz_door_status.bag_qr` 建唯一约束。
5. `biz_clean_order.bag_qr` 只表示当前清运拿走的旧袋，不记录新换入袋号。
6. `biz_device_config` 必须包含设备名称、经纬度、地址和重量满溢阈值。
7. 单价优先级确认为“实际投口配置 > 机构第 N 号投口价 > 机构全局价”，全部未配置时禁止投递。
8. 机构余额到实际发起微信转账前才扣减；本地余额不足时不调微信并退回用户余额。
9. 自动提现启用后，用户仍可手动提现钱包中未被冻结的余额。
10. 微信失败先查单；成功则直接成功，处理中继续查单；可重试场景最多重试 5 次，间隔为 1、2、4、8、16 分钟。
11. 确认新增 `biz_user_balance_record`。
12. 重量不设置负向容差；自动提现阈值使用四舍五入后的两位结算金额比较。

## 27. 第三轮后仍需确认

以下问题不会阻止继续讨论，但会影响最后的字段与约束：

1. 最终使用微信“商家转账”新接口，还是“商家转账到零钱/批量转账”接口？是否接收支付回调，还是只由定时任务查单？这会决定商户单号、微信单号、状态和错误码的正式字段名。
2. 若 `ORDER_CLOSED` 查明确认未支付后仍要创建新商户单号重试，是否同意增加 `biz_withdraw_transfer_attempt`，让一张提现单保存多次渠道尝试？若不增加，则关闭单应终态退款。
3. `biz_device_config` / `biz_door_config` 中哪些字段必须经 OneNet 下发设备，哪些只供后端计算和展示？
4. 设备级 `full_weight_threshold` 应比较原始毛重 `current_weight` 还是内容物净重 `net_weight`？六个投口共用一个阈值，还是允许投口配置覆盖？当前稿建议比较净重、六口共用。
5. 是否接受新增短生命周期的 `clean_operation` 上下文表，用于在不预建清运订单的前提下可靠保存旧袋号和清运人？如果不新增，需要指定另一种可跨重启恢复的载体。
6. 清运单是否长期同时保留 `weight_before + gross_weight`、`weight + net_weight` 两组同义列？当前稿按你的要求都记录，但推荐迁移完成后各保留一个权威字段，避免后续写出不一致值。
7. 两位结算金额恰好等于自动免审阈值时，是无需审核还是需要审核？当前稿暂按 `amount <= threshold` 无需审核。
8. 机构余额通过什么入口增加：后台人工充值、审核后的线下充值、微信商户余额同步，还是多种方式并存？这会决定机构余额流水的充值来源枚举和审核字段。



answer:

- 两者结合使用吧，接受支付回调并且用定时查单来防止丢单，15分钟前不查，在15分钟后到30分钟内未支付时每3分钟查一次，并且若订单超过30分钟仍未支付则标记为过期
- 不增加，关闭单并终态退款

- 





- 对了，还需要记录满溢的时间以及种类，超过24小时及48小时未清运需要在网页端进行提醒展示
