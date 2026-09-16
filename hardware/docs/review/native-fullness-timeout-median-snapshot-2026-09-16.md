# 原生满溢接线与超时中位数快照修复实施记录

日期：2026-09-16

## 结论

本次修复把量产 `uart-v2` 原生结果上报接入了 MCU 的 `DEVICE_FACTS`
（设备事实）快照，并把投递或清运完成事件、满溢状态投影、可靠待发送事件以及
原生报告状态纳入同一个 SQLite 事务。`TIMEOUT_MEDIAN`（测量超时后取样本中位数）
现在会按实际中位数投影到运行快照，不再退化为最新一次原始重量。

本次没有修改 UART rc.27、OneNet 2.5.0、后端接口、数据库结构或 HMI，也没有制作
新镜像、烧写 TF 卡或修改后台允许版本。

## 实现

### 原生事实来源与幂等边界

- `NativeBusinessRuntime` 把 `current_device_facts` 作为只读事实提供器注入
  `NativeResultReporter`。
- 报告器先查询既有报告；报告已经存在时直接返回，不再次读取设备事实。
- 首次创建报告时只读取一次事实快照，并复制后交给存储层，避免同一报告的判断依据
  在事务外反复变化。

### 共用满溢判断

新增共用满溢构造器，原生结果上报和旧 `WorkManager` 兼容路径使用同一套规则：

- 红外传感器使用 `fullnessInfraredBlocked`。
- 超声波传感器使用
  `fullnessDistanceMm <= fullnessDistanceThresholdMm`。
- 只有 `fullnessReadStatus=VALID`、传感器种类与配置一致且字段可解释时，才把
  传感器观测用于满溢判断。
- 不检查 `fullnessCapturedUptimeMs` 的年龄；只要该观测属于当前成功解析的
  `DEVICE_FACTS` 快照，即允许沿用。
- 缺测、读取失败、字段损坏或传感器种类不匹配均投影为 `NOT_SAMPLED`，不会伪造
  `CLEAR`。
- `SENSOR_ONLY`、`WEIGHT_ONLY`、`SENSOR_OR_WEIGHT` 的组合规则保持不变；辅助满溢
  传感器异常不会成为投递或清运的业务准入条件。
- `MCU_INDEPENDENT_RECHECK` 只在调用方实际提供了独立设备事实快照时使用。
- 新模块已加入当前运行时和业务发布源码白名单；历史发布白名单保持不变。

### 原生完成事务

正常投递和清运结果创建报告时，在一个 SQLite 事务中完成：

1. 写入 `DELIVERY_COMPLETE` 或 `CLEAN_COMPLETE` 完成事件；
2. 根据同一事实快照构造并应用满溢状态变化；
3. 写入对应可靠事件；
4. 把原生报告任务改为 `REPORT_CREATED`。

任一步失败都会回滚全部写入。投递使用当前袋和末重；清运使用新袋，并把清运末重
作为新袋本次满溢计算的基准，但正式袋皮重仍按既有边界等待后端确认后再写入。
报告重试、进程重启和 MCU 重复结果都通过既有报告绑定返回同一结果，不重复读取事实，
也不重复产生满溢事件。

### `TIMEOUT_MEDIAN` 运行快照

`TIMEOUT_MEDIAN` 已加入原生重量终态白名单，投影为：

- `weightMeasurementStatus=UNSTABLE`
- `weightValueAvailable=true`
- `reportedWeightGrams=measurementWeightGrams`
- `weightValueKind=TIMEOUT_MEDIAN`
- `weightSensorHealth=OK`
- `weightFaultCode=null`

测量编号、5000 毫秒耗时、样本数、校准版本和 MCU 启动标识继续来自该终态测量。

## 验证

使用 Python 3.11.15 执行以下专项测试：

```text
python -m pytest \
  hardware/tests/test_fullness_transition.py \
  hardware/tests/test_native_result_report.py \
  hardware/tests/test_edge_boot.py \
  hardware/tests/test_edge_store.py \
  hardware/tests/test_native_business_runtime.py -q
```

结果：`180 passed in 30.06s`。

专项覆盖红外和超声波判定矩阵、旧观测、缺测和种类不匹配、原生投递与清运、事务
故障回滚、重复结果/报告重试/进程重启去重，以及中位数与最新原始读数不同时的
运行快照。稳定均值、实时原始重量和故障终态也做了回归断言。

完整硬件测试曾启动，但在用户要求停止前出现一个失败标记并在生成汇总前被中止。随后通过
保留的 pytest 临时发布包和测试顺序定位到业务发布导入检查：首次实现漏将新增的
`fullness_transition.py` 加入发布源码白名单，隔离包包含调用方却缺少实现模块。该清单已修正，
但按用户要求未重跑失败用例；因此完整硬件测试仍不作为通过证据，既有 OneNet/UART 契约校验
也未继续执行。真实 HMI、RS485、机构和断电 HIL 仍属于后续上板验证。
