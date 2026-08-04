# EcoBin 目标单机部署手册

> 适用主机：`115.159.67.35`（Ubuntu 22.04）
> 当前公网入口：`https://www.jinshoubao.com`
> 目标数据库纪元：V31
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
- 宿主机不构建 Maven/npm 制品，只运行固定 SHA-256 digest 的镜像。

相关文件：

- 应用 Compose：
  [`deploy/production/docker-compose.target-app.yml`](../../deploy/production/docker-compose.target-app.yml)
- Compose 非秘密变量模板：
  [`deploy/production/deployment.env.example`](../../deploy/production/deployment.env.example)
- 后端非秘密运行配置模板：
  [`deploy/production/runtime.env.example`](../../deploy/production/runtime.env.example)
- 宿主机 Nginx 站点：
  [`deploy/production/nginx/jinshoubao.com.conf`](../../deploy/production/nginx/jinshoubao.com.conf)

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
    └── wechatpay_platform_cert.pem         root:root 0644

/run/ecobin-secrets/backend/                root:10001 0750；每次启动重新生成
/var/lib/ecobin/miniapp-secrets/             10001:10001 0700；持久化
```

`/var/lib/ecobin/miniapp-secrets` 保存机构小程序 AppSecret 的文件原件。数据库只保存
引用和摘要，因此它是业务持久数据，不是可随容器删除的缓存。创建或修改机构小程序
配置时由后端 UID 10001 写入，必须和数据库纳入同一备份、恢复和对账流程。

## 3. 镜像门禁

后端和 Web 镜像在开发机或 CI 构建、测试并推送，然后把仓库返回的不可变 digest 写入
`/etc/ecobin/deployment.env`：

```text
ECOBIN_BACKEND_IMAGE=<registry>/<backend>@sha256:<64 位十六进制>
ECOBIN_WEB_IMAGE=<registry>/<web>@sha256:<64 位十六进制>
```

禁止使用单独的 `latest`、分支标签或提交标签启动生产容器。预检会拒绝没有 digest、
示例全零 digest、镜像本机不存在等情况。

后端镜像固定以 `10001:10001` 运行，根文件系统只读，只允许写：

- `/tmp` 的 64 MiB 临时文件系统；
- `/var/lib/ecobin/miniapp-secrets` 持久目录。

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

Real 必须一次性满足微信小程序、OneNet 上下行、COS 和微信支付配置。缺少任何一项时
启动失败，不能以“先启动再补配置”绕过。

### 5.1 非秘密参数

把以下键写入 `/etc/ecobin/runtime.env`，不要写任何 Secret/Key 的值：

| 键 | 示例或要求 |
|---|---|
| `externalMode` | `real` |
| `wechatAppid` | 微信小程序 AppID |
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
| `wechatPayPlatformCertificatePath` | 固定 `/run/secrets/wechatpay/wechatpay_platform_cert.pem` |
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
| `wechat-miniapp-secret` | `/run/secrets/wechatSecret` |
| `onenet-subscription-access-id` | `/run/secrets/iotAccessId` |
| `onenet-subscription-secret-key` | `/run/secrets/iotSecretKey` |
| `onenet-access-key` | `/run/secrets/onenetAccessKey` |
| `cos-secret-id` | `/run/secrets/cosSecretId` |
| `cos-secret-key` | `/run/secrets/cosSecretKey` |
| `wechatpay-api-v3-key` | `/run/secrets/wechatPayApiV3Key` |
| `wechatpay-merchant-private-key.pem` | `/run/secrets/wechatpay/apiclient_key.pem` |

APIv3 密钥文件必须正好 32 个 UTF-8 字节，不能附带换行。商户私钥必须是未加密、可由
无人值守进程读取的 PEM；其目录和文件权限承担静态保护职责。

### 5.3 微信支付证书

证书放在 `/etc/ecobin/wechatpay`：

- `apiclient_cert.pem`：商户 API 证书，只用于部署时核对私钥和配置序列号；
- `wechatpay_platform_cert.pem`：微信支付平台证书，复制给后端用于验签。

暂存脚本在接触渠道前验证：

- 两张证书均可解析且至少 7 天内不过期；
- 商户私钥公钥与 `apiclient_cert.pem` 一致；
- `wechatPayMerchantSerialNumber` 与商户证书序列号一致；
- APIv3 密钥字节长度为 32；
- 后端只得到商户私钥和平台证书，不得到商户证书、数据库 root 或备份凭证。

平台证书会轮换。正式运行后必须在旧证书过期前建立受控更新流程；当前单证书实现不应
被误写成已经支持多平台证书无缝轮换。

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

1. 确认目标 MySQL 已是完整 V31、96 张领域表、77 条权限定义且业务数据为空；
2. 把已验收的固定 digest 镜像拉取或导入服务器；
3. 写入 `deployment.env`、`runtime.env` 和本模式需要的秘密；
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
3. 保留目标 MySQL、机构小程序秘密库、容器日志和脱敏证据；
4. 诊断并前滚。目标库一旦产生权威业务写入，不得直接切旧库继续写。

始终禁止：

- `docker compose down -v`；
- 删除 `ecobin-target-mysql84-data` 或旧 `ecobin_ecobin-mysql-data`；
- 向后端注入 root、schema owner 或 backup 密码；
- 为调试发布 8080/3306；
- 把 Desktop 密钥文件、`.env`、PEM、证书私钥或任何值提交 Git；
- 在没有正式 seed 的情况下手工插入管理员或机构数据。
