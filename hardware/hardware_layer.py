# -*- coding: utf-8 -*-
"""
hardware_layer.py — 香橙派 Zero3 硬件抽象层
============================================
负责与前端垃圾桶 MCU 的串口通信、USB 摄像头拍照、COS 图片上传。

物理连接（推测，按实际接线调整）：
  - UART: 香橙派 26Pin UART5 (/dev/ttyS5) ←→ 垃圾桶 MCU 串口
  - 摄像头1 (箱外): DECXIN 的 /dev/v4l/by-id 稳定路径
  - 摄像头2 (箱内): icspring 的 /dev/v4l/by-id 稳定路径

新投递协议使用固定长度二进制帧（当前仅物理投口 1）：
  MCU → 香橙派: AA,{0|1},AA 门状态；BB,{0|1},BB 红外；
                  CC,{0|1},CC 烟雾；DD,{uint24-be},DD 最终重量（克）
  香橙派 → MCU: AA,00,AA 开盖；AA,01,AA 关盖；BB,{0..9},BB 单价

旧清运代码暂时仍通过 send_cmd() 发送 D1 文本帧，尚未适配新固件。

依赖（香橙派 ARMbian 上安装）：
  sudo apt install python3-serial fswebcam
  pip3 install pyserial cos-python-sdk-v5 opencv-python
"""

import logging
import os
import threading
import time
import uuid
import base64
from typing import Optional, Callable

# --- 串口通信 ---
try:
    import serial
except ImportError:
    serial = None
    print("[WARN] pyserial 未安装，串口通信将不可用。安装: pip3 install pyserial")

# --- 摄像头拍照 ---
try:
    import cv2
except ImportError:
    cv2 = None
    print("[WARN] opencv-python 未安装，摄像头不可用。安装: pip3 install opencv-python")

# --- COS 上传 ---
try:
    from qcloud_cos import CosConfig, CosS3Client
    from qcloud_cos.cos_exception import CosClientError, CosServiceError
except ImportError:
    CosConfig = CosS3Client = None
    CosClientError = CosServiceError = Exception
    print("[WARN] cos-python-sdk-v5 未安装，COS上传不可用。安装: pip3 install cos-python-sdk-v5")


logger = logging.getLogger("hardware")


from config import (
    SERIAL_PORT,
    SERIAL_BAUDRATE,
    CAMERA_OUTSIDE_SOURCE,
    CAMERA_INSIDE_SOURCE,
)

# ================================================================
#  配置区 — 值从 config.py 导入，环境变量覆盖
# ================================================================
SERIAL_TIMEOUT = 0.5
CAMERA_WARMUP_FRAMES = 5  # Legacy DualCamera behavior; not a formal runtime path.

# 拍照保存目录
PHOTO_DIR = "/tmp/smartbin_photos"


# ================================================================
#  数据模型 — MCU 上报的传感器状态（线程安全）
# ================================================================
class BinState:
    """垃圾桶状态快照，存储 MCU 最新上报的全部数据"""
    lock = threading.Lock()

    # 6 个舱门的状态
    # 每个舱门: {weight, fullness, spill_alarm, smoke_alarm}
    doors = [dict(weight=0.0, fullness=0, spill_alarm=False, smoke_alarm=False)
             for _ in range(6)]

    # 清运：每门当前正在进行的 clean_order_id（None = 无活跃清运）
    _clean_order_ids = [None] * 6

    @classmethod
    def _offset(cls, door_index: int):
        """物模型投口号为 1..6，转换为内部列表下标。"""
        if isinstance(door_index, int) and 1 <= door_index <= len(cls.doors):
            return door_index - 1
        return None

    @classmethod
    def update_overflow(cls, door_index: int, flag: int):
        with cls.lock:
            offset = cls._offset(door_index)
            if offset is not None:
                cls.doors[offset]["fullness"] = 100 if flag else 0
                cls.doors[offset]["spill_alarm"] = bool(flag)
                logger.info("舱门%d 溢满标志=%d", door_index, flag)

    @classmethod
    def update_weight(cls, door_index: int, weight_grams: int):
        with cls.lock:
            offset = cls._offset(door_index)
            if offset is not None:
                cls.doors[offset]["weight"] = weight_grams / 1000.0
                logger.info("舱门%d 重量=%.2fkg", door_index, weight_grams / 1000.0)

    @classmethod
    def update_smoke(cls, door_index: int, flag: int):
        with cls.lock:
            offset = cls._offset(door_index)
            if offset is not None:
                cls.doors[offset]["smoke_alarm"] = bool(flag)
                logger.info("舱门%d 烟雾报警=%d", door_index, flag)

    @classmethod
    def get_state(cls) -> list:
        """获取全部舱门状态快照（深拷贝，避免锁外修改）"""
        with cls.lock:
            return [{**d} for d in cls.doors]

    @classmethod
    def get_door_state(cls, door_index: int) -> dict:
        with cls.lock:
            offset = cls._offset(door_index)
            if offset is not None:
                return {**cls.doors[offset]}
            return {}

    # ── 清运订单追踪 ──

    @classmethod
    def set_clean_order_id(cls, door_index: int, clean_order_id: int):
        """记录某门的活跃清运订单 ID。"""
        with cls.lock:
            offset = cls._offset(door_index)
            if offset is not None:
                cls._clean_order_ids[offset] = clean_order_id

    @classmethod
    def get_clean_order_id(cls, door_index: int):
        """获取某门的活跃清运订单 ID（None = 无）。"""
        with cls.lock:
            offset = cls._offset(door_index)
            if offset is not None:
                return cls._clean_order_ids[offset]
            return None

    @classmethod
    def clear_clean_order_id(cls, door_index: int):
        """清除某门的清运订单记录。"""
        with cls.lock:
            offset = cls._offset(door_index)
            if offset is not None:
                cls._clean_order_ids[offset] = None


# ================================================================
#  串口通信 — 解析 MCU 协议、发送门控指令
# ================================================================
class SerialBridge:
    """香橙派 ←→ MCU 串口桥接器。"""

    SINGLE_DOOR_INDEX = 1
    MAX_RECV_BUFFER = 4096
    FRAME_LENGTHS = {0xAA: 3, 0xBB: 3, 0xCC: 3, 0xDD: 5}

    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self._recv_buffer = b""   # 粘包/拆包拼接缓冲
        self._running = False
        self._write_lock = threading.Lock()
        self._event_condition = threading.Condition()
        self._door_is_open: Optional[bool] = None
        self._door_state_version = 0
        self._latest_weight_grams: Optional[int] = None
        self._weight_version = 0
        # 回调：收到重量后触发（参数: door_index, weight_grams）
        self.on_weight_received: Optional[Callable] = None
        # 回调：收到溢满报警后触发
        self.on_spill_alarm: Optional[Callable] = None
        # 回调：收到烟雾报警后触发
        self.on_smoke_alarm: Optional[Callable] = None

    def open(self, port: str = SERIAL_PORT) -> bool:
        if serial is None:
            logger.error("pyserial 未安装，无法打开串口")
            return False
        try:
            self.serial_port = serial.Serial(
                port=port,
                baudrate=SERIAL_BAUDRATE,
                timeout=SERIAL_TIMEOUT,
            )
            logger.info("串口 %s 已打开 (baud=%d)", port, SERIAL_BAUDRATE)
            return True
        except Exception as e:
            logger.error("打开串口 %s 失败: %s", port, e)
            return False

    def close(self):
        self._running = False
        with self._event_condition:
            self._event_condition.notify_all()
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            logger.info("串口已关闭")

    def send_cmd(self, door_index: int, cmd: str) -> bool:
        """
        旧清运流程专用的文本门控指令: D1,{index},{cmd},D0。

        新投递流程必须使用 send_door_control()；此方法不代表新固件兼容。
        cmd 可选: open / close / status
        """
        frame = f"D1,{door_index},{cmd},D0\r\n"
        return self._write(frame.encode("utf-8"), frame.strip())

    def send_door_control(self, door_index: int, open_door: bool) -> bool:
        """按新二进制协议控制唯一投口。00=开盖，01=关盖。"""
        if door_index != self.SINGLE_DOOR_INDEX:
            logger.error("新 UART 协议仅支持投口%d，收到 doorIndex=%s", self.SINGLE_DOOR_INDEX, door_index)
            return False
        data = 0x00 if open_door else 0x01
        frame = bytes((0xAA, data, 0xAA))
        return self._write(frame, frame.hex(" ").upper())

    def send_price_digit(self, digit: int) -> bool:
        """向 MCU 同步一位单价数据（0..9）。"""
        if isinstance(digit, bool) or not isinstance(digit, int) or not 0 <= digit <= 9:
            raise ValueError("单价协议数据必须是 0..9 的整数")
        frame = bytes((0xBB, digit, 0xBB))
        return self._write(frame, frame.hex(" ").upper())

    def _write(self, frame: bytes, display: str) -> bool:
        if self.serial_port and self.serial_port.is_open:
            try:
                with self._write_lock:
                    self.serial_port.write(frame)
                logger.info("串口发送: %s", display)
                return True
            except Exception as e:
                logger.error("串口发送失败: %s", e)
        return False

    def event_versions(self) -> tuple[int, int]:
        """返回门状态和重量事件版本，用来排除旧帧。"""
        with self._event_condition:
            return self._door_state_version, self._weight_version

    def wait_for_door_state(self, expected_open: bool, after_version: int, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        with self._event_condition:
            while True:
                if self._door_state_version > after_version and self._door_is_open is expected_open:
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._event_condition.wait(remaining)

    def wait_for_weight(self, after_version: int, timeout_s: float) -> Optional[int]:
        deadline = time.monotonic() + timeout_s
        with self._event_condition:
            while True:
                if self._weight_version > after_version:
                    return self._latest_weight_grams
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._event_condition.wait(remaining)

    def _feed_received_data(self, data: bytes) -> None:
        """追加串口字节并按固定长度帧解析。供接收线程和单元测试共用。"""
        if not data:
            return
        self._recv_buffer += data
        if len(self._recv_buffer) > self.MAX_RECV_BUFFER:
            logger.warning("串口接收缓冲区超过 %d 字节，丢弃旧数据", self.MAX_RECV_BUFFER)
            self._recv_buffer = self._recv_buffer[-self.MAX_RECV_BUFFER :]

        while self._recv_buffer:
            marker = self._recv_buffer[0]
            frame_length = self.FRAME_LENGTHS.get(marker)
            if frame_length is None:
                self._recv_buffer = self._recv_buffer[1:]
                continue
            if len(self._recv_buffer) < frame_length:
                return
            frame = self._recv_buffer[:frame_length]
            if frame[-1] != marker:
                logger.warning("无效串口帧头，重新同步: %s", frame.hex(" ").upper())
                self._recv_buffer = self._recv_buffer[1:]
                continue
            self._recv_buffer = self._recv_buffer[frame_length:]
            try:
                self._handle_frame(frame)
            except Exception as exc:
                logger.warning("串口帧处理失败: %s frame=%s", exc, frame.hex(" ").upper())

    def _handle_frame(self, frame: bytes) -> None:
        marker = frame[0]
        door_index = self.SINGLE_DOOR_INDEX
        if marker == 0xAA:
            flag = frame[1]
            if flag not in (0, 1):
                raise ValueError(f"非法门状态: {flag}")
            is_open = flag == 1
            with self._event_condition:
                self._door_is_open = is_open
                self._door_state_version += 1
                self._event_condition.notify_all()
            logger.info("舱门%d 状态=%s", door_index, "开盖" if is_open else "关盖")
            return

        if marker == 0xBB:
            flag = frame[1]
            if flag not in (0, 1):
                raise ValueError(f"非法红外状态: {flag}")
            BinState.update_overflow(door_index, flag)
            if self.on_spill_alarm and flag:
                self.on_spill_alarm(door_index)
            return

        if marker == 0xCC:
            flag = frame[1]
            if flag not in (0, 1):
                raise ValueError(f"非法烟雾状态: {flag}")
            BinState.update_smoke(door_index, flag)
            if self.on_smoke_alarm and flag:
                self.on_smoke_alarm(door_index)
            return

        weight_grams = int.from_bytes(frame[1:4], byteorder="big", signed=False)
        BinState.update_weight(door_index, weight_grams)
        with self._event_condition:
            self._latest_weight_grams = weight_grams
            self._weight_version += 1
            self._event_condition.notify_all()
        if self.on_weight_received:
            self.on_weight_received(door_index, weight_grams)

    def recv_loop(self):
        """串口接收线程 — 持续读取并解析 MCU 数据"""
        self._running = True
        if not self.serial_port or not self.serial_port.is_open:
            logger.error("串口未打开，接收线程退出")
            return

        logger.info("串口接收线程已启动")
        while self._running:
            try:
                if self.serial_port.in_waiting > 0:
                    data = self.serial_port.read(self.serial_port.in_waiting)
                    self._feed_received_data(data)
                else:
                    time.sleep(0.01)
            except Exception as e:
                logger.error("串口读取异常: %s", e)
                time.sleep(0.5)

        logger.info("串口接收线程已退出")


# ================================================================
#  双摄像头拍照
# ================================================================
class DualCamera:
    """双摄像头控制器（USB 摄像头 ×2）"""

    @staticmethod
    def capture(camera_source: str | int, save_path: str) -> bool:
        """
        使用 OpenCV 拍照
        :param camera_source: 摄像头稳定设备路径或兼容数字索引
        :param save_path: 保存图片路径
        """
        if cv2 is None:
            # 回退：使用 fswebcam 系统命令
            return DualCamera._capture_fallback(
                camera_source,
                save_path,
            )

        if isinstance(camera_source, str):
            cap = cv2.VideoCapture(camera_source, cv2.CAP_V4L2)
        else:
            cap = cv2.VideoCapture(camera_source)
        if not cap.isOpened():
            cap.release()
            logger.error("无法打开摄像头 %s", camera_source)
            return False

        frame = None
        try:
            for _ in range(CAMERA_WARMUP_FRAMES):
                ret, candidate = cap.read()
                if not ret or candidate is None:
                    break
                frame = candidate
                time.sleep(0.05)
        finally:
            cap.release()

        if frame is not None:
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            cv2.imwrite(save_path, frame)
            logger.info(
                "摄像头%s拍照成功 → %s",
                camera_source,
                save_path,
            )
            return True
        else:
            logger.error("摄像头%s拍照失败", camera_source)
            return False

    @staticmethod
    def _capture_fallback(
        camera_source: str | int,
        save_path: str,
    ) -> bool:
        """fswebcam 回退方案（OpenCV 不可用时使用）"""
        import subprocess
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        device_path = (
            camera_source
            if isinstance(camera_source, str)
            else f"/dev/video{camera_source}"
        )
        try:
            subprocess.run(
                ["fswebcam", "-d", device_path,
                 "-r", "1280x720", "--no-banner", save_path],
                timeout=5, check=True, capture_output=True,
            )
            logger.info(
                "fswebcam 拍照%s → %s",
                camera_source,
                save_path,
            )
            return os.path.exists(save_path)
        except Exception as e:
            logger.error(
                "fswebcam 拍照%s失败: %s",
                camera_source,
                e,
            )
            return False

    @classmethod
    def capture_both(cls, prefix: str) -> tuple:
        """
        同时按稳定设备路径拍摄箱外 DECXIN 和箱内 icspring
        :param prefix: 文件前缀，生成 {prefix}_outside.jpg 和 {prefix}_inside.jpg
        :return: (outside_path, inside_path)，失败为 None
        """
        outside = f"{prefix}_outside.jpg"
        inside = f"{prefix}_inside.jpg"
        ok1 = cls.capture(CAMERA_OUTSIDE_SOURCE, outside)
        ok2 = cls.capture(CAMERA_INSIDE_SOURCE, inside)
        return (outside if ok1 else None,
                inside if ok2 else None)


# ================================================================
#  COS 图片上传
# ================================================================
class CosUploader:
    """腾讯云 COS 上传器（使用 cosToken 临时密钥直传）"""

    @staticmethod
    def creds_from_cos_token(cos_token: dict) -> dict:
        """从平台下发的 cosToken 提取凭证（sessionToken 重新拼接）"""
        session = (cos_token.get("sessionToken1") or "") + (cos_token.get("sessionToken2") or "")
        return {
            "secret_id": cos_token.get("tmpSecretId", ""),
            "secret_key": cos_token.get("tmpSecretKey", ""),
            "token": session,
            "bucket": cos_token.get("bucket", ""),
            "region": cos_token.get("region", ""),
            "base_url": cos_token.get("baseUrl", ""),
        }

    @staticmethod
    def upload(creds: dict, local_path: str, object_key: str) -> str:
        """
        上传文件到 COS，返回可访问 URL。
        完全对齐 bin/cos_upload.py 的 upload() 实现：
          - 用 put_object + Body=fp 流式上传
          - ContentType="image/jpeg"
          - EnableMD5=False（避免大文件MD5开销）
          - URL = base_url + object_key（base_url 来自 cosToken，后端签发时指定）
          - Scheme="https"

        :param creds: CosUploader.creds_from_cos_token() 返回的凭证字典
        :param local_path: 本地文件路径
        :param object_key: COS 对象键（如 Smartbin_1/delivery/xxx/open_outside.jpg）
        """
        if not os.path.exists(local_path):
            logger.error("文件不存在: %s", local_path)
            return ""

        if CosConfig is None:
            logger.error("cos-python-sdk-v5 未安装，COS 上传不可用")
            return ""

        config = CosConfig(
            Region=creds["region"],
            SecretId=creds["secret_id"],
            SecretKey=creds["secret_key"],
            Token=creds["token"],       # 临时密钥必须带 sessionToken
            Scheme="https",
        )
        client = CosS3Client(config)

        logger.info("上传: %s -> cos://%s/%s", local_path, creds["bucket"], object_key)
        try:
            with open(local_path, "rb") as fp:
                resp = client.put_object(
                    Bucket=creds["bucket"],
                    Body=fp,
                    Key=object_key,
                    ContentType="image/jpeg",
                    EnableMD5=False,
                )
        except CosServiceError as e:
            logger.error("COS服务端错误: %s - %s", e.get_error_code(), e.get_error_msg())
            return ""
        except CosClientError as e:
            logger.error("COS客户端错误: %s", e)
            return ""

        url = f"{creds['base_url']}/{object_key}"
        logger.info("上传成功! ETag=%s", resp.get("ETag", "N/A"))
        logger.info("访问 URL: %s", url)
        return url


# ================================================================
#  快速测试
# ================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(name)s] %(message)s")

    # 测试串口协议解析
    print("=== 测试协议解析 ===")
    bridge = SerialBridge()
    bridge._feed_received_data(bytes.fromhex("AA 01 AA BB 01 BB CC 00 CC DD 00 05 DC DD"))
    print("舱门1状态:", BinState.get_door_state(1))

    # 测试拍照（需要实际摄像头）
    print("=== 测试拍照 ===")
    camera = DualCamera()
    prefix = os.path.join(PHOTO_DIR, "test_capture")
    out, inn = camera.capture_both(prefix)
    print(f"箱外: {out}, 箱内: {inn}")
