# EcoBin 应用修改后重新部署操作手册

> 2026-09-14 14:48 异常投递安全禁用约束热修复已部署，当前发布 `20260914064458-82e72e0dca16`；现网仍为 V72 / 134 张业务表 / 72 条成功迁移。结束异常投递现在能在同一事务中正确结束会话、解除占用并安全禁用设备；[故障、验证及回退边界](../operations/abnormal-delivery-disable-constraint-deployment-2026-09-14.md)。

> 2026-09-14 14:16 异常投递会话版本冲突热修复已通过 V72 独立兼容包部署，当前发布 `20260914061433-32504237d0cb`；现网仍为 V72 / 134 张业务表 / 72 条成功迁移。补偿任务不再重复推进已投影会话版本，异常结束与报废仍独立；[故障、验证及回退边界](../operations/abnormal-delivery-version-conflict-deployment-2026-09-14.md)。

> 最新候选 [P1BT：已授权未登记发送的恢复关门撤回](../../hardware/docs/review/uart2-recovery-close-withdrawal-p1bt-2026-09-13.md)：业务库先封住旧发送，永久层另存撤回事实并保留原授权历史；独立继任仅豁免准确祖先，候选循环已接自动核对，不发新动作、不启动云端、不恢复接单，业务39/永久3不变。
> 撤回专项53项与运行入口19项分别通过，最终集成/扩大回归见实施记录，完整契约25通过。已登记可能发送的未知效果、跨新启动号新动作、完整准入/云端/正常业务与main切换仍待接，P4/P5未完成。
> 未部署/烧录；OneNet2.2.0/UART rc.22/MySQL V79/权限40及发布25/39门槛不变。外部刷写/HIL不保证共用串口锁；RS485/HMI/完整固件容量保留。两项投递取舍已确认，下方历史“待确认”不再适用。

> 最新候选 [P1BH：后台确认可靠交接](../../hardware/docs/review/uart2-native-confirmation-p1bh-2026-09-13.md)：原始确认、报告与回执原子绑定，schema32；322项相关测试、25项完整契约检查通过。
> 投递重启缺最终包只归档、归档后迟到结果只追加证据，两项已获用户确认，不再待裁决；实现继续按P4/P5推进。确认交接不释放占用、不修改当前袋或准入，main尚未切换。
> 未部署/烧录；发布25/32、RS485与HMI等剩余门槛保留。以下为历史记录，历史“P4待确认”不再适用于上述两项决定。

> 最新硬件候选 [P1BG：原生上报与编号落库](../../hardware/docs/review/uart2-native-report-persistence-p1bg-2026-09-13.md)：实际 C 多轮/5 秒中位数/末重超时及照片快照已接可靠报告；缺测不补零，清运量为本次前重减后重。
> V78 只对齐四个业务测量编号，原质量/范围/空值约束保留；启动/供应目标同步，134 表/授权39不变。真实 MySQL 297 项、相关 Java 总计399项、Pi/隔离包50项、完整契约25项通过。
> 未部署/烧录；主程序、后端确认消费、当前袋/准入与完整异常仍待接。发布25/31、RS485、完整固件容量及 P4/HMI 待确认项保留，P3/P5/P7未整体完成。下方为历史。

> 最新未发布硬件候选 P1AW（2026-09-13）：新后端要求 V77，当前袋子满溢支持中位数，
> 旧袋/迟到仅留档，原皮重不变；授权清单39不变。仅本地验证，实际准入/原生接线/成对发布未完成。
> 未来获准部署时先迁移再激活；旧应用兼容性须另核对，不删事实、不降迁移版本、不 repair。
> [实现与测试边界](../../hardware/docs/review/uart2-fullness-state-median-p1aw-2026-09-13.md)。未部署，下方候选为历史。

> 未发布硬件候选 P1AS（2026-09-13）：源码后端要求 V73，以保存真实投递超时中位数。
> 仅隔离本地验证，现网未改；余下清运/快照/基准/准入和成对发布尚未完成。
> 后续获准发布时必须先前向迁移，不能绕过数据库启动门禁；写入新事实后，
> 回退须另核对旧应用兼容性，不能删事实、降迁移版本或执行 Flyway repair。
> [实施与验证边界](../../hardware/docs/review/uart2-delivery-median-persistence-p1as-2026-09-13.md)。

> 2026-09-13 01:01 设备禁用、恢复和报废规则已部署，最终发布 `20260912165854-db67124ba87e`；现网 V72 / 134 张业务表、72 条成功迁移、授权清单 38。完整备份恢复、权限和公网校验通过；[部署及回退边界](../operations/device-lifecycle-deployment-2026-09-13.md)。

> 2026-09-12 19:41 租户统一设备配置已部署，当前发布 `20260912112713-b1be0e4a4719`；现用库 V71 / 134 张领域表、71 条成功迁移、授权清单 38。完整备份恢复及上线验证通过；设备应用进度与 V70 不兼容回退边界见[部署记录](../operations/tenant-device-configuration-deployment-2026-09-12.md)。

> 2026-09-12 18:00 全局满溢与租户设备配置已部署，当前发布为 `20260912094429-2d6c6c938e1c`；现用库已到 V70 / 133 张领域表、70 条成功迁移，授权清单 37 完整验证。默认统一 50 千克或红外满足；[部署及回退边界](../operations/global-fullness-deployment-2026-09-12.md)。

> 2026-09-12 16:39 设备列表调整已部署，当前后端 / Web 发布为 `20260912083501-6c8c4e2e91e1`，V69 不变；[部署与回退记录](../operations/device-list-deployment-2026-09-12.md)。

> 2026-09-12 15:40 设备详情页信息分层已部署，当前后端/Web 发布为 `20260912073703-8800b3d17c6f`，V69 不变；[部署与回退记录](../operations/device-detail-deployment-2026-09-12.md)。

> 日常入口：后端、Web 或应用运行镜像相关代码修改并提交后，按本文重新部署到
> `ubuntu@115.159.67.35`。
>
> 本文不负责服务器首次初始化。首次安装 Docker、目录、systemd、Nginx、配置、密钥和
> 证书时，先读[目标单机部署手册](target-single-host-deployment.md)和
> [生产配置、密钥与证书清单](production-configuration-secrets-certificates.md)。

> 2026-09-12 Web 页面信息减量调整已部署，当前后端/Web 发布为 `20260912064344-686a7febbfce`，V69 不变；
> 服务健康与公网文件校验通过，详见[本轮 Web 部署记录](../operations/web-contextual-guidance-deployment-2026-09-12.md)。

> 2026-09-11 已完成现用库 V69 及后端/Web 发布 `20260910171749-1a65fcdd58a5`，当前为
> 132 张领域表、69 条成功迁移；OneNet 18/22 已核对。具体备份、迁移和设备端未完成边界见
> [本轮云端部署记录](../operations/delivery-quarantine-deployment-2026-09-11.md)。

## 1. 固定发布方式

EcoBin 当前不使用应用镜像仓库。一次正常重新部署分为五段：

```text
开发机干净 Git 提交
  → Java 21 编译/测试后端 + Node 20 编译 Web
  → 生成带 SHA-256 的 JAR/dist 发布包
  → 上传服务器并顺序构建两张运行时镜像
  → 生产预检通过后由 systemd/Compose 切换版本
```

服务器只封装已编译的 JAR 和 `dist`，不安装 Maven、Node，也不复制项目源码。发布安装器
不会自动重启应用；因此“发布包已安装”和“新版本已投入运行”是两个独立事实。

正式发布禁止使用 `-AllowDirty`、`latest`、手工覆盖同一发布标签，或跳过服务器预检。

## 2. 先判断这次改动需要执行什么

| 修改范围 | 是否生成应用发布包 | 额外动作 |
|---|---:|---|
| 后端 Java、后端资源、`pom.xml` | 是 | 完整执行本文流程 |
| `frontend/web` | 是 | 完整执行本文流程 |
| 运行时 Dockerfile、后端健康检查、Web 容器 `nginx.conf` | 是 | 这些文件会进入发布包 |
| 应用 Compose、生产预检、秘密暂存/探针、systemd 单元、服务器安装器 | 视情况 | 先按第 4 节同步服务器控制文件；仅改这些文件时不必重新编译 JAR/dist |
| `/etc/ecobin/runtime.env` 或持久秘密/证书 | 否 | 按配置清单更新；秘密变更前先停应用，再暂存、预检和启动 |
| 宿主机 `jinshoubao.com` Nginx 配置 | 否 | `nginx -t` 通过后只 reload Nginx |
| `ecobin-bootstrap/src/main/resources/db/p0-migration`、数据库权限或目标 epoch | 通常是 | 必须先按 H-02 流程把目标库推进到新后端要求的版本，再激活新应用 |
| 小程序或 `hardware/` | 否 | 它们有独立构建/部署流程，不会进入本发布包 |

目标应用 JAR 不携带 Flyway 迁移器和迁移脚本。因此，修改了目标数据库迁移时，单纯上传
新 JAR 不会升级数据库；数据库版本不满足时，新后端会被 epoch/readiness 门禁阻止。
当前数据库操作入口见[H-02 目标数据库手册](h02-target-database.md)。

V51 是一次不可与旧应用并行的状态迁移：它会把已有的微信授权创建永久错误从
`CREATED` 前向归并为 `CREATE_REJECTED`。执行 V51 前必须先停止旧后端写入；迁移成功后
直接激活理解该状态的新后端，再恢复入口。不能让 V50 后端在 V51 数据库上继续接受新的
授权申请，否则旧代码可能把已经终结的失败行当成未知公开状态，或再次生成旧格式的展示名称。

V52 新增设备注册、厂家验收小程序、初始袋标签占用和远程维护事实，并把 epoch 门禁推进到
V52。V53 又增加投递自动审核与自动提现快照/决策；V54 增加投递自动审核金额阈值及订单快照；
V55 增加 MCU 固件发布、人工灰度和进度事实；V56 增加封存授权与设备验收代次；V57 取消外部设备/微信时间与后端时间之间的数据库先后约束，增加时钟质量与微信授权展示包有效期；V58 补强封存空时钟质量、缺失微信创建时间及授权恢复约束；V59 将袋码批次和批内序号检查上限扩大到 500；V60 增加 MCU 远程升级线路能力三态事实；V61 为可靠任务尝试增加可空的外部技术请求号诊断字段；V62 为出厂进度查询增加可靠任务定位索引；V63 新增设备软件实际状态、永久管理层版本、管理架构代次和兼容性投影接收面；V64 增加业务程序发布控制面；V65 增加单台验证下发和进度事实；V66 增加设备确认的安全取消闭环；V67 再把首次远程更新前的镜像内置业务程序登记为明确、可回滚且没有虚构发布编号的来源基线；V68 将设备软件事实的数据库约束与这条可信镜像基线对齐；V69 增加物理结果未知投递的问题型隔离记录，并把当前 epoch
门禁推进到 V69。执行前同样必须停止旧后端写入；迁移完成后只能激活理解 V69 的后端。现场库
已经运行 V69、132 张领域表和 69 条成功迁移，最终最小权限已验证。其他仍停在 V68 的环境
启用当前后端前仍必须完成 V69。业务程序远程下发由独立开关控制，数据库和页面就绪不等于已经允许设备更新。远程维护和 MCU 固件升级默认
关闭，服务器 SSH 边界和条件秘密未安装完毕前不要打开功能开关。

## 3. 发布前检查

在开发机仓库根目录执行：

```powershell
git status --short
git log -1 --oneline
```

确认以下条件：

- 需要发布的修改已经提交，工作区没有未提交或未跟踪文件；
- `.env`、密钥、PEM、证书私钥、服务器凭证和真实业务数据没有进入提交；
- 使用 Java 21、Node.js 20 或更高版本和 PowerShell 7；
- 若包含数据库变更，目标数据库升级及回退兼容策略已经单独确认；
- 当前线上版本稳定，且没有另一名人员同时安装发布包或修改部署配置。

构建脚本会再次检查 Git 工作区。正式发布中检查失败时，应先处理变更，不要用
`-AllowDirty` 绕过。

## 4. 部署控制文件发生变化时先同步

发布包只包含 JAR、Web `dist`、两份运行时 Dockerfile、容器内 Nginx 配置和后端健康
检查。它不会更新服务器上的 Compose、安装器、预检脚本或 systemd 单元。

只有这些控制文件发生变化时才执行本节。先在开发机 PowerShell 创建服务器临时目录并
上传：

```powershell
ssh ubuntu@115.159.67.35 "mkdir -p /tmp/ecobin-deploy-control"

scp `
  .\deploy\production\docker-compose.target-app.yml `
  .\deploy\production\docker-compose.remote-support.yml `
  .\tools\deployment\ecobin-install-local-release.sh `
  .\tools\deployment\ecobin-target-app-compose.sh `
  .\tools\deployment\ecobin-stage-runtime-secrets.sh `
  .\tools\deployment\ecobin-production-preflight.sh `
  .\tools\deployment\ecobin-runtime-secret-probe.sh `
  .\tools\deployment\systemd\ecobin-stage-runtime-secrets.service `
  .\tools\deployment\systemd\ecobin-target-app.service `
  ubuntu@115.159.67.35:/tmp/ecobin-deploy-control/
```

登录服务器后安装固定文件：

```bash
cd /tmp/ecobin-deploy-control
sudo install -o root -g root -m 0600 \
  docker-compose.target-app.yml \
  /etc/ecobin/compose/docker-compose.target-app.yml
sudo install -o root -g root -m 0644 \
  docker-compose.remote-support.yml \
  /etc/ecobin/compose/docker-compose.remote-support.yml
sudo install -o root -g root -m 0755 \
  ecobin-install-local-release.sh \
  /usr/local/sbin/ecobin-install-local-release
sudo install -o root -g root -m 0755 \
  ecobin-stage-runtime-secrets.sh \
  /usr/local/sbin/ecobin-stage-runtime-secrets
sudo install -o root -g root -m 0755 \
  ecobin-target-app-compose.sh \
  /usr/local/sbin/ecobin-target-app-compose
sudo install -o root -g root -m 0755 \
  ecobin-production-preflight.sh \
  /usr/local/sbin/ecobin-production-preflight
sudo install -o root -g root -m 0755 \
  ecobin-runtime-secret-probe.sh \
  /usr/local/sbin/ecobin-runtime-secret-probe
sudo install -o root -g root -m 0644 \
  ecobin-stage-runtime-secrets.service \
  /etc/systemd/system/ecobin-stage-runtime-secrets.service
sudo install -o root -g root -m 0644 \
  ecobin-target-app.service \
  /etc/systemd/system/ecobin-target-app.service
sudo systemctl daemon-reload
```

仓库通过 `.gitattributes` 固定这些 Linux 文件为 LF 换行。若服务器预检报告 CRLF，不要在
服务器用忽略选项绕过，应修正来源文件后重新上传。

如果本次只修改了这些控制文件，不需要生成应用发布包；安装完成后根据变更影响直接执行
第 8 节的秘密暂存、预检和应用 reload。若只更新了尚未使用的安装器本身，则无需为了它
单独重启健康的应用。

## 5. 在开发机生成正式发布包

在 PowerShell 7 的仓库根目录执行：

```powershell
pwsh -File .\tools\deployment\New-EcobinLocalRelease.ps1
```

脚本默认执行：

1. Maven Wrapper `clean package`，包括后端测试；
2. `frontend/web` 的 `npm ci` 和 `npm run build`；
3. 只收集可运行 JAR、Web `dist` 和运行镜像白名单文件；
4. 写入 Git 提交、干净工作区标记和逐文件 `SHA256SUMS`；
5. 在 `release-output/` 生成发布目录、`.tar.gz` 和归档 `.sha256`。

只有同一提交已经在独立受控流程跑过全部测试时才考虑 `-SkipTests`。`-SkipBuild` 和
`-AllowDirty` 只用于本地诊断，不得生成正式服务器发布。

脚本最后会打印 `ReleaseId`、Git commit、发布目录、归档和校验文件路径。复制其中的
`ReleaseId`，在同一 PowerShell 窗口设置：

```powershell
$releaseId = '<脚本输出的 ReleaseId>'
```

## 6. 上传发布包

继续在刚才的 PowerShell 7 会话执行：

```powershell
scp `
  ".\release-output\ecobin-release-$releaseId.tar.gz" `
  ".\release-output\ecobin-release-$releaseId.tar.gz.sha256" `
  ubuntu@115.159.67.35:/tmp/
```

只上传脚本输出的归档及归档校验文件。不要上传整个仓库、`release-output` 目录中的其他
历史版本、Desktop 密钥目录或任何 `.env`。

## 7. 在服务器校验并安装发布包

SSH 登录服务器。把下列占位值替换为第 5 节输出的 `ReleaseId`：

```bash
release_id='<ReleaseId>'
cd /tmp
sha256sum --check "ecobin-release-${release_id}.tar.gz.sha256"

staging_root="$(mktemp -d /tmp/ecobin-release-upload.XXXXXX)"
tar --no-same-owner -xzf "ecobin-release-${release_id}.tar.gz" \
  -C "${staging_root}"

sudo /usr/local/sbin/ecobin-install-local-release \
  "${staging_root}/ecobin-release-${release_id}"
```

安装器会：

- 拒绝符号链接、特殊文件、缺失/多余文件、错误摘要和正式环境中的脏源码包；
- 把发布内容保存到 `/var/lib/ecobin/releases/<release-id>/`；
- 顺序构建 `ecobin-local/backend:<release-id>` 和
  `ecobin-local/web:<release-id>`，避免两项构建同时抢占单机内存；
- 保存两张镜像的不可变 Docker image ID、Git 提交和制品摘要；
- 把当前 `/etc/ecobin/deployment.env` 保存为
  `/etc/ecobin/deployment.env.previous`；
- 原子更新新发布的标签和 image ID，但不重启应用。

同一个 `release-id` 已安装时，安装器只接受完全相同的发布记录和镜像 ID，不会基于后来
变化的基础镜像重新构建或覆盖原标签。

服务器首次构建需要已有或能够拉取 `eclipse-temurin:21-jre` 与 `nginx:alpine`。如果已
受控导入基础镜像而服务器不能访问镜像源，可为本次安装显式设置
`ECOBIN_PULL_RUNTIME_BASE_IMAGES=false`；最终本地 image ID 校验仍不能省略。

安装成功后可以删除本次 `/tmp` 暂存，但必须保留
`/var/lib/ecobin/releases/<release-id>/` 和对应镜像：

```bash
rm -rf -- "${staging_root}"
rm -f -- \
  "/tmp/ecobin-release-${release_id}.tar.gz" \
  "/tmp/ecobin-release-${release_id}.tar.gz.sha256"
```

## 8. 激活新版本

每次激活前都重新暂存运行秘密并执行生产预检：

```bash
sudo systemctl restart ecobin-stage-runtime-secrets.service
sudo /usr/local/sbin/ecobin-production-preflight
```

预检会核对：发布 ID、两个标签、不可变 image ID、镜像内 Git/制品标签、发布记录、
Fake/Real 配置、通知根域名、运行秘密目录、持久日志目录的 UID/权限、内部数据库网络和
Compose 语法。发布安装器会创建 `/var/log/ecobin/backend`，systemd 启动前还会再次确保
它是 `10001:10001 0750`。

如果应用已经运行，使用 reload 让 Compose 按新标签重建变更的容器并等待健康：

```bash
sudo systemctl reload ecobin-target-app.service
```

如果是第一次启动目标应用，则使用：

```bash
sudo systemctl enable --now ecobin-target-app.service
```

禁止直接执行带 `--build` 的 Compose，也不要绕过 systemd 手工启动另一套同名容器。

## 9. 部署后验证

先确认 systemd 和容器健康，再验证回环入口与秘密边界：

```bash
sudo systemctl status ecobin-target-app.service --no-pager
curl --fail --silent http://127.0.0.1:18080/ >/dev/null
sudo docker exec ecobin-target-backend \
  /usr/local/bin/ecobin-backend-healthcheck
sudo /usr/local/sbin/ecobin-runtime-secret-probe
sudo test "$(stat -c '%u:%g:%a' /var/log/ecobin/backend)" = \
  '10001:10001:750'
sudo test -s /var/log/ecobin/backend/ecobin.log
```

后端仍向控制台输出，因此 `sudo docker logs -f --tail 200 ecobin-target-backend` 可实时查看；
需要跨容器版本回查时使用 `/var/log/ecobin/backend/ecobin.log`、`errors.log` 和
`archive/*.gz`。

如果公网入口已经启用，再从外部检查 `https://www.jinshoubao.com`。涉及真实 OneNet、COS、
微信充值或提现的代码变更，还必须执行对应的小流量真实验收；容器健康只能证明应用可运行，
不能证明外部资金或设备闭环正确。

每次发布至少记录以下非秘密证据：

- 发布时间、操作者、`ReleaseId` 和完整 Git commit；
- 安装器输出的后端/Web image ID；
- 数据库 epoch（如果本次涉及迁移）；
- 生产预检、systemd 健康、回环请求和运行时秘密探针的结果；
- 持久日志目录权限及新容器成功写入 `ecobin.log` 的结果；
- 本次是否执行真实渠道/设备验收及其脱敏结论。

## 10. 失败处理与应用版本回退

安装器只更新部署参数，不会立即停止旧容器。因此安装或预检失败时，旧应用通常仍在
运行；先保留错误输出和发布目录，修复原因，不要删除数据库或使用 `docker compose
down -v`。

如果新容器激活失败，且上一版本与当前数据库仍兼容，可恢复安装器保存的上一份部署参数：

```bash
sudo systemctl stop ecobin-target-app.service
sudo install -o root -g root -m 0600 \
  /etc/ecobin/deployment.env.previous \
  /etc/ecobin/deployment.env
sudo /usr/local/sbin/ecobin-production-preflight
sudo systemctl start ecobin-target-app.service
```

`deployment.env.previous` 只保存上一次切换前的部署参数。第一次安装时它可能仍是占位配置，
不能假设一定可回退；每次稳定发布后再开始下一次发布，避免覆盖仍需要的上一版本入口。

数据库迁移属于独立的前向过程。不得通过删除数据、降低 Flyway/epoch 版本或切回不兼容
旧应用来“回滚”数据库。若新后端依赖不可向后兼容的数据库变更，应在部署前设计兼容窗口
和前滚修复方案。

## 11. 脚本与配置索引

| 用途 | 仓库文件 | 服务器位置/产物 |
|---|---|---|
| 本地编译和生成发布包 | [`New-EcobinLocalRelease.ps1`](../../tools/deployment/New-EcobinLocalRelease.ps1) | `release-output/` |
| 后端运行时镜像 | [`backend.Dockerfile`](../../deploy/production/runtime-images/backend.Dockerfile) | 随发布包上传 |
| Web 运行时镜像 | [`web.Dockerfile`](../../deploy/production/runtime-images/web.Dockerfile) | 随发布包上传 |
| 安装发布并写镜像身份 | [`ecobin-install-local-release.sh`](../../tools/deployment/ecobin-install-local-release.sh) | `/usr/local/sbin/ecobin-install-local-release` |
| 生成只读运行秘密目录 | [`ecobin-stage-runtime-secrets.sh`](../../tools/deployment/ecobin-stage-runtime-secrets.sh) | `/usr/local/sbin/ecobin-stage-runtime-secrets` |
| 根据功能开关组合并执行 Compose | [`ecobin-target-app-compose.sh`](../../tools/deployment/ecobin-target-app-compose.sh) | `/usr/local/sbin/ecobin-target-app-compose` |
| 激活前生产门禁 | [`ecobin-production-preflight.sh`](../../tools/deployment/ecobin-production-preflight.sh) | `/usr/local/sbin/ecobin-production-preflight` |
| 容器秘密和隔离探针 | [`ecobin-runtime-secret-probe.sh`](../../tools/deployment/ecobin-runtime-secret-probe.sh) | `/usr/local/sbin/ecobin-runtime-secret-probe` |
| 应用 Compose | [`docker-compose.target-app.yml`](../../deploy/production/docker-compose.target-app.yml) | `/etc/ecobin/compose/docker-compose.target-app.yml` |
| 远程维护 Compose 叠加 | [`docker-compose.remote-support.yml`](../../deploy/production/docker-compose.remote-support.yml) | `/etc/ecobin/compose/docker-compose.remote-support.yml` |
| systemd 应用入口 | [`ecobin-target-app.service`](../../tools/deployment/systemd/ecobin-target-app.service) | `/etc/systemd/system/ecobin-target-app.service` |
| 完整首次部署 | [`target-single-host-deployment.md`](target-single-host-deployment.md) | 文档 |
| 配置、密钥和证书 | [`production-configuration-secrets-certificates.md`](production-configuration-secrets-certificates.md) | 文档 |
| 目标数据库升级 | [`h02-target-database.md`](h02-target-database.md) | 文档 |
| 业务更新生产只读门禁 | [`ecobin-business-remote-readiness.sh`](../../tools/deployment/ecobin-business-remote-readiness.sh) | `/usr/local/sbin/ecobin-business-remote-readiness` |
| 业务更新下发开关 | [`ecobin-business-release-dispatch.sh`](../../tools/deployment/ecobin-business-release-dispatch.sh) | `/usr/local/sbin/ecobin-business-release-dispatch` |
