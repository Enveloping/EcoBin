# 双摄拍照与 COS 真机验收（2026-07-28）

## 验收范围

本次验收只覆盖香橙派照片链路：

1. `PhotoManager` 按内外槽位驱动两只真实 USB 摄像头；
2. 照片事实和文件先写入边缘 SQLite/持久目录；
3. 后端长期凭证在操作员开发机签发短期 STS，香橙派只在进程内接收短期凭证；
4. 香橙派使用正式 `CosPhotoUploader` 上传；
5. 不带签名或凭证的 HTTPS URL 能下载原图，且字节数和 SHA-256 与本地文件一致。

本次不触发 MCU、门控、OneNet 业务命令或订单。

## 发现并修复的缺陷

原配置使用 `ECOBIN_CAMERA_OUTSIDE=1`、`ECOBIN_CAMERA_INSIDE=3`。真实设备拓扑为：

| 物理摄像头 | 业务位置 | 稳定设备路径 | 本次枚举节点 |
|---|---|---|---|
| DECXIN | 外部 | `/dev/v4l/by-id/usb-DECXIN_CAMERA_DECXIN_CAMERA_01.00.00-video-index0` | `/dev/video3` |
| icspring | 内部 | `/dev/v4l/by-id/usb-icSpring_icspring_camera-video-index0` | `/dev/video0` |

`/dev/video1` 是 icspring 的 metadata 节点，不是采集节点。OpenCV 用整数索引 `1`
打开失败后会尝试其他后端，最终与索引 `3` 拍到同一只 DECXIN；因此旧配置虽然两次
调用都返回图片，实际丢失了内部摄像头。

修复后：

- 生产配置只接受 `/dev/v4l/by-id/` 稳定路径，拒绝依赖 USB 枚举顺序的数字索引；
- 外部固定为 DECXIN，内部固定为 icspring；
- `PhotoManager` 对字符串设备源显式使用 V4L2 后端，不允许 OpenCV 静默换设备；
- 内外设备解析到同一路径时启动失败；
- 摄像头打开后读取 5 帧，以最后一张有效帧避开首帧曝光或分辨率不稳定；
- COS SDK 增加默认 15 秒网络超时，网络异常后由原照片队列进入可重试状态，而不是
  永久阻塞在一次 SDK 调用中。

## 自动回归

在开发机使用仓库目标 Python 3.11 执行：

```powershell
uv run --python 3.11 --with pytest pytest -q
```

结果为 `166 passed, 5 subtests passed`。新增回归覆盖：

- 内外槽位使用不同的稳定设备路径；
- 字符串路径以 `CAP_V4L2` 打开；
- 配置的预热帧数确实被读取，最终保存最后一张有效帧；
- COS SDK 收到有限的网络超时参数。

契约套件结果为 `43 passed, 752 subtests passed`；66 个生成文件检查和契约校验全部
通过。本次没有改变 OneNet、后端或 MCU 协议。

## 香橙派真实结果

香橙派当前系统 Python 为 3.12.3；同一份代码已先在目标 Python 3.11 完成自动回归。
临时验收副本没有覆盖现有服务或目录。

使用正式 `PhotoManager.capture_open_photos()` 一次产生两个槽位：

| 槽位 | 摄像头 | 本地字节数 | 上传状态 | 匿名 GET | 下载校验 |
|---|---|---:|---|---|---|
| `BEFORE_OUTER` | DECXIN | 130480 | `UPLOADED` | `200 image/jpeg` | 130480 字节，SHA-256 一致 |
| `BEFORE_INNER` | icspring | 79100 | `UPLOADED` | `200 image/jpeg` | 79100 字节，SHA-256 一致 |

两张图的视野明显不同：外部为笔记本画面，内部为设备机箱/工作台画面。测试对象随后
使用操作员侧管理凭证幂等删除；短期 STS 未写入 SQLite、照片文件、仓库或普通日志。
香橙派和开发机临时文件均已删除。

## 非阻塞网络观察

首次由香橙派上传时，COS 连接停在 `SYN-SENT`。原因不是 COS 凭证或上传代码，而是
设备存在两条默认路由：

- `enx2089846a96ab` 经 `192.168.10.1`，metric 100，但该路径没有公网；
- `wlan0` 经 `10.30.138.187`，metric 600，可以访问 COS。

同时 systemd-resolved 仍优先错误链路，产生 DNS 超时。验收期间只做了可逆的运行时
调整：临时让 COS/DNS 走 Wi-Fi，并临时解析当前 COS 地址；验收结束已恢复原
`/etc/hosts`、默认路由和所有主机路由。仓库代码没有硬编码 COS IP。

项目负责人随后明确本轮不继续处理该网络问题，接受“香橙派真实拍照 + 开发机运行同一
`CosPhotoUploader` 的真实上传/下载”作为当前验收口径。因此结论分两层：

- 双摄寻址、拍照、SQLite 照片事实、STS 上传和 URL 下载的软件链路已经验收通过；
- 香橙派当时的默认路由/DNS 波动不在本轮继续诊断，也不阻塞 F-11。以后若要求设备
  无人值守持续上传，再单独复现并处理网络环境；15 秒 SDK 超时只负责让失败有界返回。
