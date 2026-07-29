# Web 管理端能力地图

> 状态日期：2026-07-29
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
| `/organizations` | Web 账号 | `organization.read` | `organization.manage` | 已接入；名称可深链机构用户，含关联数据导航 |
| `/organization-users` | Web 账号 | `user.read` | `user.freeze` | 已接入；含全部/已禁用预设、详情与冻结/恢复 |
| `/staff` | Web 账号 | `staff.read`、`permission.read`（授权页签） | `staff.manage`、`permission.manage` | 已接入；账号安全、租户权限和机构任职统一从“编辑”进入 |
| `/user-bindings` | Web 账号 | `user.read` 且 `staff.bind` | `staff.bind` | 已接入；手机号只进入请求体 |
| `/devices` | Web 账号 | `device.read` | - | 已接入；按 URL 机构作用域查询部署与配置/连接摘要 |
| `/account` | 租户主体、工作人员 | 当前会话 | 本人资料与密码命令 | 已接入 |

旧 `/access` 只重定向到 `/staff`，不再保留独立“任职与授权”页面。租户、机构、机构用户
和设备表格使用 URL 中的 `tenant`、`organization`、`organizationUserUid` 传递非敏感
作用域；完整手机号等隐私字段不得进入 URL。列表默认开放列设置并按页面持久化，工作人员
“安全版本”默认隐藏。

路由支持两种能力组合：

- `allOf`：必须同时具备全部能力；
- `anyOf`：具备任一能力即可，用于后续多角色共享的查询或审核入口。

菜单隐藏与直接地址访问使用同一判断；页面内写按钮继续按更细的真实能力控制。前端控制
只改善交互，不替代服务端授权。

## 3. 业务能力进度

| 业务切片 | Web 目标范围 | 当前状态 | 进入实施的前置证据 |
|---|---|---|---|
| 身份与组织 | 租户、机构、工作人员、机构用户、任职授权、人工绑定、账号安全 | 可用 | 已有 OpenAPI paths/schema 与后端实现 |
| 设备 | 设备部署列表、连接与配置摘要 | 可用（只读首切片） | 资产写入、投口详情和配置命令继续按真实能力逐步接入 |
| 投递 | 订单、审核纠错、待返现与用户钱包查询 | 仅导航占位 | 原始事实/认定版本/金额字段和运行时 Web paths |
| 清运 | 清运操作、袋、满溢、基准和恢复 | 仅导航占位 | 可恢复操作、异常分支、设备结果 schema 和运行时 Web paths |
| 资金 | 机构充值、额度、提现审核与渠道状态 | 仅导航占位 | 金额字符串、双侧冻结、渠道终态和运行时 Web paths |
| 运营 | 概览、告警、审计、对账与技术任务 | 等待契约 | 只读投影、游标分页、任务状态和审计契约 |

等待项只显示明确的“后端接口尚未接入”，不建立模拟业务终态页面，也不复活旧接口。
当前投递目标契约只有 `PENDING/APPROVED`，没有“已拒绝订单”；“已纠正”也尚无列表
筛选参数。机构响应目前没有余额字段，前端不会用 `0` 或旧表字段伪造余额。对应纵向契约
可运行后，再把现有导航和作用域深链接到真实表格。

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
