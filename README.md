# EcoBin 智慧环保回收箱管理系统

基于 Spring Boot 4.0.6 + Java 21 的智慧环保回收箱后端管理系统，提供设备管理、投递订单、清运订单、数据统计等核心功能。已实现**多租户数据隔离**、三类登录主体（平台管理员 / 租户 / 小程序用户）、基于角色的接口鉴权，以及角色/状态变更后的 JWT 强制失效。详细权限设计见 [`docs/permission-design.md`](docs/permission-design.md)。

## 技术栈

| 组件              | 版本     | 说明                                           |
|-----------------|--------|----------------------------------------------|
| Java            | 21     | 运行环境                                         |
| Spring Boot     | 4.0.6  | 核心框架                                         |
| Spring Security | —      | 认证鉴权                                         |
| MyBatis Plus    | 3.5.16 | ORM（需使用 `mybatis-plus-spring-boot4-starter`） |
| JWT (jjwt)      | 0.12.6 | 无状态 Token 认证                                 |
| MySQL           | —      | 生产数据库                                        |
| H2              | —      | 测试环境内存数据库                                    |
| Flyway          | —      | 数据库版本迁移                                      |
| Lombok          | —      | 编译期注解处理器                                     |
| Maven           | —      | 多模块构建                                        |

> **注意**：Spring Boot 4.x 与 MyBatis Plus 3.5.9 不兼容，必须使用 3.5.16 版本及 `boot4-starter`；Flyway 必须通过 `spring-boot-starter-flyway` 触发自动配置。

## 项目结构

```
EcoBin/
├── pom.xml                          # 父 POM（依赖管理 + 模块声明）
├── ecobin-common/                   # 公共模块：枚举、基础实体（BaseEntity/PlatformBaseEntity）、统一响应、业务异常
├── ecobin-framework/                # 框架模块：Security + JWT、租户隔离拦截器、Token 失效登记表、AES 加密、全局异常处理
├── ecobin-module-system/            # 系统管理：平台管理员、租户、用户管理、登录认证
├── ecobin-module-device/            # 设备管理：设备 CRUD、投口管理
├── ecobin-module-business/          # 业务模块：投递订单、清运订单、数据统计
└── ecobin-bootstrap/                # 启动模块：配置、Flyway 迁移、启动入口
```



### 模块依赖关系

```
ecobin-bootstrap ──→ 组装所有模块
  ├── ecobin-module-system   ──→ ecobin-framework ──→ ecobin-common
  ├── ecobin-module-device   ──→ ecobin-framework
  └── ecobin-module-business ──→ ecobin-framework
```

## API 接口

所有接口统一前缀 `/api`，认证接口除外，其余接口需携带 JWT Token（`Authorization: Bearer <token>`）。各接口按角色鉴权，规则见 [`docs/permission-design.md`](docs/permission-design.md) §9。

### 认证

| 方法   | 路径                            | 说明                |
|------|-------------------------------|-------------------|
| POST | `/api/system/auth/login`      | 网页端登录（`userType=admin`/`tenant`，缺省 admin），返回 JWT |
| POST | `/api/system/auth/wx-login`   | 微信小程序登录（需传 `appid` 定位租户），返回 JWT |

### 系统管理

| 方法     | 路径                          | 说明           | 角色 |
|--------|-----------------------------|--------------|------|
| CRUD   | `/api/system/admin/**`      | 平台管理员管理      | 仅超管(9) |
| CRUD   | `/api/system/tenant/**`     | 租户管理         | 超管(9)/管理员(8) |
| CRUD   | `/api/system/user/**`       | 用户管理（角色提升/降低） | 超管(9)/租户(7) |

> 租户(7) 只能管理本租户下的用户（数据隔离）；角色 1↔2↔3 的提升/降低由租户操作，变更后该用户旧 token 立即失效。

### 设备管理

> 角色：`/api/device/**`（含投口）→ 超管(9)/管理员(8)/租户(7)；租户仅能操作本租户设备。

| 方法     | 路径                                 | 说明      |
|--------|------------------------------------|---------|
| GET    | `/api/device?page=1&pageSize=20`   | 设备分页查询  |
| GET    | `/api/device/{id}`                 | 设备详情    |
| POST   | `/api/device`                      | 新增设备    |
| PUT    | `/api/device/{id}`                 | 更新设备    |
| DELETE | `/api/device/{id}`                 | 删除设备    |
| GET    | `/api/device/door/list/{deviceId}` | 设备下投口列表 |
| GET    | `/api/device/door/{id}`            | 投口详情    |
| POST   | `/api/device/door`                 | 新增投口    |
| PUT    | `/api/device/door/{id}`            | 更新投口    |
| DELETE | `/api/device/door/{id}`            | 删除投口    |

### 业务模块

> 角色：投递订单 `/api/business/delivery/**` → 超管(9)/租户(7)；清运订单 `/api/business/clean/**` 读 → 超管(9)/租户(7)，写 → 租户(7)/设备管理员(3)/清运员(2)。

| 方法     | 路径                                             | 说明     |
|--------|------------------------------------------------|--------|
| GET    | `/api/business/delivery?page=1&pageSize=20`    | 投递订单分页 |
| GET    | `/api/business/delivery/{id}`                  | 投递订单详情 |
| POST   | `/api/business/delivery`                       | 创建投递订单 |
| DELETE | `/api/business/delivery/{id}`                  | 删除投递订单 |
| GET    | `/api/business/delivery/today-overview`        | 今日投递概览 |
| GET    | `/api/business/clean?page=1&pageSize=20`       | 清运订单分页 |
| GET    | `/api/business/clean/{id}`                     | 清运订单详情 |
| POST   | `/api/business/clean`                          | 创建清运订单 |
| PUT    | `/api/business/clean/{id}`                     | 更新清运订单 |
| DELETE | `/api/business/clean/{id}`                     | 删除清运订单 |
| PUT    | `/api/business/clean/{id}/audit?auditStatus=1` | 清运订单审核 |

### 统计

> 角色：`/api/statistics/**` → 超管(9)/租户(7)。

| 方法  | 路径                          | 说明     |
|-----|-----------------------------|--------|
| GET | `/api/statistics/dashboard` | 首页概览数据 |

### 统一响应格式

```json
{
  "code": 200,
  "message": "success",
  "data": { }
}
```

分页响应：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "records": [],
    "total": 100,
    "page": 1,
    "pageSize": 20
  }
}
```

## 数据库设计

所有业务表包含 `tenant_id BIGINT NOT NULL DEFAULT 1`。`tenant_id = 1` 为保留值（平台池/未分配），真实租户
`tenant_id = sys_tenant.id 且恒 > 1`（`sys_tenant` 自增从 2 开始）。表前缀：`sys_`（系统表）、`biz_`（业务表）。
完整设计见 [`docs/database-design.md`](docs/database-design.md)。

| 表名                   | 说明                              |
|----------------------|-----------------------------------|
| `sys_admin`          | 平台管理员（超管/管理员，无 tenant_id）   |
| `sys_tenant`         | 系统租户（含登录字段 + 小程序配置）         |
| `sys_user`           | 终端用户（小程序微信 openid 登录，role=1/2/3） |
| `biz_device`         | 设备                              |
| `biz_door`           | 投口（关联设备）                        |
| `biz_delivery_order` | 投递订单                            |
| `biz_clean_order`    | 清运订单                            |
| `biz_device_status`  | 设备实时状态                          |
| `biz_weight_record`  | 重量变更记录                          |

迁移脚本位于 `ecobin-bootstrap/src/main/resources/db/migration/`（V1 建表、V2 微信登录、V3 角色与登录主体重构）。

## 快速开始

### 环境要求

- JDK 21+
- MySQL 8.0+
- Maven 3.8+（或使用项目自带的 `mvnw`）

### 1. 创建数据库

```sql
CREATE DATABASE IF NOT EXISTS ecobin DEFAULT CHARACTER SET utf8mb4;
```

### 2. 修改数据库配置

编辑 `ecobin-bootstrap/src/main/resources/application.yml`：

```yaml
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=Asia/Shanghai&createDatabaseIfNotExist=true
    username: root
    password: your_password
```

### 3. 编译项目

```bash
./mvnw clean compile
```

### 4. 运行测试

```bash
./mvnw test
```

### 5. 启动应用

```bash
./mvnw spring-boot:run -pl ecobin-bootstrap
```

启动后 Flyway 自动建表并初始化数据，应用运行在 `http://localhost:8080`。

### 6. 登录验证

```bash
curl -X POST http://localhost:8080/api/system/auth/login \
  -H "Content-Type: application/json" \
  -d '{"userType":"admin","username":"admin","password":"admin123"}'
```

> `userType` 取 `admin`（平台管理员，查 `sys_admin`）或 `tenant`（租户，查 `sys_tenant`），缺省按 `admin` 处理。

### 微信小程序配置

如需启用微信登录，在 `application.yml` 中配置真实的小程序参数：

```yaml
wechat:
  miniapp:
    appid: your_appid     # 替换为真实 AppID
    secret: your_secret   # 替换为真实 AppSecret
```

微信登录接口为 `POST /api/system/auth/wx-login`，请求体 `{"code": "wx.login()返回的临时code", "appid": "租户小程序AppID"}`。
后端按 `appid` 定位租户并用其（AES 解密后的）secret 调微信换取 openid，首次登录在该租户下自动注册用户（role=1 普通用户）。
每个租户绑定独立小程序，`sys_tenant.miniapp_appid`/`miniapp_secret` 在租户管理中配置；`application.yml` 中的
`wechat.miniapp` 仅作全局默认/兜底。

### 默认账号

| 用户名   | 密码       | 角色          | 表          | 登录 userType |
|-------|----------|-------------|------------|-------------|
| admin | admin123 | 超级管理员(9) | `sys_admin` | admin |

## 架构设计

### 多租户数据隔离

已落地的自动隔离机制：

- **数据库层**：所有业务表含 `tenant_id`；`tenant_id=1` 为平台池，真实租户 id 从 2 起。
- **框架层**：`TenantContextHolder`（ThreadLocal）持有 `tenantId` + `ignore`（平台域放行标志），由 `JwtAuthenticationFilter` 按登录主体设置。
- **MyBatis 层**：MyBatis-Plus `TenantLineInnerInterceptor` + `EcoBinTenantLineHandler` 自动为业务表注入 `WHERE tenant_id=?` 并在 INSERT 时回填；平台域（超管/管理员）及 `sys_admin`/`sys_tenant` 表放行。

### 角色与强制失效

- **角色体系**：9-超管、8-管理员、7-租户、3-设备管理员、2-清运员、1-用户；分平台域/租户域/终端域（非线性高低）。
- **强制失效**：角色/状态变更时由 `TokenInvalidationRegistry` 登记标识符（管理员/租户/用户/租户名下用户），
  JWT 校验时比对签发时间，旧 token 立即返回 401「权限已变更，请重新登录」，重新登录后自动恢复。
  覆盖：超管禁用管理员、租户改用户角色/状态、禁用租户连带其名下用户下线。

### 认证流程

```
网页端登录:
POST /api/system/auth/login (userType=admin/tenant) → 查 sys_admin/sys_tenant → BCrypt 校验 → 签发 JWT(role)

微信小程序登录:
POST /api/system/auth/wx-login (code+appid) → 按 appid 查租户 → code 换 openid → 查找/自动注册用户 → 签发 JWT

后续请求 → Authorization: Bearer <token> → JwtAuthenticationFilter：
  校验签名 → 强制失效登记表比对 → 设租户上下文 → 构造 GrantedAuthority → SecurityFilterChain 角色鉴权
```

### 包结构规范

```
org.enveloping.ecobin.{module}
├── controller/          # REST 控制器
├── service/
│   └── impl/            # 服务接口与实现
├── mapper/              # MyBatis Mapper 接口
├── entity/              # 实体类（POJO）
└── dto/                 # 数据传输对象
```

## 常用命令

```bash
# 编译
./mvnw compile

# 编译单个模块
./mvnw compile -pl ecobin-module-system

# 运行测试
./mvnw test

# 运行单个测试类
./mvnw test -Dtest=EcoBinApplicationTests -pl ecobin-bootstrap

# 打包（跳过测试）
./mvnw package -DskipTests

# 启动
./mvnw spring-boot:run -pl ecobin-bootstrap

# 清理
./mvnw clean
```
