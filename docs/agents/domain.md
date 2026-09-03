# EcoBin 工程领域上下文

本仓库采用单一工程上下文，不另建一套 `CONTEXT.md` 或 ADR 目录来复制已经冻结的设计。Agent 先读 `CLAUDE.md`；涉及跨端、IoT、硬件、旧实现差距或历史决策时，再完整读取 `docs/architecture/project-context.md`。

## 目标基线顺序

判断“后续应实现什么”时，按以下顺序读取：

1. `docs/planning/requirements-baseline.md`
2. `docs/planning/p0-scope-baseline.md`
3. `docs/planning/business-model-baseline.md`
4. `docs/planning/system-architecture-draft.md`
5. `docs/planning/database-design-draft.md`
6. `docs/planning/interface-design-draft.md`
7. `docs/planning/detailed-design-draft.md`
8. 当前 initiative 的独立任务文件

上游冻结规则高于下游任务措辞。发现冲突时停止实现并交给主审修订，不得由领取任务的 agent 自行选择一份更容易实现的解释。

## 当前实现与目标

- 当前代码、测试、根 POM、旧 Flyway、当前 OneNet 物模型和设备程序描述“现在如何运行”。
- planning 基线描述“目标系统应如何运行”。
- 两者差异是实施工作，不表示可以用旧实现推翻冻结目标，也不表示设计已经落地。
- 当前 initiative 是 `p0-controlled-loop`，目标是公司自用的投递及其审核返现、清运、机构充值和真实微信零钱提现受控闭环。

## 当前模块语言

当前模块化单体包含八个 Maven 模块：

- `framework`：通用 Web 响应与异常契约、可信上下文、安全、租户/机构防线和事务骨架；
- `identity`：租户、机构、工作人员、机构用户、认证和授权；
- `device`：设备资产、部署、配置、占位、投递 session、命令和物理事实；继续轮次只存在设备本地；
- `funds`：钱包、机构资金、充值、免确认收款授权、提现和微信转账业务状态；
- `recycling`：投递订单及其审核纠错、清运记录及直接修改留痕、袋、基准和满溢；清运记录不审核，`clean.edit` 保存即生效；
- `operations`：可靠任务、审计、异常、告警、对账和只读概览；
- `integration`：OneNet、COS 和微信适配；
- `bootstrap`：组装、全局 Flyway 和跨模块测试。

跨模块只通过冻结的 `.api` 边界协作。DD-004 与 PDD-001 的窄例外以接口设计 I-051～I-053 和详细设计为准，不能概括成裸主键传递、跨模块查表或事实所有权转移。

## 任务工作方式

- 正式任务位于 `docs/planning/tasks/p0-controlled-loop/`。
- 任务编号和依赖不得由实现者自行重排。
- `ready` 不等于实施授权。
- 资金、物理安全、租户/机构隔离、真实 MySQL、真机和真实微信证据不能因目标窗口紧张而降级。
