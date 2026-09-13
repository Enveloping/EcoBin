# P1AV：独立空袋基准中位数与迟到结果隔离

日期：2026-09-13。范围：已批准计划 P5 的独立基准纵向切片；本地实现/验证，尚非整套发布。

## 1. 业务效果

香橙派完成独立空袋测量并经 OneNet 上报后，后端核对原任务、袋子、投口、配置版本和
两个摘要、校准版本及空袋确认。符合已约定质量要求的超时中位数可以建立非负空袋皮重。
原始结果仍保存 `UNSTABLE`（未达到稳定窗口）和 `TIMEOUT_MEDIAN`（5 秒后的中位数），
不再改写为稳定均值或 `WEIGHT_UNSTABLE` 故障。

正常完成仍在原事务内保存物理证据、建立基准、完成测量任务、更新当前容量/袋子皮重、
完成原设备命令并登记可靠确认。容量状态可进入 `READY`，但这不代表解除其他设备故障、
释放投递/清运占用或发出开门命令，不产生订单、返现、余额或提现任务。

- 实测为 0 可以建立真实零基准；负数保留原值，但以 `NEGATIVE_EMPTY_BAG_WEIGHT`
  标记基准建立失败，不把负数当成 0。
- 真正无值超时仍为失败，重量为空；旧不稳定诊断均值仍为失败，不变成可用基准。
- 重复上报不再建一份基准或重复登记确认；最后确认登记失败时，前面所有业务写入回滚。
- 任务已被新容量代次、袋子、检测或已应用配置取代时，仅更新旧任务和保留证据。
  已技术取消的任务保留原取消原因和原命令状态；迟到数据不能覆盖新基准。

## 2. 实现

`TrustedOrangePiRuntimeFactService` 保留独立基准的实际可用标记和取值方式，新增中位数
校验后才进入可用重量分支。质量规则与 P1AR～P1AU 一致：明确无故障、健康 OK、
可用且非空、有符号 32 位克数、5000 毫秒、5～32 样本、合法测量 UUIDv4、
启动/事件编号和校准版本。缺少故障字段不等于明确无故障。

合格中位数写入 `baseline_total_weight_g` 主重量列；旧失败的诊断读数仍写
`baseline_last_observed_weight_g`。新数据的 `baseline_weight_value_available` 与
`baseline_weight_value_kind` 成对保存。已存在记录的取值元数据保持空，不补造历史。

历史名称 `stable_total_weight_g` / `latest_stable_total_weight_g` 继续承担数值投影，
不作为测量已稳定的证明。真实质量由关联物理结果提供，P6 展示仍需同步。
本批未改硬件准确度、热点参考重量设置、MCU 实际采样驱动或香橙派原生主入口。

## 3. 同时修复的旧问题

按 diagnose 技能先复现，再定位和回归。最小真实服务/MySQL 用例只把当前容量代次
从原任务记录的 7 改为 8。两次运行都观察到：旧任务已是 `STALE_IGNORED`，但容量
代次又从 8 改成 9，袋子从 `MEASURING` 变成 `FAILED/BASELINE_FACT_STALE`。

排查了三个解释：普通失败分支继续写当前投影、过期判定未生效、数据库触发器改写。
旧任务状态证明过期判定已生效，测试库触发器为 0；源码及两项状态差异共同定位到
`applyFailedBaseline` 仅根据 stale 改任务标签，却仍无条件执行当前容量/袋子更新。

修正为：旧任务及证据写入后，若 stale 则返回，不再写当前容量/袋子。正常失败继续
沿用原失败处理。技术取消的迟到分支原已隔离，继续保留其原因和命令状态。

回归覆盖新中位数、旧均值、旧失败、袋子更换、检测已开始、配置版本/摘要变化以及
已有 1500 克新基准的情况；迟到 1180 克结果只留证据，新基准及 READY 状态不变。
没有添加调试日志或遗留临时插桩。这类缺陷可由“旧任务不能修改新代次状态”的实际
事务测试预防，无须为此建立另一套恢复框架。

## 4. 数据库与运行门禁

新增 `V76__baseline_timeout_median.sql`，未修改 V1～V75：单条原子 ALTER 添加两个
可空元数据字段及其一致性 CHECK，替换独立基准测量/结果形状的两条 CHECK。
旧分支保持可读写；中位数分支通过 `COALESCE(..., FALSE)` 拒绝 SQL 空值绕过，
明确区分主重量和失败诊断重量，不允许同时填两份。

没有新增表、历史回填或权限扩大。物理结果表原本已有整表 INSERT 权限且无 UPDATE
授权，本次两个追加字段不要求增加修改权限，授权清单仍为 39。源码最低数据库版本、
健康说明、H-02/F-07 目标与资源打包检查同步为 V76；保留 V72～V75 续跑识别。

专用缓存 MySQL 8.4.10 中先完整迁移 V1→V75，再迁移 V75→V76。最终 76 条成功
迁移、134 张领域表及 Flyway 历史表；V76 checksum `-435401523`，耗时 380 毫秒。
V73/V74/V75 文件摘要与既有记录一致。V76 SHA-256：

`83c913ca7f62a44f8c83c0e96acb9b16e636f450cc30d247f46784a9f3ae0cc6`

未迁移现网、未发布。新应用要求 V76，未来获准成对部署时先迁移并核对权限再激活；
不能用删事实、降低版本号或 Flyway repair 冒充兼容回退。

## 5. 验证

采用 TDD：数据库首测在 V75 因缺取值字段失败；加 V76 后通过。真实服务首测在
有效夹具下得到 `FAILED` 而非 `COMPLETED`，接通真实中位数分支后通过。
夹具初次遗漏 QUEUED 命令必需的 queued_at，按原约束补齐；这个准备错误不计为
生产缺陷。启动门禁先出现两项预期失败，再同步 V76 后通过。

新增 **98 项 MySQL 专项通过，零失败/错误/跳过**：

- `BaselineMedianMysqlConstraintTest` 41 项：合法保存、35 个反例、两组精确数值
  边界、旧元数据空值、旧不稳定诊断均值及真正无值超时。
- `BaselineMedianTransactionTest` 57 项：真实公共服务和确认服务、真实事务，覆盖
  基准/容量/袋子/原命令效果，30 个质量反例，重复、确认失败回滚后重试、旧测量形状、
  零/负重量、过期/已技术取消、7 个冻结身份或空袋确认反例及已有新基准保护。

事务用例每例创建随机 `ecobin_p1av_case_*` 数据库，八张写表复制迁移后的 CHECK 和
唯一键；父级查询使用最小夹具，可靠任务登记是同事务 SQL 探针。LIKE 不复制外键和
触发器，单连接不证明并发锁序，根账号不证明生产权限。因此这不是完整外键/生产
最小权限、真实历史数据迁移、云端或现场验收。无指定专用环境变量时会明确跳过。

在同一专用容器内另建 P1AS/P1AT/P1AU 检查副本，重跑前面 297 项投递、清运和快照
专项；副本复制 V76 对应表，不是另外三次完整迁移。

相关回归 **36 个 Java 类、576 项通过、零失败/错误/跳过**：

```powershell
.\mvnw.cmd -o -q -pl ecobin-bootstrap -am '-Dtest=BaselineMedian*,RuntimeMedian*,TrustedOrangePiRuntimeFactServiceSqlTest,RuntimeSnapshotPolicyMigrationBoundaryTest,MiniappDeliveryDeviceQueryPolicyTest,DeviceRuntimeWeightPolicyTest,CleanMedian*,ApplyCleanComplete*,StartCleanOperation*,CleanCommandObservationDecision*,CleanRecordMigrationBoundaryTest,DeliveryMedian*,TrustedDeliveryCompletion*,DeliveryRecoveryQuarantine*,ApplyDeliveryComplete*,OneNetEventDispatcher*,P0DatabaseEpochPolicyTest,RuntimeSafetyConfigurationTest' '-Dsurefire.failIfNoSpecifiedTests=false' test
```

四组专项各自设置专用回环 URL。最终报告 SHA-256（后续运行会覆盖）：

- `BaselineMedianTransactionTest.xml`：`88f3f9859c681deb44a6b887191dde56fc4095676a0d54cd0adb02aa32b608fc`。
- `BaselineMedianMysqlConstraintTest.xml`：`c372279a2d8e77fc16186df2e68d0b56507f6fba5cd61cf6a783100fd32f0230`。

完整契约 25 项通过、0 notes；104 份生成物一致，67 条消息、50 帧、1707 条流轨迹、
16 个摘要配置，Python/Java 21/两种 C 校验通过。授权清单静态检查与三个 PowerShell
脚本语法检查通过。复用 Maven 离线缓存、缓存 MySQL 镜像，没有重新下载依赖。

专用容器 `ecobin-p1av-baseline-20260913-01` 使用回环端口 43632、tmpfs、无宿主数据
绑定。四组专项用例库残留为 0，核对精确 ID/标签/自动删除设置后移除专用容器；
其他既有容器、卷及 Windows MySQL 未停止或修改。

## 6. 下一步

继续满溢处理中位数与数据库约束、香橙派实际接单条件、原生结果上云、主程序和 P6
展示/HMI；不能以未被业务调用的 `DeviceRuntimeWeightPolicy` 测试代替真实接线。
P5 尚未整体完成，P7 不可发布。P4 两项资金取舍不擅自实施；真实 RS485 迟到回复
归属与 HMI 按钮选择仍待资料/确认。

OneNet 候选 2.1.2、UART rc.20、业务 SQLite schema 30、永久层 schema 3 不变。
发布清单 25/30 和 Windows SQLite 强杀恢复 1546 问题保留，本批未重跑全硬件套件。
未 SSH、未操作 UART/GPIO、未部署/导入物模型/烧录、未修改真实设备或资金。
本批无需用户现场操作。
