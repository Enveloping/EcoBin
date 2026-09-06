# EcoBin 生产部署配置、密钥与证书清单

> 适用目标：`ubuntu@115.159.67.35`、Ubuntu 22.04、
> `https://www.jinshoubao.com`、目标数据库 V68。
>
> 本文只记录配置项名称、用途和存放位置，不记录任何真实值。完整的安装、启动、
> Nginx 切换和回退步骤见
> [目标单机部署手册](target-single-host-deployment.md)；应用代码修改后的日常发布见
> [应用修改后重新部署操作手册](application-redeployment-runbook.md)。

## 1. 先记住四个结论

1. 服务器上的配置分为两类：非秘密写入 `/etc/ecobin/*.env`，密码、Secret 和私钥按
   “一个值一个文件”写入 `/etc/ecobin/secrets`。不要把秘密写进 `.env`。
2. 小程序 AppID/AppSecret 属于平台共享渠道，启动后由平台管理接口写入目标数据库；
   设备二维码入口是单独的全局非秘密配置，不属于机构或渠道。
3. 微信支付使用公司同一个普通商户号；共享小程序 AppID 需要在微信侧绑定该商户号，
   机构资金绑定核验事实仍分别记录。
4. 当前实现只使用“微信支付公钥”模式，即 `pub_key.pem + PUB_KEY_ID_...`。不需要、
   也不支持微信支付平台证书。`apiclient_cert.pem` 是公司自己的商户 API 证书，和
   “微信支付平台证书”不是同一种证书。

域名备案、DNS、HTTPS 或真实渠道参数没有全部就绪时，先以 `externalMode=fake` 启动。
只有本文 Real 清单全部满足后，才能一次性切换为 `externalMode=real`。

## 2. 服务器最终目录

```text
/etc/ecobin/
├── compose/
│   ├── docker-compose.target-app.yml       root:root 0600
│   └── docker-compose.remote-support.yml   root:root 0644
├── h02/
│   ├── docker-compose.h02-server.yml       数据库 Compose
│   └── compose.env                         数据库非秘密部署参数
├── deployment.env                          root:root 0600；镜像和部署参数
├── runtime.env                             root:root 0600；后端非秘密参数
├── secrets/                                root:root 0700
│   ├── mysql-root-password                 root:root 0600
│   ├── db-app-password                     root:root 0600
│   ├── db-backup-password                  root:root 0600
│   ├── jwt-secret                          root:root 0600
│   ├── bag-code-key-k1                     root:root 0600
│   ├── device-enrollment-key-k1            root:root 0600；条件启用
│   ├── remote-support-maintenance-user-ca  root:root 0600；条件启用
│   ├── default-platform-admin-password     root:root 0600
│   └── ...                                 Real 模式渠道秘密
├── business-release-keys/                  root:root 0755；只含正式 Ed25519 公钥
│   └── business_2026.pem                   root:root 0644
├── wechatpay/                              root:root 0755
│   ├── apiclient_cert.pem                  root:root 0644
│   └── pub_key.pem                         root:root 0644
└── backup-public/                          root:root；数据库备份接收方公钥证书

/run/ecobin-secrets/backend/                root:10001 0750；启动时生成
/var/log/ecobin/backend/                    10001:10001 0750；后端持久滚动日志
/var/lib/ecobin/business-release-upload-tmp/ 10001:10001 0700；发布包临时落盘
/var/lib/ecobin/releases/<release-id>/      root:root 0750；发布制品和镜像身份记录
/etc/nginx/sites-available/jinshoubao        Nginx 站点配置
/etc/letsencrypt/live/www.jinshoubao.com/   Certbot 管理的 HTTPS 证书与私钥
```

`/run/ecobin-secrets` 位于运行时目录，服务器重启后会重新生成。长期原件必须留在
`/etc/ecobin/secrets`，不能只放在 `/run` 下。`/var/log/ecobin/backend` 是宿主机持久
目录，由发布安装器和 systemd 以容器用户 `10001:10001`、模式 `0750` 创建；容器重建
不会删除其中的历史日志。发布包临时目录同样由安装器创建，但使用更严格的 `0700`，且只
挂入后端容器的固定上传路径。

当前试验期在操作机使用仓库外的
`C:\Users\24217\.ecobin\production\115.159.67.35\` 保存受 ACL 限制的长期原件。
如果某些值现在只在桌面的汇总文本中，不要把该文本整个上传到服务器；应逐项核对后写入
对应的单值文件，并把汇总文本迁移到受控的仓库外目录。

## 3. 必须安装的非秘密配置文件

### 3.1 应用部署参数 `/etc/ecobin/deployment.env`

从
[`deploy/production/deployment.env.example`](../../deploy/production/deployment.env.example)
复制后填写：

```dotenv
ECOBIN_IMAGE_MODE=local
ECOBIN_RELEASE_ID=<服务器已安装的发布ID>
ECOBIN_BACKEND_IMAGE=ecobin-local/backend:<同一发布ID>
ECOBIN_BACKEND_IMAGE_ID=sha256:<服务器构建后的不可变镜像ID>
ECOBIN_WEB_IMAGE=ecobin-local/web:<同一发布ID>
ECOBIN_WEB_IMAGE_ID=sha256:<服务器构建后的不可变镜像ID>
ECOBIN_RUNTIME_ENV_FILE=/etc/ecobin/runtime.env
ECOBIN_BACKEND_LOG_DIRECTORY=/var/log/ecobin/backend
ECOBIN_BUSINESS_RELEASE_UPLOAD_DIRECTORY=/var/lib/ecobin/business-release-upload-tmp
ECOBIN_PUBLIC_ORIGIN=https://www.jinshoubao.com
ECOBIN_WEB_LOOPBACK_PORT=18080

ECOBIN_COMPOSE_PROJECT_NAME=ecobin-target
ECOBIN_BACKEND_CONTAINER_NAME=ecobin-target-backend
ECOBIN_WEB_CONTAINER_NAME=ecobin-target-web
ECOBIN_APP_NETWORK_NAME=ecobin-target-app
ECOBIN_DB_NETWORK_NAME=ecobin-target-db
```

这六个镜像字段不需要人工计算。开发机使用
[`New-EcobinLocalRelease.ps1`](../../tools/deployment/New-EcobinLocalRelease.ps1) 生成只含
JAR/dist 的校验发布包，服务器使用
[`ecobin-install-local-release.sh`](../../tools/deployment/ecobin-install-local-release.sh)
构建运行时镜像后自动写入。当前部署不需要镜像仓库，也没有 `docker login` 凭证。

本地标签仍可能被覆盖，因此生产预检不会单独信任标签，而是同时核对 Docker image ID、
镜像内 Git/制品标签和 `/var/lib/ecobin/releases/<release-id>/images.env`。禁止手工填
`latest` 或只复制标签、不保存发布记录。

### 3.2 Fake 模式 `/etc/ecobin/runtime.env`

从
[`deploy/production/runtime.env.example`](../../deploy/production/runtime.env.example)
复制。域名、微信、OneNet 或 COS 尚未就绪时使用以下完整内容：

```dotenv
dbUrl=jdbc:mysql://ecobin-target-mysql84:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=UTC
dbUsername=ecobin_app
defaultPlatformAdminEnabled=true
externalMode=fake
ecobinLogPath=/var/log/ecobin/backend
miniappDeviceEntryBaseUrl=https://www.jinshoubao.com/device-entry/
ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS=0.1.0,hardware-runtime-20260827-01,hardware-runtime-20260830-11,hardware-runtime-20260831-12,hardware-runtime-20260831-13,hardware-runtime-20260903-19,hardware-runtime-20260903-21,hardware-runtime-20260904-23,hardware-runtime-20260904-24,hardware-runtime-20260904-26,hardware-runtime-20260905-27,hardware-runtime-20260906-29
onenetSubscriptionEnabled=false
deviceEnrollmentEnabled=false
remoteSupportEnabled=false
TZ=UTC
```

Fake 模式不要提前混入任何 Real 参数。它能验证数据库、后端、Web、登录、机构、钱包和
Fake 资金状态机，但不能证明真实 OneNet、COS、微信充值、微信通知或微信零钱提现可用。

生产后端同时向控制台和持久文件写日志：控制台继续由 `docker logs` 查看；通用日志写入
`/var/log/ecobin/backend/ecobin.log`，ERROR 及以上另写
`/var/log/ecobin/backend/errors.log`。归档位于 `archive/`：单段 50 MB，通用日志保留
7 天且总上限 1 GB，错误日志保留 14 天且总上限 1 GB。

### 3.3 Real 模式 `/etc/ecobin/runtime.env`

切换 Real 时保留数据库基础项，并把下面一整组非秘密参数一次性补齐：

```dotenv
dbUrl=jdbc:mysql://ecobin-target-mysql84:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=UTC
dbUsername=ecobin_app
defaultPlatformAdminEnabled=true
externalMode=real
ecobinLogPath=/var/log/ecobin/backend
miniappDeviceEntryBaseUrl=https://www.jinshoubao.com/device-entry/
ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS=0.1.0,hardware-runtime-20260827-01,hardware-runtime-20260830-11,hardware-runtime-20260831-12,hardware-runtime-20260831-13,hardware-runtime-20260903-19,hardware-runtime-20260903-21,hardware-runtime-20260904-23,hardware-runtime-20260904-24,hardware-runtime-20260904-26,hardware-runtime-20260905-27,hardware-runtime-20260906-29
TZ=UTC

iotSubscriptionName=<OneNet北向订阅名称>
onenetProductId=<OneNet产品ID>
onenetSubscriptionEnabled=true
deviceEnrollmentEnabled=false
remoteSupportEnabled=false

cosRegion=<例如ap-shanghai>
cosBucketName=<完整bucket-appId>
cosBaseUrl=https://<对应COS访问域名>
cosDurationSeconds=1800

businessReleaseCosRegion=<业务更新包私有桶地域>
businessReleaseCosBucketName=<与照片桶不同的完整bucket-appId>
businessReleaseCosBasePrefix=edge-runtime/releases
businessReleaseDownloadBaseUrl=https://<私有桶对应COS访问域名>
businessReleaseSigningPublicKeysDirectory=/run/secrets/business-release-keys
businessReleaseUploadDirectory=/var/lib/ecobin/business-release-upload-tmp
businessReleaseRemoteDispatchEnabled=false

wechatPayMchid=<公司普通商户号>
wechatPayMerchantSerialNumber=<apiclient_cert.pem的证书序列号>
wechatPayMerchantPrivateKeyPath=/run/secrets/wechatpay/apiclient_key.pem
wechatPayPublicKeyId=PUB_KEY_ID_<微信支付公钥ID>
wechatPayPublicKeyPath=/run/secrets/wechatpay/pub_key.pem
wechatPayNotifyBaseUrl=https://www.jinshoubao.com
wechatPayTransferSceneId=1010
```

`ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS` 是以英文逗号分隔的边缘软件
允许列表。发布新香橙派运行时前，应保留仍在服役的旧版本并追加候选版本；设备提交的
`edgeSoftwareVersion` 不在该列表时，机器验收会明确失败为
`UNSUPPORTED_EDGE_SOFTWARE`，不能通过人工修改验收结果绕过。

已被淘汰且不再作为下一次写卡源的候选版本不能保留在允许列表。允许列表保留仍可能由现有
设备实际报告的版本，并只预先追加已经完成离线复验、准备写卡验收的新候选。当前 v26 已由
测试设备实际使用；v27 把该设备业务程序更新、自动回滚和重启恢复验收期间形成的正式修复
固化进递增镜像，并已完成两套离线复验。加入允许列表只使后端能够校验设备稍后实际上报的
v27，不表示镜像已经写卡、取得真机资格或可以远程下发。

这里不允许出现 `wechatAppid`、`wechatSecret`、`miniappSecretStoreDirectory`、
`wechatPayApiV3Key`、数据库密码或任何 COS/OneNet Secret。前面三项已经从当前设计中
移除，后面这些秘密必须通过文件注入。

`wechatPayNotifyBaseUrl` 必须与 `ECOBIN_PUBLIC_ORIGIN` 完全一致，只能是 HTTPS 根地址，
不能带路径、查询参数或 `#`。后端会在它后面生成：

- Native 充值回调：
  `/api/v1/wechat-pay/notifications/native-payments`
- 商家转账回调：
  `/api/v1/wechat-pay/notifications/merchant-transfers`

### 3.4 数据库服务配置

数据库已经供应时，保留服务器现有的 `/etc/ecobin/h02/compose.env`，不要为了部署应用
重新生成密码或数据卷。该文件至少负责给
[`docker-compose.h02-server.yml`](../../deploy/production/docker-compose.h02-server.yml)
提供固定 MySQL 镜像、root 密码文件路径、容器、数据卷和内部网络名称：

```dotenv
H02_MYSQL_IMAGE=<已验收的MySQL-8.4.10镜像digest>
H02_ROOT_PASSWORD_FILE=/etc/ecobin/secrets/mysql-root-password
H02_COMPOSE_PROJECT_NAME=ecobin-target
H02_CONTAINER_NAME=ecobin-target-mysql84
H02_VOLUME_NAME=ecobin-target-mysql84-data
H02_NETWORK_NAME=ecobin-target-db
```

当前服务器目标库的实际版本必须在应用部署前现场核对并前向升级到 V68。V68 是数据库纪元门禁，
不是可以通过修改 `runtime.env` 绕过的配置项。

### 3.5 从仓库安装到服务器的固定文件

| 仓库文件 | 服务器位置 | 建议权限 |
|---|---|---|
| `deploy/production/docker-compose.target-app.yml` | `/etc/ecobin/compose/docker-compose.target-app.yml` | `root:root 0600` |
| `deploy/production/docker-compose.remote-support.yml` | `/etc/ecobin/compose/docker-compose.remote-support.yml` | `root:root 0644` |
| `deploy/production/docker-compose.h02-server.yml` | `/etc/ecobin/h02/docker-compose.h02-server.yml` | `root:root 0600` |
| `deploy/production/nginx/jinshoubao.com.conf` | `/etc/nginx/sites-available/jinshoubao` | `root:root 0644` |
| `tools/deployment/ecobin-stage-runtime-secrets.sh` | `/usr/local/sbin/ecobin-stage-runtime-secrets` | `root:root 0755` |
| `tools/deployment/ecobin-target-app-compose.sh` | `/usr/local/sbin/ecobin-target-app-compose` | `root:root 0755` |
| `tools/deployment/ecobin-production-preflight.sh` | `/usr/local/sbin/ecobin-production-preflight` | `root:root 0755` |
| `tools/deployment/ecobin-runtime-secret-probe.sh` | `/usr/local/sbin/ecobin-runtime-secret-probe` | `root:root 0755` |
| `tools/deployment/ecobin-install-local-release.sh` | `/usr/local/sbin/ecobin-install-local-release` | `root:root 0755` |
| `tools/deployment/systemd/ecobin-stage-runtime-secrets.service` | `/etc/systemd/system/ecobin-stage-runtime-secrets.service` | `root:root 0644` |
| `tools/deployment/systemd/ecobin-target-app.service` | `/etc/systemd/system/ecobin-target-app.service` | `root:root 0644` |

这些文件必须保持 Linux LF 换行。不要直接把 Windows 工作目录里的 `.env`、本地 Secret
YAML 或整个仓库根目录挂进容器。

## 4. 基础秘密：Fake 和 Real 都需要

`/etc/ecobin/secrets` 必须为 `root:root 0700`；下列文件必须为 `root:root 0600`。
每个文件只放值本身，不写 `KEY=`、引号或说明文字。

| 持久文件 | 谁使用 | 后端是否可见 | 约束 |
|---|---|---:|---|
| `mysql-root-password` | MySQL 容器初始化和受控维护 | 否 | 必须与目标 MySQL root 密码一致 |
| `db-app-password` | MySQL 的 `ecobin_app` 与后端 | 是，暂存为 `/run/secrets/dbPassword` | 两边必须是同一个值 |
| `db-backup-password` | `ecobin_backup` 备份流程 | 否 | 后端不应得到该值 |
| `jwt-secret` | 后端签发和校验登录令牌 | 是，暂存为 `/run/secrets/jwtSecret` | 至少 32 个 UTF-8 字节，使用独立随机值 |
| `bag-code-key-k1` | 后端签发和验证 EB1 实体袋码 | 是，暂存为 `/run/secrets/bagCodeKeyK1` | 至少 32 个随机字节的 Base64；不得与 JWT 或渠道密钥复用 |
| `default-platform-admin-password` | 仅在平台管理员表完全为空时创建默认管理员 | 是，暂存为 `/run/secrets/defaultPlatformAdminPassword` | 使用项目负责人约定的引导密码；已有管理员时不会读取它重置密码 |

已有目标数据库部署应用时，前三个数据库密码应复用已供应的生产值，而不是随应用重新
生成。只有执行受控数据库密码轮换时，才同时修改数据库账号和对应文件。

## 5. Real 模式新增的秘密

以下文件也放在 `/etc/ecobin/secrets`，权限同样为 `root:root 0600`：

| 持久文件 | 值从哪里取得 | 运行时位置或用途 |
|---|---|---|
| `onenet-subscription-access-id` | OneNet 北向服务端订阅/消费组 | `/run/secrets/iotAccessId` |
| `onenet-subscription-secret-key` | 同一 OneNet 北向订阅 | `/run/secrets/iotSecretKey` |
| `onenet-access-key` | OneNet 产品下行 API | `/run/secrets/onenetAccessKey` |
| `cos-secret-id` | 腾讯云最小权限 CAM 身份 | `/run/secrets/cosSecretId` |
| `cos-secret-key` | 与上项配套 | `/run/secrets/cosSecretKey` |
| `business-release-cos-secret-id` | 业务更新包私有桶 CAM 身份 | `/run/secrets/businessReleaseCosSecretId` |
| `business-release-cos-secret-key` | 与上项配套 | `/run/secrets/businessReleaseCosSecretKey` |
| `wechatpay-api-v3-key` | 微信商户平台 API 安全设置 | `/run/secrets/wechatPayApiV3Key` |
| `wechatpay-merchant-private-key.pem` | 商户 API 证书包中的私钥 | `/run/secrets/wechatpay/apiclient_key.pem` |

特别注意：

- `wechatpay-api-v3-key` 必须正好 32 个 UTF-8 字节，不能有末尾换行；它用于解密微信
  APIv3 通知，不是 AppSecret，也不是 APIv2 密钥。
- `wechatpay-merchant-private-key.pem` 必须是未加密 PEM。它用于商户 APIv3 请求签名，
  不能换成 `pub_key.pem`，也不能公开。
- OneNet 的北向订阅凭证和产品下行 AccessKey 是两套用途，不能只配其中一套。
- COS 应使用只满足当前 STS 签发和业务制品读写所需权限的 CAM 身份，不要放腾讯云主账号
  密钥。照片与业务更新包可以使用同一账号下的凭据，但必须使用不同存储桶，便于后续拆分
  最小权限身份。
- 照片桶允许普通 HTTPS 读取；业务更新包桶必须保持私有读、私有写且从未开启版本控制。
  `businessReleaseRemoteDispatchEnabled=false` 只关闭真实设备下发，不影响管理员上传和后端
  校验业务发布包。

业务发布验签公钥放在 `/etc/ecobin/business-release-keys`，目录为 `root:root 0755`，每把
公钥以 `<signingKeyId>.pem` 命名并为 `root:root 0644`。暂存脚本会拒绝空目录、链接、额外
文件、私钥、非 Ed25519 公钥以及错误权限，再把公钥以只读方式复制到后端运行目录。对应私钥
不得出现在服务器、浏览器、COS、数据库或设备中。

## 6. 需要的证书和公钥

### 6.1 微信支付材料

| 文件或编号 | 服务器位置 | 是否秘密 | 当前用途 |
|---|---|---:|---|
| `apiclient_cert.pem` | `/etc/ecobin/wechatpay/apiclient_cert.pem` | 否 | 商户 API 证书；部署时核对证书序列号、有效期和商户私钥配对关系，不挂入后端 |
| `wechatpay-merchant-private-key.pem` | `/etc/ecobin/secrets/` | 是 | 商户 APIv3 请求签名，运行时改名为 `apiclient_key.pem` |
| `pub_key.pem` | `/etc/ecobin/wechatpay/pub_key.pem` | 否 | 微信支付公钥；后端验签 API 响应和回调 |
| `PUB_KEY_ID_...` | `/etc/ecobin/runtime.env` 的 `wechatPayPublicKeyId` | 否 | 指明 `pub_key.pem` 对应的微信支付公钥 ID |
| APIv3 密钥 | `/etc/ecobin/secrets/wechatpay-api-v3-key` | 是 | 解密回调资源 |

三组配对关系不能配错：

1. `wechatpay-merchant-private-key.pem` 必须与 `apiclient_cert.pem` 属于同一份商户 API
   证书；
2. `wechatPayMerchantSerialNumber` 必须等于该 `apiclient_cert.pem` 的序列号；
3. `pub_key.pem` 必须与 `wechatPayPublicKeyId` 是微信商户平台同一次下载/查询得到的一对。

当前服务器不需要以下材料：

- 微信支付平台证书；
- 平台证书序列号；
- 平台证书自动下载任务；
- `apiclient_cert.p12` 运行时文件；
- APIv2 密钥；
- 把共享小程序 AppID/AppSecret 写成服务器启动配置。

`.p12` 如果已经从商户平台下载，可以作为商户 API 证书的离线恢复材料保存，但不要
上传到应用服务器，也不要挂入容器。

### 6.2 HTTPS 证书

Nginx 配置当前读取：

```text
/etc/letsencrypt/live/www.jinshoubao.com/fullchain.pem
/etc/letsencrypt/live/www.jinshoubao.com/privkey.pem
/etc/letsencrypt/options-ssl-nginx.conf
/etc/letsencrypt/ssl-dhparams.pem
```

这些文件由 Certbot 申请和自动续期，不手工复制到 Git。`privkey.pem` 是 TLS 私钥，
只允许 root/Certbot/Nginx 按系统权限读取。

当前证书和 443 站点只按 `www.jinshoubao.com` 设计。根域名 `jinshoubao.com` 要启用
HTTPS，必须先让 DNS 生效，再把根域名加入证书 SAN（证书覆盖的域名列表），之后才能
增加根域名 443 配置。不能用只覆盖 `www` 的证书冒充根域名 HTTPS 已就绪。

### 6.3 数据库备份接收方证书

受控数据库备份脚本使用：

```text
/etc/ecobin/backup-public/ecobin-backup-recipient.crt
```

服务器只保存该公钥证书，用于把备份加密。对应恢复私钥必须留在操作机或离线介质，
不能放到服务器。它不是应用启动必需项，但在正式产生业务数据前必须确保“可加密、
异机可解密、可恢复”的备份链路仍然有效。

## 7. 共享小程序渠道与设备入口放在哪里

共享渠道的 AppID/AppSecret 不属于服务器启动配置，不能写进 `runtime.env` 或
`/etc/ecobin/secrets`。设备入口地址是非秘密全局配置，统一由
`miniappDeviceEntryBaseUrl` 提供，默认值为 `https://www.jinshoubao.com/device-entry/`。
正确顺序是：

1. 平台创建租户和机构；系统在机构创建事务中同步建立该机构的钱包/资金账户；
2. 平台管理员通过管理接口创建或选择共享小程序渠道，填写 AppID、AppSecret 和展示名称，
   再把机构绑定到该渠道；机构页面不配置二维码入口；
3. 核对配置版本后激活渠道，再启用小程序登录；
4. 在微信侧把共享 AppID 绑定到公司的 `wechatPayMchid`；
5. 平台管理员在“机构资金”中核查外部绑定后，记录本地 `VERIFIED` 事实；未核验时该
   机构不能创建真实充值或提现。

平台管理接口为：

```text
PUT  /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-configuration
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-configuration/activations
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/miniapp-login/enablements
POST /api/v1/web/platform/tenants/{tenantCode}/organizations/{organizationCode}/wechat-merchant-binding/verifications
```

目前后端和 OpenAPI 已提供平台共享渠道配置接口。不要直接改数据库，也不要把
AppID/AppSecret 或每机构二维码入口重新放回服务器全局文件和机构表单。

AppSecret 以明文保存在目标数据库中，因此数据库备份也包含 AppSecret。备份密文、恢复
环境和能读取 `iam_organization_miniapp` 的账号都要按秘密数据管理；日志、审计、告警和
普通接口不得记录完整值。

## 8. 服务器之外还要完成的控制台配置

| 平台 | 必须完成的事项 | 未完成会阻止什么 |
|---|---|---|
| DNS/备案 | `www.jinshoubao.com` 指向服务器；备案和公网访问稳定 | 微信真实回调和正式公网访问 |
| 腾讯云安全组/UFW | 只开放必要的 22、80、443；不开放 8080、3306、13306 | 公网安全门禁 |
| Certbot/Nginx | HTTPS 证书有效、自动续期、Nginx 反向代理 `/api` | 登录、API 和微信回调 |
| 微信商户平台 | 普通商户启用 Native 支付、商家转账；设置 APIv3 密钥；取得商户 API 证书、微信支付公钥和公钥 ID | 真实充值与提现 |
| 微信商户平台 | 每个机构 AppID 分别绑定到同一个公司商户号；场景 ID `1010` 与产品权限一致 | 对应机构的真实提现 |
| 微信公众平台 | 共享小程序配置合法 request 域名；普通二维码规则匹配 `https://www.jinshoubao.com/device-entry/`，校验文件可直接访问 | 小程序调用后端 API 和设备扫码入口 |
| OneNet | 产品 ID、北向订阅名称/Access ID/Secret Key、下行 AccessKey 正确 | 设备上报和后端下行命令 |
| 腾讯云 COS/CAM | 图片桶与私有业务更新包桶的 Bucket、地域、访问域名和 CAM 身份一致 | 照片 STS 直传；更新包普通地址拒绝、短时签名地址可读 |

ICP备案或回调域名未完成时，代码和文件可以先准备好，但 `externalMode` 必须保持
`fake`，不能把“配置已填写”当成真实资金闭环已经通过。

## 9. 启动前校验

不要用 `cat`、`env`、`docker inspect` 或 `docker compose config` 把真实值打印到普通
终端记录或日志。可以执行下列不显示秘密内容的检查：

```bash
# 1. 重新从持久源暂存运行秘密；脚本会检查权限、APIv3 长度、证书/私钥配对和序列号
sudo systemctl restart ecobin-stage-runtime-secrets.service

# 2. 检查镜像身份、配置、日志目录权限、通知域名和数据库网络
sudo /usr/local/sbin/ecobin-production-preflight

# 3. 启动并等待后端/Web 健康
sudo systemctl enable --now ecobin-target-app.service

# 4. 检查容器只能读取应有秘密，且没有获得 root/backup 密码
sudo /usr/local/sbin/ecobin-runtime-secret-probe

# 5. 只从宿主机回环验证 Web
curl --fail --silent http://127.0.0.1:18080/ >/dev/null
```

Real 模式切换前还应人工确认：

- [ ] 目标数据库已经是 V68，且共有 131 张领域表、68 条成功迁移和 76 条权限定义；
- [ ] 发布包来自干净 Git 提交，归档和包内逐文件 SHA-256 校验均通过；
- [ ] 两个本地镜像的标签、image ID、发布记录和镜像标签一致；
- [ ] `deployment.env` 与 `runtime.env` 均为 `root:root 0600` 且使用 LF；
- [ ] `/var/log/ecobin/backend` 为真实目录、不是符号链接，权限精确为
  `10001:10001 0750`；
- [ ] `/etc/ecobin/secrets` 的基础项和 Real 渠道项全部存在；启用设备注册/远程维护时，对应 K1/CA 私钥也存在且权限正确；
- [ ] `bag-code-key-k1` 是有效 Base64，解码后至少 32 字节；
- [ ] APIv3 密钥正好 32 字节且没有换行；
- [ ] 商户私钥、商户 API 证书和证书序列号互相匹配；
- [ ] `pub_key.pem` 与 `PUB_KEY_ID_...` 配对；
- [ ] `wechatPayNotifyBaseUrl` 与公网入口都是
  `https://www.jinshoubao.com`；
- [ ] HTTPS 从公网可访问，两个微信通知路径能到达后端而不是静态 404；
- [ ] OneNet 北向、下行和 COS 分别用真实小流量探针通过；
- [ ] 至少一个测试机构已经配置 AppID/AppSecret、启用登录并完成商户绑定核验；
- [ ] 先用可承受的小金额完成一次 Native 充值和一次提现全链路验收。

## 10. 轮换时必须成组修改

| 轮换对象 | 必须同时处理 |
|---|---|
| `db-app-password` | 修改 MySQL `ecobin_app` 密码和服务器文件，再重新暂存并重启后端 |
| JWT 密钥 | 更新 `jwt-secret` 并重启后端；已有登录令牌会失效，应安排重新登录窗口 |
| EB1 袋码密钥 | 增加新 key ID 并把它设为活动签发密钥；仍在流通的旧标签退场前保留旧 key 只用于验真 |
| 商户 API 证书 | 新商户私钥、`apiclient_cert.pem` 和 `wechatPayMerchantSerialNumber` 一起发布 |
| 微信支付公钥 | 新 `pub_key.pem` 与对应 `wechatPayPublicKeyId` 一起发布 |
| APIv3 密钥 | 微信商户平台与服务器 `wechatpay-api-v3-key` 同步变更，避免新旧回调无法解密 |
| OneNet/COS 凭证 | 先建立新凭证和最小权限，再更新对应文件并验证，最后撤销旧凭证 |
| 共享渠道 AppSecret | 只通过平台小程序渠道配置接口更新，不改服务器全局文件；更新后验证所有绑定机构登录 |

修改任何持久秘密前先停止目标应用。更新完成后重新运行秘密暂存、生产预检和运行时秘密
探针；不要在容器运行时直接覆盖 bind mount 内的文件并假设进程会安全热加载。

## 11. 微信支付官方依据

- [证书密钥概览](https://pay.weixin.qq.com/doc/v3/merchant/4024350132.md)：区分商户 API
  证书、微信支付公钥/平台证书与 APIv3 密钥。知识库路径：
  `APIv3/普通商户/安全工具/证书密钥概览-4024350132.md`。
- [开发必要参数说明](https://pay.weixin.qq.com/doc/v3/merchant/4013070756.md)：说明商户号、
  AppID 绑定、商户私钥、证书序列号、公钥 ID 和 APIv3 密钥的用途。知识库路径：
  `APIv3/普通商户/通用规则/开发须知/开发参数申请和配置/开发必要参数说明-4013070756.md`。
- [微信支付公钥产品介绍](https://pay.weixin.qq.com/doc/v3/merchant/4012153196.md)：说明从未
  使用平台证书的新商户可直接使用微信支付公钥模式。知识库路径：
  `APIv3/普通商户/安全工具/微信支付公钥/产品介绍-4012153196.md`。
