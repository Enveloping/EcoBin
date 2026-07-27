# 香橙派本机投递命令

这些脚本直接在香橙派上运行，将一条与 OneNet 下行等价的命令写入网关
SQLite `command_inbox`。正在运行的 `main.py` 会继续完成命令校验、工作槽
占用、UART 下发和 MCU 状态机处理。脚本本身不会打开串口。

## 开启投递流程

在 `hardware/` 目录中执行：

```bash
python3 tools/start_delivery.py --port-no 1
```

脚本会从当前已生效的配置中读取部署编码、配置摘要、投递倒计时、负重量
阈值和端口单价，并自动生成 `commandUid`、`sessionUid` 和测试用 `bagUid`。
默认命令有效期只有 30 秒，避免网关未运行时遗留的测试命令在很久以后执行。

只生成并检查命令，不实际投递：

```bash
python3 tools/start_delivery.py --port-no 1 --dry-run
```

如果网关使用了非默认数据库路径：

```bash
python3 tools/start_delivery.py \
  --port-no 1 \
  --db-path /absolute/path/to/edge.db
```

成功输出中的 `inboxState` 通常会从 `PENDING` 很快变成
`WAITING_MCU_RESULT`。如果仍为 `PENDING`，检查 `main.py` 是否正在运行。

## 查询状态

查询当前是否有投递或清运任务：

```bash
python3 tools/edge_local_command.py active-work
```

按开启投递时输出的 `commandUid` 查询命令：

```bash
python3 tools/edge_local_command.py status \
  --command-uid <commandUid>
```

脚本会拒绝在已有活动工作时再次开启投递，也会拒绝使用不存在、禁用或没有
已生效配置的投口。该入口仅用于本机联调，不替代正式 OneNet 下行链路。
