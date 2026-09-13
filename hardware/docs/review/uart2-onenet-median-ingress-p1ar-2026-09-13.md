# P1AR：OneNet 中位数传输与后端接收

日期：2026-09-13。范围是已批准 P1/P5 中的正常重量格式和消息接收边界，
不是 P5 业务事务完成，也不是设备可发布声明。继续使用测试驱动方式，先验证拒绝/保留行为再改实现。

## 本批结果

投递、清运和运行快照现在可以在候选 OneNet 格式中传递 `TIMEOUT_MEDIAN`（超时中位数），
香橙派投影和 Java 接收器保留原始重量、测量编号、采样数、耗时和取值方式。
它仍是 `UNSTABLE`（未满足稳定窗口），不改称稳定均值，也不自动附加 `WEIGHT_UNSTABLE` 故障。

例如 MCU 在一个测量阶段积累足够的合法样本，5 秒仍持续波动，按已批准规则得到中位数。
本批允许消息表达并接收这个结果；后续应按既有业务规则处理，不能仅因使用中位数强制人工审核。
但本批没有连接原生结果到云端发件任务，也没有修改订单、钱包、自动提现、袋关系或接单许可。
目前后端业务服务和数据库仍会拒绝或按旧语义处理它，不能据接收测试通过上线。

### 格式规则

- OneNet `mappingVersion` 从 `2.1.1` 增量到 `2.1.2`，仍为 `IMPLEMENTATION_CANDIDATE`。
- 在既有五个取值方式之后追加 `TIMEOUT_MEDIAN`，线级值为 6；原 1～5 的含义不变。
- 中位数要求状态 `UNSTABLE`、值可用、整数克非空、传感器 `OK`、故障码为空、耗时 5000 ms、
  样本数 5～32。缺值不转为零，故障不能凭取值方式标签被掩盖。
- 运行快照使用相同质量规则；测量 UUIDv4、启动编号、事件序号必须存在，字段范围遵守契约。
- 运输层保留有符号 32 位整数，包括零和负数；这不证明实物准确度，也不替代冻结配置中的范围和标定版本核对。
- 旧 `LAST_FOUR_MEAN` / `AVAILABLE_SAMPLES_MEAN` 仍保留原故障；没有将这些旧波动均值升级成正常中位数。

修改 `contracts/onenet/common.schema.json`、`events/events.schema.json` 和唯一映射源，
通过既有生成器同步物模型候选、线级映射、`hardware/onenet_projection_model.json` 和源清单。
没有手改生成文件。物模型仍为 18 服务、22 事件，没有新增函数点。

UART Registry 仍为 `2.0.0-rc.20 / CLEAN_INTERRUPTION_CUSTODY_NOT_RUNNABLE`。
冻结旧 UART `WeightValueKind` 不追加云端符号；原生结果已有独立的 `ResultMeasurementKind`。
契约对齐测试明确检查“旧 UART 五种 + 云端中位数”集合，而非放宽为任意符号子集。
本批不改 UART 字节布局、MCU、主程序或旧适配器。

## Java 接收边界

`OneNetEventDispatcher` 对普通测量和运行快照复用取值方式表及中位数质量检查。
依旧先恢复语义载荷、核对设备提供的规范摘要，再调用可信收件接口；非法输入走原格式隔离入口。
测量数据不完整时不会到达可信收件接口。这里的“格式隔离”不是业务异常收口，不清原业务占用。

清运首重允许已验证的稳定均值或中位数；清运末重若可用，新袋基准必须存在且与末重相同。
末重缺值/故障不能借旧均值标签变成可用重量。既有清运员完成确认、关门确认、电锁断电、照片、
原业务身份和摘要检查没有取消。固定帧运行快照仍保留原单次 `LAST_OBSERVED` 规则。

新增 Java 测试只模拟设备来源解析和可信收件两个模块边界，实际调用公开 `handle`，
核对规范消息及隔离结果；不是数据库持久化、Pulsar 实链路或业务结算测试。

## 验证与证据

- 新增 Python 契约/实际 OneNet 编码测试：45 项通过，5.46 秒。
- 新增 Java 接收测试：53 项通过；投递/清运前后测量、两个投口快照、空值、样本/耗时边界、
  取值方式与故障矛盾、缺身份、整数范围、新袋基准不一致及旧格式回归均覆盖。
- Java 相关回归：111 项通过，零失败/错误/跳过；包含所有 `OneNetEventDispatcher*`、
  原投递解析/重量策略/SQL、清运 SQL、运行重量准入策略测试。使用 Java 21、Maven 离线模式和已有缓存。
- Python 最终相关回归：478 通过、2 跳过、1645 子测试通过，70.39 秒，退出码 0。
  两项跳过分别为 POSIX 目录权限和 root/systemd 次级 IPC 组验证，不记作通过。
- 完整契约校验：25 通过、0 注释；104 生成物、67 UART 消息、50 帧、1707 流轨迹、
  16 摘要样例，Java 21 与两种 C 形式通过；冻结旧 UART 生成物保持。
- 本批修改文件的空白检查通过。没有运行完整硬件套件、SQLite 强杀、真实 MySQL 或真实设备验收。

首轮新增用例分别证明旧接收器拒绝枚举 6、清运拒绝中位数首重/基准、运行快照缺少中位数质量和字段边界检查。
每步修改后重跑；同时以反例补上清运“仅均值标签但状态失败”不能通过的条件。
边界测试曾因测试内 Java LongNode 与读回 IntNode 的对象相等比较失败，改为精确整数值断言；
未修改产品值或绕过摘要验证。最终上述套件全部达到记录的结果。

Python 最终报告：`C:\Users\24217\AppData\Local\Temp\ecobin-p1ar-onenet-median-final.xml`。
SHA-256：`88f6995439b236152a5d2b2d9c9e5d5e73b578164efe11afddadc60353963bfc`。
XML 共 2125 项展开记录，失败/错误 0、跳过 2，等于 480 主用例加 1645 子测试。

Java 新增套件报告：`ecobin-integration/target/surefire-reports/TEST-org.enveloping.ecobin.integration.onenet.inbound.OneNetEventDispatcherTimeoutMedianTest.xml`。
SHA-256：`b994ef8ed43ca2f23db8cfe9c99d5fab4d70909b98aa309c10ec7277a46c16ba`。
报告位于临时/构建目录，不等于永久审计存储。

当前物模型候选 SHA-256：`463589480312fb5d2b285e97f5639c7feb3b12795b62a612408bec6b6d8ca007`。
香橙派投影模型 SHA-256：`a65c8d16bdc061009a729871eeb945c8a94bcbf37d754b4b8e2a80a737bc9487`。
它们尚未导入 OneNet 或部署到设备。

复跑入口：

```powershell
$py = 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe'
$env:JAVA_HOME = 'C:/D/002-Tools/004-DevTool/jdk-21.0.10'
$env:PATH = "$env:JAVA_HOME/bin;$env:PATH"
& $py contracts/tools/validate_contracts.py
& $py -m pytest contracts/tests hardware/tests/test_onenet_wire.py hardware/tests/test_direct_onenet_transport.py hardware/tests/test_device_runtime_projection.py -q -rs --tb=short --junitxml="$env:TEMP/ecobin-p1ar-onenet-median-final.xml"
.\mvnw.cmd -o -pl ecobin-integration -am '-Dtest=OneNetEventDispatcher*,TrustedDeliveryCompletion*,ApplyDeliveryCompleteServiceSqlTest,ApplyCleanCompleteServiceSqlTest,DeviceRuntimeWeightPolicyTest' '-Dsurefire.failIfNoSpecifiedTests=false' test
```

## 后续必须成对修改的消费者

下列为源码核对结果，本批尚未修改，不能把本次接收能力解释为它们已完成：

| 消费者 | 当前差距与下一步 |
|---|---|
| `TrustedDeliveryCompletionService` | 正常完成仍硬编码前后 `STABLE/STABLE_WINDOW_MEAN`；需接可用中位数、冻结范围/标定/净重核对，并保留原事实与订单事务规则 |
| `ApplyCleanCompleteService` | 首末重、净重计算、新袋基准 SQL 仍依赖稳定均值；需保留真实中位数标签，同事务处理原清运/袋/皮重，不能只改一个前置判断 |
| `TrustedOrangePiRuntimeFactService` | 运行快照入库受旧枚举约束；独立空袋基准的归一化仍将所有 `UNSTABLE` 改成波动故障，需同步取值方式和事实存储 |
| `DeviceRuntimeWeightPolicy` | 当前仅认可稳定状态及旧两种取值方式；业务准入还未支持中位数，需结合可信快照质量/配置证据，不凭一个标签放行 |
| 满溢/独立基准与原生结果到云端转换 | 共用格式新增符号不等于它们的业务处理已支持；固定帧满溢兼容检查未放宽；原生测量身份、阶段、配置与云端 UUID 对应仍须明确接线 |

数据库需新增届时可用的前向迁移，不能修改已部署 V4、V12、V14、V21：

- V4 `dev_physical_result` 原质量约束把非稳定重量与故障绑定。
- V12 `ck_dev_port_runtime_snapshot_projection` 的取值方式白名单不含中位数。
- V14 投递前后可用重量、正常完成形状仍仅接受稳定均值。
- V21 清运前后重量、完成形状及新袋基准仍依赖稳定均值。
- 后续一并核对 epoch guard、最小权限、历史数据兼容和真实 MySQL 约束/事务；本批只读确认最高源码迁移为 V72，未预占版本号或连接数据库。

P4 两项待确认资金取舍、实际称重模块时序资料和 HMI 返回交互均未自动采纳建议。
发布清单 25/业务库 30、后端旧白名单/schema18、Windows SQLite1546 等既有问题保持未解决；
详见 [P1AQ](uart2-release-custody-p1aq-2026-09-13.md)。本批相关套件转绿不代表这些问题已修复。
原生主程序、真实 RS485、恢复归档/许可/反馈核对、清运恢复和 P7 仍未整体完成。

未部署、导入物模型、构建发布制品/镜像、烧录、改 HMI、SSH 或操作真实 UART/GPIO。
当前无需用户现场操作；下一步继续独立的正常中位数业务/数据库纵向实现，不跨入待确认资金政策。
