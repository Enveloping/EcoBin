# -*- coding: utf-8 -*-
"""
香橙派 (Orange Pi) 用「临时密钥」直传图片到腾讯云 COS。

临时密钥来源：
  - 正式流程：随开门命令的 cosToken 由 OneNet 下发给设备
    （见 docs/onenet-thing-model.md §3.4）
  - 联调测试：通过 EcoBin 后端 /api/iot/cos/temp-credentials

依赖（uv 已装）：cos-python-sdk-v5, requests
运行：
    uv run cos_upload.py <本地图片> [对象key]
    # 例：uv run cos_upload.py test.jpg delivery/test/1.jpg
"""

import sys
import logging

from qcloud_cos import CosConfig, CosS3Client
from qcloud_cos.cos_exception import CosClientError, CosServiceError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("cos_upload")


def upload(creds, local_path, object_key):
    """用临时密钥流式上传一张图片，返回可访问 URL。"""
    config = CosConfig(
        Region=creds["region"],
        SecretId=creds["secret_id"],
        SecretKey=creds["secret_key"],
        Token=creds["token"],     # ← 临时密钥必须带 sessionToken，否则鉴权失败，还有就是onenet传输的字段最多为512但这个sessionToken一般会超过，所以onenet传过来的时候会有sessionToken1和sessionToken2，使用的时候需要将它们拼起来
        Scheme="https",
    )
    client = CosS3Client(config)

    logger.info(f"上传: {local_path} -> cos://{creds['bucket']}/{object_key}")
    try:
        with open(local_path, "rb") as fp:
            # 不写死 StorageClass：MAZ（多可用区）桶指定单可用区 STANDARD 会被拒，交给桶默认。
            resp = client.put_object(
                Bucket=creds["bucket"],
                Body=fp,
                Key=object_key,
                ContentType="image/jpeg",
                EnableMD5=False,
            )
    except CosServiceError as e:
        logger.error(f"服务端错误: {e.get_error_code()} - {e.get_error_msg()}")
        raise
    except CosClientError as e:
        logger.error(f"客户端错误: {e}")
        raise

    url = f"{creds['base_url']}/{object_key}"
    logger.info(f"上传成功! ETag={resp.get('ETag', 'N/A')}")
    logger.info(f"访问 URL: {url}")
    return url


def main():
    if len(sys.argv) < 2:
        print("用法: uv run cos_upload.py <本地图片> [对象key]")
        print("例:  uv run cos_upload.py test.jpg delivery/test/1.jpg")
        sys.exit(1)

    local_path = sys.argv[1]
    # 对象 key：测试期可自定义；正式对接用后端下发的 {sn}/{doorIndex}/{token}/<slot>.jpg
    object_key = sys.argv[2] if len(sys.argv) >= 3 else "delivery/test/upload.jpg"

    print("本脚本为命令行测试工具，需要 COS 临时凭证。")
    print("通过 EcoBin 后端或 OneNet 开门指令获取 cosToken。")
    print(f"本地图片: {local_path}")
    print(f"对象 key: {object_key}")
    sys.exit(0)


if __name__ == "__main__":
    main()
