# EcoBin 数据库设计文档

> 版本：V8（钱包+提现）  
> 数据库：MySQL 8.0+  
> 字符集：utf8mb4  
> 迁移工具：Flyway（脚本位于 `ecobin-bootstrap/src/main/resources/db/migration/`）  
> 末次同步：2026-06-07（已对齐 V1–V8 全部迁移）；关联 [[permission-design.md](permission-design.md)]

---

## 设计原则

- **多租户预留**：所有业务表包含 `tenant_id BIGINT NOT NULL DEFAULT 1`。`tenant_id = 1` 为**保留值**，
  表示"平台池/未分配"，不对应真实租户；真实租户 `tenant_id = sys_tenant.id 且恒 > 1`（`sys_tenant`
  `AUTO_INCREMENT = 2`）。权限与角色细节见 [[permission-design.md](permission-design.md)]
- **命名规范**：系统表前缀 `sys_`，业务表前缀 `biz_`；数据库字段名下划线，Java 实体驼峰映射
- **索引命名**：全局唯一命名（`idx_{表名}_{字段}`），避免 H2 测试环境冲突
- **审计字段**：每表包含 `create_time`（创建时间），核心表包含 `update_time`（更新时间）
- **软状态**：使用 TINYINT 表示状态，避免硬删除

---

## 表概览

| 表名 | 前缀 | 说明 | 迁移版本 |
|------|------|------|----------|
| `sys_admin` | sys | 平台管理员（超管/管理员，网页登录，无 tenant_id） | V3 |
| `sys_tenant` | sys | 系统租户/机构（含租户登录 + 小程序配置） | V1, V3 |
| `sys_user` | sys | 终端用户（小程序微信登录，role=1/2/3；V8 加钱包余额） | V1, V2, V3, V6, V8 |
| `biz_device` | biz | 回收设备 | V1 |
| `biz_door` | biz | 设备投口（V8 加单价 price） | V1, V4, V5, V8 |
| `biz_delivery_order` | biz | 投递订单（V7 加投递两阶段字段） | V1, V7 |
| `biz_clean_order` | biz | 清运订单（V9 去皮链；V11 照片；V12 开门即建单 new_bag_qr） | V1, V9, V11, V12 |
| `biz_clean_bag` | biz | 垃圾袋追踪（每投口当前袋去皮，V9） | V9 |
| `biz_device_status` | biz | 设备实时状态（V10 收敛为设备级：去 total_weight/spill/smoke，加 rssi/fw_version） | V1, V10 |
| `biz_door_status` | biz | 投口实时状态（V10 新增：投口级重量/满溢/烟雾快照） | V10 |
| `biz_device_session` | biz | 设备活跃会话（V13 新增：当前活跃用户，支撑投递上传后建单） | V13 |
| `biz_weight_record` | biz | 重量变更记录 | V1 |
| `biz_withdraw_order` | biz | 提现申请单 | V8 |

---

## 表详细设计

### 1. sys_tenant — 系统租户

租户既是数据隔离空间（`id` 即各业务表的 `tenant_id`），也是网页登录主体（role=7）与小程序归属。
`AUTO_INCREMENT = 2`，id=1 保留给"平台池"，不分配给真实租户。本表**无 `tenant_id` 列**（自身即租户）。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO（起点2） | 主键 = tenant_id |
| `name` | VARCHAR(100) | | | 租户名称 |
| `code` | VARCHAR(50) | | | 租户编码（UNIQUE） |
| `username` | VARCHAR(50) | ✓ | NULL | 租户登录用户名（V3） |
| `password` | VARCHAR(255) | ✓ | NULL | BCrypt 加密密码（V3） |
| `miniapp_appid` | VARCHAR(32) | ✓ | NULL | 小程序 AppID（V3，未配置存 NULL） |
| `miniapp_secret` | VARCHAR(256) | ✓ | NULL | 小程序 Secret，AES 加密（V3） |
| `merchant_no` | VARCHAR(64) | ✓ | NULL | 微信商户号（V3） |
| `contact_name` | VARCHAR(50) | ✓ | NULL | 联系人 |
| `contact_phone` | VARCHAR(20) | ✓ | NULL | 联系电话 |
| `address` | VARCHAR(255) | ✓ | NULL | 地址 |
| `status` | TINYINT | | 1 | 0-禁用 1-启用 |
| `create_time` | DATETIME | | NOW() | 创建时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`uk_code`（UNIQUE）、`uk_tenant_miniapp_appid`（UNIQUE，wx-login 按 appid 反查租户；NULL 允许多行）

### 2. sys_admin — 平台管理员（V3 新增）

平台级登录主体，存储超管(9)/管理员(8)，网页用户名+密码登录。**无 `tenant_id`、`openid`、`phone`、`email`**。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `username` | VARCHAR(50) | | | 登录用户名（UNIQUE） |
| `password` | VARCHAR(255) | | | BCrypt 加密密码 |
| `real_name` | VARCHAR(50) | ✓ | NULL | 真实姓名 |
| `role` | TINYINT | | | 9-超级管理员 8-管理员 |
| `status` | TINYINT | | 1 | 0-禁用 1-启用 |
| `create_time` | DATETIME | | NOW() | 创建时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`uk_admin_username`（UNIQUE）

**默认数据**：

| id | username | password | real_name | role |
|----|----------|----------|-----------|------|
| 1 | admin | `$2a$10$...` (BCrypt: admin123) | 系统管理员 | 9 |

> 仅超管(9) 可创建/禁用其他 `sys_admin` 记录。

### 3. sys_user — 终端用户

终端用户表，**仅供小程序微信用户**（openid 登录，role=1/2/3）。平台管理员迁至 `sys_admin`，租户迁至 `sys_tenant`，
故本表不再存 username/password 登录主体（V2 起 username/password 可空，V3 起新注册用户均为微信用户）。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID（所属租户，恒 >1） |
| `username` | VARCHAR(50) | ✓ | NULL | 用户名（UNIQUE，微信用户为空；历史遗留字段） |
| `password` | VARCHAR(255) | ✓ | NULL | BCrypt 加密密码（微信用户为空；历史遗留字段） |
| `real_name` | VARCHAR(50) | ✓ | NULL | 真实姓名 |
| `phone` | VARCHAR(20) | ✓ | NULL | 手机号 |
| `email` | VARCHAR(100) | ✓ | NULL | 邮箱 |
| `openid` | VARCHAR(64) | ✓ | NULL | 微信小程序 openid（UNIQUE） |
| `unionid` | VARCHAR(64) | ✓ | NULL | 微信开放平台 unionid |
| `nickname` | VARCHAR(100) | ✓ | NULL | 微信昵称 |
| `avatar` | VARCHAR(500) | ✓ | NULL | 微信头像 URL |
| `role` | TINYINT | | 1 | 3-设备管理员 2-清运员 1-普通用户（默认） |
| `status` | TINYINT | | 1 | 0-禁用 1-启用 |
| `balance` | DECIMAL(12,2) | | 0.00 | 可用余额（V8，投递返现入账） |
| `pending_balance` | DECIMAL(12,2) | | 0.00 | 待审核余额（V8，提现申请中冻结） |
| `create_time` | DATETIME | | NOW() | 创建时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`uk_username`（UNIQUE）、`uk_tenant_openid (tenant_id, openid)`（UNIQUE，V6）、`idx_sys_user_tenant_id`

> 同一微信 openid 在不同租户下为独立记录，唯一性为复合 `(tenant_id, openid)`（V6 由全局 `uk_openid` 改建为
> `uk_tenant_openid`）。openid 可空（非微信用户为 NULL），NULL 不参与唯一性判定。详见 [[permission-design.md](permission-design.md)] §6.4。
>
> **钱包余额（V8）**：`balance`（可用）+ `pending_balance`（待审核）直接挂在本表，未建独立钱包表。
> 入账走 `UserMapper` 原子条件 SQL，资金链路见 `docs/archive/withdraw-design-notes.md`。

**注册逻辑**：仅通过小程序微信 `code2session` 自动注册，**不允许手动创建**，默认 `role=1`。租户(7) 可在自己
`tenant_id` 下提升/降低用户角色（1↔2↔3）。

### 4. biz_device — 回收设备

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `sn` | VARCHAR(50) | | | 设备序列号（UNIQUE） |
| `name` | VARCHAR(100) | | | 设备名称 |
| `type` | TINYINT | | 1 | 1-智能垃圾箱 2-滚动系统 |
| `lat` | DECIMAL(10,7) | ✓ | NULL | 纬度 |
| `lng` | DECIMAL(10,7) | ✓ | NULL | 经度 |
| `address` | VARCHAR(255) | ✓ | NULL | 安装地址 |
| `status` | TINYINT | | 1 | 0-离线 1-在线 2-维护中 |
| `create_time` | DATETIME | | NOW() | 创建时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`uk_sn`（UNIQUE）、`idx_device_tenant_id`

### 5. biz_door — 设备投口

每个设备有 1-6 个投口，每个投口对应一种垃圾类型。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `device_id` | BIGINT | | | 所属设备ID（FK → biz_device） |
| `door_index` | TINYINT | | | 投口号（1-6） |
| `name` | VARCHAR(50) | ✓ | NULL | 投口名称（如"纸类回收"） |
| `waste_type1` | TINYINT | | | 一级分类：1-厨余 2-可回收 3-有害 4-其他 |
| `waste_type2` | TINYINT | | 0 | 二级分类：0-不区分 1-纸类 2-塑料 3-织物 4-金属 5-其他 |
| `price` | DECIMAL(10,2) | ✓ | NULL | 单价（元/kg，V8），投递完成按 `单价 × 重量` 返现 |
| `enabled` | TINYINT | | 1 | 0-禁用 1-启用 |
| `sort_order` | INT | | 0 | 排序 |
| `create_time` | DATETIME | | NOW() | 创建时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`idx_door_device_id`、`idx_door_tenant_id`、`uk_door_device_index (device_id, door_index)`（UNIQUE，V4）
外键：`fk_door_device`（`device_id` → `biz_device.id` ON DELETE CASCADE，V5）

### 6. biz_delivery_order — 投递订单

用户投放垃圾时产生的订单记录。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `order_sn` | VARCHAR(50) | | | 订单编号（UNIQUE） |
| `delivery_token` | VARCHAR(64) | ✓ | NULL | 投递标识符（V7，开投口生成下发，关投口上报回填关联同一记录） |
| `device_id` | BIGINT | ✓ | NULL | 设备ID（FK → biz_device） |
| `door_id` | BIGINT | ✓ | NULL | 投口ID（FK → biz_door） |
| `user_id` | BIGINT | ✓ | NULL | 投递用户ID（FK → sys_user） |
| `waste_type1` | TINYINT | | | 一级分类 |
| `waste_type2` | TINYINT | | 0 | 二级分类 |
| `weight` | DECIMAL(10,3) | ✓ | NULL | 重量（kg） |
| `price` | DECIMAL(10,2) | ✓ | NULL | 单价（投递完成时由投口 `biz_door.price` 回填） |
| `score` | INT | | 0 | 获得积分 |
| `login_type` | TINYINT | ✓ | NULL | 登录方式：1-手机 2-IC卡 3-人脸 4-二维码 5-微信小程序 |
| `status` | TINYINT | | 0 | 0-正常 -1-异常 |
| `delivery_status` | TINYINT | | 1 | 投递阶段（V7）：0-进行中（已开投口待回填） 1-已完成 |
| `photo_open_outside` | VARCHAR(512) | ✓ | NULL | 开门前箱外照片 URL（V11） |
| `photo_open_inside` | VARCHAR(512) | ✓ | NULL | 开门前箱内照片 URL（V11） |
| `photo_close_outside` | VARCHAR(512) | ✓ | NULL | 关门后箱外照片 URL（V11） |
| `photo_close_inside` | VARCHAR(512) | ✓ | NULL | 关门后箱内照片 URL（V11） |
| `create_time` | DATETIME | | NOW() | 投递时间 |

索引：`uk_delivery_order_sn`（UNIQUE）、`uk_delivery_token`（UNIQUE，V7）、`idx_delivery_device_id`、`idx_delivery_user_id`、`idx_delivery_tenant_id`、`idx_delivery_create_time`

> **注意**：此表无 `update_time`，投递订单一旦创建不修改，异常仅标记。
> **两阶段流程（V7）**：①C 端开投口建「进行中」记录（生成 `delivery_token`）；②设备 IoT 按 SN + token 上报回填重量并置「已完成」。后台/历史直接创建的订单 `delivery_status` 默认 1（已完成）。

### 7. biz_clean_order — 清运订单

设备自动称重驱动的清运记录（V9 重做）。**开门即建单**（V12 起）：清运员小程序 `open` 时后端用登录态 `user_id` + 扫到的新空袋（记 `new_bag_qr`）建单，`cleanOrderId` 随开门命令下发给设备；后续设备只回传 `cleanOrderId` + 物理量。每次清运设备上报**毛重**，后端按「毛重 − 该投口当前(旧袋)去皮」得到实际清运量。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `order_sn` | VARCHAR(50) | | | 订单编号（UNIQUE，open 建单时生成） |
| `device_id` | BIGINT | ✓ | NULL | 设备ID（FK → biz_device） |
| `door_id` | BIGINT | ✓ | NULL | 投口ID（FK → biz_door） |
| `bag_qr` | VARCHAR(64) | ✓ | NULL | 本次清走的垃圾袋编号（旧袋，毛重上报时回填，V9） |
| `new_bag_qr` | VARCHAR(64) | ✓ | NULL | 本次换上的新空袋编号（open 时小程序扫到，待去皮，V12） |
| `user_id` | BIGINT | ✓ | NULL | 清运员ID（open 时登录态写入，FK → sys_user, role=2） |
| `waste_type1` | TINYINT | | | 一级分类 |
| `waste_type2` | TINYINT | | 0 | 二级分类 |
| `weight` | DECIMAL(10,3) | ✓ | NULL | 实际清运量（=net_weight，兼容旧字段） |
| `gross_weight` | DECIMAL(10,3) | ✓ | NULL | 清运毛重（设备上报满袋重量，V9） |
| `tare_weight` | DECIMAL(10,3) | ✓ | NULL | 去皮重量（清运时该投口当前去皮，V9） |
| `net_weight` | DECIMAL(10,3) | ✓ | NULL | 实际清运量 = 毛重 − 去皮（V9） |
| `audit_status` | TINYINT | | 0 | 审核状态：0-待审核 1-通过 2-拒绝；设备称重后仍需人工审核，新记录默认 0 |
| `status` | TINYINT | | 0 | 0-创建 1-完成 2-取消 |
| `photo_open_outside` | VARCHAR(512) | ✓ | NULL | 开门前箱外照片 URL（V11） |
| `photo_open_inside` | VARCHAR(512) | ✓ | NULL | 开门前箱内照片 URL（V11） |
| `photo_close_outside` | VARCHAR(512) | ✓ | NULL | 关门后箱外照片 URL（V11） |
| `photo_close_inside` | VARCHAR(512) | ✓ | NULL | 关门后箱内照片 URL（V11） |
| `create_time` | DATETIME | | NOW() | 创建时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`uk_clean_order_sn`（UNIQUE）、`idx_clean_device_id`、`idx_clean_user_id`、`idx_clean_tenant_id`、`idx_clean_create_time`

### 7b. biz_clean_bag — 垃圾袋追踪（V9）

维护**每个设备投口当前那只垃圾袋**的去皮重量与编号。清运员换上新空袋后设备上报去皮，按 `(device_id, door_index)` upsert；下一次清运的毛重减去此去皮即为实际清运量（首次清运无记录，去皮按 0）。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `device_id` | BIGINT | | | 设备ID |
| `door_index` | INT | | | 投口号（物理编号，第几个投口） |
| `bag_qr` | VARCHAR(64) | ✓ | NULL | 当前垃圾袋编号 |
| `tare_weight` | DECIMAL(10,3) | ✓ | NULL | 当前垃圾袋去皮重量（kg） |
| `user_id` | BIGINT | ✓ | NULL | 最近换袋清运人ID |
| `create_time` | DATETIME | | NOW() | 创建时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`uk_clean_bag_device_door`（UNIQUE：device_id + door_index）、`idx_clean_bag_tenant_id`

**去皮链式追踪**（图④⑤）：
```
开清运门(扫新空袋) → 设备报毛重 → 建清运单 net = 毛重 − 该投口当前去皮
                  → 清运员换新空袋 → 设备报去皮 → upsert biz_clean_bag(该投口)
```

### 8. biz_device_status — 设备实时状态（设备级·V10 重整）

与设备一对一的最新快照，由设备端定时上报更新。**职责收敛为「设备级配置/物理/健康」**：重量/满溢/烟雾等投口级数据迁往 `biz_door_status`（§8b）。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `device_id` | BIGINT | | | 设备ID（UNIQUE，FK → biz_device） |
| `online` | TINYINT | | 0 | 0-离线 1-在线 |
| `voltage` | DECIMAL(5,2) | ✓ | NULL | 电压（V） |
| `rssi` | INT | ✓ | NULL | 信号强度（dBm，V10 新增） |
| `fw_version` | VARCHAR(32) | ✓ | NULL | 固件版本（V10 新增） |
| `last_report_time` | DATETIME | ✓ | NULL | 最后上报时间 |

索引：`uk_device_status_device_id`（UNIQUE）、`idx_device_status_tenant_id`

> **V10 变更**：去 `total_weight`（由各投口重量聚合得出，不落库）、`spill_alarm`、`smoke_alarm`（迁 `biz_door_status`）；新增 `rssi`、`fw_version`。

### 8b. biz_door_status — 投口实时状态（V10 新增）

每个设备投口一条最新快照，承载**重量/投递相关的实时状态与告警**，由设备端定时上报 upsert。配置（启用/分类/单价）仍归 `biz_door`，当前垃圾袋号/去皮仍归 `biz_clean_bag`，本表不重复存。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `device_id` | BIGINT | | | 设备ID（FK → biz_device） |
| `door_index` | INT | | | 投口号（物理编号，第几个投口） |
| `weight` | DECIMAL(10,3) | ✓ | NULL | 当前即时重量（kg） |
| `fullness` | INT | ✓ | NULL | 满溢度（0-100） |
| `spill_alarm` | TINYINT | | 0 | 满溢报警：0-否 1-是 |
| `smoke_alarm` | TINYINT | | 0 | 烟雾报警：0-否 1-是 |
| `last_report_time` | DATETIME | ✓ | NULL | 最后上报时间 |

索引：`uk_door_status_device_door (device_id, door_index)`（UNIQUE）、`idx_door_status_tenant_id`

### 8c. biz_device_session — 设备活跃会话（V13 新增）

一台设备一行「当前活跃用户」，支撑**投递「上传后建单」时的用户归属**（设备无用户信息）。用户小程序「开启设备」时按 `device_id` upsert（覆盖为最近用户）；设备投递上报时按 `device_id` 查未过期会话确定用户，过期/无则建无主单。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `device_id` | BIGINT | | | 设备ID（唯一，一台设备一行） |
| `user_id` | BIGINT | ✓ | NULL | 当前活跃用户ID |
| `login_type` | TINYINT | ✓ | NULL | 登录方式：1-手机 2-IC卡 3-人脸 4-二维码 |
| `expire_time` | DATETIME | ✓ | NULL | 会话过期时间（超过即无活跃用户，TTL 默认 15min） |
| `create_time` / `update_time` | DATETIME | | NOW() | 时间戳 |

索引：`uk_device_session_device_id (device_id)`（UNIQUE）、`idx_device_session_tenant_id`

> 详见 `onenet-thing-model.md` §8。覆盖语义：后来用户开启覆盖上一行（接受「最近用户」，交叠极罕见不额外处理）。

### 9. biz_weight_record — 重量变更记录

设备重量变化的时序日志，用于异常检测和历史追溯。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `device_id` | BIGINT | | | 设备ID（FK → biz_device） |
| `door_id` | BIGINT | ✓ | NULL | 投口ID（FK → biz_door） |
| `weight` | DECIMAL(10,3) | ✓ | NULL | 重量（kg） |
| `record_time` | DATETIME | | NOW() | 记录时间 |

索引：`idx_weight_device_id`、`idx_weight_tenant_id`、`idx_weight_record_time`

### 10. biz_withdraw_order — 提现申请单（V8 新增）

用户发起的现金返现提现申请，由租户人工审核。出账明细即本表（不另建余额流水表）。

| 字段 | 类型 | 可空 | 默认值 | 说明 |
|------|------|:---:|--------|------|
| `id` | BIGINT | | AUTO | 主键 |
| `tenant_id` | BIGINT | | 1 | 租户ID |
| `user_id` | BIGINT | | | 申请用户ID（FK → sys_user） |
| `amount` | DECIMAL(12,2) | | | 提现金额 |
| `status` | TINYINT | | 0 | 0-待审核 1-已通过 2-已驳回 |
| `audit_by` | BIGINT | ✓ | NULL | 审核租户主体ID |
| `audit_time` | DATETIME | ✓ | NULL | 审核时间 |
| `audit_remark` | VARCHAR(255) | ✓ | NULL | 审核备注 |
| `transfer_no` | VARCHAR(64) | ✓ | NULL | 微信转账单号（真实转账接入后回填，见 `docs/open-items.md` §3） |
| `create_time` | DATETIME | | NOW() | 申请时间 |
| `update_time` | DATETIME | | NOW() | 更新时间（ON UPDATE） |

索引：`idx_withdraw_user_id`、`idx_withdraw_tenant_id`、`idx_withdraw_status`

**资金流**：投递完成按 `biz_door.price × weight` 入账 `sys_user.balance`；申请提现时可用→待审核（条件 SQL 防透支）；
审核通过扣减待审核（资金转出），驳回则退回可用。设计差异记录见 `docs/archive/withdraw-design-notes.md`。

---

## 枚举值汇总

### 角色体系（跨三张登录主体表）

> 角色不是线性高低，而是分属平台域 / 租户域 / 终端域，详见 [[permission-design.md](permission-design.md)] §3。

| 值 | 角色 | 所在表 | 登录端 |
|----|------|--------|--------|
| 9 | 超级管理员 | `sys_admin` | 网页 |
| 8 | 管理员 | `sys_admin` | 网页 |
| 7 | 租户 | `sys_tenant` | 网页 |
| 6/5/4 | 预留 | — | — |
| 3 | 设备管理员 | `sys_user` | 小程序 |
| 2 | 清运员 | `sys_user` | 小程序 |
| 1 | 普通用户（默认） | `sys_user` | 小程序 |

> 旧 `sys_user.role`（1 超管 / 2 设备管理员 / 3 运营 / 4 清运员 / 5 普通用户）已废弃，V3 迁移：
> 超管→`sys_admin`(9)、设备管理员→3、清运员→2、普通用户→1，运营人员废弃（由租户角色替代）。

### 设备类型 (biz_device.type)

| 值 | 说明 |
|----|------|
| 1 | 智能垃圾箱 |
| 2 | 滚动系统 |

### 设备状态 (biz_device.status)

| 值 | 说明 |
|----|------|
| 0 | 离线 |
| 1 | 在线 |
| 2 | 维护中 |

### 垃圾一级分类 (waste_type1)

| 值 | 说明 |
|----|------|
| 1 | 厨余垃圾 |
| 2 | 可回收物 |
| 3 | 有害垃圾 |
| 4 | 其他垃圾 |

### 垃圾二级分类 (waste_type2)

| 值 | 说明 |
|----|------|
| 0 | 不区分 |
| 1 | 纸类 |
| 2 | 塑料 |
| 3 | 织物 |
| 4 | 金属 |
| 5 | 其他 |

### 登录方式 (login_type)

| 值 | 说明 |
|----|------|
| 0 | 未知 |
| 1 | 手机号 |
| 2 | IC卡 |
| 3 | 人脸识别 |
| 4 | 二维码 |
| 5 | 微信小程序 |

### 审核状态 (biz_clean_order.audit_status) — 已废弃（V9）

清运改为设备自动称重上报后，人工审核流程取消，该字段保留仅为兼容历史数据，新记录默认 `1`。

| 值 | 说明 |
|----|------|
| 0 | 待审核（历史） |
| 1 | 审核通过（新记录默认） |
| 2 | 审核拒绝（历史） |

### 订单状态 (biz_clean_order.status / biz_delivery_order.status)

| 表 | 0 | 1 | 2 | -1 |
|----|---|---|---|---|
| `biz_delivery_order` | 正常 | — | — | 异常 |
| `biz_clean_order` | 创建 | 完成 | 取消 | — |

### 投递阶段 (biz_delivery_order.delivery_status，V7)

| 值 | 说明 |
|----|------|
| 0 | 进行中（已开投口，待设备上报回填） |
| 1 | 已完成（后台/历史直接创建默认此值） |

### 提现状态 (biz_withdraw_order.status，V8)

| 值 | 说明 |
|----|------|
| 0 | 待审核 |
| 1 | 已通过 |
| 2 | 已驳回 |

---

## 实体关系图

```
sys_tenant ──< sys_user
                  │
                  ├──< biz_delivery_order >── biz_device ──< biz_door
                  │                                    │
                  ├──< biz_clean_order >───────────────┤
                  │                                    │
                  │                          biz_device_status (1:1)
                  │                          biz_door_status (device+door 唯一)
                  │                          biz_clean_bag (device+door 唯一)
                  │
                  └──< biz_weight_record >────────────┘
```

- 一个租户下有多个用户、多台设备
- 一台设备有 1-6 个投口，一条设备级实时状态，每投口一条投口级实时状态
- 一个用户可产生多条投递订单和多条清运订单
- 投递订单和清运订单关联设备和投口
- 每个设备投口维护一条 `biz_clean_bag` 当前袋去皮记录
- 重量变更记录关联设备和投口

---

## 迁移版本历史

| 版本 | 文件名 | 说明 |
|------|--------|------|
| V1 | `V1__init_schema.sql` | 初始建表：8 张基础表 + 默认数据 |
| V2 | `V2__add_wechat_login.sql` | sys_user 新增微信登录字段（openid/unionid/nickname/avatar），username/password 改为可空 |
| V3 | `V3__add_role_tables.sql` | 新增 sys_admin；sys_tenant 增加登录/小程序字段（含 uk_tenant_miniapp_appid、AUTO_INCREMENT=2）；sys_user.role 迁移为 1/2/3；admin 默认数据迁至 sys_admin(role=9) |
| V4 | `V4__add_door_unique.sql` | biz_door 加复合唯一 `uk_door_device_index (device_id, door_index)` |
| V5 | `V5__add_door_fk.sql` | biz_door 加外键 `fk_door_device`（→ biz_device，ON DELETE CASCADE） |
| V6 | `V6__fix_openid_tenant_unique.sql` | sys_user 唯一约束 `uk_openid` → 复合 `uk_tenant_openid (tenant_id, openid)`，支持同 openid 跨租户独立注册 |
| V7 | `V7__add_delivery_two_phase.sql` | biz_delivery_order 加 `delivery_token`（uk_delivery_token）+ `delivery_status`，支撑投递两阶段闭环 |
| V8 | `V8__add_wallet_withdraw.sql` | sys_user 加 balance/pending_balance；biz_door 加 price；新建 biz_withdraw_order |
| V9 | `V9__add_clean_bag_and_refactor.sql` | 新建 biz_clean_bag（每投口去皮）；biz_clean_order 加 bag_qr/gross_weight/tare_weight/net_weight，audit_status 业务废弃 |
| V10 | `V10__refactor_device_door_status.sql` | biz_device_status 去 total_weight/spill_alarm/smoke_alarm、加 rssi/fw_version；新建 biz_door_status（投口级重量/满溢/烟雾快照） |
| V11 | `V11__add_order_photos.sql` | biz_delivery_order / biz_clean_order 各加 4 个 photo URL 列（开门前/关门后 × 箱内/箱外） |
| V12 | `V12__clean_order_new_bag.sql` | biz_clean_order 加 `new_bag_qr`（开门即建单：open 时扫到的新空袋，待去皮） |
| V13 | `V13__add_device_session.sql` | 新建 biz_device_session（设备当前活跃用户，支撑投递上传后建单的用户归属） |
