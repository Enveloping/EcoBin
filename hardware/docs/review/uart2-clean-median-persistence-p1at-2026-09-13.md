# P1AT：清运中位数、记录与新袋皮重

日期：2026-09-13。承接 [P1AS 投递落库](uart2-delivery-median-persistence-p1as-2026-09-13.md)
及[实施计划 P5](../../../docs/planning/mcu-edge-contract-implementation-plan-2026-09-12.md)。

## 1. 实现结果

香橙派上报清运完成时，首重和最终重量现在都可以使用合格的超时中位数。
`ApplyCleanCompleteService.apply` 继续要求清运人员确认完成、确认关门，以及清运锁已断电；
中位数不能代替这些事实，也不能改变原清运操作绑定的人员、袋、投口和配置。

在同一个事务内保存原始设备事实、计算并建立清运记录、移除旧袋绑定、安装原先预留的新袋，
再用有效的最终重量建立新袋皮重、更新容量状态、结束清运并释放设备占用。
最后登记给香橙派的可靠业务确认。登记失败会连同前面的业务修改一起回滚，原占用和
袋绑定保留，重试原事实可完成；成功后的重复上报不再次换袋或建立皮重。

该清运记录沿用原规则，不进入订单审核、返现或提现链路。
真正最终称重失败仍形成系统异常清运记录，皮重无效，不把缺失重量补为零。
是否允许接下一笔业务还要结合原有完整准入条件；本批不单独宣称新协议已能恢复接单。

## 2. 修复点和保留规则

原实现有三处稳定值专用逻辑：首重 SQL 固定写成稳定均值；非稳定末重只能存为
最后观察值；只有稳定末重能产生可靠末重记录和有效新袋皮重。现已成对修改：

- 首重保存实际状态、取值方式、可用标记、健康与故障字段，不再写死稳定均值；
- 合格中位数在首重/末重主重量列保存，状态仍为 `UNSTABLE`，方法为 `TIMEOUT_MEDIAN`；
- 末重与新皮重必须完全相等；合格中位数参与原清运计算和非负皮重建立，不冒充故障；
- 首末重量继续受冻结校准版本、最小/最大克数约束，不依据当前可变配置重算历史事实；
- 不合格中位数不能通过填写故障码而降格混入旧失败分支；
- 旧固定帧单样本稳定均值和真正末重失败路径保留。

中位数要求与 P1AR/P1AS 一致：5000 毫秒、5～32 样本、有符号 32 位克数、健康 `OK`、
故障码为空、重量非空且可用，测量标识和启动/事件编号合法。
新袋皮重的负值规则没有放宽；硬件校准准确度仍由人工处理。

容量投影仍有 `latest_stable_total_weight_g` 这一历史列名，本批没有改写全端字段。
它保存可用的数值投影，不是稳定性证明；真实质量和取值方法仍由关联物理结果提供。
P6 展示适配还需核对这类历史名称，不能把中位数在界面上描述为稳定测量。

## 3. 数据库与启动要求

新增 V74 `V74__clean_timeout_median.sql`。核对后分配，未修改 V1～V73 迁移。
单个原子 `ALTER TABLE` 替换清运首/末质量、首/末取值、清运扩展字段及结果形状共六条
CHECK 约束。保留旧非中位数和失败分支；中位数新增分支以 `COALESCE(..., FALSE)`
拒绝缺字段。最终中位数必须有相等的新袋皮重，不能利用 SQL 空值比较绕过。

无新增表、回填、权限或资金修改；仍为 134 张领域表，授权清单 38。
新后端的数据库版本门禁、健康说明、打包排除清单、H-02/F-07 目标同步为 V74。
H-02 保留 V72/V73 续跑识别，并增加已到 V74 的识别；没有修改 V72 的历史功能检查，
也没有把测试中的重量值 73.125 错当版本号替换。

本地隔离环境先执行 V1→V73，再单独执行 V73→V74；最终 74 条成功迁移、135 张表
（含 Flyway 历史表）。V74 checksum 为 `-2081360150`，执行耗时 89 毫秒。
V73 文件摘要仍与 P1AS 相同。V74 文件 SHA-256：

`29b4662dc6ff04f38d4eba770e8f450bd0cf06ef2c0459cfe97bedf902fab75a`

**未发布、未迁移现网。** 新后端不可绕过 V74 启动门禁；成对发布前还需完成余下 P5/P7。
不能假定旧应用正确识别新事实，不能通过删事实、降低 Flyway 版本或 repair 回退。

## 4. 验证

采用已批准的 test-driven development（先用测试定位差异、再实现与回归）方式。
首个实际 MySQL 测试先在 V73 被清运 CHECK 拒绝，V74 后通过；版本门禁测试先出现
2 项预期失败，证明旧门禁仍接受 V73，同步后通过。
清运服务测试初次卡在照片夹具缺少 `CAMERA_NOT_READY` 原因；按真实约束补齐夹具后
通过，未放宽生产照片约束。这个夹具错误不记为生产服务缺陷或功能红灯证据。

使用缓存的 MySQL 8.4.10 镜像、Maven `-o` 离线缓存、Java 21；未重下依赖。
专用容器 `ecobin-p1at-median-20260913-01`、本机回环端口 `43502`，数据位于 tmpfs，
没有宿主机数据绑定。基础迁移库为 `ecobin_median_p1at`。
验证结束后已核对专用容器 ID、标签与自动移除设置并停止容器，容器和 tmpfs 数据已移除。
未停止或删除预先存在的其他容器、卷或 Windows MySQL 实例。

新增 **113 项清运 MySQL 专项通过，零失败/错误/跳过**：

- `CleanMedianMysqlConstraintTest`：65 项，覆盖两侧中位数、54 个质量字段反例、
  数值/样本边界、新皮重缺失/不一致、旧单样本均值及真正末重失败。
- `CleanMedianTransactionTest`：48 项，调用真实清运业务服务、真实事务与真实确认服务，
  覆盖清运记录和皮重内容、换袋、重复、末端失败回滚后重试、首末取值保存、旧末重失败、
  34 个质量反例、5 个人工确认/皮重反例和 3 个冻结配置不匹配。

事务测试每例在专用容器内创建随机 `ecobin_p1at_case_*` 数据库，复制迁移后的 11 张
写入/状态表，保留 CHECK 和唯一索引，执行结束删除该例数据库。袋表必须被两个别名
联接，故不使用不支持这种重入读取的 MySQL 临时表。最终核对残留用例库为 0。

**测试边界：** 真实清运服务生成真实形状的清运记录、袋事件和皮重，未替换业务计算；
但父级资产、配置、原操作等为最小夹具，`CREATE TABLE ... LIKE` 不复制外键/触发器，
可靠任务登记为同事务 SQL 探针。因此它不是全外键、生产最小权限、含真实历史数据
迁移、多连接并发或真实云端/现场验收。无指定环境变量时专项会明确跳过；上述结果
来自真实运行，不能用默认跳过冒充通过。

同时在同一容器创建 `ecobin_median_p1as` 检查副本，复制 V74 物理结果表，重新执行
上一批投递 108 项专项，确认清运约束变更没有破坏投递中位数。该副本不是另一次完整迁移。

组合回归 **28 个 Java 测试类、382 项通过、零失败/错误/跳过**：

```powershell
.\mvnw.cmd -o -q -pl ecobin-bootstrap -am '-Dtest=CleanMedian*,ApplyCleanComplete*,StartCleanOperation*,CleanCommandObservationDecision*,CleanRecordMigrationBoundaryTest,DeliveryMedian*,TrustedDeliveryCompletion*,DeliveryRecoveryQuarantine*,ApplyDeliveryComplete*,OneNetEventDispatcher*,P0DatabaseEpochPolicyTest,RuntimeSafetyConfigurationTest' '-Dsurefire.failIfNoSpecifiedTests=false' test
```

两组专项需设置各自专用数据库 URL。报告 SHA-256（后续运行会覆盖）：

- `ecobin-bootstrap/target/surefire-reports/TEST-org.enveloping.ecobin.CleanMedianTransactionTest.xml`：
  `3c65d9ca08a281dd62db451d4b8085db40e81c4b0801f392ea5ff6eda3f13c4a`。
- `ecobin-bootstrap/target/surefire-reports/TEST-org.enveloping.ecobin.bootstrap.database.CleanMedianMysqlConstraintTest.xml`：
  `3861def9243cedf56c3bd52062ee4a06d770467668b0e8b2865afc44e0f3f4d6`。

完整契约 **25 项通过、0 notes**，104 份生成物一致；67 条消息、50 帧、1707 条流轨迹、
16 个摘要配置，Python/Java 21/两种 C 校验通过。
H-02 授权清单静态检查及三个相关 PowerShell 脚本语法检查通过。

## 5. 下一步和未改边界

继续运行快照、独立基准及重量准入与数据库的成对支持。正常投递完整资金链证明、
原生结果转云端、主程序集成、HMI/展示和现场验证仍待完成；P5 未整体完成，P7 不可发布。
P4 两项未确认的资金取舍不实施；真实 RS485 迟到应答归属仍待硬件资料。
OneNet 候选 2.1.2、UART rc.20、Pi 业务 schema 30、永久层 schema 3 不变。
发布清单 25/30 不一致与 Windows SQLite 强杀恢复 1546 未修复，本批未重跑全硬件套件。

未 SSH、未操作 GPIO/UART、未部署/导入物模型/烧录、未修改真实业务或资金。
本批无需用户现场操作。
