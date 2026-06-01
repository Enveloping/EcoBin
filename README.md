# EcoBin 智慧环保回收箱管理系统

基于 Spring Boot 4.0.6 + Java 21 的智慧环保回收箱后端管理系统，提供设备管理、投递订单、清运订单、数据统计等核心功能。当前实现单租户功能，架构上预留多租户扩展能力。

## 技术栈

| 组件 | 版本 | 说明 |
|------|------|------|
| Java | 21 | 运行环境 |
| Spring Boot | 4.0.6 | 核心框架 |
| Spring Security | — | 认证鉴权 |
| MyBatis Plus | 3.5.16 | ORM（需使用 `mybatis-plus-spring-boot4-starter`） |
| JWT (jjwt) | 0.12.6 | 无状态 Token 认证 |
| MySQL | — | 生产数据库 |
| H2 | — | 测试环境内存数据库 |
| Flyway | — | 数据库版本迁移 |
| Lombok | — | 编译期注解处理器 |
| Maven | — | 多模块构建 |

> **注意**：Spring Boot 4.x 与 MyBatis Plus 3.5.9 不兼容，必须使用 3.5.16 版本及 `boot4-starter`；Flyway 必须通过 `spring-boot-starter-flyway` 触发自动配置。

## 项目结构

```
EcoBin/
├── pom.xml                          # 父 POM（依赖管理 + 模块声明）
├── ecobin-common/                   # 公共模块：枚举、基础实体、统一响应、业务异常
├── ecobin-framework/                # 框架模块：Security + JWT、租户上下文、全局异常处理
├── ecobin-module-system/            # 系统管理：租户管理、用户管理、登录认证
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

所有接口统一前缀 `/api`，认证接口除外，其余接口需携带 JWT Token（`Authorization: Bearer <token>`）。

### 认证

| 方法   | 路径                       | 说明                |
|------|--------------------------|-------------------|
| POST | `/api/system/auth/login` | 用户登录，返回 JWT Token |

### 系统管理

| 方法     | 路径                                    | 说明     |
|--------|---------------------------------------|--------|
| GET    | `/api/system/tenant`                  | 租户列表   |
| GET    | `/api/system/tenant/{id}`             | 租户详情   |
| POST   | `/api/system/tenant`                  | 新增租户   |
| PUT    | `/api/system/tenant/{id}`             | 更新租户   |
| DELETE | `/api/system/tenant/{id}`             | 删除租户   |
| GET    | `/api/system/user?page=1&pageSize=20` | 用户分页查询 |
| GET    | `/api/system/user/{id}`               | 用户详情   |
| POST   | `/api/system/user`                    | 新增用户   |
| PUT    | `/api/system/user/{id}`               | 更新用户   |
| DELETE | `/api/system/user/{id}`               | 删除用户   |

### 设备管理

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

所有业务表包含 `tenant_id BIGINT NOT NULL DEFAULT 1`，为多租户场景预留。表前缀：`sys_`（系统表）、`biz_`（业务表）。

| 表名                   | 说明       |
|----------------------|----------|
| `sys_tenant`         | 系统租户     |
| `sys_user`           | 系统用户     |
| `biz_device`         | 设备       |
| `biz_door`           | 投口（关联设备） |
| `biz_delivery_order` | 投递订单     |
| `biz_clean_order`    | 清运订单     |
| `biz_device_status`  | 设备实时状态   |
| `biz_weight_record`  | 重量变更记录   |

迁移脚本位于 `ecobin-bootstrap/src/main/resources/db/migration/`。

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
  -d '{"username":"admin","password":"admin123"}'
```

### 默认账号

| 用户名   | 密码       | 角色    |
|-------|----------|-------|
| admin | admin123 | 超级管理员 |

## 架构设计

### 多租户预留

当前单租户阶段固定 `tenant_id = 1`，但架构上预留以下机制：

- **数据库层**：所有业务表包含 `tenant_id` 字段，索引覆盖 `(tenant_id, id)`
- **框架层**：`TenantContextHolder`（ThreadLocal）管理租户上下文
- **MyBatis 层**：`TenantInterceptor` 自动注入租户过滤条件

### 认证流程

```
POST /api/system/auth/login → BCrypt 密码校验 → 签发 JWT Token
后续请求 → Authorization: Bearer <token> → JwtAuthenticationFilter 解析 → SecurityContext
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
