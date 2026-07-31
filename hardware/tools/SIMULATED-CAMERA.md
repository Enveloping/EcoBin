# 显式双摄像头模拟使用说明

## 用途

无真实 USB 摄像头时，可把两路摄像头配置为显式的 `simulated://` 源：

```dotenv
ECOBIN_CAMERA_OUTSIDE=simulated://outside
ECOBIN_CAMERA_INSIDE=simulated://inside
```

这不是全局测试模式。`main.py`、`PhotoManager`、SQLite 照片状态、文件持久化、SHA-256、
COS 上传队列和 OneNet 照片事件仍使用正式代码；只有实际取得图像的最后一步由显式源
生成占位 JPEG。

## 行为

- 每次调用生成一个可解码的 JPEG；
- JPEG 内包含摄像头名称、UTC 拍摄时间和唯一 `captureUid`；
- 内外摄像头及连续两次拍照具有不同的文件哈希；
- 不依赖 OpenCV、Pillow、V4L2、摄像头驱动或 root 权限；
- 摄像头名称只允许字母、数字、点、下划线和连字符，最长 64 字符；
- 内外源必须不同。

此功能用于验证无实物链路，不能证明 USB 枚举、V4L2、曝光、分辨率、视野和真实成像
质量。真机部署仍应使用两个稳定且不同的 `/dev/v4l/by-id/...` 路径。

## 与虚拟 MCU 一起运行

先启动定长帧 PTY MCU：

```bash
cd /path/to/EcoBin/hardware

uv run --python 3.11 python tools/fixed_frame_pty_simulator.py \
  --link /tmp/ecobin-fixed-frame-mcu
```

然后在 `.env` 中配置：

```dotenv
ECOBIN_MCU_PROTOCOL=fixed-frame
ECOBIN_SERIAL_PORT=/tmp/ecobin-fixed-frame-mcu
ECOBIN_UART_PORT_COUNT=1
ECOBIN_CAMERA_OUTSIDE=simulated://outside
ECOBIN_CAMERA_INSIDE=simulated://inside
```

再运行真实入口：

```bash
uv run --python 3.11 python main.py
```

OneNet 下发投递或清运服务后，PTY 模拟器返回 DD/EF；`PhotoManager` 会在工作照片目录
生成四个模拟 JPEG。未取得上传授权或上传尚未完成时，完成事件中的相应 URL 继续为空，
后续仍由 `photoStatusReported` 报告上传结果。

## 自动化验证

在 Linux 上一次验证 PTY MCU、真实 PySerial 适配器、SQLite、`PhotoManager` 和四张模拟
照片，不需要 OneNet/COS 凭证：

```bash
uv run --python 3.11 python tools/hardware_free_smoke.py
```

成功时输出单行 JSON，`ok=true`、`camera.count=4`、
`camera.uniqueSha256Count=4`，并包含 DD/EF 的实际解析结果。

只验证模拟摄像头：

```bash
uv run --python 3.11 --with pytest \
  pytest -q tests/test_simulated_camera.py tests/test_photo_manager.py
```

运行完整硬件侧回归：

```bash
uv run --python 3.11 --with pytest pytest -q
```
