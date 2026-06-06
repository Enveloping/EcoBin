# 三端功能横向审查待办

> 范围：终端用户(C端) / 租户 / 管理员 三端功能完整性与设计问题。
> 与 `review-notes.md`（按提交 ID 记录的逐次 review）区分：本文件是一次**横向专项审查**的结论与下一轮改动方向。
> 审查日期：2026-06-06。本轮仅记录、不改代码；下一轮按「第 5 节顺序」实施。

---

## 1. 🔴 阻断性问题（下一轮优先修）

### 1.1 openid 全局唯一约束 与 多租户设计冲突 ✅ 已修复（V6，2026-06-06）

- **证据**
  - `ecobin-bootstrap/src/main/resources/db/migration/V2__add_wechat_login.sql:14`
    建的是 `ADD UNIQUE INDEX uk_openid (openid)` —— **全局唯一**。
  - 设计意图相反：`docs/permission-design.md:294`「同一微信 openid 在不同租户下为独立 `sys_user` 记录」、
    `docs/permission-design.md:502`「同一 openid 在不同租户下独立注册」。
- **后果**
  - 同一微信用户扫第二个租户的小程序码时，`AuthServiceImpl` 自动注册执行 `INSERT sys_user`，
    因 `uk_openid` 全局唯一触发**键冲突，注册失败** → 第二个租户永远无法获取该用户。
- **下一轮修法**
  - 新增迁移 `V6`：删除 `uk_openid`，改建复合唯一 `uk_tenant_openid (tenant_id, openid)`。
  - wx-login 查询路径已是 `WHERE tenant_id = X AND openid = Y`（`permission-design.md:197`），符合复合唯一语义，无需改逻辑。
- **实施结果**
  - 新增 `V6__fix_openid_tenant_unique.sql`：`DROP INDEX uk_openid` + `ADD UNIQUE INDEX uk_tenant_openid (tenant_id, openid)`。
  - 同步改 H2 测试 schema `schema-h2.sql` 的 `uk_openid` → `uk_tenant_openid`。
  - 删除 `UserMapper.selectByOpenid`（全局查询，无调用方，与复合唯一语义冲突）；保留带租户维度的 `selectByTenantIdAndOpenid`。
  - 新增 `OpenidTenantUniqueTest`（2 用例）：验证跨租户同 openid 可独立注册、同租户同 openid 被拒；全量测试无回归。

### 1.2 投递业务闭环断裂：无任何投递数据入口 ✅ 已实现（两阶段流程，2026-06-06）

- **证据**
  - `ecobin-module-business/.../controller/AppDeliveryController.java:18` 注释「投递订单由设备/物联网侧上报生成」，
    但全代码库**无设备上报端点**。
  - 唯一能写 `biz_delivery_order` 的是 `/api/business/delivery` POST（`DeliveryOrderController.java:30-34`），权限限 `SUPER_ADMIN/TENANT`。
- **后果**
  - C 端「我的投递」(`/api/app/delivery/my`) 永远是空列表；
  - 订单的 `price / score / weight` 字段存在却**永不产生** → 后续提现 / 积分功能全部悬空。
- **定型决策（已确认）**：投递数据来源 = **设备 IoT 主动上报**（非用户小程序主动提交、非后台手工录入）。
- **下一轮接口草案**
  - 新增 `POST /api/iot/delivery` 接收设备上报投递事件（device sn、door、wasteType、weight、投递身份标识、price/score 由服务端计算）。
  - 鉴权**独立于用户 JWT**：设备 SN + 设备密钥签名（或网关白名单），不走 `/api/app/**` 的角色体系。
  - 由上报体确定 `tenant_id`（设备归属）与 `user_id`（IC卡/人脸/二维码/手机号映射到 `sys_user`）。
- **实施结果（最终采用两阶段流程，优于单向上报）**
  - 阶段1「开投口」（C 端登录态写接口）`POST /api/app/delivery/open`：据 `doorId` 创建「进行中」投递记录（含 `user_id` + 新生成 `delivery_token`），下发开投口指令（占位）。
  - 阶段2「完成上报」（设备 IoT，明文 SN 信任）`POST /api/iot/delivery/complete`：按 `sn` 反查设备 + `delivery_token` 关联记录，校验归属后回填重量、置为已完成；重复上报被拒。
  - 迁移 `V7`：`biz_delivery_order` 加 `delivery_token`（唯一）+ `delivery_status`（0-进行中 1-完成）；同步 H2 schema。
  - `business` 模块新增对 `device` 模块依赖；设备下行指令为占位 `DeviceCommandService`（待 IoT 网关对接）。
  - `/api/iot/**` 在 SecurityConfig 放行；新增 `DeliveryTwoPhaseTest`（2 用例），全量测试无回归。
  - 鉴权按本轮决策采用「暂明文 SN 信任」（非密钥签名），后续加固见第 4 节技术债延伸。

---

## 2. 🟠 设计不自洽（下一轮一并处理）

### 2.1 `biz_device_status` / `biz_weight_record` 两表无写入口

- 与 1.2 同源：缺设备侧上报，导致设备实时状态、重量时序数据无来源。
- 现状：`DeviceStatus` 有实体（`ecobin-module-device/.../entity/DeviceStatus.java`）但**无 Service/Controller**；
  `biz_weight_record` **连实体都没有**（V1 建表后无任何业务代码）。
- 下一轮：随 IoT 上报接口一并补「设备状态上报」「重量上报」入口，并提供后台/ C 端的设备状态查询。

### 2.2 投递流水「不可变」语义不彻底

- `biz_delivery_order` 设计为不可变流水（表无 `update_time`，Entity 用 `@TableField(exist=false)` 影子屏蔽 `BaseEntity.updateTime`）。
- 但仍开放删除：`DeliveryOrderController.java:36-40` 提供 `DELETE /api/business/delivery/{id}` → 与审计不可变诉求矛盾。
- 下一轮：去掉或收紧删除接口（仅超管、或改逻辑删除 + 留痕）。

### 2.3 租户无法自查自身信息

- `TenantController.list()` 无入参、返回全量租户；`/api/system/tenant/**` 仅放给 `SUPER_ADMIN/ADMIN`（`SecurityConfig`）。
- 但权限矩阵要求「租户(7) 仅能看自己」。结果：租户登录后台后**无端点查看自身资料**。
- 下一轮：新增租户自查端点（如 `GET /api/system/tenant/me`），或在 list 中按当前登录主体收敛可见范围。

---

## 3. 🟡 功能缺口（设计在册、未实现，按优先级排队）

| 项 | 现状 | 依赖 | 备注 |
|----|------|------|------|
| 提现 / 现金返现 | **0% 实现**，仅 `docs/withdraw-design-notes.md` 设计 | 依赖 1.2 闭环 | 缺钱包/流水/提现单 3 表、`sys_tenant` 微信支付凭证字段、投递完成入账逻辑、商家转账到零钱对接 |
| 统计报表 | 仅 `GET /api/statistics/dashboard`（当日 2 字段） | — | `docs/references` 参考平台定义的 11 个统计接口基本空白 |
| C 端清运员/设备管理员作业接口 | 仅「我的投递 + profile + 设备只读」 | — | 清运员/设备管理员有角色，但无对应 C 端作业端点（看待清运、提交清运走后台 `/api/business/clean`） |

---

## 4. ⚪ 已知技术债（详见 `docs/review-notes.md`，此处不重复登记）

生产前必须处理：

- `TokenInvalidationRegistry` 内存态，**重启即失忆**，被禁用账号旧 token 复活 → 迁 Redis。
- 分页 `pageSize` 无上限保护（约定 ≤200 未生效）。
- `AesCryptoUtil` 用 `AES/ECB/PKCS5Padding` + 硬编码默认密钥 → 升 GCM + 配置化密钥。
- 强制失效触发过宽（字段无实际变化也强制下线）。

---

## 5. 下一轮改动建议顺序

1. ~~`V6` 修 openid 复合唯一（1.1）~~ ✅ 已完成 2026-06-06
2. ~~IoT 投递两阶段闭环（1.2）~~ ✅ 已完成 2026-06-06（开投口建记录 + 设备上报回填）；设备状态/重量上报（2.1）仍待办
3. 租户自查 + 投递流水删除收敛（2.2 / 2.3）
4. 钱包入账 + 提现（依赖第 2 项闭环）
5. 统计接口补齐
6. 技术债清理（第 4 节）
