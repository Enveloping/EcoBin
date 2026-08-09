# Web 管理端能力地图

> 状态日期：2026-08-09
> 适用目录：`frontend/web/`
> 机器契约：`contracts/http/openapi.yaml`

## 1. 当前边界

Web 管理端只调用同源 `/api/v1/**`，使用 `Secure + HttpOnly` Cookie 会话、SPA CSRF
和服务端实时能力。客户端不保存 Bearer Token，不接受旧 `/api/**` 路由，也不根据历史
数字角色推导权限。

平台管理员与租户/工作人员使用不同登录入口，但共用一个浏览器 Cookie。前端记住的
登录域只用于优先探测；启动时若该入口返回会话类 `401`，还会检查另一入口，以适应其他
标签页替换 Cookie 受众的合法场景。只有两类入口都返回 `401` 才判定未登录；网络或服务
异常会保留当前地址并显示“无法确认登录状态”，不会伪装成匿名会话。登录成功、恢复会话
和登出后都保留最后使用的非凭据入口偏好。

平台管理员必须在 URL 查询参数 `tenant` 中显式携带目标租户。`sessionStorage` 仅记录
下次进入时的候选默认值，不是授权来源；后端仍对每次平台特权访问做租户寻址和审计。

## 2. 已接入页面

| 路由 | 账号范围 | 页面读取能力 | 页面内写能力 | 状态 |
|---|---|---|---|---|
| `/tenant` | 平台管理员 | `tenant.read` | `tenant.manage` | 已接入；含全部/已禁用预设、主体账号与状态编辑 |
| `/my-tenant` | 租户主体、工作人员 | `tenant.read` | `tenant.manage` | 已接入 |
| `/organizations` | Web 账号 | `organization.read` | `organization.manage`、`miniapp.manage`、`delivery.configuration.manage` | 已接入；名称可深链机构用户，编辑内含小程序登录与投递规则配置 |
| `/organization-users` | Web 账号 | `user.read` | `user.freeze` | 已接入；含全部/已禁用预设、详情与冻结/恢复 |
| `/wallet-entries` | Web 账号 | `wallet.read` | - | 已接入；机构真实钱包流水、稳定游标和来源单号跳转 |
| `/staff` | Web 账号 | `staff.read`、`permission.read`（授权页签） | `staff.manage`、`permission.manage` | 已接入；账号安全、租户权限和机构任职统一从“编辑”进入 |
| `/user-bindings` | Web 账号 | `user.read` 且 `staff.bind` | `staff.bind` | 已接入；手机号只进入请求体 |
| `/devices` | Web 账号 | `device.read` | `device.configuration.manage`、`device.assignment.manage`；平台固定用例另管理资产、租户永久分配、自动验收复核、禁用/恢复和报废 | 已接入；永久资产、一次性归属、运行事实、配置应用和自动机器验收证据 |
| `/clean-operations` | Web 账号 | `clean.read` | - | 已接入；清运操作状态、边缘保存/可能解锁等安全事实、袋码和关联记录 |
| `/clean-records` | Web 账号 | `clean.read` | `clean.edit` | 已接入；完成记录、设备原始/复算重量、照片、异常、当前有效值和只追加修正历史 |
| `/account` | 租户主体、工作人员 | 当前会话 | 本人资料与密码命令 | 已接入 |

旧 `/access` 只重定向到 `/staff`，不再保留独立“任职与授权”页面。租户、机构、机构用户
和设备表格使用 URL 中的 `tenant`、`organization`、`organizationUserUid` 传递非敏感
作用域；完整手机号等隐私字段不得进入 URL。列表默认开放列设置并按页面持久化，工作人员
“安全版本”默认隐藏。

机构编辑中的“小程序登录”页签仅向具有 `miniapp.manage` 的当前操作者开放，支持首次写入
AppID、展示名称和 AppSecret，后续轮换密钥、激活 AppID，以及启用/停用机构小程序身份
登录。AppID 激活后不可修改；启用登录前必须先完成激活，停用会撤销该机构现有小程序
会话。激活只代表后端本地配置已锁定，不代表微信平台验证成功。

授权配置详情会按冻结契约返回完整 AppSecret。前端仅在机构编辑窗口存活期间把它保存在
组件内存中，以密码框默认遮挡；不会自动回填到轮换字段，也不会放入 URL、浏览器存储或
日志。所有配置命令携带调用方持有的幂等键和权威版本，版本冲突后重新加载并要求操作者
再次确认。

机构编辑中的“投递规则”页签仅向具有 `delivery.configuration.manage`
的操作者开放，与独立 `/delivery-configuration` 页共用同一个版本面板。
当前可查看审核方式、负余额停投下限、人工认定重量上限和历史版本；
调整时发布新的不可变版本，不覆盖历史。已开始的投递会话和订单
继续使用开始时冻结的旧规则。

路由支持两种能力组合：

- `allOf`：必须同时具备全部能力；
- `anyOf`：具备任一能力即可，用于后续多角色共享的查询或审核入口。

菜单隐藏与直接地址访问使用同一判断；页面内写按钮继续按更细的真实能力控制。前端控制
只改善交互，不替代服务端授权。

## 3. 业务能力进度

| 业务切片 | Web 目标范围 | 当前状态 | 进入实施的前置证据 |
|---|---|---|---|
| 身份与组织 | 租户、机构、工作人员、机构用户、任职授权、人工绑定、账号安全 | 可用 | 已有 OpenAPI paths/schema 与后端实现 |
| 设备 | 平台资产登记、自动机器验收、租户/机构一次性永久归属、整机/投口运行事实、完整配置版本和应用跟踪 | 可用 | OneNet 设备预建、Device Key 写入香橙派以及真实 MCU/摄像头联网仍是受控厂家步骤；验收结果由机器证据自动形成 |
| 投递 | 机构规则、订单列表/详情、初审与纠错 | 可用 | 已支持规则版本发布和钱包来源单号深链打开投递详情 |
| 清运 | 清运操作、完成记录、袋、满溢、基准和恢复 | 操作与记录查询可用；袋/满溢/基准/恢复管理页待接入 | 当前接口已覆盖七种正式操作状态和记录直接修正；再次解锁、原人恢复及设备筛选仍等待跨端闭环 |
| 资金 | 用户钱包；机构充值、额度、提现审核与渠道状态 | 用户钱包只读可用；其余仅导航占位 | 机构资金与提现继续等待各自运行时 Web paths |
| 运营 | 概览、告警、审计、对账与技术任务 | 等待契约 | 只读投影、游标分页、任务状态和审计契约 |

等待项只显示明确的“后端接口尚未接入”，不建立模拟业务终态页面，也不复活旧接口。
设备页面保留唯一 `/devices` 入口，但按账号显示不同范围。平台管理员登记真实资产、
查看机器验收证据、永久分配一次租户，并可禁用、恢复或永久报废资产；平台登记本身
不调用 OneNet，也不生成或显示 Device Key。租户主体及同时具有 `device.read` 和
`device.assignment.manage` 的工作人员只看到本租户仍为 `NORMAL` 的资产，并只能把
尚未归属机构的资产永久分配一次。普通机构工作人员只看到已授权机构仍为 `NORMAL`
的设备；已禁用或已报废设备会立刻从租户和机构列表消失，平台仍保留历史与审计视图。

设备没有租户池、机构部署记录、退回、调拨、重新分配或人工经营开关。自动机器验收
发生在租户分配之前：真实设备联网后提交 MCU、双摄像头、协议与运行证据，系统自动计算
`PENDING/FAILED/PASSED`；平台的“重新读取验收证据”只重算既有机器证据，不是人工通过。
机构归属写入后，系统自动下发初始配置并重测厂家随设备安装的空袋皮重。机构人员只需
安装、通电和联网，之后可按权限维护日常价格与配置，不需要确认安装、验收、激活或经营。

配置发布与重同步真实校验 HTTP `202 + Location`，按服务端
`recommendedPollAfterMs` 轮询。当前 `fixed-frame` 运行例外下，`APPLIED` 只证明
香橙派已可靠保存并启用配置，不宣称 MCU 已真实接收。

设备列表和运行事实不得把“在线”压成一个状态：`oneNetConnectionStatus` 表示
OneNet 传输连接，`edgeConnectionStatus` 表示 OneNet 已在线且可信运行快照仍在
后端心跳窗口内的业务有效在线。列表可分别筛选两层状态；抽屉同时展示 OneNet
状态观测时间和可信运行事实接收时间。OneNet 在线但业务有效离线时，页面明确提示
普通业务命令仍会被阻止，不能把传输连接当作设备已经可以作业。

投递页面已经接入目标游标查询、四图/异常/修订详情、初审和纠错命令；设备事实保持只读，
金额由后端按锁定单价计算。目标契约只有 `PENDING/APPROVED`，没有“已拒绝订单”；
“已纠正”可在当前页按修订号识别，但仍没有独立服务端列表筛选。机构响应目前没有余额
字段，前端不会用 `0` 或旧表字段伪造余额。

清运管理分为两个入口：`/clean-operations` 展示尚未形成记录之前的设备执行过程，
`/clean-records` 只展示设备完成后形成的业务记录。正式操作状态只有
`PREPARED/EDGE_SAVED/IN_PROGRESS/RECOVERY_REQUIRED/PRE_UNLOCK_ENDED/COMPLETED/ABORTED`；
前端不兼容旧 `PRE_OPEN_ENDED`。普通失败无法证明 MCU 是否已收到字节时展示“需要恢复”并
保留占用；只有明确解锁前失败才展示“解锁前结束”。记录没有审核、通过或拒绝，
`clean.edit` 只可设置/清空当前有效净重量或备注，每次保存追加操作者、原因和前后值。

钱包页面独立要求 `wallet.read`，不会借用 `user.read` 或 `delivery.read`。机构用户页在
同时拥有 `user.read + wallet.read` 时提供钱包摘要和个人流水抽屉；独立钱包页仍允许只
具备 `wallet.read` 的账号按当前会话机构范围查询。机构流水按服务端不透明游标翻页，
不解析或拼装游标；投递来源号只有在当前会话另有 `delivery.read` 时才可打开投递详情。

## 4. 每个后续切片的交付门槛

1. 在 `contracts/http/openapi.yaml` 增加完整 paths、schemas、错误和示例，并提供平台显式
   镜像入口（如该能力允许平台访问）。
2. 后端实现通过权限、租户/机构隔离、幂等、版本冲突和失败分支测试。
3. 运行 `npm run api:types`，提交生成的 TypeScript 类型，并通过
   `npm run api:types:check`。
4. 在 `src/api/` 编写薄 HTTP 包装；页面只从稳定别名层读取生成类型。
5. 新增懒加载路由、页面加载/空/错误状态和真实能力控制。
6. 增加 Playwright 覆盖：成功、无权限、版本冲突、可重试失败与窄视口。
7. 通过直接入口 500 KiB gzip 预算；不能通过调高 Vite 警告阈值掩盖体积增长。

所有写操作由页面为一次用户意图创建幂等键。可重试网络失败复用该键；成功、终态失败、
目标或载荷变化后创建新意图。版本冲突必须刷新权威数据并要求操作者重新确认，不做假
乐观终态。

## 5. 本地验证

在 `frontend/web/` 运行：

```powershell
npm run api:types:check
npm run test:http-foundation
npm run test:architecture
npm run build
npm run test:bundle-budget
npm run test:e2e
```

契约变更还需从仓库根目录运行：

```powershell
python -B contracts/tools/http_contract.py
python -B -m unittest contracts.tests.test_http_contract -v
```
