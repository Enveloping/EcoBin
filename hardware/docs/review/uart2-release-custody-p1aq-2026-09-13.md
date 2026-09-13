# P1AQ：发布包原记录读取与打包前版本核对

日期：2026-09-13。范围是已批准 P7 中可独立推进的文件清单与构建检查，
不是 P7 完成，也不是新协议可发布声明。P4 两项资金取舍仍待用户确认，未按建议实现。

## 实际问题与修改

P1AK 的 `mcu_action_evidence.py` 和 P1AO 的 `work_recovery.py` 不在运行包清单内。
只在源码目录测试、或仅导入 `main`，不能证明安装后的程序能够读取持久化的动作/恢复记录。

本轮使用 TDD，先按真实业务包、运行包清单复制源码到临时独立目录；由实际 MCU C 核心
产生投递过程证据，Pi 通过真实临时 SQLite 保存恢复意图后关闭数据库，再用 Python 3.11
隔离进程读取。原清单的两个用例均在初始化数据库时出现 `ModuleNotFoundError: work_recovery`。

在 `hardware/install/runtime_payload_manifest.py` 的 `RUNTIME_APP_FILES` 补入上述两个模块，
`BUSINESS_APP_FILES` 按既有派生规则同时纳入。不改变历史 `LEGACY_RUNTIME_APP_FILES`，
也没有把业务数据库或恢复编排模块加入永久通信代理/更新器私有文件清单。

补齐后覆盖投递/清运 × 业务包/运行包四种组合：调用包内 `edge_store_prepare.main`，
再初始化、读取原恢复意图、完整原始事实、动作身份和占用；验证这些数据逐项保持不变。
子进程使用 `-I`，且核对关键模块的实际加载路径均在独立包内，不能借用源码目录补漏。
不发送串口，不确认旧动作成功，不释放占用，不产生云事件或结果分类任务。

对这两个入口模块做本地导入关系检查，涉及 11 个本地模块，在两种当前清单中均无遗漏。
这只是当前模块的静态依赖检查，不声称验证任意动态导入或所有业务分支。

## 构建入口的版本核对

当前代码 `CURRENT_SCHEMA_VERSION=30`，发布清单仍声明 `EDGE_SCHEMA_VERSION=25`。
本轮没有通过改常量、改断言或跳过测试来消除这一差异。

新增 `verify_source_schema_version(source_root)`，由两个公开 `build_release` 入口首先调用：

- 只解析 `edge_store.py` 顶层版本声明，不导入或执行应用，不打开业务数据库。
- 要求单个正整数常量，允许普通赋值或带类型标注的赋值；缺文件、语法/编码错误、重复声明、
  布尔值、字符串或计算表达式均拒绝。
- 声明与当前打包器的发布版本不一致时，明确报错并停止；此时尚未进行目标平台检查、
  签名私钥读取、依赖准备或输出目录创建。
- 当前真实候选已只读核对，返回 `source schema 30 differs from release manifest 25`。

这不是任意 Python 源码的安全分析器，也不代替真实数据库迁移、固件能力或后端包认可。
保留既有签名流程测试，只为其临时测试源码补入一致的版本声明，没有模拟跳过新检查。
两个脚本的直接执行 `--help` 也已通过，覆盖相对导入之外的工具入口。

## 验证结果

- 新增 `test_native_release_custody.py`：4 项实际 C/SQLite/独立包读取用例。
- 新增 `test_runtime_source_schema.py`：15 项构建入口与声明检查用例。
- 新增 19 项加既有 2 项签名测试：21 项通过，4.76 秒。
- 最终相关回归：293 通过、1 失败、30 跳过、24 子测试通过，59.06 秒，退出码 1。
- 完整契约检查：25 通过、0 注释；104 生成物、67 UART 消息、50 帧、1707 流轨迹、
  16 摘要样例保持一致，Java 21 与两种 C 形式均通过。
- 7 个 Python 文件的 3.11 语法/行末空白检查通过；本轮已跟踪文件 `git diff --check` 通过。

唯一失败仍是 `test_release_manifest_schema_matches_edge_store_current_schema` 的 25/30 差异。
它仍未解决；新增入口检查的作用是拒绝构建声明错误的包，而不是让完整回归转绿。
30 项跳过涉及 Windows 无法验证的 POSIX 权限/属主、原子符号链接、Linux 安装 CLI、
Bash 与 ext4 验证，不能算通过。本轮没有重跑完整硬件套件或强杀 SQLite 恢复测试；
此前 Windows SQLite 1546 问题仍未解决，不用这次定向回归宣称其恢复。

最终报告：`C:\Users\24217\AppData\Local\Temp\ecobin-p1aq-package-custody-final.xml`。
SHA-256：`5251c3fc76054a076a08491e0fb86b9961de8508ae512d77d85b529d00352059`。
报告已核对 348 项展开记录，其中主用例 324 项、子测试 24 项，失败 1、错误 0、跳过 30。
临时目录报告不等于永久审计存储。

复跑入口：

```powershell
$py = 'C:/Users/24217/.ecobin/hardware/windows-py311/Scripts/python.exe'
& $py -m pytest hardware/tests/test_runtime_source_schema.py hardware/tests/test_native_release_custody.py hardware/tests/test_native_work_recovery.py hardware/tests/test_native_action_reconciliation.py hardware/tests/test_native_clean_action_reconciliation.py hardware/tests/test_business_release.py hardware/tests/test_runtime_release_install.py hardware/tests/test_build_device_management_maintenance_payload.py tools/orangepi-image/tests/test_image_tooling.py -q -rs --tb=short --junitxml="$env:TEMP/ecobin-p1aq-package-custody-final.xml"
$env:JAVA_HOME = 'C:/D/002-Tools/004-DevTool/jdk-21.0.10'
$env:PATH = "$env:JAVA_HOME/bin;$env:PATH"
& $py contracts/tools/validate_contracts.py
```

## 剩余边界

UART 仍为 `2.0.0-rc.20 / CLEAN_INTERRUPTION_CUSTODY_NOT_RUNNABLE`，业务库为 schema30，
永久库为 schema3；未修改 MCU、协议布局、生成物、资金逻辑或正常主程序。

当前业务清单摘要是 `54c7dfd3acf713f7bced08c3f2b13c7075f7b1498b0ee37df1ff34c2c9dcdd42`，
运行包清单摘要是 `4b94a9b3534543397cf4905baa459cbde484d317e83b6ae952ad2a2a56cae3e6`。
后端 `BusinessReleasePackageVerifier` 仍保留较早的文件白名单/摘要和 schema18 检查，
没有因本批清单补齐而扩大后端认可范围。正式成对发布仍须一起核对声明、能力、永久层、
后端允许列表、数据库迁移与 Linux/ARM64 环境；不能直接发布当前候选。

P4“过程数据齐全但缺最终包是否进入正常业务”和“问题归档后迟到完整结果的资金处理”
继续等待用户确认。归档/许可/单次发送/新动作反馈核对、清运恢复、MCU 与 Pi 主程序、
真实 RS485、HMI 与热点验收仍未整体完成。本轮仅推进不依赖上述资金取舍的发布基础工作。

未构建签名制品、制作镜像、部署、烧录、连接 SSH/UART/GPIO、操作设备或写云端数据。
复用现有本机 Python 3.11、Java 21、Clang 等工具及缓存，没有重新下载依赖。
