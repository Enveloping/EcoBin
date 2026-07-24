# EcoBin 本地任务仓库

EcoBin 当前不使用外部 issue tracker。正式实施任务以仓库内独立 Markdown 文件为唯一任务记录，统一放在：

```text
docs/planning/tasks/<initiative>/
```

当前 initiative 是 `p0-controlled-loop`。其任务索引为
`docs/planning/tasks/p0-controlled-loop/00-index.md`。

## 文件与身份

- 一个任务一个文件；文件名使用 `<lower-id>-<short-kebab>.md`，例如
  `v-04-delivery-to-pending-order.md`。
- `task_id` 是稳定身份。重命名标题或文件时不得改变 `task_id`，也不得复用已废弃编号。
- 依赖使用任务 ID，并链接到对应任务文件；不得只在正文中模糊描述“等前置完成”。
- 第 08 章是批准后的依赖来源；独立任务文件是实施期状态、负责人和证据来源。二者冲突时停止实施并由主审统一修订。

## 必需元数据

每个任务至少保存：

```yaml
task_id:
title:
status:
executor:
owner:
effort_range:
earliest_start:
blocked_by:
implementation_authorized: false
```

`executor: mixed` 的任务还必须保存 software、integration、acceptance 三阶段进度及各阶段最早开始条件。`effort_range` 使用 person-days，只估实际工作量，不把外部审批或等待时间伪装成工作量。

## 状态与更新

- 创建任务时，根据当前依赖事实设置 `ready` 或 `blocked`；未完成的任务依赖必须使整体状态保持 `blocked`。
- 进展、验证证据、阻塞变化和主审结论追加到任务的进展记录中；不要另建第二份私人任务清单。
- `done` 必须满足全部验收条件。Mixed 任务的软件完成不等于任务完成，真实联调和人工验收证据仍不可缺少。
- 外部条件消失后，应重新核对全部依赖和冻结设计，再把任务从 `blocked` 改为 `ready`。
- 关闭为 `wontfix` 时必须记录原因及替代去向；不得删除任务来隐藏历史。

## 授权边界

任务被发布或标记为 `ready`，只表示设计与依赖上可以领取，不表示已经授权修改代码、数据库、设备协议或外部平台。开始实施仍需项目负责人明确指令；未获得指令时只允许维护设计、任务元数据和只读审查。

