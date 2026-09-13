# P1AU：运行快照中位数与称重来源落库

日期：2026-09-13。范围：已批准计划 P5 的运行快照纵向切片；本地实现和验证，不是整套发布完成。

## 1. 业务效果

香橙派通过既有 OneNet 链路上报运行快照后，后端现在能保存合格的超时中位数：
重量仍为实际克数，状态仍为 `UNSTABLE`（窗口内未稳定），取值方式为 `TIMEOUT_MEDIAN`
（5 秒到期后的中位数），不伪装成稳定均值或称重故障。

投口快照另外保存称重故障码、测量所处 MCU 启动编号、测量事件编号。测量编号来自
测量自身，不能拿快照的启动编号/上传序号代替。旧记录缺失的来源保持空，不回填猜测值。

重复快照不重复更新；较旧快照可以登记其事件身份，但不覆盖较新重量和来源。
一个快照里第二个投口处理失败时，设备状态、第一个投口和事件登记一起回滚；
原数据修正后可重新处理。真正超时无值仍保存空重量和原故障，不复用上一份中位数。
重量可用不会清除烟感告警、伪造云端在线，也不会创建订单、清运记录、返现或提现任务。

这部分只更新观测记录，不单独释放业务占用或宣布设备可以接单。

## 2. 实现与边界

`TrustedOrangePiRuntimeFactService` 的真实快照入口在旧健康状态转换前检查新中位数：

- 测量状态 `UNSTABLE`、健康 `OK`、明确无故障、可用标记为真；
- 非空有符号 32 位克数、5000 毫秒、5～32 个样本；
- 合法 UUIDv4、正 MCU 启动/事件编号及范围内校准版本；
- 故障字段必须明确为空，不能把字段没上报当作无故障。

三个新字段在原投口更新 SQL 中绑定保存，没有另开事务或写旁路存储。
旧非中位数分支不因本次改动被强行升级成新能力。

核对实际接单调用链发现：`DeviceRuntimeWeightPolicy` 目前只有自己的测试引用，
没有业务调用方。`MiniappDeliveryDeviceQueryPolicy` 实际依据资产/验收、配置应用、
OneNet 在线、占用、永久运行层接单状态和投口经营配置形成云端条件；不依据旧称重
快照重新指挥机械。本批不修改这个未被调用的类来冒充接单接通，也不增加后端物理门禁。
香橙派新原生运行入口与其称重准入仍需后续真实接线验证。

独立基准完成事件仍有只接受稳定测量的旧归一化与数据库形状；满溢处理、界面历史
`stable` 字段命名及 P6 展示也要继续核对。本批没有声称这些部分已支持中位数。

## 3. 数据库与供应脚本

新增 `V75__runtime_weight_evidence.sql`，单条原子 ALTER 添加三个可空字段、来源范围
约束，并替换投口运行快照 CHECK。新中位数分支用 `COALESCE(..., FALSE)` 拒绝
SQL 空值绕过，同时保留原非中位数规则。没有新表、历史回填或资金改动。

源码数据库最低版本与健康说明推进到 V75；H-02/F-07 目标、打包资源检查同步。
H-02 保留 V72/V73/V74 续跑识别，新增 V75。授权清单由 38 增至 39，只新增投口
运行表三个字段的 UPDATE 权限，没有扩大成全表任意更新。

缓存 MySQL 8.4.10 专用容器中，先完整执行 V1→V74，再执行 V74→V75。
最终 75 条成功迁移、134 张领域表及 Flyway 历史表。V75 checksum `23926598`，
耗时 33 毫秒。V73/V74 文件摘要保持与此前记录一致；V75 SHA-256：

`2d834dbc9ba821c3272651771f6eed7b9c26a645e5df4b9586049e14d2290527`

尚未执行生产最小权限账号下的整套升级/业务链；授权脚本这里只做静态和语法验证。
新后端不能绕过 V75 门禁启动，后续成对发布需先迁移并同步权限再激活应用。
旧应用兼容性须另核对；不删事实、不降迁移版本、不使用 repair 伪装回退。

## 4. 测试证据

使用 TDD（先写行为测试、看到失败，再实现并回归）。真实接收测试首先因服务没有写
测量来源而被新 CHECK 拒绝；补齐保存后通过。遗漏故障字段测试曾被错误接受，补明确
校验后通过。版本测试先证明旧门禁仍接受 V74，同步 V75 后通过。
后续新增用例的旧 inbox 编号复用和二进制摘要比较错误属于测试准备问题，已修正，
不计为生产缺陷，不为通过测试而放宽数据库唯一约束。

新增 **76 项真实 MySQL 专项通过，零失败/错误/跳过**：

- `RuntimeMedianMysqlConstraintTest`：38 项；合法保存、33 个质量/空值反例、
  两组精确边界、缺历史身份的旧均值和真正无值超时。
- `RuntimeMedianTransactionTest`：38 项；真实公共接收服务、真实事务、两个投口，
  30 个异常字段反例、遗漏故障、原始来源、重复/乱序、末端失败回滚与重试、
  真超时、旧均值，以及烟感和传输在线事实独立。

每个事务用例拥有随机 `ecobin_p1au_case_*` 数据库，写表从迁移后数据库 LIKE 复制，
保留 CHECK/唯一键，父级查询使用最小夹具。实际固件身份服务使用合法的“无可选 F3
观测”分支；未假装原生协议完成了固定帧 F3 检查。可靠业务/资金端口不在快照路径中。

LIKE 不复制外键/触发器，单连接不能证明并发锁序，根账号不能证明生产权限；
没有真实历史数据迁移、完整云链路或现场验收。本批不以这些测试替代上述证据。

同一专用容器内还建立 P1AS/P1AT 检查副本，复制 V75 对应表，重新运行投递及清运
221 项专项；它们是检查副本，不是另外两次完整迁移。

组合回归 **34 个 Java 类、478 项通过、零失败/错误/跳过**：

```powershell
.\mvnw.cmd -o -q -pl ecobin-bootstrap -am '-Dtest=RuntimeMedian*,TrustedOrangePiRuntimeFactServiceSqlTest,RuntimeSnapshotPolicyMigrationBoundaryTest,MiniappDeliveryDeviceQueryPolicyTest,DeviceRuntimeWeightPolicyTest,CleanMedian*,ApplyCleanComplete*,StartCleanOperation*,CleanCommandObservationDecision*,CleanRecordMigrationBoundaryTest,DeliveryMedian*,TrustedDeliveryCompletion*,DeliveryRecoveryQuarantine*,ApplyDeliveryComplete*,OneNetEventDispatcher*,P0DatabaseEpochPolicyTest,RuntimeSafetyConfigurationTest' '-Dsurefire.failIfNoSpecifiedTests=false' test
```

三组专项分别显式指定本机回环专用 URL；不指定时会跳过，不能把跳过当通过。
最终报告 SHA-256（后续运行会覆盖）：

- `RuntimeMedianTransactionTest.xml`：`3eb65fb898468eb4e2aa22791ec37c19c22bd046600bfd6bc14079ff23a138f4`。
- `RuntimeMedianMysqlConstraintTest.xml`：`36084f13211d9ca6c12c145e2fa1b5b13d3bf3a21f157430538689ffb78f37fe`。

完整契约 25 项通过、0 notes；104 份生成物一致、67 条消息、50 帧、1707 条流轨迹、
16 个摘要配置，Python/Java 21/两种 C 校验通过。授权清单静态检查和三个 PowerShell
脚本语法检查通过。Maven 使用离线缓存，MySQL 使用本地缓存镜像，未重下依赖。

专用容器 `ecobin-p1au-median-20260913-01` 使用回环端口 44042、tmpfs，无宿主数据绑定。
测试结束时三个专项用例库残留为 0；核对精确 ID/标签/自动移除设置后停止并移除专用
容器。没有停止/删除其他容器、卷或 Windows MySQL。

## 5. 下一步

继续独立基准、满溢与香橙派实际准入，再接原生云端结果、主程序、HMI 和展示。
P5 未整体完成，P7 不可发布；P4 两项未确认资金取舍不实施。真实 RS485 迟到回复
归属仍待资料，HMI 按钮选择仍待确认。OneNet 候选 2.1.2、UART rc.20、业务
SQLite schema 30、永久层 schema 3 不变。

发布清单 25/30 不一致和 Windows SQLite 强杀恢复 1546 未修复；本批未重跑全硬件
套件。未 SSH、未操作串口/GPIO、未部署/导入物模型/烧录、未修改真实业务数据。
本批不需要用户现场操作。
