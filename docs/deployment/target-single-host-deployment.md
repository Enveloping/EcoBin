# EcoBin 目标单机部署手册

> 适用主机：`115.159.67.35`（Ubuntu 22.04）
> 当前公网入口：`https://www.jinshoubao.com`
> 目标数据库纪元：V34
> 本文只描述目标栈。旧 `ecobin-web`、`ecobin-backend`、`ecobin-mysql`
> 容器和旧数据卷必须继续保留，不能与目标栈交叉连接。

## 1. 目标拓扑

```text
Internet :443
    |
    v
宿主机 Nginx（TLS、唯一公网入口）
    |
    | 127.0.0.1:18080
    v
ecobin-target-web
    |
    | ecobin-target-app
    v
ecobin-target-backend:8080
    |
    | ecobin-target-db（internal）
    v
ecobin-target-mysql84:3306
```

边界如下：

- Web 只发布到宿主机 IPv4 回环地址 `127.0.0.1:18080`；
- 后端不发布 8080，MySQL 不发布 3306；
- Web 只能加入 `ecobin-target-app`，不能加入数据库网络；
- 后端同时加入应用网络和数据库网络；
- 真实客户端地址、原始 HTTPS 协议和主机名由两层 Nginx 连续传递，公网边界会丢弃
  客户端伪造的 `X-Forwarded-For`；
- JAR 和 Web `dist` 只在开发机编译；宿主机只把已校验制品封装为运行时镜像，不安装
  Maven、Node 或项目源码。

相关文件：

- 应用代码修改后的日常重新部署：
  [`application-redeployment-runbook.md`](application-redeployment-runbook.md)
- 配置、密钥与证书总清单：
  [`production-configuration-secrets-certificates.md`](production-configuration-secrets-certificates.md)
- 应用 Compose：
  [`deploy/production/docker-compose.target-app.yml`](../../deploy/production/docker-compose.target-app.yml)
- Compose 非秘密变量模板：
  [`deploy/production/deployment.env.example`](../../deploy/production/deployment.env.example)
- 后端非秘密运行配置模板：
  [`deploy/production/runtime.env.example`](../../deploy/production/runtime.env.example)
- 宿主机 Nginx 站点：
  [`deploy/production/nginx/jinshoubao.com.conf`](../../deploy/production/nginx/jinshoubao.com.conf)
- 本地发布包脚本：
  [`tools/deployment/New-EcobinLocalRelease.ps1`](../../tools/deployment/New-EcobinLocalRelease.ps1)
- 服务器发布安装脚本：
  [`tools/deployment/ecobin-install-local-release.sh`](../../tools/deployment/ecobin-install-local-release.sh)

## 2. 服务器目录和权限

正式部署前建立以下布局：

```text
/etc/ecobin/
├── compose/
│   └── docker-compose.target-app.yml       root:root 0600
├── deployment.env                          root:root 0600；只含镜像和部署参数
├── runtime.env                             root:root 0600；只含非秘密运行参数
├── secrets/                                root:root 0700
│   ├── mysql-root-password                 root:root 0600
│   ├── db-app-password                     root:root 0600
│   ├── db-backup-password                  root:root 0600
│   ├── jwt-secret                          root:root 0600
│   └── ...                                 Real 模式渠道秘密
└── wechatpay/                              root:root 0755
    ├── apiclient_cert.pem                  root:root 0644
    └── pub_key.pem                         root:root 0644

/run/ecobin-secrets/backend/                root:10001 0750；每次启动重新生成
/var/lib/ecobin/releases/<release-id>/      root:root 0750；发布制品和镜像身份记录
```

每个机构的小程序 AppID/AppSecret 都保存在目标数据库
`iam_organization_miniapp` 中，由具备 `miniapp.manage` 权限的人员配置。不存在全局
小程序 AppID/AppSecret 文件，也不再维护机构密钥文件目录。数据库备份因此包含
AppSecret 明文，备份的访问控制、加密和恢复验收必须按秘密数据处理；应用日志和审计
不得记录完整值。

## 3. 本地制品和镜像门禁

当前单机部署不依赖镜像仓库。开发机负责编译，服务器只从精确白名单文件构建运行时
镜像：

```text
Git 工作区（开发机）
  ├─ Java 21 + Maven Wrapper → ecobin-bootstrap 可运行 JAR
  └─ Node 20+ + npm lock     → frontend/web/dist
                 |
                 v
      ecobin-release-<release-id>.tar.gz
                 |
                 v
服务器校验 SHA256SUMS → 保存 /var/lib/ecobin/releases/<release-id>
                      → 构建两张运行时镜像
                      → 记录标签 + 不可变 Docker image ID
```

### 3.1 在开发机生成发布包

正式包要求 Git 工作区干净。脚本默认运行后端测试、`npm ci` 和 Web 构建：

```powershell
pwsh -File .\tools\deployment\New-EcobinLocalRelease.ps1
```

输出位于仓库外发范围的 `release-output/`，包含发布目录、`.tar.gz` 和归档校验文件。
发布包只收集下列白名单内容，不复制源码或密钥：

- `backend/app.jar`、后端运行时 Dockerfile、健康检查脚本；
- `web/dist/`、Web 运行时 Dockerfile、容器内 Nginx 配置；
- Git 提交、工作区干净标记和逐文件 `SHA256SUMS`。

`-SkipTests` 只允许用于已经单独完成同一提交完整测试的发布；`-AllowDirty` 生成的包默认
会被服务器拒绝，只能用于显式允许的非生产排查。

### 3.2 上传并安装发布包

只上传生成的归档及其校验文件，不上传仓库或 Desktop 密钥目录：

```powershell
scp .\release-output\ecobin-release-<release-id>.tar.gz `
    .\release-output\ecobin-release-<release-id>.tar.gz.sha256 `
    ubuntu@115.159.67.35:/tmp/
```

在服务器先验证归档，再解压到独立临时目录：

```bash
cd /tmp
sha256sum --check ecobin-release-<release-id>.tar.gz.sha256
tar -xzf ecobin-release-<release-id>.tar.gz
sudo /usr/local/sbin/ecobin-install-local-release \
  /tmp/ecobin-release-<release-id>
```

安装脚本会再次校验包内每个文件，顺序构建后端/Web 镜像，并原子更新
`/etc/ecobin/deployment.env` 的以下字段：

```text
ECOBIN_IMAGE_MODE=local
ECOBIN_RELEASE_ID=<release-id>
ECOBIN_BACKEND_IMAGE=ecobin-local/backend:<release-id>
ECOBIN_BACKEND_IMAGE_ID=sha256:<本机不可变镜像ID>
ECOBIN_WEB_IMAGE=ecobin-local/web:<release-id>
ECOBIN_WEB_IMAGE_ID=sha256:<本机不可变镜像ID>
```

标签只是方便辨认，不能独立作为信任依据。预检还会要求：标签当前指向的 image ID、
镜像内 Git/制品标签、`/var/lib/ecobin/releases/<release-id>/images.env` 和
`deployment.env` 四方一致。Compose 设置 `pull_policy: never`，不会到公网拉取同名镜像。

服务器首次构建仍需取得 `eclipse-temurin:21-jre` 和 `nginx:alpine` 两个运行时基础
镜像。已经受控导入基础镜像而外网暂时不可用时，可在执行安装脚本前设置
`ECOBIN_PULL_RUNTIME_BASE_IMAGES=false`；这不允许省略最终 image ID 校验。

后端镜像固定以 `10001:10001` 运行，根文件系统只读，只允许写 `/tmp` 的 64 MiB
临时文件系统。

Web 根文件系统同样只读，只给 Nginx 缓存、PID 和临时文件配置小型 tmpfs。

## 4. Fake 模式配置

域名备案、微信支付参数或真实渠道尚未全部就绪时先使用 Fake。`runtime.env` 至少为：

```text
dbUrl=jdbc:mysql://ecobin-target-mysql84:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=UTC
dbUsername=ecobin_app
defaultPlatformAdminEnabled=false
externalMode=fake
onenetSubscriptionEnabled=false
TZ=UTC
```

Fake 模式不能混入微信 AppID、OneNet 产品/订阅、COS 存储桶或微信支付真实配置。
后端使用 `production` Spring Profile，所以即使数据库为空，也不会自动创建弱口令
`admin/admin123` 账号。没有正式 seed 时页面无法登录是正确结果，不能手工绕过。

Fake 模式后端只得到两项运行秘密：

| 持久源 | 容器内 Config Tree 名称 | 用途 |
|---|---|---|
| `/etc/ecobin/secrets/db-app-password` | `/run/secrets/dbPassword` | `ecobin_app` 数据库密码 |
| `/etc/ecobin/secrets/jwt-secret` | `/run/secrets/jwtSecret` | 登录令牌签名 |

MySQL root 和备份密码虽然由启动脚本检查持久源存在，但绝不复制到后端目录。

## 5. Real 模式完整配置

Real 必须一次性满足 OneNet 上下行、COS 和微信支付平台级配置。缺少任何一项时启动
失败，不能以“先启动再补配置”绕过。机构小程序凭证是数据库中的机构级业务配置：
未配置只阻止该机构激活或启用小程序登录，不属于全局启动参数。

### 5.1 非秘密参数

把以下键写入 `/etc/ecobin/runtime.env`，不要写任何 Secret/Key 的值：

| 键 | 示例或要求 |
|---|---|
| `externalMode` | `real` |
| `iotSubscriptionName` | OneNet 服务端订阅名称 |
| `onenetSubscriptionEnabled` | `true` |
| `onenetProductId` | OneNet 产品 ID |
| `cosRegion` | 如 `ap-shanghai` |
| `cosBucketName` | 完整 `bucket-appId` |
| `cosBaseUrl` | 对应 COS HTTPS 域名 |
| `cosDurationSeconds` | 建议 `1800` |
| `wechatPayMchid` | 普通商户号 |
| `wechatPayMerchantSerialNumber` | `apiclient_cert.pem` 的证书序列号 |
| `wechatPayMerchantPrivateKeyPath` | 固定 `/run/secrets/wechatpay/apiclient_key.pem` |
| `wechatPayPublicKeyId` | 微信支付公钥 ID，格式 `PUB_KEY_ID_...` |
| `wechatPayPublicKeyPath` | 固定 `/run/secrets/wechatpay/pub_key.pem` |
| `wechatPayNotifyBaseUrl` | `https://www.jinshoubao.com` |
| `wechatPayTransferSceneId` | 当前设计为 `1010`，变更前需核对商户产品配置 |

`wechatPayNotifyBaseUrl` 只填公开 HTTPS 根地址，不能包含路径、查询参数或片段。后端会
形成两个实际通知地址：

- Native 充值：
  `https://www.jinshoubao.com/api/v1/wechat-pay/notifications/native-payments`
- 商家转账：
  `https://www.jinshoubao.com/api/v1/wechat-pay/notifications/merchant-transfers`

### 5.2 长期秘密

以下文件只能在仓库外写入 `/etc/ecobin/secrets`，owner/mode 必须为
`root:root 0600`：

| 持久文件名 | 暂存后的 Config Tree/文件 |
|---|---|
| `onenet-subscription-access-id` | `/run/secrets/iotAccessId` |
| `onenet-subscription-secret-key` | `/run/secrets/iotSecretKey` |
| `onenet-access-key` | `/run/secrets/onenetAccessKey` |
| `cos-secret-id` | `/run/secrets/cosSecretId` |
| `cos-secret-key` | `/run/secrets/cosSecretKey` |
| `wechatpay-api-v3-key` | `/run/secrets/wechatPayApiV3Key` |
| `wechatpay-merchant-private-key.pem` | `/run/secrets/wechatpay/apiclient_key.pem` |

APIv3 密钥文件必须正好 32 个 UTF-8 字节，不能附带换行。商户私钥必须是未加密、可由
无人值守进程读取的 PEM；其目录和文件权限承担静态保护职责。

### 5.3 微信支付商户证书与公钥

证书放在 `/etc/ecobin/wechatpay`：

- `apiclient_cert.pem`：商户 API 证书，只用于部署时核对商户私钥和配置序列号，不会
  复制给后端；
- `pub_key.pem`：微信支付公钥，复制给后端并与 `wechatPayPublicKeyId` 配对，用于
  API 响应和回调验签。

暂存脚本在接触渠道前验证：

- 商户 API 证书可解析且至少 7 天内不过期，微信支付公钥可解析；
- 商户私钥公钥与 `apiclient_cert.pem` 一致；
- `wechatPayMerchantSerialNumber` 与商户证书序列号一致；
- `wechatPayPublicKeyId` 满足 `PUB_KEY_ID_...` 格式；
- APIv3 密钥字节长度为 32；
- 后端只得到商户私钥和微信支付公钥，不得到商户证书、数据库 root 或备份凭证。

当前实现固定使用微信支付公钥模式，不下载或依赖平台证书。更换微信支付公钥时，必须
把新的 `pub_key.pem` 与对应公钥 ID 作为同一次受控配置变更发布；二者不一致时后端会
拒绝启动或拒绝验签，不能只替换其中一个。

本项目的普通商户号从未使用微信支付平台证书，首次 APIv3 真实接入即使用
`pub_key.pem + PUB_KEY_ID_...`。因此这不是“平台证书迁移到公钥”的灰度切换，
后端会有意拒绝任何非当前公钥 ID 的响应或回调签名。如果未来改用曾经接入
平台证书的其他商户号，必须先在微信商户平台完成公钥切换，再把该商户号交给
本部署使用；本系统不提供平台证书与公钥并行的过渡模式。微信支付公钥的用途和配置见
[微信支付公钥介绍](https://pay.weixin.qq.com/doc/v3/merchant/4012153196.md)。

## 6. 安装脚本和 systemd 单元

将仓库中的文件安装到固定位置，保持 LF 换行：

```bash
sudo install -d -o root -g root -m 0700 /etc/ecobin/compose
sudo install -o root -g root -m 0600 \
  deploy/production/docker-compose.target-app.yml \
  /etc/ecobin/compose/docker-compose.target-app.yml
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-stage-runtime-secrets.sh \
  /usr/local/sbin/ecobin-stage-runtime-secrets
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-production-preflight.sh \
  /usr/local/sbin/ecobin-production-preflight
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-runtime-secret-probe.sh \
  /usr/local/sbin/ecobin-runtime-secret-probe
sudo install -o root -g root -m 0755 \
  tools/deployment/ecobin-install-local-release.sh \
  /usr/local/sbin/ecobin-install-local-release
sudo install -o root -g root -m 0644 \
  tools/deployment/systemd/ecobin-stage-runtime-secrets.service \
  /etc/systemd/system/ecobin-stage-runtime-secrets.service
sudo install -o root -g root -m 0644 \
  tools/deployment/systemd/ecobin-target-app.service \
  /etc/systemd/system/ecobin-target-app.service
sudo systemctl daemon-reload
```

秘密暂存单元排在 Docker 之前，以免服务器重启后 Docker 先创建一个错误 owner 的空
`/run/ecobin-secrets/backend`。应用单元同时依赖 Docker 和秘密暂存，并用 Compose
`--wait` 等待 Web/后端健康。

修改任何持久秘密前先停止目标应用。完成写入、权限检查和暂存后再启动；不要在运行中
替换目录并假设已有 bind mount 会自动获得新 inode。

## 7. 启动前门禁和 Fake 验证

顺序如下：

1. 确认目标 MySQL 已是完整 V34、97 张领域表、77 条权限定义且业务数据为空；
2. 上传、校验并安装同一干净 Git 提交生成的本地发布包；
3. 确认安装脚本已写入本地标签和对应 image ID，再写入其余运行配置和秘密；
4. 执行秘密暂存；
5. 执行预检；
6. 启动目标应用；
7. 只从服务器回环地址验证，再决定是否切换宿主机 Nginx。

```bash
sudo systemctl restart ecobin-stage-runtime-secrets.service
sudo /usr/local/sbin/ecobin-production-preflight
sudo systemctl enable --now ecobin-target-app.service

curl --fail --silent http://127.0.0.1:18080/
sudo /usr/local/sbin/ecobin-runtime-secret-probe
```

外层 Web 只代理 `/api`，因此公网不会暴露 Actuator；后端 readiness 使用镜像内置、
不依赖 curl/wget 的健康脚本检查：

```bash
sudo docker exec ecobin-target-backend \
  /usr/local/bin/ecobin-backend-healthcheck
```

不得打印 `docker compose config` 的完整展开内容到普通日志。预检只使用
`docker compose config --quiet`。

## 8. Nginx 切换

应用、数据库纪元、秘密探针和回环接口全部通过后，才安装并启用新站点。切换前保留
当前站点文件副本；先 `nginx -t`，成功后使用 reload，不能直接停 Nginx：

```bash
sudo install -o root -g root -m 0644 \
  deploy/production/nginx/jinshoubao.com.conf \
  /etc/nginx/sites-available/jinshoubao
sudo ln -sfn /etc/nginx/sites-available/jinshoubao \
  /etc/nginx/sites-enabled/jinshoubao
sudo nginx -t
sudo systemctl reload nginx
```

切换后验证首页、CSRF 初始化接口、Fake 边界和两个通知路径的非法请求拒绝行为。非法
微信通知应返回业务拒绝，不应被 Nginx 404、静态页面或另一站点吞掉。

当前证书只覆盖 `www.jinshoubao.com`，根域名尚无 A/AAAA 记录。因此：

- HTTP 根域名可以预先配置为跳转到 `www`；
- 不得宣称根域名 HTTPS 已可用；
- 根域名 DNS 生效后，先扩展证书 SAN，再增加根域名 443 服务。

备案未完成或域名尚不能稳定作为微信回调时，保持 `externalMode=fake`。Nginx 可先完成
代码和候选配置，但不能据此宣布真实充值闭环已验收。

## 9. 回退和禁止动作

应用切换失败时：

1. 恢复先前的宿主机 Nginx 站点并执行 `nginx -t`、reload；
2. `systemctl stop ecobin-target-app.service`；
3. 如需回到上一应用版本，使用安装脚本在每次切换前原子保存的
   `/etc/ecobin/deployment.env.previous` 恢复部署参数，重新执行预检后再启动；不得回滚
   数据库纪元或删除新业务数据；
4. 保留目标 MySQL、发布目录、机构小程序秘密库、容器日志和脱敏证据；
5. 诊断并前滚。目标库一旦产生权威业务写入，不得直接切旧库继续写。

始终禁止：

- `docker compose down -v`；
- 删除 `ecobin-target-mysql84-data` 或旧 `ecobin_ecobin-mysql-data`；
- 向后端注入 root、schema owner 或 backup 密码；
- 为调试发布 8080/3306；
- 把 Desktop 密钥文件、`.env`、PEM、证书私钥或任何值提交 Git；
- 使用 `latest`、仅凭本地标签启动，或删除仍可能用于回退的发布目录和镜像；
- 在没有正式 seed 的情况下手工插入管理员或机构数据。
