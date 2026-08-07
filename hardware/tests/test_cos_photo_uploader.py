import sys
import types
import io
import hashlib

from cos_photo_uploader import CosPhotoUploader


def test_cos_uploader_uses_execution_only_sts_grant(monkeypatch, tmp_path):
    captured = {}

    class FakeCosConfig:
        def __init__(self, **kwargs):
            captured["config"] = kwargs

    class FakeCosClient:
        def __init__(self, config):
            captured["client_config"] = config

        def put_object(self, **kwargs):
            captured["put"] = {
                **kwargs,
                "Body": kwargs["Body"].read(),
            }

    fake_sdk = types.SimpleNamespace(
        CosConfig=FakeCosConfig,
        CosS3Client=FakeCosClient,
    )
    monkeypatch.setitem(sys.modules, "qcloud_cos", fake_sdk)
    image_path = tmp_path / "photo.jpg"
    image_bytes = b"\xff\xd8\xff\xe0ecobin-test\xff\xd9"
    image_path.write_bytes(image_bytes)
    grant = {
        "region": "ap-shanghai",
        "bucket": "ecobin-test-1250000000",
        "baseUrl": (
            "https://ecobin-test-1250000000.cos."
            "ap-shanghai.myqcloud.com"
        ),
        "tmpSecretId": "temporary-id",
        "tmpSecretKey": "temporary-key",
        "sessionTokenParts": ["token-part-1", "token-part-2"],
    }
    object_key = (
        "ecobin/SN-TEST-0001/delivery-session/work/"
        "BEFORE_OUTER/photo.jpg"
    )

    url = CosPhotoUploader(timeout_seconds=15).upload(
        grant,
        str(image_path),
        object_key,
    )

    assert captured["config"] == {
        "Region": "ap-shanghai",
        "SecretId": "temporary-id",
        "SecretKey": "temporary-key",
        "Token": "token-part-1token-part-2",
        "Scheme": "https",
        "Timeout": 15,
    }
    assert captured["put"] == {
        "Bucket": "ecobin-test-1250000000",
        "Body": image_bytes,
        "Key": object_key,
        "ContentType": "image/jpeg",
        "EnableMD5": False,
    }
    assert url == (
        "https://ecobin-test-1250000000.cos."
        f"ap-shanghai.myqcloud.com/{object_key}"
    )


def test_cos_uploader_reads_back_and_verifies_the_uploaded_bytes(
    monkeypatch,
    tmp_path,
):
    captured = {}

    class FakeCosConfig:
        def __init__(self, **kwargs):
            captured["config"] = kwargs

    class FakeCosClient:
        def __init__(self, config):
            self.uploaded = b""

        def put_object(self, **kwargs):
            self.uploaded = kwargs["Body"].read()

        def get_object(self, **kwargs):
            return {"Body": io.BytesIO(self.uploaded)}

    monkeypatch.setitem(
        sys.modules,
        "qcloud_cos",
        types.SimpleNamespace(
            CosConfig=FakeCosConfig,
            CosS3Client=FakeCosClient,
        ),
    )
    image_path = tmp_path / "probe.jpg"
    content = b"acceptance-camera-probe"
    image_path.write_bytes(content)
    grant = {
        "region": "ap-shanghai",
        "bucket": "ecobin-test-1250000000",
        "baseUrl": (
            "https://ecobin-test-1250000000.cos."
            "ap-shanghai.myqcloud.com"
        ),
        "tmpSecretId": "temporary-id",
        "tmpSecretKey": "temporary-key",
        "sessionTokenParts": ["temporary-token"],
    }

    result = CosPhotoUploader().upload_and_readback(
        grant,
        str(image_path),
        "ecobin/device-acceptance/challenge/OUTSIDE/probe.jpg",
    )

    expected = hashlib.sha256(content).hexdigest()
    assert result["uploadedSha256"] == expected
    assert result["readbackSha256"] == expected
