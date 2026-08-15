"""Power-loss-safe first-boot enrollment for an EcoBin Orange Pi."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from device_credentials import validate_device_credentials
from secure_files import atomic_write_bytes, atomic_write_json, unlink_and_fsync


MODEL_CODE = "EC-M0"
PORT_COUNT = 1
ENROLLMENT_SCHEMA_VERSION = 1
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_TRANSCRIPT_DOMAIN = b"ecobin-device-enrollment-v1\0"
_RESPONSE_DOMAIN = b"ecobin-device-enrollment-response-v1\0"
_LEGACY_PROOF_DOMAIN = b"ecobin-legacy-onenet-proof-v1\0"


class EnrollmentRetryableError(RuntimeError):
    """A systemd restart can safely retry the persisted enrollment state."""


class EnrollmentRejectedError(RuntimeError):
    """The backend rejected this device identity or proof permanently."""


class EnrollmentChallengeExpiredError(EnrollmentRetryableError):
    """The persisted identity is retained while a fresh challenge is obtained."""


@dataclass(frozen=True)
class EnrollmentPaths:
    state: Path
    credentials: Path
    global_key: Path
    ssh_host_private_key: Path
    ssh_host_public_key: Path
    cleanup_files: tuple[Path, ...] = ()


class DeviceEnrollmentClient:
    """Advance enrollment by one network attempt.

    Every random identity and the exact signed request are persisted before a
    request is sent.  Re-instantiating this class after power loss therefore
    never creates a second logical device.
    """

    def __init__(
        self,
        *,
        backend_base_url: str,
        paths: EnrollmentPaths,
        enrollment_key_id: str = "K1",
        enrollment_mode: str = "SELF_ENROLLMENT",
        legacy_onenet_secret: str | None = None,
        http_post: Callable[..., Any] | None = None,
        timeout_seconds: float = 15.0,
    ):
        base = backend_base_url.rstrip("/")
        if not base.startswith("https://"):
            raise ValueError("enrollment backend URL must use HTTPS")
        if timeout_seconds <= 0:
            raise ValueError("enrollment HTTP timeout must be positive")
        self._base_url = base
        self._paths = paths
        if not re.fullmatch(r"[A-Z0-9_-]{1,16}", enrollment_key_id):
            raise ValueError("enrollment key ID is invalid")
        if enrollment_mode not in {"SELF_ENROLLMENT", "LEGACY_ADOPTION"}:
            raise ValueError("enrollment mode is invalid")
        self._key_id = enrollment_key_id
        self._mode = enrollment_mode
        if enrollment_mode == "LEGACY_ADOPTION" and not legacy_onenet_secret:
            raise ValueError("legacy adoption requires the current OneNet secret")
        self._legacy_onenet_secret = legacy_onenet_secret
        self._post = http_post or requests.post
        self._timeout = timeout_seconds

    def run_once(self) -> dict[str, Any]:
        if self._paths.credentials.exists():
            document = json.loads(
                self._paths.credentials.read_text(encoding="utf-8")
            )
            validate_device_credentials(document)
            self._cleanup_after_success()
            return document

        state = self._load_or_create_state()
        if not state.get("enrollmentRequest"):
            state = self._obtain_challenge_and_sign(state)
        try:
            response = self._send_enrollment(state["enrollmentRequest"])
        except EnrollmentChallengeExpiredError:
            state["challenge"] = None
            state["enrollmentRequest"] = None
            atomic_write_json(self._paths.state, state)
            raise
        encrypted = response.get("encryptedResponse")
        if not isinstance(encrypted, dict):
            raise EnrollmentRetryableError(
                "READY enrollment response omitted encryptedResponse"
            )
        credentials = decrypt_enrollment_response(
            encrypted,
            response_private_key_b64=state["responsePrivateKey"],
            enrollment_uid=response.get("enrollmentUid"),
            hardware_sn=state["hardwareSn"],
            challenge_nonce_b64=state["challenge"]["nonce"],
        )
        if credentials.get("hardwareSn") != state["hardwareSn"]:
            raise EnrollmentRejectedError(
                "enrollment response hardwareSn does not match device identity"
            )
        remote = credentials.get("remoteSupport")
        if not isinstance(remote, dict):
            raise EnrollmentRejectedError(
                "enrollment response omitted remoteSupport configuration"
            )
        remote["tunnelIdentityPrivateKey"] = state["tunnelPrivateKey"]
        credentials["sshHostPublicKey"] = state["sshHostPublicKey"]
        validate_device_credentials(credentials)
        atomic_write_json(
            self._paths.credentials,
            credentials,
            validator=validate_device_credentials,
        )
        self._cleanup_after_success()
        return credentials

    def _load_or_create_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self._paths.state.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = self._new_state()
            atomic_write_json(self._paths.state, state)
        else:
            if (
                os.name != "nt"
                and stat.S_IMODE(self._paths.state.stat().st_mode) & 0o077
            ):
                raise EnrollmentRejectedError(
                    "enrollment state must not be group/world accessible"
                )
        self._validate_state(state)
        return state

    def _new_state(self) -> dict[str, Any]:
        identity = ed25519.Ed25519PrivateKey.generate()
        response = x25519.X25519PrivateKey.generate()
        tunnel = ed25519.Ed25519PrivateKey.generate()
        identity_public = identity.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        )
        identity_raw = identity.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        response_public = response.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        tunnel_public = tunnel.public_key().public_bytes(
            serialization.Encoding.OpenSSH,
            serialization.PublicFormat.OpenSSH,
        ).decode("ascii")
        ssh_host_public = ensure_ssh_host_key(
            self._paths.ssh_host_private_key,
            self._paths.ssh_host_public_key,
        )
        return {
            "schemaVersion": ENROLLMENT_SCHEMA_VERSION,
            "enrollmentUid": str(uuid.uuid4()),
            "enrollmentKeyId": self._key_id,
            "enrollmentMode": self._mode,
            "hardwareSn": derive_hardware_sn(identity_raw),
            "identityPrivateKey": _raw_private_b64(identity),
            "identityPublicKey": identity_public.decode("ascii"),
            "responsePrivateKey": _raw_private_b64(response),
            "responsePublicKey": _b64(response_public),
            "tunnelPrivateKey": tunnel.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            ).decode("ascii"),
            "tunnelPublicKey": tunnel_public,
            "sshHostPublicKey": ssh_host_public,
            "challenge": None,
            "enrollmentRequest": None,
        }

    def _obtain_challenge_and_sign(
        self,
        state: dict[str, Any],
    ) -> dict[str, Any]:
        challenge_request: dict[str, Any] = {}
        response = self._request_json(
            f"{self._base_url}/api/v1/device-enrollment/challenges",
            challenge_request,
        )
        if response.get("schemaVersion") != 1:
            raise EnrollmentRetryableError(
                "challenge response schemaVersion is invalid"
            )
        challenge = {
            "challengeUid": response.get("challengeUid"),
            "enrollmentKeyId": response.get("enrollmentKeyId"),
            "nonce": response.get("nonce"),
            "expiresAt": response.get("expiresAt"),
        }
        _validate_challenge(challenge)
        if challenge["enrollmentKeyId"] != state["enrollmentKeyId"]:
            raise EnrollmentRejectedError(
                "backend challenge selected a different enrollment key"
            )
        request_fields = {
            "schemaVersion": ENROLLMENT_SCHEMA_VERSION,
            "enrollmentUid": state["enrollmentUid"],
            "challengeUid": challenge["challengeUid"],
            "enrollmentKeyId": challenge["enrollmentKeyId"],
            "enrollmentMode": state["enrollmentMode"],
            "hardwareSn": state["hardwareSn"],
            "identityPublicKey": state["identityPublicKey"],
            "responseWrapPublicKey": state["responsePublicKey"],
            "tunnelPublicKey": state["tunnelPublicKey"],
            "sshHostPublicKey": state["sshHostPublicKey"],
        }
        canonical_request = canonical_enrollment_request_bytes(
            request_fields
        )
        transcript = enrollment_transcript(
            _b64decode(challenge["nonce"], minimum_length=16),
            canonical_request,
        )
        global_key = _read_global_key(self._paths.global_key)
        identity_private = ed25519.Ed25519PrivateKey.from_private_bytes(
            _b64decode(state["identityPrivateKey"], expected_length=32)
        )
        signed_request = {
            **request_fields,
            "registrationMac": _b64(
                hmac.new(global_key, transcript, hashlib.sha256).digest()
            ),
            "signature": _b64(identity_private.sign(transcript)),
            "legacyProof": (
                _b64(
                    hmac.new(
                        self._legacy_onenet_secret.encode("utf-8"),
                        _LEGACY_PROOF_DOMAIN + transcript,
                        hashlib.sha256,
                    ).digest()
                )
                if self._mode == "LEGACY_ADOPTION"
                else None
            ),
        }
        state["challenge"] = challenge
        state["enrollmentRequest"] = signed_request
        atomic_write_json(self._paths.state, state)
        return state

    def _send_enrollment(self, request: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/api/v1/device-enrollments"
        status, body = self._perform_request(url, request)
        response_status = body.get("status")
        if status == 409 and body.get("code") == (
            "DEVICE.ENROLLMENT_CHALLENGE_UNAVAILABLE"
        ):
            raise EnrollmentChallengeExpiredError(
                "enrollment challenge is unavailable; request a fresh challenge"
            )
        if status in {408, 425, 429} or status >= 500:
            raise EnrollmentRetryableError(
                f"enrollment backend temporarily returned HTTP {status}"
            )
        if status not in {200, 202, 422}:
            raise EnrollmentRejectedError(
                f"enrollment backend rejected the request with HTTP {status}"
            )
        if body.get("schemaVersion") != 1:
            raise EnrollmentRetryableError(
                "enrollment response schemaVersion is invalid"
            )
        if body.get("enrollmentUid") != request["enrollmentUid"]:
            raise EnrollmentRejectedError(
                "enrollment response enrollmentUid does not match"
            )
        if status == 202 and response_status == "PENDING":
            raise EnrollmentRetryableError("enrollment is still pending")
        if status == 200 and response_status == "READY":
            return body
        if status == 422 and response_status == "FAILED":
            failure_code = body.get("failureCode") or "ENROLLMENT_FAILED"
            raise EnrollmentRejectedError(
                f"enrollment backend rejected the request: {failure_code}"
            )
        raise EnrollmentRetryableError(
            f"invalid enrollment response HTTP/status pair: {status}/{response_status}"
        )

    def _request_json(
        self,
        url: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        status, body = self._perform_request(url, document)
        if status == 410:
            raise EnrollmentChallengeExpiredError(
                "enrollment challenge expired; a new challenge will be requested"
            )
        if status in {408, 425, 429} or status >= 500:
            raise EnrollmentRetryableError(
                f"enrollment backend temporarily returned HTTP {status}"
            )
        if status not in {200, 201}:
            raise EnrollmentRejectedError(
                f"enrollment backend rejected the request with HTTP {status}"
            )
        return body

    def _perform_request(
        self,
        url: str,
        document: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        try:
            response = self._post(
                url,
                data=canonical_json_bytes(document),
                headers={"Content-Type": "application/json"},
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise EnrollmentRetryableError("enrollment network request failed") from error
        status = int(getattr(response, "status_code", 0))
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as error:
            raise EnrollmentRetryableError(
                "enrollment backend returned invalid JSON"
            ) from error
        if not isinstance(body, dict):
            raise EnrollmentRetryableError("enrollment response must be an object")
        return status, body

    def _validate_state(self, state: dict[str, Any]) -> None:
        if state.get("schemaVersion") != ENROLLMENT_SCHEMA_VERSION:
            raise EnrollmentRejectedError("unsupported enrollment state")
        if state.get("enrollmentKeyId") != self._key_id:
            raise EnrollmentRejectedError("enrollment state key ID changed")
        if state.get("enrollmentMode") != self._mode:
            raise EnrollmentRejectedError("enrollment state mode changed")
        identity_public = _parse_openssh_ed25519(
            state.get("identityPublicKey"),
            "identityPublicKey",
        )
        if derive_hardware_sn(identity_public) != state.get("hardwareSn"):
            raise EnrollmentRejectedError("enrollment state identity is corrupt")
        _b64decode(state.get("identityPrivateKey"), expected_length=32)
        _b64decode(state.get("responsePrivateKey"), expected_length=32)
        if not isinstance(state.get("tunnelPrivateKey"), str):
            raise EnrollmentRejectedError("enrollment tunnel key is missing")

    def _cleanup_after_success(self) -> None:
        # The credential file has already been fsynced and re-read before any
        # bootstrap material is removed.  Each deletion is independently
        # idempotent so a power cut during cleanup resumes safely.
        unlink_and_fsync(self._paths.global_key)
        unlink_and_fsync(self._paths.state)
        for file_path in self._paths.cleanup_files:
            unlink_and_fsync(file_path)


def canonical_json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_enrollment_request_bytes(document: dict[str, Any]) -> bytes:
    """Byte-for-byte peer of DeviceEnrollmentCrypto.canonicalRequest."""

    ordered = {
        "schemaVersion": 1,
        "enrollmentUid": document["enrollmentUid"],
        "challengeUid": document["challengeUid"],
        "enrollmentKeyId": document["enrollmentKeyId"],
        "enrollmentMode": document["enrollmentMode"],
        "hardwareSn": document["hardwareSn"],
        "identityPublicKey": document["identityPublicKey"],
        "responseWrapPublicKey": document["responseWrapPublicKey"],
        "tunnelPublicKey": document["tunnelPublicKey"],
        "sshHostPublicKey": document["sshHostPublicKey"],
    }
    return json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def enrollment_transcript(challenge_nonce: bytes, canonical: bytes) -> bytes:
    return _TRANSCRIPT_DOMAIN + challenge_nonce + canonical


def derive_hardware_sn(identity_public_key: bytes) -> str:
    """ECM0- plus the first 130 SHA-256 bits in Crockford Base32."""

    digest = hashlib.sha256(identity_public_key).digest()
    value = int.from_bytes(digest[:17], "big") >> 6
    encoded = "".join(
        _CROCKFORD[(value >> shift) & 31]
        for shift in range(125, -1, -5)
    )
    return f"ECM0-{encoded}"


def ensure_ssh_host_key(private_path: Path, public_path: Path) -> str:
    try:
        public = public_path.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        public = ""
    try:
        private_bytes = private_path.read_bytes()
    except FileNotFoundError:
        if public:
            raise EnrollmentRejectedError(
                "SSH host public key exists without its private key"
            )
        private_key = ed25519.Ed25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        )
        atomic_write_bytes(private_path, private_bytes, mode=0o600)
    else:
        if (
            os.name != "nt"
            and stat.S_IMODE(private_path.stat().st_mode) & 0o077
        ):
            raise EnrollmentRejectedError(
                "SSH host private key must not be group/world accessible"
            )
        try:
            private_key = serialization.load_ssh_private_key(
                private_bytes,
                password=None,
            )
        except (TypeError, ValueError) as error:
            raise EnrollmentRejectedError("SSH host private key is invalid") from error
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise EnrollmentRejectedError("SSH host key must be Ed25519")
    public = private_key.public_key().public_bytes(
        serialization.Encoding.OpenSSH,
        serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    if public_path.exists():
        configured = " ".join(
            public_path.read_text(encoding="ascii").strip().split()[:2]
        )
        _validate_public_key(configured, "SSH host public key")
        if not hmac.compare_digest(configured, public):
            raise EnrollmentRejectedError("SSH host key pair does not match")
    else:
        atomic_write_bytes(
            public_path,
            (public + "\n").encode("ascii"),
            mode=0o644,
        )
    return public


def decrypt_enrollment_response(
    encrypted: dict[str, Any],
    *,
    response_private_key_b64: str,
    enrollment_uid: Any,
    hardware_sn: str,
    challenge_nonce_b64: str,
) -> dict[str, Any]:
    if not isinstance(enrollment_uid, str) or not _UUID4.fullmatch(enrollment_uid):
        raise EnrollmentRejectedError("enrollmentUid must be a lowercase UUIDv4")
    private = x25519.X25519PrivateKey.from_private_bytes(
        _b64decode(response_private_key_b64, expected_length=32)
    )
    if encrypted.get("schemaVersion") != 1:
        raise EnrollmentRejectedError("unsupported encrypted response schemaVersion")
    if encrypted.get("algorithm") != "X25519-HKDF-SHA256-AES-256-GCM":
        raise EnrollmentRejectedError("unsupported encrypted response algorithm")
    peer = x25519.X25519PublicKey.from_public_bytes(
        _b64decode(encrypted.get("ephemeralPublicKey"), expected_length=32)
    )
    nonce = _b64decode(encrypted.get("nonce"), expected_length=12)
    ciphertext = _b64decode(encrypted.get("ciphertext"), minimum_length=17)
    challenge_nonce = _b64decode(challenge_nonce_b64, minimum_length=16)
    aad = json.dumps(
        {
            "schemaVersion": 1,
            "enrollmentUid": enrollment_uid,
            "hardwareSn": hardware_sn,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    expected_aad_sha256 = hashlib.sha256(aad).hexdigest()
    if not hmac.compare_digest(
        str(encrypted.get("aadSha256", "")),
        expected_aad_sha256,
    ):
        raise EnrollmentRejectedError("encrypted response AAD digest mismatch")
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=challenge_nonce,
        info=_RESPONSE_DOMAIN + enrollment_uid.encode("ascii"),
    ).derive(private.exchange(peer))
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        document = json.loads(plaintext.decode("utf-8"))
    except Exception as error:
        raise EnrollmentRejectedError(
            "encrypted enrollment response authentication failed"
        ) from error
    if not isinstance(document, dict):
        raise EnrollmentRejectedError("decrypted credentials must be an object")
    return document


def _read_global_key(path: Path) -> bytes:
    try:
        encoded = path.read_text(encoding="ascii").strip()
    except FileNotFoundError as error:
        raise EnrollmentRejectedError("global enrollment key is missing") from error
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise EnrollmentRejectedError(
            "global enrollment key must not be group/world accessible"
        )
    key = _b64decode(encoded, minimum_length=32)
    if len(key) > 128:
        raise EnrollmentRejectedError("global enrollment key is too long")
    return key


def _validate_challenge(challenge: dict[str, Any]) -> None:
    if not isinstance(challenge.get("challengeUid"), str) or not _UUID4.fullmatch(
        challenge["challengeUid"]
    ):
        raise EnrollmentRetryableError("challengeUid is invalid")
    if not isinstance(challenge.get("enrollmentKeyId"), str) or not re.fullmatch(
        r"[A-Z0-9_-]{1,16}",
        challenge["enrollmentKeyId"],
    ):
        raise EnrollmentRetryableError("challenge enrollmentKeyId is invalid")
    _b64decode(challenge.get("nonce"), minimum_length=16)
    expires = challenge.get("expiresAt")
    if not isinstance(expires, str) or not expires.endswith("Z"):
        raise EnrollmentRetryableError("challenge expiresAt is invalid")
    try:
        datetime.fromisoformat(expires[:-1] + "+00:00")
    except ValueError as error:
        raise EnrollmentRetryableError("challenge expiresAt is invalid") from error


def _validate_public_key(value: str, field: str) -> None:
    parts = value.split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519":
        raise EnrollmentRejectedError(f"{field} must be ssh-ed25519")
    _b64decode(parts[1], minimum_length=32)


def _parse_openssh_ed25519(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise EnrollmentRejectedError(f"{field} must be a string")
    parts = value.split()
    if len(parts) != 2 or parts[0] != "ssh-ed25519":
        raise EnrollmentRejectedError(f"{field} must be canonical ssh-ed25519")
    blob = _b64decode(parts[1], minimum_length=51)
    expected_prefix = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"
    if len(blob) != len(expected_prefix) + 32 or not blob.startswith(expected_prefix):
        raise EnrollmentRejectedError(f"{field} SSH blob is invalid")
    return blob[-32:]


def _raw_private_b64(private_key: Any) -> str:
    return _b64(
        private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(
    value: Any,
    *,
    expected_length: int | None = None,
    minimum_length: int | None = None,
) -> bytes:
    if not isinstance(value, str):
        raise EnrollmentRejectedError("base64 field must be a string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception as error:
        raise EnrollmentRejectedError("base64 field is invalid") from error
    if expected_length is not None and len(decoded) != expected_length:
        raise EnrollmentRejectedError("base64 field length is invalid")
    if minimum_length is not None and len(decoded) < minimum_length:
        raise EnrollmentRejectedError("base64 field is too short")
    return decoded
