"""COS small-object uploader using execution-only temporary STS grants."""

from __future__ import annotations

from typing import Any


class CosPhotoUploader:
    """Upload one JPEG without retaining or logging temporary credentials."""

    def upload(
        self,
        grant: dict[str, Any],
        local_path: str,
        object_key: str,
    ) -> str:
        try:
            from qcloud_cos import CosConfig, CosS3Client
        except ImportError as error:
            raise RuntimeError("COS_SDK_NOT_INSTALLED") from error

        config = CosConfig(
            Region=grant["region"],
            SecretId=grant["tmpSecretId"],
            SecretKey=grant["tmpSecretKey"],
            Token="".join(grant["sessionTokenParts"]),
            Scheme="https",
        )
        client = CosS3Client(config)
        with open(local_path, "rb") as source:
            client.put_object(
                Bucket=grant["bucket"],
                Body=source,
                Key=object_key,
                ContentType="image/jpeg",
                EnableMD5=False,
            )
        return f"{grant['baseUrl'].rstrip('/')}/{object_key}"
