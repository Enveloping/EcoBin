# EcoBin 任务状态与执行主体

EcoBin 使用两个正交维度，不用一个标签同时表达“做到哪一步”和“由谁完成”。

## 生命周期 `status`

| 状态 | 含义 |
|---|---|
| `needs-triage` | 尚未完成范围、依赖或验收审查 |
| `needs-info` | 缺少会改变方案的必要信息 |
| `ready` | 设计和任务依赖允许领取；仍须另行获得实施授权 |
| `in-progress` | 已有明确负责人，正在执行获授权的工作 |
| `in-review` | 交付物已提交，等待主审或指定人工验收 |
| `blocked` | 存在未完成任务依赖、外部条件或安全门槛 |
| `done` | 所有验收和必需人工证据已完成 |
| `wontfix` | 经明确裁决不再执行，并已记录原因 |

典型流转为：

```text
needs-triage → needs-info → ready → in-progress → in-review → done
                        ↘ blocked ↗
```

`blocked` 可以从任何未终态进入；阻塞解除后回到它原本应处的状态。`done` 和 `wontfix` 是终态，重开必须留下记录。

## 执行主体 `executor`

| 值 | 含义 |
|---|---|
| `agent` | 软件 agent 可以完成全部工作和自动证据；仍由主审验收 |
| `human` | 主要依赖项目负责人、部署操作者、MCU 负责人或真实渠道操作者 |
| `mixed` | 软件工作可由 agent 完成，但联调或最终验收必须有人参与 |

`executor` 不随生命周期变化。Mixed 任务分别维护：

```yaml
phase_progress:
  software:
  integration:
  acceptance:
```

软件阶段完成后，若人工联调尚未具备，整体任务仍为 `blocked` 或 `in-progress`，不能标记 `done`。

## 与常见 triage 角色的映射

| 通用角色 | EcoBin 表达 |
|---|---|
| `needs-triage` | `status: needs-triage` |
| `needs-info` | `status: needs-info` |
| `ready-for-agent` | `status: ready` + `executor: agent` |
| `ready-for-human` | `status: ready` + `executor: human` |
| `ready-for-mixed` | `status: ready` + `executor: mixed` |
| `wontfix` | `status: wontfix` |

当前是本地 Markdown 任务仓库，不创建或同步 GitHub label。

