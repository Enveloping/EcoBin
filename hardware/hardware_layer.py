# -*- coding: utf-8 -*-
"""
hardware_layer.py — 香橙派 Zero3 硬件抽象层
============================================
负责与前端垃圾桶 MCU 的串口通信、USB 摄像头拍照、COS 图片上传。

物理连接（推测，按实际接线调整）：
  - UART: 香橙派 GPIO (ttyS3 或 ttyS5) ←→ 垃圾桶 MCU 串口
  - 摄像头1 (箱外): /dev/video0 — 拍摄箱体外部
  - 摄像头2 (箱内): /dev/video1 — 拍摄箱体内部

MCU 通信协议（垃圾桶 → 香橙派，逗号分隔）：
  A1,{flag},A0    溢满标志位     (flag: 0=正常, 1=满溢)
  B1,{weight},B0  重量（克）     (weight: int, 单位g)
  C1,{flag},C0    烟雾报警标志   (flag: 0=正常, 1=报警)

香橙派 → 垃圾桶 MCU（门控指令）：
  D1,{index},{cmd},D0    cmd: open / close / status  （D0=动作完成/超时）

依赖（香橙派 ARMbian 上安装）：
  sudo apt install python3-serial fswebcam
  pip3 install pyserial cos-python-sdk-v5 opencv-python
"""

import logging
import os
import re
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


from config import SERIAL_PORT, SERIAL_BAUDRATE, CAMERA_OUTSIDE, CAMERA_INSIDE

# ================================================================
#  配置区 — 值从 config.py 导入，环境变量覆盖
# ================================================================
SERIAL_TIMEOUT = 0.5

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
    def update_overflow(cls, door_index: int, flag: int):
        with cls.lock:
            if 0 <= door_index < 6:
                cls.doors[door_index]["fullness"] = 100 if flag else 0
                cls.doors[door_index]["spill_alarm"] = bool(flag)
                logger.info("舱门%d 溢满标志=%d", door_index, flag)

    @classmethod
    def update_weight(cls, door_index: int, weight_grams: int):
        with cls.lock:
            if 0 <= door_index < 6:
                cls.doors[door_index]["weight"] = weight_grams / 1000.0
                logger.info("舱门%d 重量=%.2fkg", door_index, weight_grams / 1000.0)

    @classmethod
    def update_smoke(cls, door_index: int, flag: int):
        with cls.lock:
            if 0 <= door_index < 6:
                cls.doors[door_index]["smoke_alarm"] = bool(flag)
                logger.info("舱门%d 烟雾报警=%d", door_index, flag)

    @classmethod
    def get_state(cls) -> list:
        """获取全部舱门状态快照（深拷贝，避免锁外修改）"""
        with cls.lock:
            return [{**d} for d in cls.doors]

    @classmethod
    def get_door_state(cls, door_index: int) -> dict:
        with cls.lock:
            if 0 <= door_index < 6:
                return {**cls.doors[door_index]}
            return {}

    # ── 清运订单追踪 ──

    @classmethod
    def set_clean_order_id(cls, door_index: int, clean_order_id: int):
        """记录某门的活跃清运订单 ID。"""
        with cls.lock:
            if 0 <= door_index < 6:
                cls._clean_order_ids[door_index] = clean_order_id

    @classmethod
    def get_clean_order_id(cls, door_index: int):
        """获取某门的活跃清运订单 ID（None = 无）。"""
        with cls.lock:
            if 0 <= door_index < 6:
                return cls._clean_order_ids[door_index]
            return None

    @classmethod
    def clear_clean_order_id(cls, door_index: int):
        """清除某门的清运订单记录。"""
        with cls.lock:
            if 0 <= door_index < 6:
                cls._clean_order_ids[door_index] = None


# ================================================================
#  串口通信 — 解析 MCU 协议、发送门控指令
# ================================================================
class SerialBridge:
    """香橙派 ←→ MCU 串口桥接器"""

    def __init__(self):
        self.serial_port: Optional[serial.Serial] = None
        self._recv_buffer = b""   # 粘包/拆包拼接缓冲
        self._running = False
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
        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()
            logger.info("串口已关闭")

    def send_cmd(self, door_index: int, cmd: str) -> bool:
        """
        向 MCU 发送门控指令: D1,{index},{cmd},D0
        cmd 可选: open / close / status
        """
        frame = f"D1,{door_index},{cmd},D0\r\n"
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(frame.encode("utf-8"))
                logger.info("串口发送: %s", frame.strip())
                return True
            except Exception as e:
                logger.error("串口发送失败: %s", e)
        return False

    # --- MCU 协议解析正则 ---
    # A1,{0|1},A0 — 溢满报警
    RE_OVERFLOW = re.compile(r"A1,(\d+),A0")
    # B1,{weight_g},B0 — 重量（克）
    RE_WEIGHT = re.compile(r"B1,(\d+),B0")
    # C1,{0|1},C0 — 烟雾报警
    RE_SMOKE = re.compile(r"C1,(\d+),C0")

    def _parse_line(self, line: str):
        """解析一行协议文本，更新 BinState 并触发回调"""
        line = line.strip()

        # 溢满标志 A1,{flag},A0
        m = self.RE_OVERFLOW.search(line)
        if m:
            flag = int(m.group(1))
            BinState.update_overflow(0, flag)  # TODO: 多舱门需MCU协议增加舱门编号
            if self.on_spill_alarm and flag:
                self.on_spill_alarm(0)
            return

        # 重量 B1,{weight_g},B0
        m = self.RE_WEIGHT.search(line)
        if m:
            weight_g = int(m.group(1))
            BinState.update_weight(0, weight_g)
            if self.on_weight_received:
                self.on_weight_received(0, weight_g)
            return

        # 烟雾报警 C1,{flag},C0
        m = self.RE_SMOKE.search(line)
        if m:
            flag = int(m.group(1))
            BinState.update_smoke(0, flag)
            if self.on_smoke_alarm and flag:
                self.on_smoke_alarm(0)
            return

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
                    self._recv_buffer += data

                    # 按 \n 拆帧，处理完整行，残余保留到 buffer
                    while b"\n" in self._recv_buffer:
                        line_bytes, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
                        try:
                            line = line_bytes.decode("utf-8", errors="replace")
                            self._parse_line(line)
                        except Exception:
                            pass
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
    def capture(device_id: int, save_path: str) -> bool:
        """
        使用 OpenCV 拍照
        :param device_id: 摄像头设备ID (0=外部, 1=内部)
        :param save_path: 保存图片路径
        """
        if cv2 is None:
            # 回退：使用 fswebcam 系统命令
            return DualCamera._capture_fallback(device_id, save_path)

        cap = cv2.VideoCapture(device_id)
        if not cap.isOpened():
            logger.error("无法打开摄像头 /dev/video%d", device_id)
            return False

        # 预热摄像头（丢弃前几帧）
        for _ in range(5):
            cap.read()
            time.sleep(0.05)

        ret, frame = cap.read()
        cap.release()

        if ret and frame is not None:
            os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
            cv2.imwrite(save_path, frame)
            logger.info("摄像头%d 拍照成功 → %s", device_id, save_path)
            return True
        else:
            logger.error("摄像头%d 拍照失败", device_id)
            return False

    @staticmethod
    def _capture_fallback(device_id: int, save_path: str) -> bool:
        """fswebcam 回退方案（OpenCV 不可用时使用）"""
        import subprocess
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        try:
            subprocess.run(
                ["fswebcam", "-d", f"/dev/video{device_id}",
                 "-r", "1280x720", "--no-banner", save_path],
                timeout=5, check=True, capture_output=True,
            )
            logger.info("fswebcam 拍照%d → %s", device_id, save_path)
            return os.path.exists(save_path)
        except Exception as e:
            logger.error("fswebcam 拍照%d失败: %s", device_id, e)
            return False

    @classmethod
    def capture_both(cls, prefix: str) -> tuple:
        """
        同时拍摄箱外 (video0) 和箱内 (video1)
        :param prefix: 文件前缀，生成 {prefix}_outside.jpg 和 {prefix}_inside.jpg
        :return: (outside_path, inside_path)，失败为 None
        """
        outside = f"{prefix}_outside.jpg"
        inside = f"{prefix}_inside.jpg"
        ok1 = cls.capture(CAMERA_OUTSIDE, outside)
        ok2 = cls.capture(CAMERA_INSIDE, inside)
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
    bridge._parse_line("A1,1,A0")
    bridge._parse_line("B1,1500,B0")
    bridge._parse_line("C1,0,C0")
    print("舱门0状态:", BinState.get_door_state(0))

    # 测试拍照（需要实际摄像头）
    print("=== 测试拍照 ===")
    camera = DualCamera()
    prefix = os.path.join(PHOTO_DIR, "test_capture")
    out, inn = camera.capture_both(prefix)
    print(f"箱外: {out}, 箱内: {inn}")
