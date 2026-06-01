# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

EcoBin是一个智慧环保回收箱后端管理系统，基于 Spring Boot 4.0.6 + Java 21 + Maven 多模块构建。
当前实现单租户功能，架构上预留了多租户扩展能力（所有业务表含 `tenant_id`，框架层有 `TenantContextHolder` 和 MyBatis 租户拦截器）。

`docs/references/` 目录下是小智环保平台的参考对接文档（中文），仅供参考，不作为重大决策依据。

## 构建与运行命令

```bash
# 编译全部模块
./mvnw compile

# 仅编译单个模块
./mvnw compile -pl ecobin-module-system

# 运行测试
./mvnw test

# 运行单个测试类
./mvnw test -Dtest=EcoBinApplicationTests -pl ecobin-bootstrap

# 打包
./mvnw package -DskipTests

# 启动应用
./mvnw spring-boot:run -pl ecobin-bootstrap

# 清理
./mvnw clean
```

## 技术栈

| 组件 | 版本/说明 |
|------|----------|
| Java | 21 |
| Spring Boot | 4.0.6 |
| Spring MVC | spring-boot-starter-webmvc（REST API） |
| Spring Security | spring-boot-starter-security（认证鉴权） |
| JWT | jjwt 0.12.6（无状态 Token） |
| MyBatis Plus | mybatis-plus-spring-boot4-starter 3.5.16（ORM，Spring Boot 4.x 需用 boot4-starter） |
| MySQL | mysql-connector-j（生产数据库） |
| H2 | 内存数据库（测试环境） |
| Flyway | spring-boot-starter-flyway + flyway-mysql（Spring Boot 4.x 必须用 starter 触发自动配置） |
| Lombok | 编译期注解处理器 |
| Jakarta Validation | 参数校验（@Valid, @NotBlank 等） |
| 测试 | JUnit 5 + spring-boot-starter-webmvc-test + H2 |

## Maven 多模块结构

```
EcoBin/
├── pom.xml                          # 父 POM（dependencyManagement + module 声明）
├── ecobin-common/                   # 公共模块：枚举、BaseEntity、Result/PageResult、BusinessException
├── ecobin-framework/                # 框架模块：Security + JWT、TenantContextHolder、全局异常处理
├── ecobin-module-system/            # 系统管理：租户 CRUD、用户管理、登录认证
├── ecobin-module-device/            # 设备管理：设备 CRUD、投口管理
├── ecobin-module-business/          # 业务模块：投递订单、清运订单、统计
└── ecobin-bootstrap/                # 启动模块（空壳）：仅组装依赖、配置、Flyway 迁移、启动入口
```

### 模块依赖链

```
ecobin-bootstrap → 组装所有模块
  ├── ecobin-module-system  → ecobin-framework → ecobin-common
  ├── ecobin-module-device  → ecobin-framework
  └── ecobin-module-business → ecobin-framework
```

### 各模块包结构（以 ecobin-module-device 为例）

```
org.enveloping.ecobin.device
├── controller/          # REST 控制器
├── service/
│   └── impl/            # 服务接口与实现
├── mapper/              # MyBatis Mapper 接口
├── entity/              # 实体类（POJO）
└── dto/                 # 数据传输对象（按需）

resources/mapper/        # MyBatis XML 映射文件
```

## 数据库设计要点

- 所有业务表包含 `tenant_id BIGINT NOT NULL DEFAULT 1`
- 表前缀：`sys_`（系统表）、`biz_`（业务表）
- 命名风格：下划线（数据库） ↔ 驼峰（Java），MyBatis 已配置 `map-underscore-to-camel-case: true`
- 迁移脚本：`ecobin-bootstrap/src/main/resources/db/migration/V1__init_schema.sql`
- 测试用 H2 内存数据库（`application-test.yml`），Flyway 在测试环境关闭

## 架构约定

- **包路径**：所有代码在 `org.enveloping.ecobin` 下，子模块在对应子包（如 `.system`、`.device`）
- **Mapper**：接口 + XML 混合，XML 放在各模块的 `resources/mapper/` 下
- **统一响应**：使用 `Result<T>` 和 `PageResult<T>`（在 `ecobin-common` 中定义）
- **异常处理**：业务异常抛 `BusinessException`，由 `GlobalExceptionHandler` 统一捕获
- **租户**：`TenantContextHolder`（ThreadLocal），单租户阶段默认 tenant_id=1
- **认证**：`POST /api/system/auth/login` 获取 JWT Token，后续请求携带 `Authorization: Bearer <token>`
- **分页**：默认 page=1, pageSize=20，最大 200
- **Lombok**：使用 `@Data`、`@RequiredArgsConstructor`、`@Slf4j` 减少样板代码
- **测试**：`@SpringBootTest` + `@ActiveProfiles("test")` 使用 H2 内存数据库
