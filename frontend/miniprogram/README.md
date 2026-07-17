# EcoBin 微信小程序（终端用户端）

微信原生小程序（TypeScript）。当前页面主要根据产品设计稿制作，登录、扫码/填写投口 ID 开门等核心入口已接入后端；AppID：`wx1e05b648c1d16f52`。

## 当前页面

底部导航固定为“首页 · 扫码开门 · 我的”。页面还包括附近设备、订单列表、预约记录、上门回收、绑定号码、故障上报、联系客服、招商加盟、优选商城和统一占位页。

`miniprogram/config/index.ts` 中的 `test` 控制扫码入口：`true` 时可选择扫码或填写投口 ID，`false` 时直接扫码。识别成功后调用 `POST /api/app/delivery/open`，后端激活设备用户会话并经 OneNet 下发开门指令；订单在设备完成称重并上报后生成。

## 目录结构

```
miniprogram/
├── app.ts / app.json            # 入口；app.json 启用 custom tabBar + 全局 TDesign 组件
├── config/index.ts              # baseURL、超时、storage key
├── config/roleTabs.ts           # 角色 → tab 列表（扩展点）
├── utils/request.ts             # wx.request 封装：Bearer 头 / Result 解包 / 401 跳登录
├── utils/auth.ts                # 微信静默登录、token/role 读写、logout
├── utils/guard.ts               # 页面级角色守卫 requireRole
├── api/                         # auth/delivery/clean/wallet/profile/device
├── types/api.d.ts               # 与后端 DTO 对齐的类型
├── custom-tab-bar/              # 首页 · 中央扫码 · 我的
└── pages/
    ├── home/     设计稿首页与扫码入口
    ├── profile/  设计稿“我的”页面
    ├── pickup/   上门回收三步流程
    └── ...       其他设计稿页面
```

## 登录与接口接入

小程序冷启动先进入 `pages/login/login`：本地 JWT 仍有效时直接进入首页；没有 Token 或 Token 已过期时，调用 `wx.login` 获取临时 code，再请求 `POST /api/system/auth/wx-login`。后端按 AppID 定位租户、按 openid 查找用户，首次登录会自动注册普通用户并返回 JWT。扫码或填写投口 ID 开门前也会复用同一登录检查，避免未登录请求业务接口。

## 本地运行

1. **改后端地址**：`miniprogram/config/index.ts` 的 `BASE_URL`（默认 `http://localhost:8080`）。
2. **构建 npm**：开发者工具 → 工具 → 构建 npm（已 `npm install tdesign-miniprogram`）。
3. **关闭域名校验**：开发者工具 → 详情 → 本地设置 → 勾选「不校验合法域名…」（开发期）。
4. **启动后端**：`./mvnw spring-boot:run -pl ecobin-bootstrap`（改过非 bootstrap 模块需先 `mvn install`）。
5. **后端数据前置**：该租户 `sys_tenant.miniapp_appid` 必须 = `wx1e05b648c1d16f52`，且配好 `miniapp_secret`，否则 wx-login 无法定位租户。

## 角色分流验证

在 DB 改 `sys_user.role`（1/2）后重新进入小程序：
- role=1：tabBar 无「清运」；手动进 `pages/clean` 被守卫弹回首页。
- role=2：tabBar 出现「清运」；可选设备/投口并提交清运。

## 类型检查

```bash
npx tsc --noEmit -p tsconfig.json
```

（`tsconfig.json` 已开启 `skipLibCheck`，跳过 wx 官方 typings 自身的声明告警。）
