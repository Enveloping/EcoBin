from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from device_credentials import load_device_credentials
from device_enrollment import (
    DeviceEnrollmentClient,
    EnrollmentPaths,
    EnrollmentRejectedError,
    EnrollmentChallengeExpiredError,
    EnrollmentRetryableError,
    canonical_enrollment_request_bytes,
    canonical_json_bytes,
    derive_hardware_sn,
    enrollment_transcript,
    ensure_ssh_host_key,
)


CHALLENGE_UID = "11111111-1111-4111-8111-111111111111"
CHALLENGE_NONCE = bytes(range(32))


class FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


def _public(key: ed25519.Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode("ascii")


def _paths(tmp_path: Path) -> EnrollmentPaths:
    key = tmp_path / "enrollment.key"
    key.write_text(base64.b64encode(b"k" * 32).decode("ascii"), encoding="ascii")
    key.chmod(0o600)
    cleanup = tmp_path / "device_enrollment.py"
    cleanup.write_text("sensitive generation logic", encoding="utf-8")
    return EnrollmentPaths(
        state=tmp_path / "enrollment-state.json",
        credentials=tmp_path / "device-credentials.json",
        global_key=key,
        ssh_host_private_key=tmp_path / "ssh_host_ed25519_key",
        ssh_host_public_key=tmp_path / "ssh_host_ed25519_key.pub",
        cleanup_files=(cleanup,),
    )


def _challenge() -> dict:
    return {
        "schemaVersion": 1,
        "challengeUid": CHALLENGE_UID,
        "enrollmentKeyId": "K1",
        "nonce": base64.b64encode(CHALLENGE_NONCE).decode("ascii"),
        "expiresAt": "2099-01-01T00:00:00.000Z",
    }


def _credential_plaintext(request: dict) -> dict:
    server_host = ed25519.Ed25519PrivateKey.generate()
    return {
        "schemaVersion": 1,
        "assetUid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "hardwareSn": request["hardwareSn"],
        "modelCode": "EC-M0",
        "expectedPortCount": 1,
        "oneNet": {
            "productId": "product-1",
            "deviceName": request["hardwareSn"],
            "deviceId": "onenet-device-id",
            "deviceKey": "one-net-secret",
            "mqttHost": "studio-mqtt.heclouds.com",
            "mqttPort": 1883,
        },
        "deviceEntryUrl": "https://www.jinshoubao.com/d/factory",
        "remoteSupport": {
            "tunnelHost": "support.example.com",
            "tunnelSshPort": 22,
            "tunnelUser": "ecobin-tunnel",
            "tunnelServerHostPublicKey": _public(server_host),
            "jumpUser": "ecobin-jump",
            "maintenancePrincipal": (
                f"ecobin-device-{request['hardwareSn']}"
            ),
            "maintenanceCaPublicKey": _public(
                ed25519.Ed25519PrivateKey.generate()
            ),
        },
    }


def _ready_response(request: dict, *, corrupt: bool = False) -> dict:
    enrollment_uid = request["enrollmentUid"]
    recipient = x25519.X25519PublicKey.from_public_bytes(
        base64.b64decode(request["responseWrapPublicKey"])
    )
    ephemeral = x25519.X25519PrivateKey.generate()
    shared = ephemeral.exchange(recipient)
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=CHALLENGE_NONCE,
        info=(
            b"ecobin-device-enrollment-response-v1\0"
            + enrollment_uid.encode("ascii")
        ),
    ).derive(shared)
    aad = json.dumps(
        {
            "schemaVersion": 1,
            "enrollmentUid": enrollment_uid,
            "hardwareSn": request["hardwareSn"],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = b"n" * 12
    ciphertext = AESGCM(key).encrypt(
        nonce,
        canonical_json_bytes(_credential_plaintext(request)),
        aad,
    )
    if corrupt:
        ciphertext = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    return {
        "schemaVersion": 1,
        "enrollmentUid": enrollment_uid,
        "status": "READY",
        "retryAfterMs": None,
        "encryptedResponse": {
            "schemaVersion": 1,
            "algorithm": "X25519-HKDF-SHA256-AES-256-GCM",
            "ephemeralPublicKey": base64.b64encode(
                ephemeral.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            ).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "aadSha256": hashlib.sha256(aad).hexdigest(),
            "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
        },
        "failureCode": None,
    }


def test_enrollment_persists_exact_request_across_pending_retry_then_cleans_up(
    tmp_path: Path,
):
    paths = _paths(tmp_path)
    enrollment_bodies: list[bytes] = []

    def post(url: str, *, data: bytes, **_kwargs):
        if url.endswith("/challenges"):
            assert data == b"{}"
            return FakeResponse(201, _challenge())
        enrollment_bodies.append(data)
        request = json.loads(data)
        if len(enrollment_bodies) == 1:
            return FakeResponse(
                202,
                {
                    "schemaVersion": 1,
                    "enrollmentUid": request["enrollmentUid"],
                    "status": "PENDING",
                    "retryAfterMs": 1000,
                    "encryptedResponse": None,
                    "failureCode": None,
                },
            )
        return FakeResponse(200, _ready_response(request))

    client = DeviceEnrollmentClient(
        backend_base_url="https://backend.example.com",
        paths=paths,
        http_post=post,
    )
    with pytest.raises(EnrollmentRetryableError, match="pending"):
        client.run_once()

    state = json.loads(paths.state.read_text(encoding="utf-8"))
    request = state["enrollmentRequest"]
    canonical = canonical_enrollment_request_bytes(request)
    transcript = enrollment_transcript(CHALLENGE_NONCE, canonical)
    assert hmac.compare_digest(
        base64.b64decode(request["registrationMac"]),
        hmac.new(b"k" * 32, transcript, hashlib.sha256).digest(),
    )
    identity = serialization.load_ssh_public_key(
        request["identityPublicKey"].encode("ascii")
    )
    identity.verify(base64.b64decode(request["signature"]), transcript)
    assert request["legacyProof"] is None

    credentials = client.run_once()

    assert enrollment_bodies[0] == enrollment_bodies[1]
    assert credentials["remoteSupport"]["tunnelIdentityPrivateKey"].startswith(
        "-----BEGIN OPENSSH PRIVATE KEY-----"
    )
    assert load_device_credentials(paths.credentials, required=True)
    assert not paths.state.exists()
    assert not paths.global_key.exists()
    assert not paths.cleanup_files[0].exists()


def test_authenticated_decryption_failure_never_deletes_bootstrap_material(
    tmp_path: Path,
):
    paths = _paths(tmp_path)

    def post(url: str, *, data: bytes, **_kwargs):
        if url.endswith("/challenges"):
            return FakeResponse(201, _challenge())
        request = json.loads(data)
        return FakeResponse(200, _ready_response(request, corrupt=True))

    client = DeviceEnrollmentClient(
        backend_base_url="https://backend.example.com",
        paths=paths,
        http_post=post,
    )

    with pytest.raises(EnrollmentRejectedError, match="authentication"):
        client.run_once()
    assert paths.state.exists()
    assert paths.global_key.exists()
    assert not paths.credentials.exists()


def test_ssh_host_public_key_cannot_exist_without_matching_private_key(
    tmp_path: Path,
):
    private_path = tmp_path / "host-key"
    public_path = tmp_path / "host-key.pub"
    public_path.write_text(
        _public(ed25519.Ed25519PrivateKey.generate()) + " comment\n",
        encoding="ascii",
    )

    with pytest.raises(EnrollmentRejectedError, match="without its private"):
        ensure_ssh_host_key(private_path, public_path)


def test_hardware_sn_is_stable_130_bit_crockford_encoding():
    public = bytes(range(32))

    first = derive_hardware_sn(public)

    assert first == derive_hardware_sn(public)
    assert first.startswith("ECM0-")
    assert len(first) == 31
    assert set(first[5:]) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_java_python_canonical_transcript_golden_vector():
    request = {
        "schemaVersion": 1,
        "enrollmentUid": "11111111-1111-4111-8111-111111111111",
        "challengeUid": "22222222-2222-4222-8222-222222222222",
        "enrollmentKeyId": "K1",
        "enrollmentMode": "SELF_ENROLLMENT",
        "hardwareSn": "ECM0-CPV0CWYPXP44QW0W5GH2V0NDM1",
        "identityPublicKey": (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHm1Vi6P5lT5QHix"
            "Euipi6eQH4U65pW+1+DjkQutBJZk"
        ),
        "responseWrapPublicKey": (
            "NYBy1jZYgNGu6jKa35EhODhR7SGijjt16WXQ0s0WYlQ="
        ),
        "tunnelPublicKey": (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOfxYqEL7FWa/qGV"
            "5NzoS2lWjV0ssJY+tEbAaF4rF/Lw"
        ),
        "sshHostPublicKey": (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK3BQBH4LRxW2Vaq"
            "T51z2IWDYaYGBIUl4NCMY43HXdjH"
        ),
    }
    canonical = canonical_enrollment_request_bytes(request)
    transcript = enrollment_transcript(bytes(range(32)), canonical)

    assert hashlib.sha256(canonical).hexdigest() == (
        "3096a265a3dd4e39fbfbdd524f7e8cd25136a31eb51c652d6fef7187ca7e35e6"
    )
    assert hashlib.sha256(transcript).hexdigest() == (
        "e4a79bc49d8972e040fd6389427d695338e39527d5a049b6120789a693837224"
    )


def test_legacy_adoption_adds_dual_proof(tmp_path: Path):
    paths = _paths(tmp_path)
    captured: dict = {}

    def post(url: str, *, data: bytes, **_kwargs):
        if url.endswith("/challenges"):
            return FakeResponse(201, _challenge())
        captured.update(json.loads(data))
        return FakeResponse(
            202,
            {
                "schemaVersion": 1,
                "enrollmentUid": captured["enrollmentUid"],
                "status": "PENDING",
                "retryAfterMs": 1000,
                "encryptedResponse": None,
                "failureCode": None,
            },
        )

    client = DeviceEnrollmentClient(
        backend_base_url="https://backend.example.com",
        paths=paths,
        enrollment_mode="LEGACY_ADOPTION",
        legacy_onenet_secret="current-device-secret",
        http_post=post,
    )
    with pytest.raises(EnrollmentRetryableError):
        client.run_once()

    canonical = canonical_enrollment_request_bytes(captured)
    transcript = enrollment_transcript(CHALLENGE_NONCE, canonical)
    expected = hmac.new(
        b"current-device-secret",
        b"ecobin-legacy-onenet-proof-v1\0" + transcript,
        hashlib.sha256,
    ).digest()
    assert base64.b64decode(captured["legacyProof"]) == expected


def test_expired_challenge_is_replaced_without_changing_device_identity(tmp_path: Path):
    paths = _paths(tmp_path)
    challenge_count = 0
    requests = []

    def post(url: str, *, data: bytes, **_kwargs):
        nonlocal challenge_count
        if url.endswith("/challenges"):
            challenge_count += 1
            challenge = _challenge()
            challenge["challengeUid"] = str(uuid.UUID(int=challenge_count, version=4))
            return FakeResponse(201, challenge)
        request = json.loads(data)
        requests.append(request)
        if len(requests) == 1:
            return FakeResponse(
                409,
                {"code": "DEVICE.ENROLLMENT_CHALLENGE_UNAVAILABLE"},
            )
        return FakeResponse(
            202,
            {
                "schemaVersion": 1,
                "enrollmentUid": request["enrollmentUid"],
                "status": "PENDING",
                "retryAfterMs": 1000,
                "encryptedResponse": None,
                "failureCode": None,
            },
        )

    client = DeviceEnrollmentClient(
        backend_base_url="https://backend.example.com",
        paths=paths,
        http_post=post,
    )
    with pytest.raises(EnrollmentChallengeExpiredError):
        client.run_once()
    with pytest.raises(EnrollmentRetryableError, match="pending"):
        client.run_once()

    assert challenge_count == 2
    assert requests[0]["enrollmentUid"] == requests[1]["enrollmentUid"]
    assert requests[0]["identityPublicKey"] == requests[1]["identityPublicKey"]
    assert requests[0]["challengeUid"] != requests[1]["challengeUid"]
