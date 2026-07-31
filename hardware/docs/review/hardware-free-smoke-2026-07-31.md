# 无 MCU / 无摄像头香橙派 smoke（2026-07-31）

## 目标

验证不切换全局运行模式时，香橙派可以用 Linux PTY 代替固定帧 MCU，并用两个显式
`simulated://` 源代替内外摄像头。测试不得读取、复制或输出正式设备密钥及 COS
凭证。

## 现场环境

- SSH：`orangepi@172.20.10.12`，已配置免密登录；
- 系统：Orange Pi Zero 3，Linux `6.1.31-sun50iw9`，AArch64；
- Python：`3.11.2`；
- `/dev/ttyS5` 存在，但未连接 MCU；
- 未发现 `/dev/v4l/by-id/`，未连接 USB 摄像头；
- 正式 systemd 服务处于 inactive，工作目录为 `/root/EcoBin/hardware`；
- `orangepi` 用户不能读取 `/root/EcoBin`，且 `sudo -n` 要求密码。

因此本次未读取或修改正式工作目录、`.env`、凭证和 systemd 服务。无密钥测试副本部署
在：

```text
/home/orangepi/ecobin-hardware-free-20260731
```

其独立 venv 只安装了 `pyserial==3.5`。

## 联合硬件边界 smoke

执行：

```bash
.venv/bin/python tools/hardware_free_smoke.py
```

结果为 `ok=true`，并确认：

- PySerial 通过真实 `/dev/pts/*` 打开 PTY；
- 下发 `BB 04 BB + AA 01 AA`，收到并解析
  `DD 00 27 10 00 30 D4 00 DD`；
- 下发 `EE 01 EE`，收到并解析
  `EF 00 30 D4 00 03 20 00 EF`；
- `PhotoManager` 通过 `simulated://outside` 和 `simulated://inside` 完成投递前后四个
  槽位；
- SQLite 中四张照片均为 `PENDING`；
- 四个 JPEG 的 SHA-256 均不同。

## 正式入口组装 smoke

随后使用完整但不含 `.env` 的源码副本启动真实 `main.py`：

- MQTT 主机显式设置为 `127.0.0.1:9`，保证不会接触 OneNet；
- COS、设备和部署字段使用无权限的占位配置；
- MCU 串口指向本轮 PTY；
- 双摄分别指向 `simulated://outside`、`simulated://inside`；
- SQLite 和照片目录位于上述用户测试目录。

现场日志确认：

1. SQLite 从 v0 初始化到 v6；
2. `FixedFrameMcuAdapter` 成功打开 PTY；
3. 固定帧本地握手完成并建立 MCU 接收代次；
4. MQTT 在 8 秒后按预期超时，启动结果为 `DEGRADED`；
5. UART 读取线程和命令消费线程正常启动；
6. `SIGTERM` 后线程、MQTT、UART 和 SQLite 正常关闭。

退出码 `124` 来自外层 `timeout 15s`，用于终止持续运行的网关，不代表进程崩溃。
测试结束后未残留网关或 PTY 模拟器进程，临时 PTY 软链接已清理。

## 尚未覆盖

本轮没有正式 OneNet/COS 凭证，也无权读取 `/root/EcoBin`，所以没有在该设备上执行
真实 OneNet 服务下发、事件上报或 COS 上传。完成该层验证需要将本次代码部署到正式
工作目录并由有权限的运维身份启动；不能通过复制或输出 `/root` 中的秘密绕过权限。

真实 USB/V4L2 成像、电气 UART、MCU 屏幕/按钮、门、电磁阀和称重仍属于真机 HIL
边界。
