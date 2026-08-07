"""COS small-object uploader using execution-only temporary STS grants."""

from __future__ import annotations

import hashlib
from typing import Any


class CosPhotoUploader:
    """Upload one JPEG without retaining or logging temporary credentials."""

    def __init__(self, timeout_seconds: int = 15):
        if timeout_seconds <= 0:
            raise ValueError("COS request timeout must be positive")
        self._timeout_seconds = timeout_seconds

    def upload(
        self,
        grant: dict[str, Any],
        local_path: str,
        object_key: str,
    ) -> str:
        client = self._client(grant)
        with open(local_path, "rb") as source:
            client.put_object(
                Bucket=grant["bucket"],
                Body=source,
                Key=object_key,
                ContentType="image/jpeg",
                EnableMD5=False,
            )
        return f"{grant['baseUrl'].rstrip('/')}/{object_key}"

    def upload_and_readback(
        self,
        grant: dict[str, Any],
        local_path: str,
        object_key: str,
    ) -> dict[str, str]:
        """Upload one probe and hash bytes read back through the same grant."""
        expected = self._file_sha256(local_path)
        client = self._client(grant)
        with open(local_path, "rb") as source:
            client.put_object(
                Bucket=grant["bucket"],
                Body=source,
                Key=object_key,
                ContentType="image/jpeg",
                EnableMD5=False,
            )
        response = client.get_object(
            Bucket=grant["bucket"],
            Key=object_key,
        )
        actual = self._response_sha256(response)
        if actual != expected:
            raise RuntimeError("COS_READBACK_DIGEST_MISMATCH")
        return {
            "url": f"{grant['baseUrl'].rstrip('/')}/{object_key}",
            "uploadedSha256": expected,
            "readbackSha256": actual,
        }

    def _client(self, grant: dict[str, Any]):
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
            Timeout=self._timeout_seconds,
        )
        return CosS3Client(config)

    @staticmethod
    def _file_sha256(path: str) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _response_sha256(response: Any) -> str:
        body = response.get("Body") if isinstance(response, dict) else None
        if body is None:
            raise RuntimeError("COS_READBACK_BODY_MISSING")
        stream = (
            body.get_raw_stream()
            if hasattr(body, "get_raw_stream")
            else body
        )
        if not hasattr(stream, "read"):
            raise RuntimeError("COS_READBACK_BODY_UNREADABLE")
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()
