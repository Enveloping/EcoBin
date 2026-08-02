# 假设备关联数据受控清理

当前联调环境只保留 OneNet 产品 `tB6NlBWW0V` 下设备名/硬件 SN
`test-divice-1`。清理目标不是只删除设备资产，而是删除其他假设备沿外键关联的部署、运行
事实、业务记录、可靠收件箱、可靠任务和技术尝试，避免遗留确认任务继续下发。

工具为 `tools/database/purge-fake-device-data.py`，要求 Python 3.11 和 MySQL 8.4。密码只从
进程环境变量 `ECOBIN_PURGE_MYSQL_PASSWORD` 读取，不写入命令行、日志或版本库。

## 1. 先只读预演

先准备数据库账号密码，再运行：

```powershell
$env:ECOBIN_PURGE_MYSQL_PASSWORD = '<从仓库外秘密存储读取>'
uv run --python 3.11 --with mysql-connector-python python `
  tools/database/purge-fake-device-data.py `
  --host 127.0.0.1 --port 13306 --database ecobin `
  --user root --keep-hardware-sn test-divice-1
```

默认模式只开启串行化事务、计算删除闭包并打印各表行数，最后回滚，不修改数据。必须人工
核对保留设备、假设备清单、每张关联表行数和总行数。若当前已经只有
`test-divice-1`，报告会显示 0 个假设备，无需执行删除。

## 2. 执行前条件

只有同时满足以下条件才执行：

1. 停止后端写入和 OneNet 可靠任务 worker，避免预演后数据集合变化；
2. 已取得本次数据库的可读备份证据文件，并验证备份不是空文件；
3. 预演结果经过人工确认，总行数不超过预期；
4. 使用拥有相关表 `SELECT` / `DELETE` 权限的受控账号；H-02 的 schema owner 默认锁定，
   本地容器演练可显式使用 root，正式环境按既有数据库权限流程临时授权。

## 3. 受控执行

```powershell
uv run --python 3.11 --with mysql-connector-python python `
  tools/database/purge-fake-device-data.py `
  --host 127.0.0.1 --port 13306 --database ecobin `
  --user root --keep-hardware-sn test-divice-1 `
  --maximum-rows 100000 --execute `
  --backup-evidence C:\path\outside-repository\backup-evidence.txt `
  --confirmation DELETE-ALL-EXCEPT-test-divice-1
```

工具在一个事务内执行删除，提交前逐条检查所有外键没有孤儿，并确认资产表最终恰好只剩
`test-divice-1`。超过 `--maximum-rows`、缺少备份证据、确认短语不精确、MySQL 版本不是
8.4、保留设备不存在或不唯一时都会拒绝执行。若删除集合包含不可删除的清运记录变更审计
`rec_clean_record_change`，工具会要求改用受控重建/恢复方案，不会绕过不可变审计规则。

执行完成后清除当前进程的密码环境变量，再启动后端并确认：设备资产只剩一台、待处理可靠
任务不再引用假设备、`test-divice-1` 的部署和业务事实仍可查询。

```powershell
Remove-Item Env:ECOBIN_PURGE_MYSQL_PASSWORD
```
