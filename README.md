# EcoBin 智慧环保回收箱管理系统

基于 Spring Boot 4.0.6 + Java 21 的智慧环保回收箱后端管理系统，提供设备管理、投递订单、清运订单、数据统计等核心功能。已实现**多租户数据隔离**、三类登录主体（平台管理员 / 租户 / 小程序用户）、基于角色的接口鉴权，以及角色/状态变更后的 JWT 强制失效。文档入口见 [`docs/README.md`](docs/README.md)，详细权限设计见 [`docs/architecture/permission-design.md`](docs/architecture/permission-design.md)。

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
| Flyway          | —      | 独立 Maven 迁移作业；不进入后端运行制品                         |
| Lombok          | —      | 编译期注解处理器                                     |
| Maven           | —      | 多模块构建                                        |

> **当前阶段**：目标数据库迁移已推进到 V25，运行制品不包含 Flyway 运行库或迁移脚本，
> 只执行只读 epoch guard；目标迁移必须由一次性迁移作业执行。V25 将满溢准入切换为设备
> 状态变化被动上报，只有当前袋明确 `FULL` 才阻止下一次投递，详见
> [`docs/architecture/fullness-reporting-v25.md`](docs/architecture/fullness-reporting-v25.md)。

## 项目结构

```
EcoBin/
├── pom.xml                          # 父 POM（依赖管理 + 模块声明）
├── ecobin-common/                   # 公共值对象、响应和异常
├── ecobin-framework/                # Security、JWT、租户隔离和基础设施
├── ecobin-module-identity/          # 平台、租户、机构和登录身份
├── ecobin-module-device/            # 设备、投口和设备命令公开端口
├── ecobin-module-funds/             # 钱包与资金边界
├── ecobin-module-recycling/         # 投递、清运和回收边界
├── ecobin-module-operations/        # 审计、可靠任务与运营边界
├── ecobin-integration/              # OneNet、COS、微信及 Fake 适配器
└── ecobin-bootstrap/                # 应用组装、epoch guard 与启动入口
```



### 模块依赖关系

```
ecobin-bootstrap → integration → operations → recycling → funds
                 ↘ device / identity → framework → common
```

跨业务模块只通过各模块 `.api` 包协作；完整约束见
[`CLAUDE.md`](CLAUDE.md) 和
[`docs/planning/detailed-design/01-foundation-modules-database.md`](docs/planning/detailed-design/01-foundation-modules-database.md)。

## API 接口

所有接口统一前缀 `/api`，认证接口除外，其余接口需携带 JWT Token（`Authorization: Bearer <token>`）。各接口按角色鉴权，规则见 [`docs/architecture/permission-design.md`](docs/architecture/permission-design.md) §9。

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

目标迁移源码固定在
`ecobin-bootstrap/src/main/resources/db/p0-migration/`，V1～V10 建立 83 张领域表，
V10 只写 71 行环境无关权限参考数据。该目录仅由 Flyway Maven 插件从文件系统读取；
运行制品不包含 Flyway 运行库，也不包含目标或旧纪元的任何迁移脚本。
旧 V1～V14 只属于 H-01 保存的旧栈恢复制品，历史结构说明见
[`docs/architecture/database-design.md`](docs/architecture/database-design.md)；目标结构见
[`docs/planning/database-design-draft.md`](docs/planning/database-design-draft.md)。

## 快速开始

### 环境要求

- JDK 21+
- MySQL 8.4.x
- Maven 3.8+（或使用项目自带的 `mvnw`）
- Docker（运行真实 F-07 数据库验收时）

### 1. 编译并运行快速测试

```bash
./mvnw test
```

### 2. 执行 F-07 真实 MySQL 8.4 验收

先按多模块约束生成最新制品，再运行临时数据库矩阵：

```powershell
mvn.cmd install -DskipTests
.\tools\database\verify-f07-bootstrap.ps1
```

脚本使用随机密码和随机容器名，结束后自动清理；它验证正确 V11 可就绪、错误纪元
严格失败、启动前后 schema/history 不变、无业务实例 seed、Fake 入站/出站闩锁，
以及运行身份不能执行 DDL 或删除事实。

### 3. 一键切换本地运行模式

本地数据库和渠道密钥统一写在
`.ecobin/application-local-secrets.yml`。该文件由 Git 和 Docker build context
忽略，后端会从仓库根自动导入；日常切换模式不用修改任何密钥。

已有旧 `.env` 的工作区只需迁移一次，脚本不会把值打印到终端：

```powershell
.\tools\development\migrate-dotenv-to-local-yaml.ps1
```

全新工作区则复制
`tools/development/application-local-secrets.example.yml` 到上述路径后直接填写。YAML
保持顶层 `key: 'value'` 结构，避免同一个配置在脚本、IDEA 和 Spring 之间重复维护。

IDEA 右上角运行配置直接选择：

- `EcoBin Backend - Local Fake`：屏蔽本地 YAML 中的 OneNet/COS/微信配置，关闭真实
  MQ，允许开发默认管理员；
- `EcoBin Backend - Local Real`：使用本地 YAML 中完整真实配置，自动关闭开发默认
  管理员初始化器。

终端使用同一组 profile：

```powershell
# 默认会先安装最新多模块制品
.\tools\development\run-backend.ps1 -Mode Fake
.\tools\development\run-backend.ps1 -Mode Real

# 确认模块制品未变化时可以跳过构建
.\tools\development\run-backend.ps1 -Mode Fake -SkipBuild
```

脚本自动寻找 Java 21，并强制让 Spring Boot 以仓库根作为工作目录，因此终端与 IDEA
读取同一份本地 YAML。若机器上无法自动发现 JDK，可传一次
`-JavaHome <jdk-21目录>`。

`local-fake` 和 `local-real` profile 只决定外联边界，不保存任何凭证。真实值仍只在
Git 忽略的本地 YAML，不会被复制到共享 IDEA 配置或运行脚本。Spring 不再读取
`.env`；旧文件只可作为迁移回退材料。
`local-real` 不会删除数据库里已经存在的开发账号，只是不再创建或重置它；该 profile
只用于本机受控渠道联调，不能作为生产部署 profile。

若要运行根 Docker Compose，也使用同一份 YAML：

```powershell
.\tools\development\run-local-stack.ps1
.\tools\development\run-local-stack.ps1 -Action Logs
.\tools\development\run-local-stack.ps1 -Action Down
```

包装脚本只在当前进程内把 Compose 所需值注入环境，不创建第二份配置文件，也不输出
密钥。生产部署继续使用 `/run/secrets`，不读取本地 YAML。

首次启动前必须由环境供应者完成以下工作：

- 以一次性 `ecobin_schema_owner` 在全新 MySQL 8.4 目标库执行 V1～V11；
- 创建并限权 `ecobin_app`，只读 Flyway history 且不授予 DDL、TRIGGER 或 owner 权限；
- 在本地 YAML 中提供数据库、JWT/加密密钥，以及 Real 模式需要的渠道配置。

必须提供 `dbUrl`、`dbUsername=ecobin_app`、`dbPassword`；配置没有默认 root 或
自动建库，运行制品在物理上不具备 baseline/migrate 能力。就绪探针为
`GET /actuator/health/readiness`。

### 微信小程序配置

`local-fake` 只接受 `fake:` 前缀的测试 code，并在最终属性绑定层屏蔽 OneNet、COS、
微信真实配置；`local-real` 要求这些渠道配置完整，否则直接启动失败。生产仍必须使用
正式环境的密钥注入和 profile，不能把本地 profile 当作部署配置。

### 默认账号

目标迁移本身不写入账号。启用 `defaultPlatformAdminEnabled=true` 后，应用只在
`iam_platform_admin` 完全为空时幂等创建受保护的默认平台管理员，规范化登录名为
`enveloping`；初始密码必须通过 Git 之外的 `defaultPlatformAdminPassword` 提供。
已有管理员时绝不追加账号或重置密码；V50 会把升级前唯一且登录名匹配的现有账号标记为
默认管理员，并保留其 UID、密码摘要和版本。

默认管理员可以创建、停用、启用、永久逻辑删除普通平台管理员，也可以重置普通管理员
密码；普通管理员只能修改自己的密码。默认管理员本身不能被停用、删除或由他人重置。
生产通过 `/etc/ecobin/secrets/default-platform-admin-password` 挂载密码，禁止把它写入
`runtime.env` 或版本库。
租户、机构、工作人员、设备、袋、钱包和业务配置仍不自动创建；完整试点 seed
属于 F-12。

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

# 编译单个模块及依赖
./mvnw compile -pl ecobin-module-identity -am

# 运行测试
./mvnw test

# 运行 F-07 纯规则测试
./mvnw test -Dtest=P0DatabaseEpochPolicyTest -pl ecobin-bootstrap -am

# 打包（跳过测试）
./mvnw package -DskipTests

# 启动（必须先提供目标 V10 数据源）
./mvnw spring-boot:run -pl ecobin-bootstrap

# 清理
./mvnw clean
```
