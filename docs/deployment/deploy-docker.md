# EcoBin 后端 Docker 部署教学文档（开发联调用）

> 本文目标：把 EcoBin **后端 + MySQL** 用 Docker 部署到一台**有公网 IP 的服务器**上，
> 打通**投递整条链路**（小程序开门 → 后端下发 OneNet → 真实香橙派设备直传 COS → `deliveryComplete`
> 上行 → 后端建单）。这是**开发联调阶段**，不是正式上线。
>
> 本文是**教学型**文档：每一步不仅告诉你「怎么做」，还解释「为什么」和涉及的概念。
> 所有改动**由你自己手动操作**，照着做即可。涉及改 `application.yml` 等的地方都给了「前 → 后」对照。

---

## 0. 先建立几个概念（5 分钟热身）

Docker 部署绕不开这几个词，先把它们和 EcoBin 对应起来，后面就不懵了。

| 概念 | 一句话解释 | 在 EcoBin 里对应什么 |
|------|-----------|--------------------|
| **镜像（Image）** | 一个「打包好的只读模板」，含程序+运行环境（JDK 等），但**不运行** | 我们要构建一个含 `ecobin-bootstrap` fat jar + JRE 21 的镜像 |
| **容器（Container）** | 镜像「跑起来」后的进程实例。镜像是模板，容器是实例（一个镜像可起多个容器） | 后端容器、MySQL 容器 |
| **镜像分层（Layer）/ 缓存** | 镜像由多层叠成，Dockerfile 每条指令一层。没变的层会**复用缓存**，不重复执行 | 把「下载 Maven 依赖」单独放一层 → 改代码时不必重下依赖，构建快很多 |
| **多阶段构建（multi-stage）** | 一个 Dockerfile 里分「构建阶段」和「运行阶段」。构建阶段用重型镜像（含 Maven）编译，**只把产物 jar 拷到**轻量运行镜像 | 构建用 `maven` 镜像（约 800MB+），最终镜像只含 `JRE + jar`（小很多），且不泄露源码 |
| **Volume（数据卷）** | 容器内的数据默认随容器删除而丢失；volume 是「挂在容器外的持久存储」 | MySQL 的数据目录挂 volume，容器重建数据库数据不丢 |
| **docker compose** | 用一个 `docker-compose.yml` 文件**一次性编排多个容器**（这里是 后端 + MySQL），管理它们的网络、依赖、启动顺序 | 一条 `docker compose up` 同时起两个容器 |
| **Compose 网络 / 服务名** | compose 会把同一文件里的服务放进**同一虚拟网络**，服务之间**用「服务名」当主机名互相访问** | 后端连数据库不写 `localhost`，而是写 `mysql`（MySQL 服务名） |
| **`.env` 注入** | 把密钥/配置写在镜像**外部**的 `.env` 文件里，运行时再喂给容器 | 数据库密码、JWT 密钥、OneNet/COS/微信凭证都走 `.env`，**绝不打进镜像** |

> **为什么密钥绝不能打进镜像？** 镜像会被推送、分发、缓存，分层内容可被 `docker history` 等还原。
> 密钥一旦进了某一层，等于公开了。正确做法是密钥只存在于服务器上的 `.env` 文件，运行时挂载进容器。

---

## 1. 部署前置检查

在**服务器**上依次确认（下面命令在服务器的终端里跑）：

```bash
# 1) 确认装了 Docker（建议 20.10+），且 compose 是内置子命令（注意是 "docker compose" 不是老的 "docker-compose"）
docker --version
docker compose version

# 2) 确认当前用户能跑 docker（否则每条命令前加 sudo，或把用户加入 docker 组）
docker ps
```

若没装 Docker，Ubuntu/Debian 可参考官方一键脚本：`curl -fsSL https://get.docker.com | sh`（生产环境请按官方文档逐步装）。

**网络要求**（这次链路能不能通的关键）：
- **入站**：服务器防火墙/云安全组放行 **8080**（后端端口）。小程序、你的浏览器要能访问 `http://<公网IP>:8080`。
- **出站**（容器默认能出站，一般无需额外配置，但若服务器有严格出站策略需放行）：
  - OneNet 北向 MQ：`iot-north-mq.heclouds.com:6651`（上行收设备事件）
  - OneNet API：`iot-api.heclouds.com:443`（下行发开门指令）
  - 腾讯云 COS 域名（设备直传图片，由设备↔COS，但后端换 STS 也要出站到 COS STS 接口）

**代码**：把项目代码拉到服务器（`git clone` 你的仓库到某目录，例如 `/opt/ecobin`），后续所有命令在这个目录里执行。

---

## 2. 第一步：收口 `application.yml`（让 `.env` 真正生效）

### 为什么必须改

现在 `ecobin-bootstrap/src/main/resources/application.yml` 里，数据库连接、JWT 密钥、AES 密钥、微信凭证
**全是写死的明文**。问题有二：
1. 这些值被**编译进 jar**（jar 在镜像里），改不动、且密钥泄露。
2. Docker 里数据库主机名是 `mysql`（服务名）不是 `localhost`，写死的 `localhost` 根本连不上容器里的库。

解决办法：把这些值改成 **`${envKey:本地默认值}`** 占位语法。它的含义是——

> `${dbUrl:jdbc:mysql://localhost:3306/...}`：优先读名为 `dbUrl` 的外部配置（来自 `.env` 或环境变量）；
> **读不到就用冒号后面的默认值**。

这样一举两得：
- **你本地用 IDEA 直接跑**（没设 `dbUrl`）→ 用默认值连 `localhost`，开发体验不变。
- **Docker 容器里**（`.env` 提供了 `dbUrl=jdbc:mysql://mysql:3306/...`）→ 被 `.env` 覆盖，连容器里的库。

> `onenet.*` 和 `cos.*` 已经是这种占位写法了（见 yml 里的 `${cosSecretId:}` 等），无需改。本步只补未收口的几项。

### 具体改法（前 → 后对照）

打开 `ecobin-bootstrap/src/main/resources/application.yml`，按下面四处修改：

**① 数据源**

```yaml
# 改前
  datasource:
    url: jdbc:mysql://localhost:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=Asia/Shanghai&createDatabaseIfNotExist=true
    username: root
    password: 123456
    driver-class-name: com.mysql.cj.jdbc.Driver
```

```yaml
# 改后
  datasource:
    url: ${dbUrl:jdbc:mysql://localhost:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=Asia/Shanghai&createDatabaseIfNotExist=true}
    username: ${dbUsername:root}
    password: ${dbPassword:123456}
    driver-class-name: com.mysql.cj.jdbc.Driver
```

**② JWT 密钥**

```yaml
# 改前
jwt:
  secret: EcoBin_JWT_Secret_Key_2026_Must_Be_256_Bits_Long_Change_In_Production!!
  expiration: 86400000
```

```yaml
# 改后
jwt:
  secret: ${jwtSecret:EcoBin_JWT_Secret_Key_2026_Must_Be_256_Bits_Long_Change_In_Production!!}
  expiration: 86400000
```

**③ 应用 AES 密钥**（用于加密 `sys_tenant.miniapp_secret` 等敏感字段）

```yaml
# 改前
app:
  crypto:
    aes-key: EcoBin_AES_Key_2026_Change_In_Production
```

```yaml
# 改后
app:
  crypto:
    aes-key: ${appAesKey:EcoBin_AES_Key_2026_Change_In_Production}
```

**④ 微信小程序凭证**（当前完全没接 `.env`，是 `your_appid/your_secret` 占位；联调小程序登录 `code2session` 必须填真实值）

```yaml
# 改前
wechat:
  miniapp:
    appid: your_appid
    secret: your_secret
```

```yaml
# 改后
wechat:
  miniapp:
    appid: ${wechatAppid:}
    secret: ${wechatSecret:}
```

> 注意 ④ 默认值留空（`:` 后什么都不写）。因为没有合理的开发默认，必须由 `.env` 提供真实小程序 AppID/Secret。

### （推荐）顺手放行 actuator 健康检查

后端引入了 `spring-boot-starter-actuator`，它提供 `/actuator/health` 健康检查端点。但当前
`ecobin-framework/.../config/SecurityConfig.java` 的规则是 `anyRequest().authenticated()`，
意味着 `/actuator/health` **也要求登录**，没带 token 直接访问会返回 **401**——没法用来做健康探测。

打开 `SecurityConfig.java`，在 `authorizeHttpRequests(...)` 里、`/api/system/auth/**` 那行附近，加一行放行：

```java
// 认证接口放行
.requestMatchers("/api/system/auth/**").permitAll()
// 健康检查放行（部署/监控用，不含敏感信息）
.requestMatchers("/actuator/health").permitAll()
```

> 为什么只放行 `/actuator/health` 而不是整个 `/actuator/**`？因为 actuator 还有 `env`、`beans` 等
> 会暴露配置细节的端点，全放行不安全。只放 `health` 足够做存活探测。
>
> 这步是**可选**的——如果你不想动 Security 代码，跳过即可，后面我会给「不依赖 actuator」的验证方式。

---

## 3. 第二步：准备 `.env`（密钥从这里注入）

### 概念

`.env` 是一个 `KEY=VALUE` 的纯文本文件，放在**项目根目录**。EcoBin 的 `application.yml` 顶部已经配置了：

```yaml
spring:
  config:
    import: optional:file:./.env[.properties]
```

意思是：启动时把当前工作目录下的 `.env` 当作配置文件读进来。`optional:` 表示文件不存在也不报错。
之前那些 `${cosSecretId:}` 占位，值就来自这个 `.env`。

> `.env` 已经在 `.gitignore` 里（含密钥，**绝不提交到 git**）。我们会另建一个 `.env.example` 模板（不含真值）提交，方便协作和记录有哪些 key。

### 3.1 建 `.env.example`（模板，提交到 git）

在**项目根目录**新建 `.env.example`，内容如下（**全是占位值**，真值绝不写进这个文件）：

```properties
# ===== 数据库（Docker 容器内）=====
# 注意 host 用 compose 服务名 mysql，不是 localhost；端口是容器内端口 3306
dbUrl=jdbc:mysql://mysql:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=Asia/Shanghai&createDatabaseIfNotExist=true
dbUsername=root
# 这个密码必须与 docker-compose.yml 里 MySQL 的 MYSQL_ROOT_PASSWORD 完全一致！
dbPassword=换成你的开发数据库密码

# ===== JWT / 加密密钥 =====
jwtSecret=换成一个足够长的随机串（至少32字节，HS256 需要）
appAesKey=换成你的AES密钥

# ===== 微信小程序 =====
wechatAppid=你的小程序AppID
wechatSecret=你的小程序Secret

# ===== 腾讯云 COS（设备直传图片用的 STS 临时密钥来源）=====
cosSecretId=你的COS SecretId
cosSecretKey=你的COS SecretKey
cosRegion=ap-xxx
cosBucketName=你的桶名-appid
cosBaseUrl=https://你的桶访问域名
cosDurationSeconds=1800

# ===== 中国移动 OneNet =====
# 上行（北向 MQ 订阅）
iotAccessId=你的OneNet北向MQ AccessId
iotSecretKey=你的OneNet北向MQ SecretKey
iotSubscriptionName=你的订阅名
# 下行（物模型服务调用，发开门指令）—— 已就绪
onenetProductId=你的产品ID
onenetAccessKey=你的产品AccessKey
```

### 3.2 在服务器上建真正的 `.env`

```bash
# 在项目根目录，从模板拷一份，然后把每个值改成真实值
cp .env.example .env
vim .env     # 或 nano .env，逐项填真值
```

> 务必检查 `.env` 没被 git 跟踪：`git status` 不应出现 `.env`（应只出现 `.env.example`）。

---

## 4. 第三步：写 `Dockerfile`（怎么把项目变成镜像）

在**项目根目录**新建文件 `Dockerfile`（无扩展名），内容如下。**注释逐行解释**，照抄即可：

```dockerfile
# ========== 构建阶段：用带 Maven 的镜像编译打包 ==========
# 用官方 maven 镜像，内置 Maven + JDK 21，专门用来编译（这一阶段的产物只取一个 jar，镜像本身不保留）
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build

# 先只拷贝所有 pom.xml（父 + 各模块），单独跑一次依赖下载。
# 为什么？Docker 分层缓存：只要这些 pom 没变，"下载依赖" 这一层就命中缓存，
# 后面你改 Java 代码重新构建时，不会重新下载几百兆依赖，构建快很多。
COPY pom.xml .
COPY ecobin-common/pom.xml        ecobin-common/
COPY ecobin-framework/pom.xml     ecobin-framework/
COPY ecobin-module-system/pom.xml ecobin-module-system/
COPY ecobin-module-device/pom.xml ecobin-module-device/
COPY ecobin-module-business/pom.xml ecobin-module-business/
COPY ecobin-bootstrap/pom.xml     ecobin-bootstrap/
# 预下载依赖到本地仓库。多模块项目里 go-offline 偶尔会因模块间依赖未安装而报警告，
# 故加 "|| true" 让它即使不完整也不中断（缺的依赖会在下面 package 时补下）。
RUN mvn -B dependency:go-offline || true

# 再拷贝全部源码，做全量 clean package。
# 为什么 clean 全量？EcoBin 是多模块，改了非 bootstrap 模块如果只增量编译，
# spring-boot:run/打包可能用到旧的模块产物（曾经踩过"登录莫名 401"的坑）。
# 在干净的容器里 clean package 一把全量构建，彻底规避这个问题。
COPY . .
RUN mvn -B clean package -DskipTests

# ========== 运行阶段：只要一个 JRE + 那个 jar ==========
# 用只含 JRE（不含编译器/Maven）的轻量镜像，最终镜像体积小、攻击面小、不含源码。
FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

# 从构建阶段把打好的可执行 fat jar 拷过来（用通配符避免写死版本号 0.0.1-SNAPSHOT）。
# Spring Boot 的 spring-boot-maven-plugin 会在 target 下生成可直接 java -jar 运行的 fat jar。
COPY --from=build /build/ecobin-bootstrap/target/ecobin-bootstrap-*.jar /app/app.jar

# 声明容器对外暴露 8080（仅文档性质，真正映射在 compose 的 ports 里）
EXPOSE 8080

# 容器启动就跑这个 jar。工作目录是 /app，所以 application.yml 里的 ./.env 指向 /app/.env
# （我们会在 compose 里把宿主机 .env 挂载到 /app/.env）。
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
```

---

## 5. 第四步：写 `.dockerignore`（哪些东西不要进镜像）

在**项目根目录**新建 `.dockerignore`。作用：构建镜像时，`COPY . .` 会把当前目录发送给 Docker（叫「构建上下文」）。
`.dockerignore` 里列的东西**不会被发送/拷入镜像**——既保护机密，又减小上下文、加快构建。

```gitignore
# 构建产物（容器里会重新编译，宿主机的 target 不要带进去，避免污染/旧产物）
target/
**/target/

# 机密文件！绝不进镜像（.env 在运行时通过 volume 挂载，不打进镜像层）
.env
hardware/

# 与后端镜像无关的大目录
frontend/
test_report/
docs/

# 杂项
*.zip
.git/
.idea/
*.iml
```

> 重点：`.env` 和 `hardware/`（含设备密钥、COS 永久密钥）**必须**在这里排除。
> `hardware/` 已被 git 忽略，但 git 忽略 ≠ docker 忽略，两个机制独立，必须各自配置。

---

## 6. 第五步：写 `docker-compose.yml`（编排 后端 + MySQL）

在**项目根目录**新建 `docker-compose.yml`。**逐段解释**：

```yaml
services:
  # ---------- MySQL 数据库容器 ----------
  mysql:
    image: mysql:8.4                 # 用官方 MySQL 8.4 镜像
    container_name: ecobin-mysql
    environment:
      # 首次启动时，MySQL 会用这个密码初始化 root，并自动创建名为 ecobin 的库
      MYSQL_ROOT_PASSWORD: 换成你的开发数据库密码   # 必须与 .env 里的 dbPassword 一致！
      MYSQL_DATABASE: ecobin
      TZ: Asia/Shanghai
    # 设字符集为 utf8mb4，支持中文和 emoji，避免乱码
    command: --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
    volumes:
      # 把数据库数据目录挂到名为 ecobin-mysql-data 的 volume，容器删了数据还在
      - ecobin-mysql-data:/var/lib/mysql
    healthcheck:
      # 健康检查：反复 ping 数据库，直到能连上才算 healthy
      # 这是关键——后端必须等数据库就绪后再启动，否则 Flyway 迁移会连不上库直接启动失败
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-uroot", "-p换成你的开发数据库密码"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped          # 容器异常退出自动重启（手动 stop 不重启）

  # ---------- 后端应用容器 ----------
  backend:
    build: .                         # 用当前目录的 Dockerfile 构建镜像
    container_name: ecobin-backend
    depends_on:
      mysql:
        condition: service_healthy   # 等 mysql 健康检查通过后，才启动 backend
    ports:
      - "8080:8080"                  # 宿主机 8080 -> 容器 8080，外部通过 公网IP:8080 访问
    volumes:
      # 把宿主机项目根的 .env 以只读方式挂到容器的 /app/.env
      # 这样密钥不进镜像、改 .env 不用重建镜像，application.yml 的 ./.env 正好读到它
      - ./.env:/app/.env:ro
    restart: unless-stopped

# 声明上面用到的具名 volume
volumes:
  ecobin-mysql-data:
```

> **三个一致性要点，错了就连不上：**
> 1. compose 里 `MYSQL_ROOT_PASSWORD`、healthcheck 里的 `-p密码`、`.env` 里的 `dbPassword`，**三处密码必须完全一致**。
> 2. `.env` 里 `dbUrl` 的主机名是 **`mysql`**（compose 服务名），不是 `localhost`。容器之间靠服务名通信。
> 3. `.env` 里 `dbUrl` 端口是 **3306**（MySQL 容器内部端口）。我们没把 3306 映射到宿主机（不需要外部直连库），后端在同一虚拟网络里直接连 `mysql:3306`。

> **想从宿主机用客户端连库调试？** 在 mysql 服务下加 `ports: - "3306:3306"` 即可把库暴露到宿主机（开发期方便，上线应去掉）。

---

## 7. 第六步：构建并启动

在项目根目录（有 `docker-compose.yml` 的地方）执行：

```bash
# 构建镜像 + 启动所有容器，-d 表示后台运行，--build 表示先重新构建后端镜像
docker compose up -d --build
```

首次会比较久（要下载基础镜像 + Maven 依赖 + 编译）。常用命令：

```bash
docker compose ps                 # 看容器状态（backend / mysql 是否 Up、healthy）
docker compose logs -f backend    # 实时跟后端日志（-f = follow，Ctrl+C 退出只是停止看日志，不停容器）
docker compose logs -f mysql      # 看数据库日志
docker compose restart backend    # 只重启后端（比如改了 .env 后）
docker compose down               # 停止并删除容器（数据 volume 保留）
docker compose down -v            # 连数据 volume 一起删（慎用，数据库数据会清空）
docker compose up -d --build      # 改了代码/Dockerfile 后，重新构建并启动
```

> **改了 `.env` 之后**：`.env` 是运行时挂载的，但 Spring 只在**启动时**读一次，所以改完要
> `docker compose restart backend` 让它重新读。
>
> **改了 Java 代码 / Dockerfile 之后**：要 `docker compose up -d --build` 重新构建镜像。

---

## 8. 验证：投递整条链路是否打通

### 8.1 应用起来了吗

```bash
docker compose ps          # backend 应为 Up，mysql 应为 Up (healthy)
docker compose logs backend | grep -i flyway     # 应看到 Flyway 迁移 V1 → V13 执行成功的日志
docker compose logs backend | grep -i -E 'onenet|pulsar|consumer'  # 应看到 OneNet 北向 MQ 消费者连接成功
docker compose logs backend | tail -50           # 看是否有 "Started ...Application" 启动完成
```

- **Flyway**：首次启动会对空库执行 `V1__init_schema.sql` 到 `V13__add_device_session.sql`，自动建好所有表。
  日志里能看到迁移列表。若失败，多半是数据库没连上（看 §10 排错）。
- **OneNet 消费者**：`.env` 里 `iotAccessId/iotSecretKey/iotSubscriptionName` 齐全且非 test 环境，
  消费者才会启动并连 `pulsar+ssl://iot-north-mq.heclouds.com:6651`。日志里会有连接相关输出
  （pulsar-client 在 Java 21 会打两条无害 WARN，不影响连接，忽略即可）。

### 8.2 健康/连通性

```bash
# 若你做了 §2 的 actuator 放行：
curl http://localhost:8080/actuator/health
# 期望返回 {"status":"UP"}

# 若没放行 actuator，用登录接口探活（它是 permitAll 的，能拿到响应就说明应用在跑）
curl -i http://localhost:8080/api/system/auth/login
# 返回 405（方法不允许，因为该接口要 POST）或 400 都说明应用活着并在路由，而不是连接被拒
```

> 然后从浏览器/Postman 用 `http://<公网IP>:8080` 跑一次登录，确认外网能进来。

### 8.3 投递全链路（端到端）

1. 小程序（开发者工具，**勾选「不校验合法域名/HTTPS 证书」**，详见 §9）把后端地址指向 `http://<公网IP>:8080`，发起「开投递门」。
2. 看后端日志：应有调用 OneNet **下行** `openDeliveryDoor` 服务调用的记录（因为 `onenetProductId/onenetAccessKey` 已就绪，是**真发**指令，不再是占位日志）。
3. 真实香橙派设备收到指令 → 拍照直传 COS → 通过 `deliveryComplete` 事件上报 4 张照片 URL + 参数。
4. 看后端日志：北向 MQ **上行**消费到 `deliveryComplete`，后端**建单**并回填照片 URL。
5. 用浏览器打开日志里那几个照片 URL，能正常显示即为通。

---

## 9. ⚠ 关于小程序「分享给别人真机测试」（必须先知道的限制）

这是这次部署**无法靠 Docker 解决**的一点，提前说清楚预期：

微信小程序有「服务器合法域名」校验，规则是：
- **开发版**（开发者工具里编译/预览的版本，含你自己扫码的真机预览）：可以在开发者工具里勾选
  **「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」**，这样能直接用
  `http://<公网IP>:8080` 联调。**这次投递链路联调就靠这个**。
- **体验版 / 正式版**（你说的「分享给其他人，在他们真机上跑」）：**无法关闭**域名校验，
  `request/uploadFile/downloadFile` 的域名**必须是 HTTPS + 已 ICP 备案**的域名。
  公网 IP、带端口、自签证书、未备案域名**统统会被拦**。

你的服务器目前**有公网 IP 但无备案域名**，所以：
- ✅ 能做到：你自己（或被加为开发成员的人）在开发者工具/真机预览下，联调打通投递全链路。
- ❌ 暂时做不到：把体验版分享给任意非开发成员，在其真机上正常请求后端。

要解决这一步，后续需要：**备案一个域名 → 配 HTTPS 证书（如 Let's Encrypt）→ 用 Nginx 反向代理到
8080 → 在小程序后台填入这个 HTTPS 域名**。这属于「上线准备」，不在本次开发部署范围内。

---

## 10. 常见排错

| 现象 | 可能原因 / 排查 |
|------|----------------|
| 后端启动即退出，日志报数据库连接失败 | `.env` 的 `dbUrl` 主机名写成了 `localhost`（应为 `mysql`）；或三处密码不一致；或没等 mysql healthy（确认 compose 里 `depends_on: condition: service_healthy`） |
| Flyway 报错 / 迁移失败 | 库连不上（同上）；或库非空且与迁移历史冲突（开发期可 `docker compose down -v` 清空 volume 重来） |
| 日志没有 OneNet 消费者连接 | `.env` 缺 `iotAccessId/iotSecretKey/iotSubscriptionName` 任一 → 消费者不启动；检查这三项是否填全 |
| 开投递门后日志显示「占位/未真发」 | `onenetProductId` 或 `onenetAccessKey` 没填（缺失时下行只记日志不真发）；填上后 `restart backend` |
| 外网访问不了 8080 | 云服务器**安全组/防火墙**没放行 8080；或 compose 没写 `ports: - "8080:8080"` |
| `curl /actuator/health` 返回 401 | 没做 §2 的放行；要么加放行那行，要么改用 §8.2 的登录接口探活 |
| 改了 `.env` 没生效 | Spring 只在启动时读 `.env`，需 `docker compose restart backend` |
| 改了代码没生效 | 需 `docker compose up -d --build` 重新构建镜像（仅 restart 不会重新编译） |
| 镜像构建很慢 | 首次正常（下依赖+编译）；之后只要 pom 没变，依赖层走缓存会快很多 |

---

## 11. 上线前 TODO（本次开发部署不做，仅登记）

这次是开发联调，下面这些**上线前**必须补，现在可以先不管：

- [ ] 域名备案 + HTTPS 证书 + Nginx 反向代理（解决小程序体验版/正式版域名校验）
- [ ] 数据库：不用 root，建独立的限权账号；root 密码用强随机；考虑不把 3306 暴露到宿主机
- [ ] 密钥：`jwtSecret`/`appAesKey` 换成足够长的强随机值（别再用 `..._Change_In_Production` 默认）
- [ ] 日志：`application.yml` 的 `logging.level.org.enveloping.ecobin` 由 `DEBUG` 降为 `INFO`，
      并关掉 MyBatis 的 SQL 打印（`log-impl` 改掉或调级），避免刷屏和泄露数据
- [ ] Flyway：生产建议预先建库，`createDatabaseIfNotExist` 可去掉
- [ ] 给后端容器配资源限制、日志轮转，必要时上反向代理统一入口
```
