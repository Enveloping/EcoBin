# F-01｜九模块骨架与过渡退出清单

> 状态：**已实施**
>
> 实施日期：2026-07-24
>
> 目标：只改变物理模块、装配和外部适配边界，保持旧可观察业务行为。

## 1. 当前 reactor

目标九模块已经全部建立：

| 目标模块 | F-01 结果 |
|---|---|
| `ecobin-common` | 保留旧共享代码，后续由 F-03 收紧 |
| `ecobin-framework` | 只保留 Security/JWT、租户、MyBatis、加密和异常处理 |
| `ecobin-module-identity` | 建立 POM、模块配置、`.api` 和微信会话出站端口 |
| `ecobin-module-device` | 建立显式 Mapper 配置及设备下行/COS 凭证端口 |
| `ecobin-module-funds` | 建立 POM、模块配置和 `.api` 骨架 |
| `ecobin-module-recycling` | 建立 POM、模块配置和 `.api` 骨架 |
| `ecobin-module-operations` | 建立 POM、模块配置和 `.api` 骨架 |
| `ecobin-integration` | 承接 OneNet/Pulsar、COS 和微信实现 |
| `ecobin-bootstrap` | 显式组装目标模块和过渡模块，不再通配扫描 Mapper |

F-02/F-03 完成前，根 reactor 还包含两个 legacy 过渡模块：

- `ecobin-module-system`；
- `ecobin-module-business`。

因此 F-01 结束时是“九个目标模块已存在 + 两个 legacy 模块待退出”的 11 子模块过渡
reactor，不把尚未完成的业务搬迁伪装成最终九模块收口。

## 2. 外部适配迁移

| 能力 | F-01 前 | F-01 后 |
|---|---|---|
| OneNet 北向 Pulsar/解密 | `ecobin-framework/.../onenet` | `ecobin-integration/.../onenet/inbound` |
| OneNet 业务事件分发 | `ecobin-module-business/.../onenet` | `ecobin-integration/.../onenet/inbound` |
| OneNet 下行/token | `ecobin-framework/.../onenet` | `ecobin-integration/.../onenet/outbound` |
| COS STS | `ecobin-framework/.../cos` | `ecobin-integration/.../cos` |
| 微信 code2session | `ecobin-framework/.../wechat` | `ecobin-integration/.../wechat` |

`ecobin-framework` 的 POM 已移除 COS STS 和 Pulsar 依赖；相关 SDK 只由
`ecobin-integration` 声明。

## 3. 端口反转

| 消费模块定义的端口 | integration 实现 |
|---|---|
| `identity.api.port.WechatSessionPort` | `WechatMiniappClient` |
| `device.api.port.DeviceCommandGateway` | `OneNetClient` |
| `device.api.port.CosUploadCredentialPort` | `CosTokenClient` |

端口返回 EcoBin 自有的不可变 record，不向 identity/device 暴露腾讯云、微信或 OneNet
SDK 类型。OneNet 入站消费者与出站客户端是不同 Bean。

## 4. Mapper 装配

- bootstrap 已移除 `@MapperScan("org.enveloping.ecobin.**.mapper")`；
- legacy system、legacy business 和 device 各自扫描本模块 Mapper；
- identity、funds、recycling、operations 已建立各自目标持久化包的显式扫描边界；
- 目标模块尚无 Mapper 时会产生“未发现 Mapper”提示，但不会回退为全项目通配扫描。

## 5. F-02/F-03 退出清单

| legacy 范围 | 当前暂存模块 | 退出任务 |
|---|---|---|
| 管理员、租户、用户、认证、角色 | `ecobin-module-system` | F-02 搬入 identity 后删除 |
| 钱包、提现 | `ecobin-module-business` + system 用户余额字段 | F-03 搬入 funds，并删除旧余额耦合 |
| 投递及其审核、清运、统计 | `ecobin-module-business` | F-03 按事实所有权搬入 recycling/operations |
| 设备 Entity/Mapper 直连 | business → device 私有实现 | F-03 改为 device `.api` 端口 |
| 用户 Entity/Mapper 直连 | business → system 私有实现 | F-02/F-03 改为 identity/funds 公开端口 |
| 旧 OneNet 业务字段和状态机 | integration 中的 legacy dispatcher | F-11/纵向切片按 F-10 契约整体替换 |
| common 中 MyBatis 基类和业务枚举 | `ecobin-common` | F-03 收紧为小型纯 Java 共享内核 |
| bootstrap 中旧 V1～V14 | `db/migration` | F-07 前只供旧栈恢复；目标库使用独立迁移纪元 |

在这些退出任务完成前，不得向 legacy system/business 增加目标新业务行为，也不得为旧
OneNet 协议建立生产双协议回退。

## 6. 验证

| 验证 | 结果 |
|---|---|
| Java | 21.0.10 |
| Reactor | parent + 11 子模块全部成功 |
| `mvn.cmd test` | 59 tests，0 failures/errors/skipped |
| `mvn.cmd install -DskipTests` | 成功，最新模块已安装到本地仓库 |
| framework 外部平台源码扫描 | 0 |
| bootstrap 全局 MapperScan 扫描 | 0 |
| 旧 Controller 行为 | 既有集成测试全部保持通过 |

