# EcoBin 项目上下文（Claude Code → Codex）

> 整理日期：2026-07-11。来源为项目 `CLAUDE.md`、`~/.claude/projects/C--D-004-Project-002-Java-EcoBin/memory/`、近期 Claude Code 会话/计划、仓库文档与 Git 状态。

> [!IMPORTANT]
> 2026-08-22 已获整体实施授权并已完成仓库内 P1～P10 软件、测试入口与操作文档的
> [`香橙派可重复量产镜像与首次启动编排计划`](../planning/orangepi-production-image-first-boot-plan.md)：
> 同一锁定 Debian 12/Linux 6.1、Python 3.11 镜像固化 UART5、wPi 2/5 MCU 升级控制、版本化
> `/opt` 安装和 `/etc/ecobin/hardware.env`。正式上行直接使用现有 Air780E 载板的 USB RNDIS，
> 不要求用户确定或升级模组固件；版本/USB 标识只作逐台诊断记录，真实联网能力由试点和出厂
> 预检证明。固定使用已稳定的 DECXIN/icspring 双摄及现有 by-id 默认路径。NRST 改为物理 11
> 号针 PC6 经 2N7002 开漏级控制，香橙派高电平触发复位，避免推挽驱动复位线。板载 Wi-Fi 不作
> Station；工厂接受全批次共用 WPA2 热点密码。量产介质为标称 32 GB TF 卡，镜像采用紧凑
> 固定布局并在首启幂等扩容；称重预检
> 暂用 500 g 砝码，允许差分误差 ±10 g。尚未
> 封存设备每次通电固定开启隔离 AP 和本地网页，先在不连接后端、不写生产 EdgeStore 的前提下
> 完成 revision 2/双摄/Bootloader 以及真实 AA/DD 投递、EE/EF 清运动作验收，再验证蜂窝上行、
> 校时、注册、K1 清理和正式运行时。动作测试只允许最小本地安全恢复状态，AA/EE 不自动重发；
> 验收通过前由整机出站锁阻止任何后台服务联网，转正式 UART 前复位/清缓冲并隔离迟到 DD/EF；
> AP 只在后端当前代次机器验收 `PASSED` 并可靠下发 `AUTHORIZE_FACTORY_SEAL` 后，才由操作员
> 写入单向 `SEALED` 并关闭，封存中断也不重开；
> 含 K1 的最终镜像只进入受控制品库。按负责人决定不增加运行时防克隆检查，新 MCU 由厂家线下
> 预烧 revision 2。当前仓库内实现已经收口；真实 32 GB 介质资格、正式外部信任策略、K1 注入、
> Orange Pi/MCU/Air780E/双摄/500 g 砝码的整机 HIL（hardware-in-the-loop，硬件在环）验收仍保持
> 失败关闭，取得并复核这些现场证据前不得量产放行。

> [!IMPORTANT]
> 2026-08-20 已实施 STM32F103C8T6 经香橙派 UART 的远程固件升级。MCU 固定帧修订号 2
> 增加 F2 停机执行准备和 F3 固件身份/执行结果；香橙派负责升级准入、生命周期状态机、
> Ed25519 包验签、BOOT0/NRST、ROM
> Bootloader 擦写、断电恢复、三次目标尝试和自动回滚。后端 V55 增加不可变发布、单机
> 验证、人工推广和逐批放行，OneNet 下行只在实际发送时附加短期只读 COS 凭证，任何回滚
> 或失败都会阻止下一批。接线、首次旧固件迁移、签名发布和故障恢复见
> [`../../hardware/docs/mcu-remote-firmware-update-runbook.md`](../../hardware/docs/mcu-remote-firmware-update-runbook.md)。
> 2026-08-21 进一步冻结职责：香橙派收到升级命令后依据本地维护锁、工作槽和未决物理命令
> 决定是否准入；忙碌时由香橙派可靠上报 `REJECTED` 且不发送 F2。MCU 收到 F2 模式 02 后
> 不得以 BUSY/UNSAFE 再否决，只执行停止本地活动、关闭输出和锁存升级状态。现有投递、
> 清运和屏幕状态机仍留在 MCU。本地 MCU 的完整可构建 Keil 源码已纳入 `hardware_mcu/`，
> 构建输出、个人配置、备份和发布生成身份不纳入版本管理。
> 2026-08-21 补齐 F2 与边缘日志的失效安全边界：SQLite v14 在发送 F2 前持久化原 F3
> 身份和“F2 可能已执行”标记。此后即使 `PREPARED` 写入失败或进程重启，也必须先复位、
> 核对原身份并通过 F1 才能 `REJECTED` 解锁；否则进入 `FAILED_LOCKED` 继续阻止业务。
> 2026-08-21 增加两层升级模拟：Linux PTY 虚拟 MCU 覆盖 F2/F3 身份、准备锁存和应答故障；
> 跨平台内存适配器再模拟 BOOT0、NRST、擦写和应用重启，并驱动真实边缘升级状态机验证
> 成功、拒绝、自动回滚及失败锁定。生产入口仍禁止为模拟 MCU 启用真实烧录。
> 2026-08-21 严格模拟复现并修复目标重试/回滚绕过 F2：每次应用复位都会清除准备锁存；
> 只要目标应用已启动，下一次目标擦写或回滚都必须重新持久化当前 F3 身份并执行 F2。
> 只有已证明仍在系统 Bootloader 的擦写失败可以直接重试；运行模式不确定时保持维护锁。

> [!IMPORTANT]
> 2026-08-20 补充首次准入闭环：设备启动或本地升级完成后查询 F3，只有
> `STATUS=00 + revision 2` 的完整身份才随已认证运行快照登记到资产；因此 SSH 首次迁移不再
> 依赖一次不可能先发起的云端升级。COS 临时失败跨可靠任务唤醒总计最多尝试 3 次；签名、
> 板型、摘要或发布身份错误，以及现场忙碌/升级禁用，均以可靠 `REJECTED` 终态收敛。

> [!IMPORTANT]
> 2026-08-17 已冻结 V54 投递审核金额阈值和 Web 配置中心。自动审核模式必须设置单笔
> 结算金额上限，新订单把上限固化为快照；超过上限的正常订单等待人工审核且不记异常。
> 提现继续复用现有单次最大提现金额。Web 只集中投递审核规则与提现审核规则，不迁移设备或渠道
> 配置。详见
> [`delivery-review-amount-limit-and-configuration-center-v54.md`](delivery-review-amount-limit-and-configuration-center-v54.md)。

> [!IMPORTANT]
> 2026-08-19 已把香橙派反向 SSH 从普通硬件主进程拆为独立
> `ecobin-remote-support.service`。普通硬件进程仍是唯一 OneNet 客户端，通过 root 专用
> Unix Domain Socket 下发 OPEN/CLOSE，并把代理私有 SQLite 中的状态事实幂等转入
> EdgeStore；代理以低权限账号独立持有 OpenSSH 子进程和隧道凭证。只重启或更新
> `ecobin-hardware.service` 不再中断已建立的维护连接，代理或整机重启才按原期限重连。
> 设备侧实现与首次切换步骤见
> [`../../hardware/docs/enrollment-and-remote-support.md`](../../hardware/docs/enrollment-and-remote-support.md)
> 和
> [`../deployment/device-enrollment-and-remote-support-rollout.md`](../deployment/device-enrollment-and-remote-support-rollout.md)。

> [!IMPORTANT]
> 2026-08-17 已重新冻结并实施 V53 投递自动审核与审核后自动提现。机构可选择全部
> 人工、正常订单立即自动审核、收到后 24 小时或 48 小时自动审核；负重量、不可可靠计算
> 或带用户/系统异常的订单仍由人工处理。首次审核形成正返现后，只有机构已启用、金额在
> 范围内、用户有有效微信自动收款授权、没有活动提现且双方余额满足时才同步创建并冻结
> 自动提现；不满足属于本次安全跳过，不回溯补建。详见
> [`delivery-auto-review-and-withdrawal-v53.md`](delivery-auto-review-and-withdrawal-v53.md)。

> [!IMPORTANT]
> 2026-08-15 已实施 V52 设备自注册、厂家初始袋和按需远程维护：香橙派用厂家全局 K1
> 完成一次性 HTTPS 注册，原子保存加密返回的 OneNet/SSH 配置后删除 K1 与注册实现；厂家
> 共享小程序通过独立厂家操作员身份进入隐藏设备出厂端，必须真实扫描每个投口的 EB1
> 初始袋，之后自动机器验收才可启动；用户端和清运端不显示厂家入口。平台管理员
> 只登记一次 Ed25519 公钥，后端经 OneNet 控制设备使用四个复用端口建立短期反向 SSH。
> 完整边界见
> [`device-enrollment-factory-acceptance-remote-support-v52.md`](device-enrollment-factory-acceptance-remote-support-v52.md)。

> [!IMPORTANT]
> 2026-08-10 完成设备接入、投递和清运字段级审查。厂家安装袋是历史事实；当前占用袋、
> 容量当前袋和有效重量基准袋必须属于同一代。审查确认投递满溢/重量基准准入、清运旧基准
> 信任、清运运行快照门槛以及配置应用与激活耦合等问题；软件修复、分层回归及独立
> MySQL 8.4 集成验证已经完成。完整结论和
> 实施状态见
> [`device-delivery-clean-generation-consistency-review-2026-08-10.md`](device-delivery-clean-generation-consistency-review-2026-08-10.md)。

> [!IMPORTANT]
> 2026-08-10 项目负责人重新冻结配置失败恢复：只有当前最高配置应用仍为 `PENDING`、
> 尚未边缘落盘，且原可靠任务以明确下发前原因阻断时，才允许同版本重同步并唤醒原任务。
> 应用一旦 `FAILED`，原命令不再重发；排除故障后必须发布更高版本，以新的应用、命令和
> 任务继续。原版本和摘要完全一致的迟到可信 `APPLIED` 可以纠正失败事实，但迟到
> `EDGE_SAVED` 不重新打开应用。证据超时、确认超时和未知阻断原因默认禁止重发。

> [!IMPORTANT]
> 2026-08-10 项目负责人冻结 V46 运行快照策略：`DEVICE_RUNTIME_SNAPSHOT` 只用于诊断，
> 不参与 OneNet 在线判断。启动、重连和实际状态变化时上报，变化最多每 5 秒合并一次；
> 空闲兜底默认 60 分钟，由平台全局配置且最小 10 分钟，不允许单设备覆盖。禁用设备
> 不下发，恢复时自动补齐最新策略；历史事实暂不清理或归档。完整裁决见
> [`runtime-snapshot-reporting-v46.md`](runtime-snapshot-reporting-v46.md)。

> 2026-08-09 清运 Web 纵切推进到 V44：普通/平台 Web 均可按机构读取清运操作列表和详情，
> 并与既有清运记录、直接修正和只追加历史互相深链。可信香橙派命令阶段开始投影清运
> 操作的边缘保存、可能解锁、需要恢复和解锁前结束事实；七种正式状态不再兼容
> `PRE_OPEN_ENDED`。V44 只增加机构/状态时间查询索引，不增加表或修改清运业务事实。
>
> 同日清运小程序正常主线推进到 V45：清运员可按全部、在线、24 小时无投递、24 小时未清运、
> 已满和满载超 2 小时六类设备查询；失去权威业务目标的历史可信边缘事件改为终态隔离并回执，
> 不再无限重试。V45 只增加活动查询索引和隔离原因，不增加表，也不改变清运结束/恢复流程。
> 本文记录对话中形成、仅靠代码不容易恢复的决策。代码和更新日期更晚的专题文档若与本文冲突，以较新的事实为准。

> [!IMPORTANT]
> 2026-08-10 项目负责人补充冻结 V36 未分配设备事实作用域：已注册但尚未永久分配机构的
> 设备是正常平台资产，不得把其运行快照、故障或安全变化仅因缺少 `organization_id` 判为
> 不可信。分配前事实按平台收件并在可靠事件上回执，不生成机构投影；分配后产生的新事实
> 进入永久机构。裁决使用可信事件发生时间与不可变机构分配时间，保证同一事件跨分配重投时
> 作用域不变。无需修改 OneNet、UART 或边缘 SQLite 契约。

> [!IMPORTANT]
> 2026-08-09 项目负责人冻结 V43 平台防伪袋码：平台按批签发并打印 EB1 标签，但不预建
> 租户/机构库存；厂家初始袋和清运换入袋由后端验真，未知有效码首次使用时才在当前机构
> 建袋。旧格式当前袋可继续收敛，取下后不能重新装入。完整裁决见
> [`authenticated-bag-labels-v43.md`](authenticated-bag-labels-v43.md)。

> [!IMPORTANT]
> 2026-08-13 项目负责人补充设备二维码刷新规则：固定帧模式除保存新 URL、香橙派启动和
> 串口重开时立即发送外，还在投递/清运均空闲且串口打开时默认每 60 秒重发本地最新
> URL；作业占用和写入失败均按 5 秒复查。该周期发送复用命令消费者线程，不与物理命令
> 并发。当前仍是静态公开 URL，重复发送本身不防远距离扫码；未来动态码必须使用后端可
> 验证的短期签名，裸时间戳不能作为防重放凭据。完整边界见
> [`device-entry-url-edge-delivery-v42.md`](device-entry-url-edge-delivery-v42.md)。

> [!IMPORTANT]
> 2026-08-08 项目负责人冻结 V42 设备入口 URL 下发：完整公开 URL 在平台机器验收时由
> `requestDeviceAcceptance` 携带，在全局基础地址改变时由独立可靠命令下发；香橙派先
> 保存到 SQLite，再通过无应答的固定 195 字节 `A0` 帧交给 MCU，并在自身重启或串口
> 重开后重发。二维码验收只证明香橙派保存的摘要正确，不检查 MCU 或屏幕。完整裁决见
> [`device-entry-url-edge-delivery-v42.md`](device-entry-url-edge-delivery-v42.md)。

> [!IMPORTANT]
> 2026-08-08 项目负责人冻结 V41 全局设备二维码入口：所有设备统一使用 `https://www.jinshoubao.com/device-entry/`，每台设备只通过唯一公开码形成不同二维码；入口地址改为应用全局非秘密配置，渠道、租户和机构不再保存或展示该字段。微信校验文件随 Web 静态资源发布。完整裁决见 [`global-miniapp-device-entry-v41.md`](global-miniapp-device-entry-v41.md)。

> [!IMPORTANT]
> 2026-08-08 项目负责人重新冻结 D-048 / I-058 / V39：一个平台小程序渠道可以服务多个租户和机构；AppID 只用于微信渠道和 OpenID 作用域，设备公开码通过永久资产归属决定机构。同一微信身份允许创建多个相互隔离的机构账号；V39 当时按最近注册账号选择，已由下方 V49 的最近成功登录规则覆盖。无扫码且从未注册的用户保持游客。小程序首页游客可见，登录和手机号授权只在“我的”或受保护操作中触发。完整裁决见 [`shared-miniapp-multi-organization-identity-v39.md`](shared-miniapp-multi-organization-identity-v39.md)。

> [!IMPORTANT]
> 2026-08-14 项目负责人以 D-049 / I-059 / V49 补充冻结多机构账号选择：客户端有效
> 会话继续使用；需要签发新会话且没有设备公开码时，按最近成功登录时间选择第一个可用
> 机构账号，设备扫码仍优先。最近账号不可用时回退到次近账号，不向客户端公开时间字段。
> 完整裁决见
> [`recent-miniapp-organization-account-v49.md`](recent-miniapp-organization-account-v49.md)。

> [!IMPORTANT]
> 2026-08-14 项目负责人冻结 V50 平台管理员治理：平台管理员表完全为空时创建唯一受
> 保护的默认管理员；升级前唯一且登录名匹配的现有管理员只改变账号类别，保留原密码；
> 默认管理员可以创建、停用、启用、永久逻辑删除及重置其他普通平台管理员密码，普通
> 平台管理员只能修改自己的密码。默认管理员不能被停用、删除或由他人重置。完整裁决见
> [`platform-administrator-governance-v50.md`](platform-administrator-governance-v50.md)。

> [!IMPORTANT]
> 2026-08-07 项目负责人重新冻结 D-047 / I-057 / V36，并以 V38 修订机器验收条件：设备资产只允许一次性永久分配租户和机构；部署实例、部署码、回收/调拨、现场人工验收和人工经营开关全部退出。平台在分配前自动机器验收，MCU/摄像头是否模拟只作诊断且不影响结果，机构只安装联网；小程序使用永久设备公开码，OneNet/COS/边缘 v2 不含部署码。禁用/报废只阻止新业务，既有作业继续收敛，物理安全事件例外。实施不保留旧协议兼容层。完整裁决见 [`permanent-device-ownership-v36.md`](permanent-device-ownership-v36.md)。

> [!IMPORTANT]
> 2026-08-02：满溢准入已切换到 V25“设备被动上报、只有当前袋明确 `FULL` 才阻止下一次投递”。后端不再为投递/清运创建检测 gate 或主动下发 `SAMPLE_FULLNESS`；无上报、失败或旧袋结果不新增阻断，新袋默认 `NOT_FULL`。投递选项与开始接口使用同一当前袋 `FULL` 事实。完整裁决见 [`fullness-reporting-v25.md`](fullness-reporting-v25.md)，本文及旧规划中相反描述均由该裁决覆盖。

> [!IMPORTANT]
> 2026-07-28：需求、P0 范围、业务模型、系统架构、数据库设计、接口设计和详细设计修订已确认。H-01、H-02、F-01～F-11、V-01、V-02 已完成；V-02 的个人主体和开发版微信限制已由项目负责人接受，设备来源归因由非阻塞 P0-FOLLOWUP-01 延期跟踪。H-03、V-09 为 `ready` 但尚未授权。F-11 的 SQLite v4、OneNet 命令受理、固定帧 MCU 适配、照片/COS 和故障自动测试已按负责人接受的当前范围转为 `done`。后续真实双摄验证发现数字索引会重复选择 DECXIN，现已改用稳定 `by-id` 路径并通过真实 STS 上传和 URL 下载验收；项目负责人决定暂不继续处理香橙派当时的默认路由/DNS 波动。项目负责人确认现有 MCU 使用协商后的固定帧协议，香橙派保留云端契约；对 MCU 不支持的能力，按逐项决策采用香橙派本地保存、fixed-frame 正常兼容投影或明确拒绝/失败，不增加物理命令重发、MCU 作业重启恢复或双事实安全锁；真实线路和执行器验收归 H-03。2026-07-29 又确认当前 OneNet 控制台已经是 9 服务 / 13 事件版本，并以“先跑起来”为首要目标：冻结 MCU 无法提供的状态允许由 fixed-frame 兼容层构造；`applyConfiguration` 只要由香橙派可靠保存并设为活动配置即可返回成功和 `APPLIED`，不要求真实下发 MCU。逐项决策持续记录在 [`onenet-edge-handling-decisions.md`](../../hardware/docs/review/onenet-edge-handling-decisions.md)。其他任务仍须逐项授权，`ready` 只表示依赖允许领取。正式上游依次为
> [`requirements-baseline.md`](../planning/requirements-baseline.md)、
> [`p0-scope-baseline.md`](../planning/p0-scope-baseline.md) 和
> [`business-model-baseline.md`](../planning/business-model-baseline.md)，冻结的系统结构见
> [`system-architecture-draft.md`](../planning/system-architecture-draft.md)，冻结的目标数据库见
> [`database-design-draft.md`](../planning/database-design-draft.md)，冻结的目标接口见
> [`interface-design-draft.md`](../planning/interface-design-draft.md)，施工入口见
> [`detailed-design-draft.md`](../planning/detailed-design-draft.md)，正式任务入口为
> [`tasks/p0-controlled-loop/00-index.md`](../planning/tasks/p0-controlled-loop/00-index.md)；截至 2026-08-06，I-001～I-056 均已确认。I-051～I-053 已包含同事务 FK 构造引用、首次注册同步参与扩展以及 recycling 复合投递协调/device 事实所有权的窄边界；I-055 明确首版前不强制跨端契约 CI、完整自动契约门禁或自动 HIL 发布门禁；I-056 增加一次性免确认收款授权及授权后自动收款。本文下方若描述旧实现或历史决策，不能替代这些新基线。

## 1. 项目与组件

EcoBin 是智慧环保回收箱系统：Spring Boot 4.0.6 + Java 21 的 Maven 多模块后端，配套 Web 管理后台、微信小程序和香橙派设备程序。

- `ecobin-common`：极小纯 Java 共享内核，只保留响应、异常和通用角色值。
- `ecobin-framework`：Security/JWT、多租户和通用基础设施；F-01 后不再保存外部平台实现。
- `ecobin-module-identity`：F-02 已承接原 system 的管理员、租户、用户和认证行为，并建立可信执行上下文与公开身份边界。
- `ecobin-module-device`：设备、投口、设备会话，并通过迁移期公开端口提供设备查询与统计。
- `ecobin-module-funds`：F-03 已承接旧钱包、提现行为，并保留 F-02 首次注册事务参与端口。
- `ecobin-module-recycling`：F-03 已承接旧投递审核、清运和袋行为。
- `ecobin-module-operations`：F-03 已承接旧统计聚合，并只通过其他模块公开端口读取数据。
- `ecobin-integration`：F-01 后承接 OneNet/Pulsar、COS 和微信适配实现。
- `ecobin-bootstrap`：依赖组装、配置、Flyway、启动入口。
- `frontend/web`：React 18 + TypeScript + Vite + Ant Design/ProComponents。
- `frontend/miniprogram`：原生 TypeScript 微信小程序 + TDesign。
- `hardware`：香橙派 Python 设备侧；目标运行时 Python 3.11，1 GB 内存，不运行 Chromium。

后端结构、命令和通用约定见根目录 `CLAUDE.md`。

当前已是 common、framework、identity、device、funds、recycling、operations、
integration、bootstrap 共 9 个模块的终态物理 reactor；原 `system` 已在 F-02 退出，
原 `business` 已在 F-03 搬迁并退出，跨业务模块只导入 `.api`。完整依赖和事务规则见
[`system-architecture-draft.md`](../planning/system-architecture-draft.md) 与
[`I-051～I-055`](../planning/interface-design/11-module-ports-machine-contracts-i051-i055.md)。

DD-004 保留内部 `BIGINT` 复合外键，只允许点名同步端口在同线程同事务传递不可序列化
的关系专用 FK 构造引用；首次机构用户由 identity 事务同步调用 funds 参与者创建零余额
钱包。修订后的 PDD-001 只规定“开始投递”由 recycling 按 D-037 协调，device 仍唯一
拥有和写入 session 等设备作业事实；继续投递属于已授权 session 内设备本地动作。

## 2. 不应反复推翻的业务决策

### 身份与租户

- 不引入全局“平台用户 → 多租户成员关系”模型。
- B 端：平台管理员创建租户，只创建 `sys_tenant`。
- C 端当前 V36 实现允许直接进入小程序或扫描设备二维码注册；首次 `wx.login` 成功创建机构用户记录即写入独立注册时间和可空的来源永久设备资产（`registered_via_asset_id`），用于按设备和时间统计地推成果，尚未绑定手机号的用户也计入注册。直接注册的来源为空，后续扫码不回填；手机号绑定时间另存，归因不影响权限、钱包或投递资格。二维码只携带永久 `deviceCode`，不携带租户主键、设备凭证或已废弃的部署身份。
- 所有业务表带 `tenant_id`；普通业务请求由登录上下文和租户拦截器隔离，平台域账号按既定规则放行。
- 完整权限模型见 `docs/architecture/permission-design.md`。

### 投递

- 当前模型是“设备上传后建单”，不是用户开门时预建订单。
- 小程序开门只激活 `biz_device_session` 并下发开门；设备完成称重后发 `deliveryComplete`，后端再创建每袋独立订单。
- 当前旧实现的归属取设备活跃用户会话。无会话或会话过期时仍建无主单（`user_id=null`），不返现；会话被后来的用户覆盖时接受“最近用户”语义。该规则仅描述待替换现状，不是 2026-07-24 目标设计。
- 当前旧实现使用 OneNet 消息 ID（缺失时回退 MQ message ID）作为幂等键，历史列
  `delivery_token` 承载该键；目标使用后端 `sessionUid`、香橙派持久 `eventUid`、
  规范摘要和设备资产内全局事件序号。不要从旧字段名反推目标语义。
- 2026-07-24 目标投递改为一次有效扫码 session 最多一单：session 单独是订单业务唯一根，
  `eventUid` 只负责可靠事件去重/冲突。可空首末重量及状态、开始单价和整场四图只在唯一
  `DELIVERY_COMPLETE` 中上报；明确终态称重失败仍形成系统异常订单，后置满溢采样不在
  完成载荷内，中间继续轮次不上传。任一本地轮次重量减少达到后端下发阈值（默认 500 克）
  时，只随最终载荷上报 `negativeWeightAnomaly` 布尔标志；后端原样固化，不按整场净重补判。
- 照片由设备决定 COS 对象 key；投递或清运完成事件先回传四个槽位的当时状态，仍在拍摄或上传的槽位 `url=null`。实际上传完成后再由 `photoStatusReported: AVAILABLE` 回传可信 URL，后端不自行拼 URL。

### 清运：现状与目标不要混淆

后端的目标正常清运切片已经落地：清运员准备操作后，后端冻结旧袋/基准、预留新袋并下发 `START_CLEAN_OPERATION`；可信命令阶段单调推进 `EDGE_SAVED/IN_PROGRESS/RECOVERY_REQUIRED/PRE_UNLOCK_ENDED`，固定帧执行期重启仍由既有事务中止为 `ABORTED` 并建立安全联锁；可信 `CLEAN_COMPLETE` 到达后在同一事务创建清运记录、交换袋关系、处理新基准、建立清运后检测 gate 并提交确认意图。清运员本人查询、Web 操作/记录查询以及 `clean.edit` 直接修改和只追加变更历史也已实现。当前未闭合的是香橙派/MCU 的目标屏幕状态机、再次解锁、主动解锁前结束和超时恢复等跨端真机路径；设备侧旧 D1 毛重/皮重链仍须退出，不能与后端目标事实混用。

2026-07-23 已正式确认 [`I-026～I-030`](../planning/interface-design/06-cleaning-bags-fullness-recovery-i026-i030.md)：

1. 清运员扫描永久设备二维码、选择投口并扫描换入袋；后端创建可恢复 `operationUid`、冻结旧袋/旧基准、预留新袋并取得整机占位，不提前创建清运记录，也不使用最近心跳重量冒充开门前重量。
2. 香橙派必须先把完整操作上下文和期限可靠写入 SQLite，再从 MCU 取得并保存本次真实开门前稳定总重量，二者都成功且开始授权仍有效才允许 MCU 开门。MCU 负责屏幕/按钮、门控和稳定称重；香橙派负责操作身份、基准、照片、结果计算、COS 和 OneNet。
3. 清运门没有门磁或关门执行器，MCU 只给电磁阀通电解锁且门自动弹开；第一次解锁可能
   已执行即为不可逆边界。以后只能在原操作内再次解锁、由清运员人工关门确认完成，或
   超时进入原清运员恢复。电磁阀通断不能推定物理门位，门位保持 `UNKNOWN`，
   `SAFE_CLOSE` 不适用于清运门。恢复中
   只有“断电 + 原清运员现场确认门扇关闭”才可释放整机占位，原投口、操作和袋预留仍锁定。
4. 最终安全关门并由清运员确认后，香橙派可靠保存单一 `cleanComplete`；后端一次事务创建清运记录、交换袋关系、建立/清空新基准、把新袋容量初始化为 `NOT_FULL` 并结束操作。香橙派随后可以按 V25 规则上报新袋状态变化，后端不主动发起采样。
5. 袋没有生命周期状态；无效新皮重仍只能由确认当前袋为空后的真实重测恢复。满溢准入只认当前袋设备上报的 `FULL/NOT_FULL`；投递结果待处理和严重安全锁继续通过各自目标接口规定的现场确认边界恢复。
6. 清运员是可信工作人员；清运完成事务直接建立已完成记录，不进入审核、认定、通过或驳回流程，也不产生钱包、积分、返现或其他奖励。有 `clean.edit` 权限的后台人员可以直接修改允许修改的清运业务数据，保存后立即生效并追加变更留痕；设备原始证据以及袋关系、重量基准、容量 gate 和设备安全状态仍只能由对应真实业务事实推进。
7. 2026-08-05 已补当前清运员袋扫码追溯：`aud=miniapp` 只能在当前机构、当前有效清运资格下读取一个已扫描袋的安装周期及周期内投递证据；周期由后端安装/取走边界和订单冻结的袋、投口、会话时间确定，不按设备时钟或当前位置倒推。V25 满溢读取使用被动 `fullness-state-changes` 历史，未恢复旧主动检测接口。

旧家目录计划 `~/.claude/plans/tender-hopping-moth.md` 和其中 `G1` 示例只保留历史参考价值；若与当前接口、数据库设计或 UART 审计冲突，以仓库内较新的设计为准。OneNet/COS 和边缘 SQLite 语义现已由 I-041～I-045 冻结；正式实施仍须生成机器物模型、完成 UART 契约及跨端测试，不能直接把旧示例代码视为已验证实现。

### 钱包、充值与提现

- 当前代码已有 Native 充值、机构双账本、微信免确认授权、授权查单、授权后转账、小程序授权页，以及手动/投递自动提现各自的免审阈值。历史 `USER_CONFIRM` 单仍沿原链收敛。
- 2026-07-23 确认的 [`I-031～I-035`](../planning/interface-design/07-funds-recharge-withdrawal-wechat-i031-i035.md) 负责充值、提现和渠道终态基础；[`D-046`](../planning/database-design/10-merchant-transfer-authorization-d046.md) 与 [`I-056`](../planning/interface-design/12-merchant-transfer-authorization-i056.md) 冻结免确认授权；V53 在这些边界之上增加投递自动审核和首次正返现自动提现，不改变微信原单收敛规则。
- 2026-08-04 项目负责人确认：该普通商户号从未接入微信支付平台证书，首次真实 APIv3 接入就使用 `pub_key.pem + 微信支付公钥 ID`；后端只支持公钥验签，不引入平台证书依赖或双模式过渡。
- 充值先建立本地单和固定下单意图，微信成功事实与机构净额入账分为两个可恢复事务。提现创建先冻结用户和机构两侧资金，审核通过后才建立唯一 `out_bill_no`；只有微信 `SUCCESS/FAIL/CANCELLED` 终态可以结算。
- 新提现创建前必须有当前 `ACTIVE` 授权，创建和提交各锁定复核一次并保存授权身份快照；历史 `USER_CONFIRM` 单继续使用原 `package_info` 收敛，不批量改挂新授权。
- V51 把微信明确未受理的授权创建请求保存为本地终态 `CREATE_REJECTED`（对外
  `FAILED`）：旧不可变请求和任务不得再次外调，旧单与观察继续保留但释放当前槽；用户排除
  原因后以新操作、新单号重新申请。网络中断或响应验签失败进入 `UNKNOWN`，继续占槽并查
  原单，禁止换号。
- `NOT_ENOUGH` 表示公司公共运营账户流动性不足，只暂停系统商户出款闸门；机构本地额度继续可信。V53 自动提现在机构本地额度不足时只记录安全跳过并产生聚合告警，不创建失败提现；P0 不实现充值退款、机构额度人工调整或更换微信原单重试。

### 运营治理与最小概览

- 2026-07-23 已正式确认 [`I-036～I-040`](../planning/interface-design/08-operations-audit-alert-reconciliation-statistics-i036-i040.md)：平台技术页只能查看并恢复原可靠任务、确认隔离项，不能直接重放消息或修改业务终态。
- 操作审计、技术尝试、聚合告警、对账问题和业务事实保持分层；工作人员确认告警或标记对账问题已处理都不等于来源已经恢复。
- M0 每日资金对账只做 EcoBin 内部逐交易收敛和可证明的完整遗漏补齐，不宣称已经接入微信官方账单文件；复杂财务归档仍是 M1。
- 最小运营概览按北京时间日期范围查询，并把期间流量与当前快照分开。operations 通过各业务模块公开查询端口，在仅供该只读概览使用的 `REPEATABLE READ` 事务中从同一快照组装；业务写入仍使用 `READ COMMITTED`，且不保存可人工修改的累计统计。
- 2026-08-05 已实现 I-036、I-037、I-038、I-040 的后端/OpenAPI：平台可查看并精确恢复原可靠任务、查看尝试、确认隔离项；普通/平台 Web 可按实时授权读取审计和告警并确认告警；工作人员小程序只读当前机构告警和概览。设备故障、当前满溢、可靠任务阻断及出款余额不足会投影为告警，只有来源真实收敛才解决。I-039 每日资金对账整体暂缓，当前不发布对账运行、问题查询或人工处置端点。

## 3. IoT、OneNet 与照片链路

- 正式上行：设备 MQTT → OneNet → 北向 Pulsar MQ → `OneNetMqConsumer` 解密 → `OneNetEventDispatcher` 分发。
- 正式下行：后端调用 OneNet 物模型服务 API。设备连接 token 与平台下行 API token 是两套凭证、版本和资源路径，不能混用。
- P0 后端只配置一个 OneNet 产品 ID，可信设备名固定等于硬件 SN；资产表不重复保存该可推导映射。设备 Key 只配置在对应香橙派，后端下行使用产品级 AccessKey。
- 整条链路只需要出站连接：上行订阅 MQ、下行调用 OneNet HTTP、设备访问 COS；没有 OneNet HTTP 公网入站回调需求。
- 2026-07-23 已正式确认 [`I-041～I-045`](../planning/interface-design/09-onenet-cos-edge-confirmation-i041-i045.md)，并由 V36 改为永久资产寻址：可靠边缘事实使用稳定 `eventUid`、原作业身份、规范摘要和设备资产内全局 `edgeEventSequence`；OneNet 传输 ACK、设备受理、物理结果和后端业务确认严格分层。后端权威事务与确认意图共同提交，设备持久化确认并回执后才清理原事件。
- 2026-08-03 最终把“能否新建物理业务”收敛为 OneNet 传输在线事实：资产级 `oneNetConnectionStatus` 的唯一权威来源是北向 `deviceOnline` / `deviceOffline` 生命周期通知；只有明确 `ONLINE` 才能创建新的投递或清运任务，`OFFLINE` 和尚无生命周期事实的 `UNKNOWN` 都阻止创建。普通已鉴权消息、运行快照、OneNet 下行错误和数据库时间差都不能覆盖或推断该在线状态。接口还可返回资产级 `edgeConnectionStatus` 供诊断，但它不再是后端创建投递/清运的准入条件。
- 后端只判断自己能够权威确认的条件，例如用户和机构权限、永久归属、自动机器验收、资产生命周期、整机是否已有后端作业占用、机构规则、OneNet 当前是否明确在线，以及已经由可信重启事件形成的清运中断锁。不存在人工经营开关；资格由这些当前事实自动计算。称重、烟感、投口执行器、满溢和香橙派本地清运锁等需要观察现场的条件，由香橙派收到命令后在物理动作开始前判断；拒绝时上报稳定错误码，后端不再用较早的运行快照替香橙派猜测当前物理状态。
- OneNet `10421` 只把当前任务暂停为 `PENDING / DEVICE_OFFLINE`，不改写资产在线事实；后续 `deviceOnline` 通知重新计算并唤醒任务。如果开始授权已过 60 秒，并且任务仍因 `DEVICE_OFFLINE` 或 `DEVICE_PRESENCE_UNKNOWN` 停在下发前，后端在同一事务结束会话、释放整机占用/清运新袋预留并取消该未下发任务。只要香橙派已经回报受理或物理动作已经开始，后来离线就只阻止新任务，不自动结束、不释放旧占用，也不根据离线推断作业结果。`10410` 表示当前产品下找不到设备，任务阻断为 `DEVICE_IDENTITY_UNRESOLVED`，不能盲目重试。
- 物理命令和配置命令被 OneNet 接受后不再自动重复下发，而是进入 `AWAITING_DEVICE_EVIDENCE` 等待香橙派命令观察或最终业务事实；到期仍无证据时阻断为 `DEVICE_EVIDENCE_TIMEOUT`，迟到可信证据仍可完成原任务。`CONFIRM_EDGE_EVENT` 和 `PROVIDE_PHOTO_UPLOAD_GRANT` 是安全幂等控制命令，继续保留重试。
- 运行快照用于诊断和当前配置/安全投影：启动、重连和状态变化时上报，状态变化最多每 5 秒合并一次，正常在线期间默认每 60 分钟兜底一次；周期由平台全局配置且最小 10 分钟，不参与 OneNet 在线判断。后端在收件事务中直接更新投影并提交后 ACK，不为每份快照建立可靠处理任务。运行快照仍属于只追加设备证据，日常应用账号不得删除；本阶段暂不实施归档或清理，也不扩大 `ecobin_app` 的删除权限。
- OneNet 返回 HTTP 200 但业务码非零时，只将明确的临时平台内部错误 `10500` 归为可重试；`10415` 等参数、物模型、权限及其他未进入临时白名单的业务错误直接永久失败，不能让冻结载荷持续重放。
- 当前联调产品为 `tB6NlBWW0V`，唯一应保留的设备名/硬件 SN 为 `test-divice-1`。历史假设备及其关联事实应使用受控清理工具处理，不能只删资产主表；操作手册见 [`fake-device-cleanup.md`](../operations/fake-device-cleanup.md)。
- 2026-07-23 已正式确认 [`I-046～I-050`](../planning/interface-design/10-uart-protocol-i046-i050.md)：UART 1.0 使用 `0xEC42`、最大 256 字节、big-endian 和 CRC-16/CCITT-FALSE 的有界二进制帧；启动先 HELLO/QUERY_STATE，命令 ACK 与物理结果分层，关键 MCU 事件提交边缘 SQLite 后才 ACK。`txSequence`、`mcuCommandUid`、`mcuBootId + mcuEventSequence` 和云端作业身份互不替代，任一端重启都禁止自动重放旧开门。
- 投递和清运照片统一为设备直传 COS。对象 key 由设备在 `ecobin/{workType}/{workUid}/` 授权前缀内生成，不放入永久公开码或任何设备凭证；临时凭证只在发送时附加且不进入稳定摘要/日志。完成事件回传四个槽位状态，尚未上传完成的槽位 URL 为空；上传完成后再由照片状态事件回传可信 URL。
- Jackson 使用 Spring Boot 4 的 Jackson 3 包 `tools.jackson.databind`；不要在 framework 模块误用 `com.fasterxml.jackson.databind`。
- 当前物模型与消息结构见 `docs/iot/onenet-thing-model.md` 和 `docs/iot/onenet-thing-model.json`，但它们是待替换的运行现状，不是目标契约。实施 I-041～I-045 时必须同步修改代码、JSON、Markdown 与 OneNet 控制台模型，禁止保留旧写协议作为生产兼容层。
- I-019/I-020 的目标契约要求配置以版本和摘要分别证明“香橙派已可靠落盘”和“必要 MCU 项已同步”。当前 fixed-frame 阶段采用 2026-07-29 确认的运行优先例外：`applyConfiguration` 可靠保存并成为香橙派活动配置后即可返回成功及 `APPLIED`，允许构造兼容 `mcuCommandUid`；这不表示 MCU 已真实接收配置。具体边界见 [`onenet-edge-handling-decisions.md`](../../hardware/docs/review/onenet-edge-handling-decisions.md)。

## 4. 硬件侧当前上下文

- 香橙派是云侧/业务侧小电脑：OneNet MQTT、COS 上传、流程编排；MCU 连接屏幕、传感器和执行器，通过 UART 与香橙派通信。
- 物理 UART 通常至少连接交叉的 TX/RX 和共地 GND。F-11 已把 `hardware/main.py`
  的正式入口切向 SQLite 新骨架，当前 schema 已推进到 v6；当前现有单片机使用双方
  确定的 `AA/BB/EE/F0` 下行与
  `DD/EF/F1/CC` 上行固定帧适配器。规范 `uart-v1` 只保留为未来明确选择的可选实现，不是
  当前 MCU 的通信路径，也不是 F-11 完成门。
- 固定帧兼容保留香橙派—OneNet—后端完整契约，但 MCU 实际能力不完整：配置只在香橙派
  保存，缺失的远程控制明确返回不支持，无法查询的作业/门状态返回
  `UNKNOWN/NOT_SAMPLED`；F0/F1 可查询真实重量、红外和烟感快照，CC 只上报烟感
  `00/01/02` 之间的变化，`02` 明确表示 MCU 无法读取烟感。DD/EF 满溢位只作为红外
  原始观测，业务满溢仍按香橙派保存的规则判断。
- 固定帧只有单投口且无 ACK、CRC、流程编号和通用作业状态查询。启动和机器验收各自
  发送新的 F0；启动自检没有取得完整健康证据时，香橙派仍连接 OneNet 并上报降级事实，
  同时在没有投递或清运占用时按 5、10、20、40、60 秒退避重查 F0；每次间隔从上次
  查询结束后计算，之后最多每 60 秒
  一次。合法 F1 必须同时证明通信正常、`VALID=3`、重量为 0～350000 克整数、红外为
  布尔值，且最新烟感为 `NORMAL/OK` 或 `ALARM/OK`；证据完整后停止重查，健康状态下
  不做周期轮询。`ALARM/OK` 证明烟感通信健康，但仍由安全准入阻止新的投递和清运。
  作业槽被占用时跳过自检，绝不重发开门或清运等物理启动命令。
- 重查取得合法 F1 后会关闭活动的 `UART/UART_PROTOCOL` 故障；如果只有通信恢复而重量、
  红外或烟感证据仍无效，则继续退避重查。相同超时或相同烟感投影只刷新故障发现次数和
  最后发现时间，不重复制造 MCU inbox 或可靠安全事件；真正恢复只产生一次恢复事实。
  `CC` 可以更新最新烟感，但不能单独替代 F0/F1 对通信、重量和红外的完整证明。
- 活动的 `UART/UART_PROTOCOL` 故障或已经关闭的串口优先于历史正常 F1；二者任一存在时，
  新投递和清运仍以 `SAFETY_SENSOR_UNHEALTHY` 拒绝，直到新的自检证据完成恢复。
- 香橙派不恢复或重放 MCU 作业；重启后只把本地未决投递、清运、满溢检测和空袋基准测量置为
  `FAILED / EDGE_RESTARTED` 并释放槽位；已经可靠写入发件箱的完成事件优先保留并继续上传。
  被重启中断的清运还会设置持久清运锁，后续投递必须拒绝，直到现场重新完成一次完整清运，
  后端收到可信 `CLEAN_COMPLETE` 后两端才清除该锁。当前不增加 UART v1 快照完整性锁、
  SQLite/MCU 作业冲突锁或额外设备身份启动锁，也不要求把 systemd 的 `Restart=on-failure`
  改为 `Restart=no`。
- 运行时必须显式选择 `fixed-frame` 或 `uart-v1`，不自动探测或失败回退；固定帧适配
  只存在于香橙派硬件边界，不改变 OneNet、后端或业务事件结构。旧 D1 和旧 gross/tare
  清运路径仍须退出。
- 固定帧实施记录和能力降级矩阵见
  `hardware/docs/review/fixed-frame-mcu-adapter-2026-07-27.md`；真实线路、屏幕和执行器
  行为仍由 H-03 验收，软件测试不得冒充真机能力。
- 当前 OneNet v2 的 10 服务 / 15 事件逐项处理结论见
  `hardware/docs/review/onenet-edge-handling-decisions.md`；后续讨论结果统一增量写入该文档。
- 2026-07-11 迁移记忆时工作区已有用户修改：`hardware/main.py`、`hardware/pyproject.toml`，以及未跟踪的 `hardware/docs/`。这些不是 Codex 创建的，必须保留。

## 5. 已知工程坑

- 2026-08-03 的
  [`投递异常情况与恢复边界`](delivery-exception-recovery-review-2026-08-03.md)
  已增加“事件驱动、边缘物理准入、重启直接取消”的冻结简化规则。文档后半仍保留早期复杂恢复
  枚举作为风险清单；若其中的“恢复旧物理作业”建议与冻结规则冲突，以文档开头的最终决定为准。
- 2026-08-02 的 V25 投递联调已经打通真实 OneNet/COS、模拟 MCU/双摄、订单、业务确认
  回执和当前袋 `FULL` 准入。期间遇到的 systemd 双实例、旧边缘数据、V24→V25 迁移、
  `.m2` 旧 jar、OneNet 确认引用缺少 `PORT_FULLNESS_STATE`、BLOCKED 原任务受控恢复和
  时钟偏差等问题，已整理为
  [`投递全链路联调复盘与复跑手册`](../operations/delivery-e2e-integration-retrospective-2026-08-02.md)。
  下次跨端联调先按该手册执行环境与契约预检，不以 OneNet `code=0` 或出现订单单独宣称
  闭环完成。
- `./mvnw spring-boot:run -pl ecobin-bootstrap` 不会重建其他模块，会直接使用 `.m2` 的旧 jar。改过 identity/framework/device/business 或其他模块后先 `./mvnw install -DskipTests`，再运行 bootstrap。不要用 `-am spring-boot:run`，它会尝试在父 POM 找主类。
- IDEA 运行使用各模块 `target/classes`，代码编译后仍需 Stop/Run 重启 JVM 才能替换已加载类。
- `AdminController.list` 与 `TenantController.list` 返回 `Result<List<T>>`，Web 端做客户端分页；用户、设备、投递、清运、提现等主要列表返回 `PageResult`，由服务端分页。
- 微信 `jscode2session` 返回 JSON 内容但可能标为 `text/plain`，要先取字符串再用 Jackson 手工解析。
- 小程序 TDesign 曾完全无样式，根因是 `ignoreDevUnusedFiles=true` 丢弃 npm 组件，加上 `es6=false/enhance=false` 不转译 ESM；不是 `style:v2`。修复后需重新构建 npm、清缓存并重启开发者工具。
- 本地数据库与渠道密钥统一保存在 Git 忽略的
  `.ecobin/application-local-secrets.yml`；IDEA、开发脚本和根 Compose 共用该文件，
  Fake/Real 只切 profile。容器内数据库地址仍由 `docker-compose.yml` 的 backend
  environment 覆盖为 `mysql` 服务名；生产继续使用 `/run/secrets`。

## 6. 产品与协作偏好

- 用户明确说“目前先计划，不改代码”时严格停留在计划阶段。
- Web 后台视觉改版曾被明确搁置，等用户与导师确定方向；目前优先保证功能与跨端契约。

## 7. 资料导航与权威顺序

1. 当前代码、测试和 Flyway 迁移：运行事实。
2. `docs/iot/onenet-thing-model.md`、`docs/architecture/database-design.md`、`docs/api/api-frontend.md`、`docs/architecture/permission-design.md`：专题设计。
3. `docs/planning/requirements-baseline.md` → `p0-scope-baseline.md` → `business-model-baseline.md` → `system-architecture-draft.md` → `database-design-draft.md` → `interface-design-draft.md` → `detailed-design-draft.md` → `tasks/p0-controlled-loop/00-index.md`：判断目标实现、施工顺序和任务状态的正式上游链。
4. `hardware/docs/review/uart-protocol-audit.md`：最近 UART 现状审查。
5. 本文：跨会话决策、旧新差距和工作方式。
6. `~/.claude/projects/C--D-004-Project-002-Java-EcoBin/*.jsonl`：只有在上述资料无法回答时才回溯的原始会话证据。

### 7.1 应用部署与重新发布索引

- 后端、Web 或运行时镜像相关代码修改后，日常重新部署统一从
  [`应用修改后重新部署操作手册`](../deployment/application-redeployment-runbook.md) 开始。
  该文档负责判断是否需要打包、是否先升级数据库、哪些服务器控制脚本需要单独同步，
  并给出构建、上传、安装、预检、激活、验证和回退的完整顺序。
- 新服务器第一次部署、目标网络/目录、systemd、Nginx 和 Fake 首启使用
  [`目标单机部署手册`](../deployment/target-single-host-deployment.md)。
- `/etc/ecobin` 配置、秘密文件、微信支付公钥/商户证书和机构小程序配置使用
  [`生产部署配置、密钥与证书清单`](../deployment/production-configuration-secrets-certificates.md)。
- 目标数据库首次供应或新增迁移使用
  [`H-02 目标数据库手册`](../deployment/h02-target-database.md)；运行 JAR 不携带迁移器，
  数据库必须先到达新后端要求的 epoch 才能激活应用。
- 发布脚本的机器入口分别是
  [`New-EcobinLocalRelease.ps1`](../../tools/deployment/New-EcobinLocalRelease.ps1)、
  [`ecobin-install-local-release.sh`](../../tools/deployment/ecobin-install-local-release.sh) 和
  [`ecobin-production-preflight.sh`](../../tools/deployment/ecobin-production-preflight.sh)。

## 8. 建议的续作入口

DD-004、修订后的 PDD-001、29 项任务粒度/依赖、`status/executor` 分类和
2026-07-30 仅作风险排序均已确认。29 项任务已发布并同步 2026-07-24 修订到
[`p0-controlled-loop/00-index.md`](../planning/tasks/p0-controlled-loop/00-index.md)：H-01、
F-01、F-02、F-03、F-04、F-05、F-06、F-07、F-09、H-02 已完成；H-02 已通过本地
MySQL 8.4.10 开发演练和服务器整改阶段 0～3 验收，项目负责人接受当前试验期的
`.ecobin` ACL 受限明文保管例外，加密密码库及其异机密文副本延期到下一版本；
2026-08-04 又完成服务器安全更新和重启验收，并把仍为空业务库的目标数据库从 V10
精确重建到 V31（96 张领域表、77 条权限定义、其余业务数据 0）；当前代码已增加 V32～V56，
部署含当前纪元门禁的应用前必须现场核对服务器实际版本，并用 H-02 续跑模式完成前向升级到 V56（119 张领域表、76 条有效权限定义）；
F-10 的机器来源、通用三语言黄金样本和适配责任边界已完成，任务已转为 `done`；
F-08 已完成 Fake 可靠任务 tracer、MySQL 8.4
验收和两项 P1 复审；F-11 已按当前软件范围转为 `done`；
V-01 已完成身份/Web 纵切、六项 P1 复审修复和真实 MySQL 验收并转为 `done`；
V-02 已按项目负责人接受的受限微信环境边界转为 `done`，设备来源归因保留在非阻塞
P0-FOLLOWUP-01；H-03 的 F-11 依赖已经解除并转为 `ready`，V-09 的 V-02/F-08
依赖也已解除并转为 `ready`，两项均未获实施授权。
当前共 `done` 15、`ready` 2、`in-progress` 0、`blocked` 12。任务依赖解除没有自动
授予实施权限。

实施入口已经明确：

- 保持旧行为的最终 9 模块物理边界已经完成，后续目标业务必须在该边界内通过公开
  `.api` 端口实现，不能恢复旧 system/business 大模块；
- V1～V31 已完成前向迁移和 MySQL 权威验证；V32～V51 保留既有身份、设备、可靠任务和资金状态演进；V52 增加设备自注册、OneNet 供应、厂家验收小程序、初始袋标签占用和按需远程维护；V53 增加投递自动审核、提现审核规则与自动提现决策；V54 增加自动审核金额阈值和订单快照；V55 增加 MCU 固件发布、灰度部署和进度事实；V56 增加封存授权与设备验收代次；F-07 又完成固定 V1 marker，当前代码已推进为
  V56 epoch/readiness guard、最小 `ecobin_app` 空业务库启动和 Fake 外联硬阻断；
  并在两项 P1 补强后通过复审；H-02 本地演练及服务器整改阶段 0～3 技术门已通过，
  服务器目标数据库身份、加密备份和恢复流均已供应并验证；应用部署、seed 和入口
  切换仍未执行；
- F-09 HTTP/OpenAPI 客户端传输已完成复审并由项目负责人确认；
- V-01 身份/Web 纵切已完成复审并获准合入；V-02 已按项目负责人接受的个人主体和
  开发版环境边界转为 `done`，真实设备来源归因由非阻塞 P0-FOLLOWUP-01 跟踪；
  V-09 已解除任务依赖成为 `ready`，但尚未获得实施授权；
- F-11 的固定帧适配、照片/COS、MQTT 重连、强杀/COS 自动诊断、契约生成边界和能力
  降级文档已按当前范围完成；真实双摄已按 DECXIN 外部、icspring 内部完成拍照、上传
  和 URL 下载校验。H-03 后续在同一适配版本上执行固定帧线路、屏幕状态机和物理行为
  真机验收，发现 F-11 范围内问题时重开；
- 后续按投递及其审核与钱包、清运、满溢恢复、充值、提现和 operations 的纵向切片推进；
- MCU 固件、数据库环境、真实微信、真机验收和成对切换分别保持 HITL；
- 完整跨端契约 CI 和自动 HIL 发布门禁按 I-055 留到共同首版形成后的升级阶段，但人工门安全、断电恢复、真实资金和真机验收不能延期。

当前 OneNet MQTT 已联通，开发环境 STS/COS upload/head/delete smoke 和真实香橙派
双摄上传/匿名下载已通过；固定帧 MCU 适配已合入，传感器使用 F0/F1 自检和 CC 烟感
变化上报，不支持的 MCU 功能仍按“本地保存/明确失败/未知占位”降级。Python 3.11
硬件套件当前在 Python 3.11/Linux 为
`353 passed, 4 skipped, 5 subtests passed`，在 Python 3.11/Windows 为
`396 passed, 13 skipped, 5 subtests passed`；契约单元套件为 `59 passed`，生成/跨语言
校验为 `24 passed, 0 notes`。香橙派当时的默认路由/DNS 波动按
项目负责人决定暂不处理且不阻塞 F-11；微信支付/商家转账仍不可联调。真实条件或
软件链路缺失时只能标记相应软件阶段，不能宣称 M0。
