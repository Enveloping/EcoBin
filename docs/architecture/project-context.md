# EcoBin 项目上下文（Claude Code → Codex）

> 整理日期：2026-07-11。来源为项目 `CLAUDE.md`、`~/.claude/projects/C--D-004-Project-002-Java-EcoBin/memory/`、近期 Claude Code 会话/计划、仓库文档与 Git 状态。
> 本文记录对话中形成、仅靠代码不容易恢复的决策。代码和更新日期更晚的专题文档若与本文冲突，以较新的事实为准。

## 1. 项目与组件

EcoBin 是智慧环保回收箱系统：Spring Boot 4.0.6 + Java 21 的 Maven 多模块后端，配套 Web 管理后台、微信小程序和香橙派设备程序。

- `ecobin-common`：公共实体、响应、异常。
- `ecobin-framework`：Security/JWT、多租户、OneNet 传输层、通用基础设施。
- `ecobin-module-system`：管理员、租户、用户、认证。
- `ecobin-module-device`：设备、投口、设备会话。
- `ecobin-module-business`：投递、清运、钱包、统计、OneNet 业务事件分发。
- `ecobin-bootstrap`：依赖组装、配置、Flyway、启动入口。
- `frontend/web`：React 18 + TypeScript + Vite + Ant Design/ProComponents。
- `frontend/miniprogram`：原生 TypeScript 微信小程序 + TDesign。
- `hardware`：香橙派 Python 设备侧；目标运行时 Python 3.11，1 GB 内存，不运行 Chromium。

后端结构、命令和通用约定见根目录 `CLAUDE.md`。

## 2. 不应反复推翻的业务决策

### 身份与租户

- 不引入全局“平台用户 → 多租户成员关系”模型。
- B 端：平台管理员创建租户，只创建 `sys_tenant`。
- C 端：回收箱二维码包含 `tenant_id`；市民扫码后通过微信登录注册到该租户。租户可把普通用户提升为清运员或设备管理员。
- 所有业务表带 `tenant_id`；普通业务请求由登录上下文和租户拦截器隔离，平台域账号按既定规则放行。
- 完整权限模型见 `docs/architecture/permission-design.md`。

### 投递

- 当前模型是“设备上传后建单”，不是用户开门时预建订单。
- 小程序开门只激活 `biz_device_session` 并下发开门；设备完成称重后发 `deliveryComplete`，后端再创建每袋独立订单。
- 归属取设备当前活跃用户会话。无会话或会话过期时仍建无主单（`user_id=null`），不返现；会话被后来的用户覆盖时接受“最近用户”语义。
- OneNet 消息 ID（缺失时回退 MQ message ID）作为幂等键；历史列 `delivery_token` 承载该键，不要从字段名反推旧业务语义。
- 照片由设备决定 COS 对象 key，并在事件中回传四个 URL。后端原样保存，不自行拼 URL。

### 清运：现状与下一阶段不要混淆

仓库当前后端仍是旧的毛重/皮重链：开门时建清运单，`cleanGross` 与 `cleanTare` 分别上报。Claude Code 最后与用户确认了替代它的 v3 方案，但尚未实施：

1. 清运员扫设备二维码和新空袋二维码，App 向后端发送 `sn + doorIndex + newBagQr`。
2. 后端不再预建订单，通过 `openCleanDoor` 下发 `doorIndex + newBagQr + cosToken`。
3. 香橙派开门前读取当前毛重和上次本地皮重，计算 `netWeight = grossWeight - oldTare`。
4. MCU 自己控制屏幕状态机。关门且重量稳定后显示“记录空袋重量”；按钮 1 通过新帧 `G1,1,G0` 通知香橙派保存新皮重。随后按钮 2 通过 `G1,2,G0` 确认上传。
5. 香橙派保存每个投口的新皮重，上传照片并发送单一 `cleanComplete` 事件：`doorIndex + netWeight + newBagQr + 4 photo URLs`。
6. 后端收到完成事件后才创建清运单，并把新袋绑定到投口。

职责边界已经拍板：MCU 管屏幕/UI 状态机；香橙派管业务状态、皮重持久化、净重计算、拍照/COS、MQTT；香橙派不运行网页屏幕，也不向 MCU 下发“该显示什么”的 UI 指令。

原始详细计划位于 Claude 家目录 `~/.claude/plans/tender-hopping-moth.md`。实施前应把它转成仓库内正式设计，并重新检查并发、失败恢复、幂等和皮重文件原子写入；计划中的示例代码不能直接视为已验证实现。

### 钱包与提现

- 投递按投口价格与重量入账用户余额；提现采用“申请 → 租户人工审核 → 通过扣减/驳回退回”。
- 真实微信商家转账仍未接入，且每个租户使用自己的商户号。不要改成自动审核到账。
- 后续工作见 `docs/planning/open-items.md` 第 3 节。

## 3. IoT、OneNet 与照片链路

- 正式上行：设备 MQTT → OneNet → 北向 Pulsar MQ → `OneNetMqConsumer` 解密 → `OneNetEventDispatcher` 分发。
- 正式下行：后端调用 OneNet 物模型服务 API。设备连接 token 与平台下行 API token 是两套凭证、版本和资源路径，不能混用。
- 整条链路只需要出站连接：上行订阅 MQ、下行调用 OneNet HTTP、设备访问 COS；没有 OneNet HTTP 公网入站回调需求。
- 投递和清运照片统一为设备直传 COS、完成事件携带 URL。`cosToken` 只携临时凭证，不携对象 key。
- Jackson 使用 Spring Boot 4 的 Jackson 3 包 `tools.jackson.databind`；不要在 framework 模块误用 `com.fasterxml.jackson.databind`。
- 物模型与消息结构见 `docs/iot/onenet-thing-model.md` 和 `docs/iot/onenet-thing-model.json`。清运 v3 实施时必须同步修改代码、JSON、Markdown 与 OneNet 控制台模型。

## 4. 硬件侧当前上下文

- 香橙派是云侧/业务侧小电脑：OneNet MQTT、COS 上传、流程编排；MCU 连接屏幕、传感器和执行器，通过 UART 与香橙派通信。
- 物理 UART 通常至少连接交叉的 TX/RX 和共地 GND；当前协议是以换行分帧的逗号分隔文本协议。
- 最近一次 UART 审计已写到 `hardware/docs/review/uart-protocol-audit.md`。核心未解决风险包括：无校验和、正则 `search` 接受夹杂垃圾的帧、接收缓冲区无上限、开关门无 MCU 应答，以及异常吞掉、参数范围不足等。开始改协议前先读完整审计，并与 MCU 固件同步设计。
- 2026-07-11 迁移记忆时工作区已有用户修改：`hardware/main.py`、`hardware/pyproject.toml`，以及未跟踪的 `hardware/docs/`。这些不是 Codex 创建的，必须保留。

## 5. 已知工程坑

- `./mvnw spring-boot:run -pl ecobin-bootstrap` 不会重建其他模块，会直接使用 `.m2` 的旧 jar。改过 system/framework/device/business 后先 `./mvnw install -DskipTests`，再运行 bootstrap。不要用 `-am spring-boot:run`，它会尝试在父 POM 找主类。
- IDEA 运行使用各模块 `target/classes`，代码编译后仍需 Stop/Run 重启 JVM 才能替换已加载类。
- `AdminController.list` 与 `TenantController.list` 返回 `Result<List<T>>`，Web 端做客户端分页；用户、设备、投递、清运、提现等主要列表返回 `PageResult`，由服务端分页。
- 微信 `jscode2session` 返回 JSON 内容但可能标为 `text/plain`，要先取字符串再用 Jackson 手工解析。
- 小程序 TDesign 曾完全无样式，根因是 `ignoreDevUnusedFiles=true` 丢弃 npm 组件，加上 `es6=false/enhance=false` 不转译 ESM；不是 `style:v2`。修复后需重新构建 npm、清缓存并重启开发者工具。
- Docker 容器内数据库地址通过 `docker-compose.yml` 的 backend environment 指向 `mysql` 服务名；`.env` 只放密钥类配置，不放环境相关 DB host，也不得入库。

## 6. 产品与协作偏好

- 用户在不熟悉的运维、部署、硬件领域更希望获得教学型说明并自己动手：说明“怎么做、为什么、相关概念和排错”，不要默认远程代操作。
- 用户明确说“目前先计划，不改代码”时严格停留在计划阶段。
- Web 后台视觉改版曾被明确搁置，等用户与导师确定方向；目前优先保证功能与跨端契约。
- 访问普通抓取失败或页面依赖 JS 时，优先改用可交互浏览器，不要机械重复抓取。

## 7. 资料导航与权威顺序

1. 当前代码、测试和 Flyway 迁移：运行事实。
2. `docs/iot/onenet-thing-model.md`、`docs/architecture/database-design.md`、`docs/api/api-frontend.md`、`docs/architecture/permission-design.md`：专题设计。
3. `docs/planning/open-items.md`：历史待办汇总，但最后同步较早，使用前需与 Git 历史及当前代码核对。
4. `hardware/docs/review/uart-protocol-audit.md`：最近 UART 审查。
5. 本文：跨会话决策和工作方式。
6. `~/.claude/projects/C--D-004-Project-002-Java-EcoBin/*.jsonl`：只有在上述资料无法回答时才回溯的原始会话证据。

## 8. 建议的续作入口

若用户下一步继续清运流程，先确认是继续设计还是开始实施。实施前至少完成：

- 把 v3 时序和失败状态正式写进仓库文档，明确断电/超时/重复按钮/重复事件/新皮重已保存但上报失败时如何恢复。
- 根据 UART 审计决定是否先升级协议（CRC、ACK、序号、严格分帧），并与 MCU 固件保持同一版本。
- 为本地皮重采用原子写入与可恢复格式，定义首次运行、负净重、传感器异常及多投口并发行为。
- 先写硬件侧测试模式/单元测试，再改 Java 后端和 OneNet 物模型；最后做跨端契约测试。
