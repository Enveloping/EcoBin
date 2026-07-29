# EcoBin agent context

开始任务前先读 `CLAUDE.md`，涉及跨端设计、IoT、硬件或历史决策时再读
`docs/architecture/project-context.md`。专题细节以该文档链接的现有设计文档为准。

## 当前阶段

- 后端、Web 管理端、小程序基础业务已成形；近期工作集中在 `hardware/` 的香橙派设备侧。
- Claude Code 最后确认但尚未实施的方向是“清运流程 v3”：MCU 负责屏幕状态机，香橙派只接收按钮事件、保存皮重、计算净重并上报；详见 `docs/architecture/project-context.md`。
- `hardware/docs/review/uart-protocol-audit.md` 是最近一次 UART 审计结果，问题尚不能视为已修复。

## 工作约束

- 先检查 `git status`，保留用户已有改动。迁移记忆时已存在：`hardware/main.py`、`hardware/pyproject.toml` 修改以及未跟踪的 `hardware/docs/`。
- 用户说“先计划”时只做设计，不改代码；获得明确实施指令后再落地。
- 提问、进度汇报和业务逻辑说明默认使用具体、完整、易懂的中文。业务术语、缩写、状态名或内部技术名词首次出现时必须解释，不能只使用开发者才能理解的名称。说明业务逻辑时至少交代“谁在什么情况下发起或上报、依据什么事实、系统修改了什么记录或状态、是否会阻止后续业务”；涉及前因后果、异常分支或取舍时，使用具体场景或例子讲清楚。
- 运维、部署或陌生硬件知识默认采用教学式说明：讲清步骤、原因和概念，让用户可以自己操作。
- 不把 `.env`、密钥、设备密钥或服务器凭证写入版本库或输出。
- 多模块后端改动后，运行 bootstrap 前先执行 `./mvnw install -DskipTests`，否则 `spring-boot:run -pl ecobin-bootstrap` 可能读取 `.m2` 中的旧模块 jar。
- Java 使用 21；香橙派目标 Python 版本为 3.11（不要依赖开发机 uv 自带的 3.14 专属语法）。

## 已定型的关键边界

- 多租户由 `tenant_id` + MyBatis-Plus 租户拦截器保证；不要引入“平台用户跨租户成员关系”。
- C 端接入：设备二维码携带 `tenant_id`，微信登录在该租户下注册；B 端创建租户只写 `sys_tenant`。
- 正式 IoT 链路是 OneNet：设备 MQTT → OneNet → 北向 MQ → 后端；下行由后端调用 OneNet 服务。无需公网入站回调。
- 投递为“设备上传后建单”，设备活跃用户会话决定订单归属；无活跃用户时创建无主单且不返现。
- 投递与清运照片都由设备直传 COS，设备在完成事件中回传四个 URL；后端不推导对象 key。
- `GET /api/system/admin` 与 `/tenant` 返回数组而非 `PageResult`；其他主要列表使用服务端分页。

## 微信支付接入偏好

- 商户角色：普通商户。
- API 版本：默认使用 APIv3。
