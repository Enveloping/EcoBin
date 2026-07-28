# V-02｜机构用户注册、手机号、钱包与管理绑定软件证据

> 验证日期：2026-07-28
> 任务：[V-02 机构用户首次注册并获得独立零余额钱包](../planning/tasks/p0-controlled-loop/v-02-organization-user-registration-wallet.md)
> 阶段：software `done`；integration `done`；acceptance `done`（项目负责人接受受限
> 微信开发环境例外，真实来源归因另由非阻塞 P0-FOLLOWUP-01 跟踪）

## 1. 实施结果

- 小程序以机构 AppID 定位启用配置，在事务外调用 `code2session`，事务内按
  tenant → organization → miniapp 的稳定顺序加锁。首次 `AppID + OpenID` 创建机构
  用户、不可变注册归因、零余额钱包和一条持久化会话；重复或并发首次登录收敛到同一用户
  和钱包。
- 注册来源由 identity 定义公开的消费者形状端口，device 实现并签发同线程、同事务、
  一次性强类型持久化引用。funds 只消费 identity 签发的钱包 owner 引用，不查询
  identity 私表，也不接收可序列化的内部数据库主键。
- 小程序 JWT 只保存最小主体、会话、audience 和时间声明，每次请求重新读取会话、
  用户、工作人员绑定、租户、机构和 AppID 当前事实。旧 JWT 过滤器仅保留在旧安全链，
  不再以 Servlet 全局过滤器重复解析目标 token。
- 登录入口按 `MANAGEMENT > CLEANING > USER` 选择唯一模式。工作人员管理绑定有效时
  创建 `MINIAPP_STAFF` 会话；否则按机构用户清运能力或普通用户进入对应入口。
- 手机号只接受微信 `getPhoneNumber` 动态码。外部换号在事务外完成；事务内校验当前
  会话和用户、同机构唯一性、首次绑定及 UUIDv4 幂等键。幂等摘要使用最终规范手机号，
  不保存动态码，响应、日志和普通审计只使用脱敏号码。
- Web 新增正文提交的精确手机号查找和工作人员小程序绑定台。设置绑定必须显式提交两侧
  可空版本快照；命令在稳定锁序下撤销工作人员侧和用户侧冲突绑定、撤销相关管理会话、
  写入新绑定并撤销目标普通小程序会话。
- Web 后端补齐 I-015 的机构用户分页列表、详情、冻结/恢复和 `CLEAN_OPERATION`
  授予/撤销，以及相同资源后缀的平台协助入口。列表支持状态、手机号绑定、注册区间、
  来源部署和清运能力筛选；响应不包含 OpenID、完整手机号、内部主键或钱包数据。
- 机构用户状态和能力命令同时校验 `expectedVersion + expectedAuthVersion`，在事务内
  重新鉴权并锁定用户；成功后两个版本各递增一次、写安全审计并只撤销普通/清运小程序
  会话。冻结保留钱包、历史、工作人员绑定和清运能力，恢复不恢复旧会话。
- 注册来源展示和筛选由 identity 定义只使用公开 UUID/公开码的查询端口，device 实现
  安全摘要查询；identity 不读取 device 私表，端口也不暴露裸内部主键。
- integration 新增真实微信适配器和 Fake 适配器。真实模式的 AppSecret 只接受
  `env:ECOBIN_*` 外部引用；登录 code、动态手机号 code、OpenID、完整手机号、token、
  AppSecret 和微信响应正文都不进入普通日志。
- Web 管理端新增机构用户身份核验与绑定页面；小程序用原生
  `button open-type="getPhoneNumber"` 完成手机号授权，并新增受限管理入口页面。
  OpenAPI 同步覆盖小程序登录/手机号和租户、平台两套人工绑定接口。

## 2. MySQL 8.4 专项

`TargetMiniappV02MysqlIntegrationTest` 使用 Flyway V1～V10 目标库、锁定触发器
definer、`ecobin_app` 运行身份和真实 epoch guard，执行五项端到端场景：

1. 直接注册、重复登录、零余额钱包、手机号绑定幂等、同机构手机号唯一、可信设备来源
   固定、Web 双快照人工绑定、管理入口优先、冲突会话撤销、解除绑定及审计脱敏；
2. 注入钱包参与端失败后验证用户和钱包整体回滚，再以两个并发首次登录验证唯一用户、
   唯一钱包和各请求独立有效会话；
3. 两个独立操作员交叉换绑两组工作人员和机构用户，验证稳定锁顺序避免死锁，且竞争
   结果收敛为一项成功、一项冲突和唯一有效绑定。
4. 机构用户目录按状态、手机号、来源部署和清运能力筛选；授予/撤销清运能力、冻结和
   恢复均验证双版本、幂等重放、业务状态错误及普通小程序会话即时失效。
5. 同一微信身份使用两个机构 AppID 登录，验证生成不同公开用户 UUID、允许同一手机号
   在两个机构各自绑定，并各自拥有唯一零余额钱包。

结果：`Tests run: 5, Failures: 0, Errors: 0, Skipped: 0`。测试连接本地 Docker
MySQL 8.4 开发库，按每轮唯一租户、机构、AppID 和公开 UUID 插入隔离测试数据，不覆盖
既有开发数据；未使用生产数据库或生产凭据。

## 3. 复验证据

```powershell
.\mvnw.cmd test

$env:ECOBIN_V02_MYSQL_URL = 'jdbc:mysql://127.0.0.1:<port>/ecobin_v02_test'
$env:ECOBIN_V02_MYSQL_USERNAME = 'ecobin_app'
$env:ECOBIN_V02_MYSQL_PASSWORD = '<ephemeral-test-secret>'
.\mvnw.cmd -pl ecobin-bootstrap -am `
  -Dtest=TargetMiniappV02MysqlIntegrationTest `
  -Dsurefire.failIfNoSpecifiedTests=false test

Set-Location frontend/web
npm run test:http-foundation
npm run build

Set-Location ../miniprogram
npx tsc --noEmit
```

结果：

- Maven 九模块 reactor：111 项，0 失败、0 错误、12 项条件测试按设计跳过；
- V-02 MySQL 8.4 专项：5 项全部执行并通过；
- Web HTTP 基础测试：3 项全部通过，TypeScript/Vite production build 通过；
- 小程序 TypeScript：通过；
- HTTP OpenAPI 官方校验 5 项通过：94 个唯一 `operationId`、822 个本地 `$ref` 和
  7 个示例全部有效；
- `git diff --check` 在最终变更审计中通过。

全合同生成校验仍报告既有 `hardware_mcu/USER/uar` 两个 UART 生成物漂移；本轮仅建设
后端，没有改写硬件生成物。HTTP 契约已由专用官方校验器独立通过。

## 4. 主审接受的阶段边界与非阻塞跟进

software 已关闭，真实 AppID/AppSecret 的 `wx.login → code2session → 后端会话` 已在
开发环境验证。项目负责人于 2026-07-28 明确将 V-02 裁定为 `done`，接受以下当前环境
边界且不让其阻塞下游任务：

1. 真实 `wx.login`、直接注册和当前小程序页面行为已验证；
2. 个人主体无法成功调用真实 `getPhoneNumber`，当前实现继续调用微信官方 API，并在
   失败时同时显示和记录微信返回错误；
3. 未绑定用户通过 `phoneBound=false` 明确表达，实际投递/提现命令门禁分别由
   V-04/V-10 的纵向切片承接；
4. 工作人员绑定、入口优先级和安全脱敏由软件与 MySQL 证据覆盖，后续真实发布环境仍可
   复验，但不再作为 V-02 阻塞门。

其中可信设备小程序码在开发环境中已能打开登录入口，但两次重建测试账号后数据库来源
仍为空。后端 Java 21 MySQL 单项测试证明请求携带部署码时能够正确落库；由于开发版
分发、缓存和小程序生命周期入口无法在当前环境稳定区分，项目负责人决定停止本轮试错，
并将真实上线后的诊断与验收独立记录为
[P0-FOLLOWUP-01](../planning/tasks/p0-controlled-loop/p0-followup-01-wechat-qr-registration-attribution.md)。
该延期不构成设备来源注册通过的证据。
