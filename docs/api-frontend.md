# EcoBin 前端对接接口文档

> 适用对象：前端开发（**网页端**：平台管理员 / 租户；**小程序端**：终端用户）
> 后端版本：Spring Boot 4.0.6 + Java 21，数据库 schema 版本 V8
> 末次同步：2026-06-07
> 关联文档：权限与角色设计 [permission-design.md](permission-design.md)、数据库设计 [database-design.md](database-design.md)

---

## 目录

1. [总体说明](#1-总体说明)
2. [通用约定](#2-通用约定)
3. [三端与角色总览](#3-三端与角色总览)
4. [核心业务流程](#4-核心业务流程)
5. [接口明细 · 公共/认证](#5-接口明细--公共认证)
6. [接口明细 · 网页·平台管理员端](#6-接口明细--网页平台管理员端)
7. [接口明细 · 网页·租户端](#7-接口明细--网页租户端)
8. [接口明细 · 小程序·终端用户端](#8-接口明细--小程序终端用户端)
9. [接口明细 · 设备 IoT 上报](#9-接口明细--设备-iot-上报)
10. [数据字典（枚举）](#10-数据字典枚举)

---

## 1. 总体说明

EcoBin 是智慧环保回收箱后端系统，对接三类前端：

| 前端 | 登录主体 | 角色 | 说明 |
|------|----------|------|------|
| **网页 · 管理后台** | 平台管理员 `sys_admin` | 9-超管 / 8-管理员 | 管平台：管理员账号、租户、设备分配 |
| **网页 · 租户后台** | 租户 `sys_tenant` | 7-租户 | 管自己租户内的用户、设备、业务数据、审核 |
| **小程序** | 终端用户 `sys_user` | 3-设备管理员 / 2-清运员 / 1-普通用户 | 投递、清运、钱包提现 |

- **服务地址（开发）**：`http://localhost:8080`
- **所有接口前缀**：`/api`
- **数据格式**：请求与响应均为 `application/json; charset=utf-8`
- **多租户隔离**：除登录/IoT 上报外，所有数据查询都自动按当前登录身份的 `tenant_id` 隔离，前端无需也无法传 `tenant_id` 跨租户访问。

---

## 2. 通用约定

### 2.1 统一响应格式

所有接口返回统一包裹结构 `Result<T>`：

```json
{
  "code": 200,
  "message": "success",
  "data": { }
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | 业务状态码，见 §2.4。`200` 表示成功 |
| `message` | string | 提示信息，成功为 `"success"`，失败为错误描述 |
| `data` | object/array/null | 业务数据，失败时通常为 `null` |

### 2.2 分页响应格式

分页接口的 `data` 为 `PageResult<T>`：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "records": [ ],
    "total": 135,
    "page": 1,
    "pageSize": 20
  }
}
```

分页请求统一用 query 参数 `page`（页码，从 1 开始）、`pageSize`（每页条数）。
**默认 `page=1, pageSize=20`，单页上限 200**（超过按 200 处理）。

### 2.3 认证方式

- 登录接口（`/api/system/auth/login`、`/api/system/auth/wx-login`）无需认证。
- 其余接口必须在请求头携带 JWT：

```
Authorization: Bearer <token>
```

- Token 由登录接口返回，有效期 **24 小时**。
- **强制失效机制**：当账号被改角色/被禁用、或租户被禁用（连带名下用户）时，旧 Token 立即失效，再次调用返回 `401`，前端需引导**重新登录**。

### 2.4 错误码

| code | 含义 | 典型场景 |
|------|------|----------|
| 200 | 成功 | — |
| 400 | 参数错误 | 入参校验失败（返回具体字段错误信息） |
| 401 | 未认证 / 登录失效 | 未带 Token、Token 过期、权限变更后旧 Token 失效 |
| 403 | 无权限 | 当前角色无权访问该接口 |
| 404 | 资源不存在 | 查询的对象不存在 |
| 500 | 服务器内部错误 | 未捕获异常 |

> 业务异常（`BusinessException`）会返回自定义 `code` 与 `message`（如余额不足、状态不合法等），前端按 `code != 200` 统一拦截并展示 `message` 即可。

### 2.5 字段命名

- JSON 字段为**小驼峰**（如 `tenantId`、`createTime`）。
- 时间格式为 `yyyy-MM-dd HH:mm:ss`。
- 金额/重量为数值类型（`BigDecimal`，单位见各字段说明：金额=元，重量=kg）。
- 敏感字段（`password`、`miniappSecret`、`openid` 等）**不会返回**给前端。

---

## 3. 三端与角色总览

### 3.1 角色编码

```
9 = SUPER_ADMIN   超级管理员（网页·管理后台）
8 = ADMIN         管理员      （网页·管理后台）
7 = TENANT        租户        （网页·租户后台）
3 = DEVICE_ADMIN  设备管理员  （小程序）
2 = CLEANER       清运员      （小程序）
1 = USER          普通用户    （小程序）
```

> 角色是**作用域**而非线性高低：平台域(9/8) 管平台但**不能看任何租户业务数据**；租户域(7) 管自己租户；终端域(3/2/1) 在小程序操作。

### 3.2 接口路径与可访问角色一览

| 路径前缀 | 9超管 | 8管理员 | 7租户 | 3设管 | 2清运 | 1用户 | 说明 |
|----------|:---:|:---:|:---:|:---:|:---:|:---:|------|
| `POST /api/system/auth/**` | 公开 | 公开 | 公开 | 公开 | 公开 | 公开 | 登录 |
| `/api/system/admin/**` | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | 管理员账号 CRUD |
| `/api/system/tenant/**` | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | 租户 CRUD |
| `GET /api/system/tenant/me` | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | 租户查自己 |
| `/api/system/user/**` | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | 用户管理 |
| `PUT /api/system/user/{id}/role` | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | 改用户角色（仅租户） |
| `/api/system/withdraw/**` | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | 提现审核 |
| `/api/device/**` | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | 设备/投口管理 |
| `/api/business/delivery/**` | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | 投递订单（后台视图） |
| `/api/business/clean/**` | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | 清运订单后台/审核 |
| `/api/statistics/**` | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ | 业务统计 |
| `/api/app/clean/**` | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | 小程序清运作业 |
| `/api/app/**` | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ | 小程序通用（设备/投递/钱包/个人） |
| `POST /api/iot/**` | 公开（设备 SN 鉴权） | | | | | | 设备上报 |

> 注意：**管理员(8) 不能查看任何业务数据**（投递/清运/统计/提现/用户列表），仅能管租户和设备。

---

## 4. 核心业务流程

### 4.1 设备生命周期（管理后台 + 租户后台）

```
① 管理员创建设备          POST /api/device           → 设备归"平台池"(tenant_id=1，未分配)
② 管理员分配设备给租户     PUT  /api/device/{id}      → 修改 tenant_id = 目标租户ID
③ 租户为设备配置投口       POST /api/device/door      → 设置投口分类、单价(元/kg)
④ 租户/终端用户使用设备                                → 仅能操作本租户名下设备
⑤ 管理员收回设备          PUT  /api/device/{id}      → tenant_id 改回 1（回平台池）
```

> 设备的"分配/收回"当前通过通用的 `PUT /api/device/{id}` 修改 `tenant_id` 实现（仅平台域可改，租户受数据隔离限制无法改归属）。

### 4.2 小程序用户注册（全自动）

```
① 用户打开"租户A的小程序"，调 wx.login() 拿到 code
② 小程序调 POST /api/system/auth/wx-login { code, appid:"租户A的appid" }
③ 后端按 appid 定位租户 → 调微信 code2session 拿 openid
④ 查 sys_user (tenant_id + openid):
     找到 → 校验状态正常，签发 JWT
     未找到 → 自动注册 { tenant_id, openid, role=1(普通用户) } 后签发 JWT
⑤ 返回 token（含 role / nickname / avatar），前端保存 token
```

> 用户**只能通过小程序自动注册**，后台不提供手动建终端用户。同一微信在不同租户小程序下是各自独立的账号。

### 4.3 投递流程（开启设备 + 设备上传后建单，支持设备端「继续投递」）

```
阶段0 选择目标
  小程序  GET /api/app/device              → 列出本租户设备
  小程序  GET /api/app/device/{id}/doors   → 列出该设备的投口（含分类、单价）

① 开启设备（小程序）—— 不建单
  小程序  POST /api/app/delivery/open { doorId }
          → 后端记「设备当前活跃用户」会话（device→userId，TTL 15min），返回 ok
          → 下发开投口指令（仅含 COS 凭证），用户投放垃圾

② 设备作业 + 继续投递（设备端自主）
          → 设备每次开门自生成 deliveryToken；用户可在设备屏点「继续投递」再投一袋（每袋独立成单）

③ 完成上报（设备 IoT，非前端调用）—— 上传后建单
  设备    POST /api/iot/delivery/complete { sn, doorIndex, deliveryToken, weight, wasteType1?, wasteType2? }
          → 按 device+deliveryToken 幂等；取「当前活跃用户」会话建单(deliveryStatus=1)
          → 命中用户：按 投口单价 price × weight 返现入账到 balance；无活跃会话：建无主单不返现

查询
  小程序  GET /api/app/delivery/my         → 我的投递记录
  小程序  GET /api/app/wallet              → 查看返现后的余额
```

> `deliveryToken` 由**设备每次开门自生成**（照片 key 前缀 + 上报幂等键）。建单时机在**设备上传称重之后**，
> 用户归属取该设备「当前活跃用户」会话；无活跃会话（过期/从未开启）则建无主单、不返现。投递流水**不可变、不可删除**。
> 详见 `onenet-thing-model.md` §8。

### 4.4 清运流程（设备自动称重 + 去皮链式追踪，V9 重做）

```
① 清运员/设备管理员 小程序选取设备/投口，扫新空垃圾袋二维码得 bagNo
② 小程序  POST /api/app/clean/open { doorId, bagNo }
          → 开门即建单：后端用登录态 userId + 新袋 bagNo 建清运单(newBagQr=bagNo)
          → 下发开清运门指令（携带 doorIndex + cleanOrderId），返回订单(含 id)
③ 设备    POST /api/iot/clean/gross { sn, cleanOrderId, weight }
          → 按 cleanOrderId 回填毛重：net = 毛重 − 该投口当前(旧袋)去皮；cleanOrderId 即幂等键
④ 清运员换上新空袋，设备称去皮
   设备    POST /api/iot/clean/tare  { sn, cleanOrderId, weight }
          → 按订单的 newBagQr + 本次去皮重 upsert 该投口当前垃圾袋（设备不传 bagNo）
⑤ 清运员  GET /api/app/clean/my     → 查看自己的清运记录（含毛重/去皮/净重）
```

> 普通用户(1) **不能清运**。**开门即建单**：`/api/app/clean/open` 用登录态 userId 建单并返回订单，设备只收 `cleanOrderId`（业务标识符）+ `doorIndex`（物理控制）；毛重/去皮由设备经 `/api/iot/clean/**`（明文 SN 信任）只回传 `{sn, cleanOrderId, weight}` 上报——**设备不碰 userId/bagNo/reportSn**。前端不手填重量，**审核流程已取消**。

### 4.5 钱包与提现流程（小程序申请 + 租户审核）

```
① 投递返现持续累计到 用户 balance（可用余额）

② 用户发起提现  POST /api/app/wallet/withdraw { amount }
                → 可用余额 balance 转入 待审核余额 pendingBalance（冻结）
                → 建提现单 status=0(待审核)

③ 租户后台审核  GET  /api/system/withdraw?status=0          → 待审核列表
                POST /api/system/withdraw/{id}/audit { pass, remark }
                  通过(pass=true)  → 扣减 pendingBalance + 占位转账，status=1
                  驳回(pass=false) → pendingBalance 退回 balance，status=2

④ 用户查询      GET /api/app/wallet/withdraw               → 我的提现记录
```

> 真实微信商家转账尚未接入，审核通过为占位逻辑（见 docs/open-items.md）。

### 4.6 用户角色管理（租户后台）

```
① 租户  GET /api/system/user                 → 查看本租户下的用户列表
② 租户  PUT /api/system/user/{id}/role { role:2 }  → 提升/降低角色（仅限 1/2/3）
        → 改后该用户旧 Token 立即失效，需重新登录
```

> 角色提升/降低的**唯一入口**是租户，且被强校验只能改为 1/2/3，无法改成平台/租户角色（防提权）。

---

## 5. 接口明细 · 公共/认证

### 5.1 网页端登录（管理员 / 租户）

`POST /api/system/auth/login` ·  公开

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `userType` | string | 否 | `admin`（平台管理员）/ `tenant`（租户）。缺省按 `admin` 处理 |
| `username` | string | 是 | 用户名 |
| `password` | string | 是 | 密码 |

```json
{ "userType": "admin", "username": "admin", "password": "admin123" }
```

**响应** `data`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `token` | string | JWT，后续请求带在 `Authorization: Bearer` |
| `userId` | long | 主体 ID（adminId / tenantId） |
| `tenantId` | long | 租户 ID（管理员为平台池 ID=1） |
| `username` | string | 用户名 |
| `realName` | string | 真实姓名 |
| `role` | int | 角色编码（见 §3.1） |
| `nickname` / `avatar` | string | 网页端为空，小程序用 |

```json
{
  "code": 200, "message": "success",
  "data": { "token": "eyJhbGci...", "userId": 1, "tenantId": 1, "username": "admin", "realName": "超级管理员", "role": 9 }
}
```

> 初始超管账号（seed）：`admin / admin123`（role=9）。

### 5.2 小程序微信登录

`POST /api/system/auth/wx-login` ·  公开

**请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `code` | string | 是 | `wx.login()` 返回的临时 code |
| `appid` | string | 是 | 所属租户的小程序 AppID（定位租户与 secret） |

```json
{ "code": "081xxx", "appid": "wx1234567890" }
```

**响应** `data`：同 §5.1（`LoginResponse`），小程序用户额外带 `nickname`、`avatar`。首次登录自动注册为普通用户(role=1)。

---

## 6. 接口明细 · 网页·平台管理员端

> 角色：超管(9) / 管理员(8)。管理员账号管理仅超管可用。

### 6.1 管理员账号管理（仅超管 9）

基路径 `/api/system/admin`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system/admin` | 管理员列表 |
| GET | `/api/system/admin/{id}` | 管理员详情 |
| POST | `/api/system/admin` | 新建管理员 |
| PUT | `/api/system/admin/{id}` | 修改管理员 |
| DELETE | `/api/system/admin/{id}` | 删除管理员 |

**`Admin` 对象字段**（请求/响应）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | long | 主键（新建免传） |
| `username` | string | 登录用户名 |
| `password` | string | 密码（**只写**，新建/改密时传，响应不返回） |
| `realName` | string | 真实姓名 |
| `role` | int | 9-超管 / 8-管理员 |
| `status` | int | 0-禁用 / 1-启用 |
| `createTime` / `updateTime` | datetime | 只读 |

新建示例：

```json
{ "username": "ops01", "password": "Init@123", "realName": "运营小王", "role": 8, "status": 1 }
```

### 6.2 租户管理（超管 9 + 管理员 8）

基路径 `/api/system/tenant`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system/tenant` | 租户列表 |
| GET | `/api/system/tenant/{id}` | 租户详情 |
| POST | `/api/system/tenant` | 新建租户（= 开辟一个数据隔离空间） |
| PUT | `/api/system/tenant/{id}` | 修改租户 |
| DELETE | `/api/system/tenant/{id}` | 删除租户 |

**`Tenant` 对象字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | long | 主键 = 该租户的 tenant_id（恒 >1，新建免传） |
| `name` | string | 租户名称 |
| `code` | string | 租户编码（内部可读标识） |
| `username` | string | 租户登录用户名 |
| `password` | string | 登录密码（**只写**） |
| `miniappAppid` | string | 小程序 AppID（不配置传 null；唯一） |
| `miniappSecret` | string | 小程序 Secret（**只写**，后端 AES 加密存储） |
| `merchantNo` | string | 微信商户号 |
| `contactName` / `contactPhone` / `address` | string | 联系人/电话/地址 |
| `status` | int | 0-禁用 / 1-启用 |

> 禁用租户(status=0) 会连带其名下所有终端用户的 Token 立即失效（强制下线）。

新建示例：

```json
{
  "name": "绿源环保", "code": "GREEN001",
  "username": "greenadmin", "password": "Green@123",
  "miniappAppid": "wx1234567890", "miniappSecret": "abcd...",
  "merchantNo": "16xxxxxxxx", "contactName": "李经理", "contactPhone": "13800000000",
  "address": "杭州市西湖区", "status": 1
}
```

### 6.3 设备与投口管理（超管 9 + 管理员 8 + 租户 7）

详见 §7.3（设备/投口接口三角色共用，按 `tenant_id` 隔离）。管理员侧主要用于**创建设备**（落入平台池）和**分配设备**（修改 `tenant_id`）。

---

## 7. 接口明细 · 网页·租户端

> 角色：租户(7)。所有数据自动限定在本租户范围内。

### 7.1 租户自查

`GET /api/system/tenant/me` · 租户(7)

返回当前登录租户自身资料（`Tenant` 对象，敏感字段已脱敏）。

### 7.2 用户管理（超管 9 全量查看 / 租户 7 管自己租户）

基路径 `/api/system/user`

| 方法 | 路径 | 角色 | 说明 |
|------|------|------|------|
| GET | `/api/system/user?page=1&pageSize=20` | 9/7 | 用户分页列表 |
| GET | `/api/system/user/{id}` | 9/7 | 用户详情 |
| PUT | `/api/system/user/{id}/role` | **仅 7** | 提升/降低用户角色 |

**改角色请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `role` | int | 是 | 目标角色，仅允许 `1`普通用户 / `2`清运员 / `3`设备管理员 |

```json
{ "role": 2 }
```

> 改角色后该用户旧 Token 立即失效。
> （`POST/PUT/DELETE /api/system/user` 等通用写接口存在但受限：终端用户只能小程序自动注册，业务上不用后台手动建/改用户主体信息。）

**`User` 对象字段（响应，脱敏）**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | long | 用户 ID |
| `realName` / `phone` / `email` | string | 资料 |
| `nickname` / `avatar` | string | 微信昵称/头像 |
| `role` | int | 3/2/1 |
| `status` | int | 0-禁用 / 1-启用 |
| `balance` | decimal | 可用余额（元） |
| `pendingBalance` | decimal | 待审核冻结余额（元） |
| `createTime` | datetime | 注册时间 |

> `password` / `openid` / `unionid` 不返回。

### 7.3 设备管理（超管 9 + 管理员 8 + 租户 7）

基路径 `/api/device`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/device?page=1&pageSize=20` | 设备分页列表（按 tenant_id 隔离） |
| GET | `/api/device/{id}` | 设备详情 |
| POST | `/api/device` | 创建设备（管理员创建落入平台池 tenant_id=1） |
| PUT | `/api/device/{id}` | 修改设备（含改 tenant_id 实现分配/收回，仅平台域） |
| DELETE | `/api/device/{id}` | 删除设备 |

**`Device` 对象字段**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `id` | long | — | 主键 |
| `sn` | string | 是 | 设备序列号 |
| `name` | string | 是 | 设备名称 |
| `type` | int | 否 | 1-智能垃圾箱 / 2-滚动系统（见 §10） |
| `lat` / `lng` | decimal | 否 | 纬度 / 经度 |
| `address` | string | 否 | 安装地址 |
| `status` | int | 否 | 0-离线 / 1-在线 / 2-维护中 |

### 7.4 投口管理（超管 9 + 管理员 8 + 租户 7）

基路径 `/api/device/door`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/device/door/list/{deviceId}` | 某设备的投口列表 |
| GET | `/api/device/door/{id}` | 投口详情 |
| POST | `/api/device/door` | 创建投口 |
| PUT | `/api/device/door/{id}` | 修改投口 |
| DELETE | `/api/device/door/{id}` | 删除投口 |

**`Door` 对象字段**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `id` | long | — | 主键 |
| `deviceId` | long | 是 | 所属设备 ID |
| `doorIndex` | int | 是 | 投口号（1-6） |
| `name` | string | 否 | 投口名称 |
| `wasteType1` | int | 是 | 一级分类（见 §10） |
| `wasteType2` | int | 否 | 二级分类 |
| `price` | decimal | 否 | 单价（元/kg），投递完成按 `price × weight` 返现 |
| `enabled` | int | 否 | 0-禁用 / 1-启用 |
| `sortOrder` | int | 否 | 排序 |

### 7.5 投递订单（后台视图，超管 9 + 租户 7）

基路径 `/api/business/delivery`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/business/delivery?page=1&pageSize=20` | 投递订单分页 |
| GET | `/api/business/delivery/{id}` | 投递订单详情 |
| GET | `/api/business/delivery/today-overview` | 今日投递概览（次数/重量/人数等） |
| POST | `/api/business/delivery` | 手工建单（一般不用，正常走两阶段流程） |

**`DeliveryOrder` 对象字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | long | 主键 |
| `orderSn` | string | 订单编号 |
| `deliveryToken` | string | 投递标识符（设备生成，关联设备上报） |
| `deviceId` / `doorId` / `userId` | long | 设备/投口/用户（无主单 `userId` 为 null） |
| `wasteType1` / `wasteType2` | int | 分类 |
| `weight` | decimal | 重量（kg） |
| `price` | decimal | 单价 |
| `score` | int | 获得积分 |
| `loginType` | int | 登录方式（见 §10） |
| `status` | int | 0-正常 / -1-异常 |
| `deliveryStatus` | int | 1-已完成（投递改为上传后建单，建单即完成；0-进行中为历史遗留语义） |
| `photoOpenOutside` | string | 开门前箱外照片 URL（V11） |
| `photoOpenInside` | string | 开门前箱内照片 URL（V11） |
| `photoCloseOutside` | string | 关门后箱外照片 URL（V11） |
| `photoCloseInside` | string | 关门后箱内照片 URL（V11） |
| `createTime` | datetime | 投递时间（投递流水无 updateTime） |

### 7.6 清运订单后台（超管 9 + 租户 7）

基路径 `/api/business/clean`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/business/clean?page=1&pageSize=20` | 清运单分页 |
| GET | `/api/business/clean/{id}` | 清运单详情 |
| POST | `/api/business/clean` | 手工建单 |
| PUT | `/api/business/clean/{id}` | 修改清运单 |
| DELETE | `/api/business/clean/{id}` | 删除清运单 |

> 审核端点 `PUT /api/business/clean/{id}/audit` 已随清运改造（V9）移除。

**`CleanOrder` 对象字段**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | long | 主键 |
| `orderSn` | string | 订单编号 |
| `deviceId` / `doorId` / `userId` | long | 设备/投口/清运员 |
| `bagQr` | string | 本次清走的垃圾袋编号 |
| `wasteType1` / `wasteType2` | int | 分类 |
| `grossWeight` | decimal | 清运毛重（kg，设备上报满袋重量） |
| `tareWeight` | decimal | 去皮重量（kg） |
| `netWeight` | decimal | 实际清运量（kg）= 毛重 − 去皮 |
| `weight` | decimal | =netWeight（兼容旧字段） |
| `auditStatus` | int | **已废弃**（审核取消，默认 1） |
| `status` | int | 0-创建 / 1-完成 / 2-取消 |
| `photoOpenOutside` | string | 开门前箱外照片 URL（V11） |
| `photoOpenInside` | string | 开门前箱内照片 URL（V11） |
| `photoCloseOutside` | string | 关门后箱外照片 URL（V11） |
| `photoCloseInside` | string | 关门后箱内照片 URL（V11） |
| `createTime` / `updateTime` | datetime | 时间 |

### 7.7 提现审核（超管 9 + 租户 7）

基路径 `/api/system/withdraw`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/system/withdraw?status=0&page=1&pageSize=20` | 提现单列表（`status` 可选：0-待审核 1-已通过 2-已驳回） |
| POST | `/api/system/withdraw/{id}/audit` | 审核提现单 |

**审核请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `pass` | boolean | 是 | true-通过 / false-驳回 |
| `remark` | string | 否 | 审核备注 |

```json
{ "pass": true, "remark": "已核实" }
```

**`WithdrawOrder` 对象字段**（列表 `records`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | long | 主键 |
| `userId` | long | 申请用户 |
| `amount` | decimal | 提现金额（元） |
| `status` | int | 0-待审核 / 1-已通过 / 2-已驳回 |
| `auditBy` | long | 审核租户 ID |
| `auditTime` | datetime | 审核时间 |
| `auditRemark` | string | 审核备注 |
| `transferNo` | string | 微信转账单号（真实转账接入后回填） |
| `createTime` | datetime | 申请时间 |

### 7.8 业务统计（超管 9 + 租户 7）

基路径 `/api/statistics`，均为 `GET`，返回 `Map` 结构（具体字段以实际返回为准）。

| 路径 | 说明 |
|------|------|
| `/api/statistics/dashboard` | 今日概览：投递次数 + 总重量 + 投递人数 |
| `/api/statistics/devices` | 设备信息统计 |
| `/api/statistics/members` | 会员统计 |
| `/api/statistics/delivery` | 本月投递统计 |
| `/api/statistics/clean` | 本月清运统计 |
| `/api/statistics/payout` | 提现支出统计 |
| `/api/statistics/member-money` | 会员资金统计 |
| `/api/statistics/devices-map` | 设备地图坐标（数组） |
| `/api/statistics/device-ranking?pageSize=5` | 本月设备投递排行（按重量降序，默认前 5） |

---

## 8. 接口明细 · 小程序·终端用户端

> 角色：设备管理员(3) / 清运员(2) / 普通用户(1)。所有接口仅访问**属于自己**的数据（按 userId + tenant_id 双重隔离）。
> 登录见 §5.2。

### 8.1 个人信息（USER / CLEANER / DEVICE_ADMIN）

`GET /api/app/profile`

返回脱敏后的个人信息 `UserProfileVO`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | long | 用户 ID |
| `realName` | string | 真实姓名 |
| `phone` | string | 手机号 |
| `nickname` / `avatar` | string | 微信昵称/头像 |
| `role` | int | 3/2/1 |
| `status` | int | 0-禁用 / 1-启用 |

### 8.2 设备与投口（只读，选投口用）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/app/device?page=1&pageSize=20` | 本租户设备分页列表 |
| GET | `/api/app/device/{id}` | 设备详情 |
| GET | `/api/app/device/{deviceId}/doors` | 某设备的投口列表（含分类、单价） |

返回结构同 §7.3 `Device` / §7.4 `Door`。

### 8.3 投递（USER / CLEANER / DEVICE_ADMIN）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/app/delivery/open` | 开启设备：记「当前活跃用户」会话 + 下发开门（**不建单**） |
| GET | `/api/app/delivery/my?page=1&pageSize=20` | 我的投递记录分页 |
| GET | `/api/app/delivery/my/{id}` | 我的单条投递详情 |

**开启设备请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `doorId` | long | 是 | 投口 ID（从 §8.2 投口列表选取） |

```json
{ "doorId": 12 }
```

**开启设备响应** `data`：`null`（仅 `{ "code":200, "message":"ok" }`）。

> 不再返回 `orderId`/`deliveryToken`：投递改为「上传后建单」——订单在设备投放称重后由后端创建，
> 用户在 `my` 列表（`DeliveryOrder`，见 §7.5）查看。用户可在设备屏「继续投递」再投一袋，每袋独立成单。
> 重量回填与返现由设备 IoT 上报触发建单（§9）。

### 8.4 清运作业（仅 CLEANER 2 / DEVICE_ADMIN 3）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/app/clean/open` | 开清运门：扫新空袋后**即建清运单**并下发开门指令，返回订单 |
| GET | `/api/app/clean/my?page=1&pageSize=20` | 我的清运记录分页 |
| GET | `/api/app/clean/my/{id}` | 我的单条清运详情 |

**开清运门请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `doorId` | long | 是 | 投口 ID（从 §8.2 选取） |
| `bagNo` | string | 是 | 新空垃圾袋编号（扫码获取） |

```json
{ "doorId": 12, "bagNo": "00001" }
```

返回 `Result<CleanOrder>`（**已建单**，含 `id`/`newBagQr`/`userId`；`cleanOrderId` 随开门指令下发给设备）。毛重/去皮由设备经 §9 按 `cleanOrderId` 回填。`my` 列表返回 `CleanOrder`（见 §7.6，含 `grossWeight/tareWeight/netWeight`）。

> 普通用户(1) 调用 `/api/app/clean/**` 会返回 403。前端不再手填重量；下发走 OneNet，凭证未到位前为占位（见 `docs/open-items.md`）。

### 8.5 钱包与提现（USER / CLEANER / DEVICE_ADMIN）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/app/wallet` | 我的钱包余额 |
| POST | `/api/app/wallet/withdraw` | 发起提现 |
| GET | `/api/app/wallet/withdraw?page=1&pageSize=20` | 我的提现记录分页 |

**钱包余额响应** `WalletVO`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `balance` | decimal | 可用余额（元） |
| `pendingBalance` | decimal | 待审核冻结余额（元） |

**发起提现请求体**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `amount` | decimal | 是 | 提现金额（元，必须 > 0.01） |

```json
{ "amount": 10.00 }
```

发起后可用余额转入冻结，返回 `WithdrawOrder`（见 §7.7）。提现记录列表同 `WithdrawOrder`。

---

## 9. 接口明细 · 设备 IoT 上报

> 由设备端调用，**非前端职责**，列出以便理解投递闭环。路径 `/api/iot/**` 放行（无用户登录态），后端按上报的 `sn` 反查设备确定租户并校验。

### 9.1 投递完成上报（上传后建单）

`POST /api/iot/delivery/complete`

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `sn` | string | 是 | 设备序列号 |
| `doorIndex` | int | 是 | 投口号（后端据 device+doorIndex 反查投口取单价/分类） |
| `deliveryToken` | string | 是 | **设备每次开门自生成**：照片 key 前缀 + 上报幂等键 |
| `weight` | decimal | 是 | 本次投递重量（kg） |
| `wasteType1` | int | 否 | 一级分类（缺省沿用投口配置） |
| `wasteType2` | int | 否 | 二级分类 |

后端此刻**建单**：按 `device+deliveryToken` 幂等；取该设备「当前活跃用户」会话确定归属——命中则建单(`deliveryStatus=1`)、按 `投口单价 × 重量` 返现入余额；无活跃会话（过期/从未开启）则建无主单、不返现。详见 `onenet-thing-model.md` §8。

### 9.2 清运毛重上报（V9）

`POST /api/iot/clean/gross`

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `sn` | string | 是 | 设备序列号（明文 SN 信任，校验订单归属） |
| `cleanOrderId` | long | 是 | 清运订单 ID（开门时下发，设备原样回传；**充当幂等键**） |
| `weight` | decimal | 是 | 本次清运毛重（kg，满袋重量） |

按 `cleanOrderId` 回填订单：`netWeight = 毛重 − 该投口当前(旧袋)去皮`（首次去皮按 0）。已回填毛重则幂等返回。设备**不传** `doorIndex/userId/reportSn`（后端按订单反查）。

### 9.3 换袋去皮上报（V9）

`POST /api/iot/clean/tare`

| 字段 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `sn` | string | 是 | 设备序列号（明文 SN 信任，校验订单归属） |
| `cleanOrderId` | long | 是 | 清运订单 ID（开门时下发，设备原样回传） |
| `weight` | decimal | 是 | 新空袋去皮重量（kg） |

按 `cleanOrderId` 取订单的 `newBagQr`（open 时小程序已扫）+ 本次去皮重，upsert 该投口当前垃圾袋。设备**不传** `doorIndex/userId/bagNo`。

按 `(device, doorIndex)` upsert `biz_clean_bag`，更新当前袋编号与去皮（换袋天然幂等）。

### 9.4 COS 照片直传（设备直传 + 设备自定 key + 回传 URL）

**无独立 HTTP 端点**（原 `POST /api/iot/photo/sts` 与 `/notify` 已移除）。**投递、清运一致**：开门命令只下发凭证（OneNet `cosToken`，不含 key），照片对象 key 由**设备自定位置**，设备直传 COS 后把 **4 个 URL 随上行业务事件回传**，后端原样存订单（不再算 key、不复原）：

- **投递（上传后建单）**：4 个 URL 随 `deliveryComplete` 回传，`completeDelivery` 建单时写入订单。
- **清运（开门即建单）**：开门时订单照片字段留空，4 个 URL 随 `cleanGross` 回传，`reportGross` 回填订单。

key 由设备自定（后端不约束格式），建议带业务段与唯一串避免覆盖：投递 `{sn}/delivery/{唯一串}/<slot>.jpg`、清运 `{sn}/clean/{唯一串}/<slot>.jpg`，
slot ∈ `open_outside`/`open_inside`/`close_outside`/`close_inside`。详见 `onenet-thing-model.md` §1.1/§3.4/§4。

> 下发「开门」走 OneNet（`OneNetClient`），COS / OneNet 凭证未到位前均为占位日志/占位值，不阻塞主流程。
> 兜底：后端不校验对象是否真上传成功，设备没传上时前端加载出 404 显示占位图。

---

## 10. 数据字典（枚举）

### 角色 `role`
| 值 | 含义 | 端 |
|---|------|----|
| 9 | 超级管理员 | 网页·管理后台 |
| 8 | 管理员 | 网页·管理后台 |
| 7 | 租户 | 网页·租户后台 |
| 3 | 设备管理员 | 小程序 |
| 2 | 清运员 | 小程序 |
| 1 | 普通用户 | 小程序 |

### 通用状态 `status`（账号/租户/设备启用）
| 值 | 含义 |
|---|------|
| 0 | 禁用 / 离线 |
| 1 | 启用 / 在线 |
| 2 | 维护中（设备） |

### 设备类型 `type`（`DeviceType`）
| 值 | 含义 |
|---|------|
| 0 | 通用 |
| 1 | 智能垃圾箱 |
| 2 | 滚动系统 |

### 垃圾一级分类 `wasteType1`（`WasteType1`）
| 值 | 含义 |
|---|------|
| 0 | 无 |
| 1 | 厨余垃圾 |
| 2 | 可回收垃圾 |
| 3 | 有害垃圾 |
| 4 | 其他垃圾 |

### 垃圾二级分类 `wasteType2`（`WasteType2`）
| 值 | 含义 |
|---|------|
| 0 | 不区分 |
| 1 | 纸类 |
| 2 | 塑料 |
| 3 | 织物 |
| 4 | 金属 |
| 5 | 其他 |

### 登录方式 `loginType`（`LoginType`）
| 值 | 含义 |
|---|------|
| 0 | 未知 |
| 1 | 手机号 |
| 2 | IC 卡 |
| 3 | 人脸识别 |
| 4 | 二维码 |
| 5 | 微信小程序 |

### 投递阶段 `deliveryStatus`
| 值 | 含义 |
|---|------|
| 0 | 进行中（历史遗留；投递改上传后建单后不再产生此态） |
| 1 | 已完成（设备上传即建单即完成） |

### 清运审核状态 `auditStatus`（已废弃，V9）
> 清运改为设备自动称重上报后审核流程取消，字段保留仅兼容历史，新记录默认 1。

| 值 | 含义 |
|---|------|
| 0 | 待审核（历史） |
| 1 | 审核通过（新记录默认） |
| 2 | 审核拒绝（历史） |

### 提现单状态 `status`
| 值 | 含义 |
|---|------|
| 0 | 待审核 |
| 1 | 已通过 |
| 2 | 已驳回 |

---

> 文档随接口演进，如发现与实际行为不一致，以代码（`*Controller` + `SecurityConfig`）为准并同步更新本文件。
