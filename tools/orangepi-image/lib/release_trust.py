#!/usr/bin/env python3
"""Validate the external trust anchors and signed Orange Pi release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Sequence


HEX_64 = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
KEY_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RELEASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
KEY_ROLES = ("buildAttestation", "sealEvidence", "releaseSigning")
BUILDER_RECEIPT_ROLE = "builderReceipt"
SIGNING_ROLES = (*KEY_ROLES, BUILDER_RECEIPT_ROLE)
BUILD_RECEIPT_KEYS = {
    "schemaVersion", "artifactClass", "invocationUid", "builderIdentity",
    "builderDomain", "builder", "releaseId", "version", "gitCommit",
    "inputLocks", "candidate", "completedAt", "signingKeyId",
}
MAX_JSON_BYTES = 1024 * 1024


class ReleaseTrustError(RuntimeError):
    """A formal release trust fact is absent, malformed, or unauthenticated."""


def enforce_secret_memory_policy(label: str) -> None:
    """Fail closed before a Linux process reads a private or secret-bearing input."""
    if os.name != "posix":
        return
    if os.geteuid() != 0:
        raise ReleaseTrustError(f"{label} requires root")
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError) as error:
        raise ReleaseTrustError(f"core dumps cannot be disabled for {label}") from error
    try:
        swaps = Path("/proc/swaps").read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseTrustError(f"active swap state cannot be proven for {label}") from error
    if any(line.strip() for line in swaps[1:]):
        raise ReleaseTrustError(f"active swap is forbidden for {label}")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseTrustError("SHA-256 input is unavailable") from error
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ReleaseTrustError(
                "SHA-256 input must be a regular non-symlink, non-hard-linked file"
            )
        total = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_nlink != before.st_nlink
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or total != before.st_size
        ):
            raise ReleaseTrustError("SHA-256 input changed while reading")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _load_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    duplicates: set[str] = set()

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicates.add(key)
            result[key] = value
        return result

    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseTrustError(f"{label} is not valid UTF-8 JSON") from error
    if duplicates:
        raise ReleaseTrustError(f"{label} contains duplicate JSON keys")
    if not isinstance(value, dict):
        raise ReleaseTrustError(f"{label} must contain one JSON object")
    return value


def _read_regular(
    path: Path,
    label: str,
    *,
    maximum: int | None = None,
    require_root_owner: bool = False,
    require_owner_only: bool = False,
    reject_group_other_write: bool = False,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseTrustError(f"{label} is unavailable") from error
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            raise ReleaseTrustError(
                f"{label} must be a regular non-symlink, non-hard-linked file"
            )
        if maximum is not None and details.st_size > maximum:
            raise ReleaseTrustError(f"{label} is too large")
        if (
            os.name == "posix"
            and require_root_owner
            and (details.st_uid != 0 or details.st_gid != 0)
        ):
            raise ReleaseTrustError(f"{label} must be owned by root:root")
        if os.name == "posix" and require_owner_only and details.st_mode & 0o077:
            raise ReleaseTrustError(f"{label} must be readable only by its owner")
        if (
            os.name == "posix"
            and reject_group_other_write
            and details.st_mode & 0o022
        ):
            raise ReleaseTrustError(f"{label} must not be group/other writable")
        payload = bytearray()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
            if maximum is not None and len(payload) > maximum:
                raise ReleaseTrustError(f"{label} is too large")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _require_exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    actual = set(value)
    if actual != keys:
        raise ReleaseTrustError(
            f"{label} keys differ: missing={sorted(keys - actual)!r} "
            f"unexpected={sorted(actual - keys)!r}"
        )


def validate_external_policy_path(policy_path: Path, repository_root: Path) -> Path:
    if not policy_path.is_absolute():
        raise ReleaseTrustError("formal release policy path must be absolute")
    try:
        raw_path = policy_path.absolute()
        current_raw = Path(raw_path.anchor)
        for component in raw_path.parts[1:]:
            current_raw /= component
            if current_raw.is_symlink():
                raise ReleaseTrustError(
                    "formal release policy path must not cross symbolic links"
                )
        path = raw_path.resolve(strict=True)
        repository = repository_root.resolve(strict=True)
    except OSError as error:
        raise ReleaseTrustError("formal release policy path cannot be resolved") from error
    if path == repository or path.is_relative_to(repository):
        raise ReleaseTrustError(
            "formal release policy must be supplied from outside the source repository"
        )
    current = path.parent
    while True:
        details = current.stat()
        if os.name == "posix" and (
            details.st_uid != 0 or details.st_mode & 0o022
        ):
            raise ReleaseTrustError(
                "formal release policy parent chain must be root-owned and not "
                "group/other writable"
            )
        if current == Path(current.anchor):
            break
        current = current.parent
    _read_regular(
        path,
        "formal release policy",
        maximum=64 * 1024,
        require_root_owner=True,
        reject_group_other_write=True,
    )
    return path


def validate_controlled_directory(directory: Path) -> Path:
    try:
        raw_path = directory.absolute()
        current_raw = Path(raw_path.anchor)
        for component in raw_path.parts[1:]:
            current_raw /= component
            if current_raw.is_symlink():
                raise ReleaseTrustError(
                    "controlled output path must not cross symbolic links"
                )
        path = raw_path.resolve(strict=True)
        details = path.stat()
    except OSError as error:
        raise ReleaseTrustError("controlled output directory is unavailable") from error
    if not path.is_dir() or (
        os.name == "posix" and (details.st_uid != 0 or details.st_gid != 0)
    ):
        raise ReleaseTrustError("controlled output directory must be root:root")
    if os.name == "posix" and details.st_mode & 0o077:
        raise ReleaseTrustError("controlled output directory must use mode 0700")
    current = path.parent
    while True:
        ancestor = current.stat()
        if os.name == "posix" and (
            ancestor.st_uid != 0
            or ancestor.st_gid != 0
            or ancestor.st_mode & 0o022
        ):
            raise ReleaseTrustError(
                "controlled output parent chain must be root-owned and not "
                "group/other writable"
            )
        if current == Path(current.anchor):
            break
        current = current.parent
    return path


def _fsync_directory(directory: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_policy(policy_path: Path, *, require_locked: bool = True) -> dict[str, Any]:
    path = policy_path.resolve()
    value = _load_json_bytes(
        _read_regular(
            path,
            "formal release policy",
            maximum=64 * 1024,
            reject_group_other_write=True,
        ),
        "formal release policy",
    )
    _require_exact_keys(
        value,
        {
            "$schema",
            "schemaVersion",
            "lockState",
            "reason",
            "rootfsQualification",
            "builderReceipts",
            "buildAttestation",
            "sealEvidence",
            "releaseSigning",
        },
        "formal release policy",
    )
    if (
        value["$schema"] != "./schemas/formal-release-policy.schema.json"
        or value["schemaVersion"] != 1
        or value["lockState"] not in {"LOCKED", "UNLOCKED"}
        or not isinstance(value["reason"], str)
        or not value["reason"].strip()
    ):
        raise ReleaseTrustError("formal release policy header is malformed")
    qualification = value["rootfsQualification"]
    if not isinstance(qualification, dict):
        raise ReleaseTrustError("formal rootfs qualification must be an object")
    _require_exact_keys(
        qualification,
        {"state", "method", "evidenceSha256"},
        "formal rootfs qualification",
    )
    if value["lockState"] == "UNLOCKED":
        if qualification != {
            "state": "UNQUALIFIED",
            "method": None,
            "evidenceSha256": None,
        }:
            raise ReleaseTrustError(
                "unlocked policy must keep deterministic rootfs unqualified"
            )
    elif (
        qualification.get("state") != "QUALIFIED"
        or qualification.get("method") != "DETERMINISTIC_EXT4_REBUILD_V1"
        or not isinstance(qualification.get("evidenceSha256"), str)
        or not HEX_64.fullmatch(qualification["evidenceSha256"])
        or set(qualification["evidenceSha256"]) == {"0"}
    ):
        raise ReleaseTrustError(
            "locked policy lacks independent deterministic rootfs qualification"
        )
    receipts = value["builderReceipts"]
    if not isinstance(receipts, list):
        raise ReleaseTrustError("builderReceipts must be an array")
    if value["lockState"] == "UNLOCKED":
        if receipts:
            raise ReleaseTrustError("unlocked policy must not contain builder receipt trust roots")
    else:
        if len(receipts) != 2:
            raise ReleaseTrustError("locked policy requires exactly two trusted builder receipt identities")
        for receipt in receipts:
            if not isinstance(receipt, dict):
                raise ReleaseTrustError("builder receipt identity is malformed")
            _require_exact_keys(receipt, {"builderIdentity", "builderDomain", "keyId", "publicKeySha256"}, "builder receipt identity")
            if any(not isinstance(receipt.get(key), str) or not receipt[key] for key in ("builderIdentity", "builderDomain", "keyId", "publicKeySha256")) or not KEY_ID.fullmatch(receipt["keyId"]) or not HEX_64.fullmatch(receipt["publicKeySha256"]):
                raise ReleaseTrustError("builder receipt identity is malformed")
        if len({item["builderIdentity"] for item in receipts}) != 2 or len({item["builderDomain"] for item in receipts}) != 2 or len({item["keyId"] for item in receipts}) != 2 or len({item["publicKeySha256"] for item in receipts}) != 2:
            raise ReleaseTrustError("trusted builder receipts must use distinct identities, domains, key IDs and keys")
    for role in KEY_ROLES:
        identity = value[role]
        if not isinstance(identity, dict):
            raise ReleaseTrustError(f"formal release policy {role} must be an object")
        _require_exact_keys(identity, {"keyId", "publicKeySha256"}, role)
        if value["lockState"] == "UNLOCKED":
            if identity != {"keyId": None, "publicKeySha256": None}:
                raise ReleaseTrustError(
                    f"unlocked formal release policy must leave {role} null"
                )
        elif (
            not isinstance(identity["keyId"], str)
            or not KEY_ID.fullmatch(identity["keyId"])
            or not isinstance(identity["publicKeySha256"], str)
            or not HEX_64.fullmatch(identity["publicKeySha256"])
            or set(identity["publicKeySha256"]) == {"0"}
        ):
            raise ReleaseTrustError(f"formal release policy {role} identity is malformed")
    if value["lockState"] == "LOCKED" and (
        len({value[role]["keyId"] for role in KEY_ROLES}) != len(KEY_ROLES)
        or len({value[role]["publicKeySha256"] for role in KEY_ROLES})
        != len(KEY_ROLES)
    ):
        raise ReleaseTrustError(
            "formal release key roles must use distinct key IDs and public keys"
        )
    if value["lockState"] == "LOCKED" and (
        {item["keyId"] for item in receipts} & {value[role]["keyId"] for role in KEY_ROLES}
        or {item["publicKeySha256"] for item in receipts} & {value[role]["publicKeySha256"] for role in KEY_ROLES}
    ):
        raise ReleaseTrustError("builder receipt keys must be distinct from release-role keys")
    if require_locked and value["lockState"] != "LOCKED":
        raise ReleaseTrustError(
            "formal release policy is not locked; trusted external key fingerprints "
            "must be provisioned before release"
        )
    return value


def _run_openssl_with_key(
    key_payload: bytes,
    *,
    private: bool,
    arguments: list[str],
    input_payload: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    with tempfile.TemporaryDirectory(prefix="ecobin-key-snapshot-") as temporary:
        key_snapshot = Path(temporary) / "key.pem"
        key_snapshot.write_bytes(key_payload)
        os.chmod(key_snapshot, 0o600)
        command = ["openssl", *arguments]
        marker = "{KEY}"
        command = [str(key_snapshot) if item == marker else item for item in command]
        return subprocess.run(
            command,
            check=False,
            input=input_payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def public_key_fingerprint(key_path: Path, *, private: bool) -> str:
    if private:
        enforce_secret_memory_policy("signing private key inspection")
    key_payload = _read_regular(
        key_path,
        "signing key",
        maximum=16 * 1024,
        require_root_owner=True,
        require_owner_only=private,
        reject_group_other_write=True,
    )
    arguments = ["openssl", "pkey"]
    if not private:
        arguments.append("-pubin")
    arguments.extend(("-in", "{KEY}", "-pubout", "-outform", "DER"))
    try:
        result = _run_openssl_with_key(
            key_payload,
            private=private,
            arguments=arguments[1:],
        )
    except OSError as error:
        raise ReleaseTrustError("OpenSSL is unavailable") from error
    if result.returncode != 0 or not result.stdout:
        raise ReleaseTrustError("signing key is not a usable Ed25519 key")
    return sha256_bytes(result.stdout)


def check_key(
    policy_path: Path,
    role: str,
    key_path: Path,
    *,
    private: bool,
    key_id: str,
) -> str:
    policy = load_policy(policy_path)
    expected = _resolve_signing_identity(policy, role, key_id)
    actual = public_key_fingerprint(key_path, private=private)
    if actual != expected["publicKeySha256"]:
        raise ReleaseTrustError(f"{role} key does not match the locked trust policy")
    return actual


def _resolve_signing_identity(
    policy: dict[str, Any],
    role: str,
    key_id: str,
) -> dict[str, Any]:
    if role in KEY_ROLES:
        expected = policy[role]
        if key_id != expected["keyId"]:
            raise ReleaseTrustError(
                f"{role} key does not match the locked trust policy"
            )
        return expected
    if role == BUILDER_RECEIPT_ROLE:
        matches = [
            identity
            for identity in policy["builderReceipts"]
            if identity["keyId"] == key_id
        ]
        if len(matches) != 1:
            raise ReleaseTrustError(
                "builder receipt key does not match the locked trust policy"
            )
        return matches[0]
    raise ReleaseTrustError("unknown formal release key role")


def _validate_builder_receipt_signing_payload(
    payload: bytes,
    identity: dict[str, Any],
) -> None:
    value = _load_json_bytes(payload, "builder receipt signing payload")
    _require_exact_keys(
        value,
        BUILD_RECEIPT_KEYS,
        "builder receipt signing payload",
    )
    if (
        isinstance(value.get("schemaVersion"), bool)
        or value.get("schemaVersion") != 1
        or value.get("artifactClass")
        != "SIGNED_INDEPENDENT_IMAGE_BUILD_RECEIPT"
        or value.get("builderIdentity") != identity["builderIdentity"]
        or value.get("builderDomain") != identity["builderDomain"]
        or value.get("signingKeyId") != identity["keyId"]
    ):
        raise ReleaseTrustError(
            "builder receipt payload identity differs from locked trust policy"
        )


def sign_detached(
    *,
    policy_path: Path,
    role: str,
    private_key_path: Path,
    key_id: str,
    payload_path: Path,
    output_path: Path,
) -> None:
    memory_policy_label = (
        "builder receipt signing"
        if role == BUILDER_RECEIPT_ROLE
        else "release signing"
    )
    enforce_secret_memory_policy(memory_policy_label)
    policy = load_policy(policy_path)
    expected = _resolve_signing_identity(policy, role, key_id)
    payload = _read_regular(payload_path, "signed payload", maximum=MAX_JSON_BYTES)
    if role == BUILDER_RECEIPT_ROLE:
        _validate_builder_receipt_signing_payload(payload, expected)
    key_payload = _read_regular(
        private_key_path,
        "signing private key",
        maximum=16 * 1024,
        require_root_owner=True,
        require_owner_only=True,
    )
    fingerprint_result = _run_openssl_with_key(
        key_payload,
        private=True,
        arguments=["pkey", "-in", "{KEY}", "-pubout", "-outform", "DER"],
    )
    if fingerprint_result.returncode != 0 or not fingerprint_result.stdout:
        raise ReleaseTrustError("signing private key is not usable")
    fingerprint = sha256_bytes(fingerprint_result.stdout)
    if fingerprint != expected["publicKeySha256"]:
        raise ReleaseTrustError(f"{role} private key differs from locked trust policy")
    with tempfile.TemporaryDirectory(prefix="ecobin-signing-snapshot-") as temporary:
        root = Path(temporary)
        key_snapshot = root / "key.pem"
        payload_snapshot = root / "payload"
        key_snapshot.write_bytes(key_payload)
        payload_snapshot.write_bytes(payload)
        os.chmod(key_snapshot, 0o600)
        os.chmod(payload_snapshot, 0o600)
        result = subprocess.run(
            [
                "openssl", "pkeyutl", "-sign", "-rawin", "-inkey",
                str(key_snapshot), "-in", str(payload_snapshot),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0 or len(result.stdout) != 64:
        raise ReleaseTrustError("detached Ed25519 signature failed")
    parent = validate_controlled_directory(output_path.parent)
    destination = parent / output_path.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(result.stdout)
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(parent)
    except BaseException:
        try:
            destination.unlink()
            _fsync_directory(parent)
        except OSError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def verify_detached(
    *,
    policy_path: Path,
    role: str,
    public_key_path: Path,
    key_id: str,
    payload_path: Path,
    signature_path: Path,
) -> None:
    check_key(
        policy_path,
        role,
        public_key_path,
        private=False,
        key_id=key_id,
    )
    payload = _read_regular(payload_path, "signed payload", maximum=MAX_JSON_BYTES)
    signature = _read_regular(signature_path, "detached signature", maximum=4096)
    _verify_signature(payload, signature, public_key_path)


def publish_new(source_path: Path, destination_path: Path, mode: int) -> None:
    payload = _read_regular(source_path, "publication source")
    parent = validate_controlled_directory(destination_path.parent)
    destination = parent / destination_path.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(destination, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        _fsync_directory(parent)
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def snapshot_new(
    source_path: Path,
    destination_path: Path,
    mode: int,
    expected_sha256: str | None,
    maximum_bytes: int | None = None,
) -> str:
    if expected_sha256 is not None and not HEX_64.fullmatch(expected_sha256):
        raise ReleaseTrustError("snapshot expected SHA-256 is malformed")
    if maximum_bytes is not None and maximum_bytes <= 0:
        raise ReleaseTrustError("snapshot maximum byte count is malformed")
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    source_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        source = os.open(source_path, source_flags)
    except OSError as error:
        raise ReleaseTrustError("snapshot source is unavailable") from error
    destination = -1
    destination_path_actual: Path | None = None
    destination_created = False
    try:
        before = os.fstat(source)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ReleaseTrustError("snapshot source must be a regular non-linked file")
        if maximum_bytes is not None and before.st_size > maximum_bytes:
            raise ReleaseTrustError("snapshot source exceeds the maximum byte count")
        parent = validate_controlled_directory(destination_path.parent)
        destination_path_actual = parent / destination_path.name
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        destination_flags |= getattr(os, "O_CLOEXEC", 0)
        destination_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        destination = os.open(destination_path_actual, destination_flags, mode)
        destination_created = True
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if maximum_bytes is not None and total > maximum_bytes:
                raise ReleaseTrustError("snapshot source exceeds the maximum byte count")
            view = memoryview(chunk)
            while view:
                written = os.write(destination, view)
                if written <= 0:
                    raise ReleaseTrustError("snapshot destination write made no progress")
                view = view[written:]
        after = os.fstat(source)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_nlink != before.st_nlink
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
            or total != before.st_size
        ):
            raise ReleaseTrustError("snapshot source changed while reading")
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ReleaseTrustError("snapshot source digest differs from signed inventory")
        os.fchmod(destination, mode)
        os.fsync(destination)
        os.close(destination)
        destination = -1
        _fsync_directory(parent)
        return actual_sha256
    except BaseException:
        if destination_created and destination_path_actual is not None:
            try:
                destination_path_actual.unlink()
            except OSError:
                pass
        raise
    finally:
        os.close(source)
        if destination >= 0:
            os.close(destination)


def _verify_signature(payload: bytes, signature: bytes, public_key: Path) -> None:
    if len(signature) != 64:
        raise ReleaseTrustError("detached Ed25519 signature must be exactly 64 bytes")
    public_key_payload = _read_regular(
        public_key,
        "verification public key",
        maximum=16 * 1024,
        reject_group_other_write=True,
    )
    with tempfile.TemporaryDirectory(prefix="ecobin-release-trust-") as temporary:
        root = Path(temporary)
        key_path = root / "public.pem"
        payload_path = root / "payload"
        signature_path = root / "signature"
        key_path.write_bytes(public_key_payload)
        payload_path.write_bytes(payload)
        signature_path.write_bytes(signature)
        os.chmod(key_path, 0o600)
        os.chmod(payload_path, 0o600)
        os.chmod(signature_path, 0o600)
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-pubin",
                "-inkey",
                str(key_path),
                "-rawin",
                "-in",
                str(payload_path),
                "-sigfile",
                str(signature_path),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if result.returncode != 0:
        raise ReleaseTrustError("detached Ed25519 signature verification failed")


def input_lock_hashes(
    config_dir: Path,
    software_payload_sha256: str,
    trust_policy_path: Path,
) -> dict[str, str]:
    if not HEX_64.fullmatch(software_payload_sha256):
        raise ReleaseTrustError("software payload lock SHA-256 is malformed")
    config = config_dir.resolve()
    names = {
        "sourceLockSha256": "source.lock.json",
        "builderLockSha256": "builder.lock",
        "aptPackagesLockSha256": "apt-packages.lock",
        "imageLayoutSha256": "image-layout.json",
    }
    values = {
        key: sha256_file(config / relative)
        for key, relative in names.items()
    }
    values["softwarePayloadLockSha256"] = software_payload_sha256
    values["formalReleasePolicySha256"] = sha256_file(trust_policy_path)
    return values


def _validate_identity(value: dict[str, Any]) -> None:
    if (
        not isinstance(value.get("releaseId"), str)
        or not RELEASE_ID.fullmatch(value["releaseId"])
        or not isinstance(value.get("version"), str)
        or not VERSION.fullmatch(value["version"])
        or not isinstance(value.get("gitCommit"), str)
        or not GIT_COMMIT.fullmatch(value["gitCommit"])
    ):
        raise ReleaseTrustError("signed release identity is malformed")


def _validate_attested_candidate_manifest(
    manifest_path: Path,
    attestation: dict[str, Any],
) -> None:
    raw = _read_regular(
        manifest_path,
        "candidate manifest",
        maximum=MAX_JSON_BYTES,
    )
    candidates = attestation["candidates"]
    if sha256_bytes(raw) != candidates[0]["manifestSha256"]:
        raise ReleaseTrustError("candidate manifest differs from build attestation")
    manifest = _load_json_bytes(raw, "candidate manifest")
    software = manifest.get("software")
    security = manifest.get("security")
    artifacts = manifest.get("artifacts")
    expected_locks = {
        key: value
        for key, value in attestation["locks"].items()
        if key != "formalReleasePolicySha256"
    }
    if (
        manifest.get("artifactClass") != "UNSIGNED_NO_SECRET_CANDIDATE"
        or manifest.get("releaseId") != attestation["releaseId"]
        or manifest.get("version") != attestation["version"]
        or manifest.get("gitCommit") != attestation["gitCommit"]
        or manifest.get("sourceDateEpoch") != attestation["sourceDateEpoch"]
        or manifest.get("sourceDirty") is not False
        or manifest.get("locks") != expected_locks
        or not isinstance(artifacts, dict)
        or artifacts.get("rawImageBytes") != candidates[0]["rawImageBytes"]
        or artifacts.get("rawImageSha256") != candidates[0]["rawImageSha256"]
        or not isinstance(software, dict)
        or software.get("buildReproducibility") != {
            "rootfsDeterministic": True,
            "releaseEligible": True,
        }
        or not isinstance(security, dict)
        or security.get("k1Injected") is not False
        or security.get("setupApKeyInjected") is not False
        or security.get("deviceCredentialsPresent") is not False
        or security.get("signingState") != "UNSIGNED"
    ):
        raise ReleaseTrustError(
            "candidate manifest lacks exact formal no-secret build facts"
        )


def verify_build_attestation(
    *,
    config_dir: Path,
    trust_policy_path: Path,
    attestation_path: Path,
    signature_path: Path,
    public_key_path: Path,
    expected_release_id: str | None = None,
    expected_version: str | None = None,
    expected_git_commit: str | None = None,
    expected_software_payload_sha256: str | None = None,
    expected_candidate_path: Path | None = None,
    expected_manifest_path: Path | None = None,
) -> dict[str, Any]:
    policy = load_policy(trust_policy_path)
    key_id = policy["buildAttestation"]["keyId"]
    check_key(
        trust_policy_path,
        "buildAttestation",
        public_key_path,
        private=False,
        key_id=key_id,
    )
    payload = _read_regular(attestation_path, "build attestation", maximum=MAX_JSON_BYTES)
    signature = _read_regular(signature_path, "build attestation signature", maximum=4096)
    _verify_signature(payload, signature, public_key_path)
    value = _load_json_bytes(payload, "build attestation")
    _require_exact_keys(
        value,
        {
            "$schema", "schemaVersion", "artifactClass", "releaseId", "version",
            "gitCommit", "sourceDateEpoch", "locks", "candidates",
            "verification", "signing", "rootfsQualification", "buildReceipts",
        },
        "build attestation",
    )
    if (
        value["$schema"] != "./schemas/build-attestation.schema.json"
        or value["schemaVersion"] != 2
        or value["artifactClass"]
        != "AUTHENTICATED_REPRODUCIBLE_CANDIDATE_BUILD"
    ):
        raise ReleaseTrustError("build attestation header is malformed")
    if value.get("rootfsQualification") != {
        "method": policy["rootfsQualification"]["method"],
        "evidenceSha256": policy["rootfsQualification"]["evidenceSha256"],
    }:
        raise ReleaseTrustError("build attestation rootfs evidence differs from policy")
    receipts = value.get("buildReceipts")
    if not isinstance(receipts, list) or len(receipts) != 2:
        raise ReleaseTrustError("build attestation requires two signed build receipts")
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != {"invocationUid", "builderIdentity", "builderDomain", "receiptSha256"} or not HEX_64.fullmatch(str(receipt.get("receiptSha256", ""))):
            raise ReleaseTrustError("build receipt reference is malformed")
    if len({item["invocationUid"] for item in receipts}) != 2 or len({item["builderIdentity"] for item in receipts}) != 2 or len({item["builderDomain"] for item in receipts}) != 2:
        raise ReleaseTrustError("build receipt references are not independent")
    trusted_receipts={(item["builderIdentity"],item["builderDomain"]) for item in policy["builderReceipts"]}
    if {(item["builderIdentity"],item["builderDomain"]) for item in receipts} != trusted_receipts:
        raise ReleaseTrustError("build receipt identities differ from policy")
    _validate_identity(value)
    if isinstance(value["sourceDateEpoch"], bool) or not isinstance(
        value["sourceDateEpoch"], int
    ) or value["sourceDateEpoch"] < 0:
        raise ReleaseTrustError("build attestation source date epoch is malformed")
    payload_sha = expected_software_payload_sha256
    if payload_sha is None:
        payload_sha = (value.get("locks") or {}).get("softwarePayloadLockSha256")
    if not isinstance(payload_sha, str) or not HEX_64.fullmatch(payload_sha):
        raise ReleaseTrustError("build attestation payload identity is malformed")
    if value.get("locks") != input_lock_hashes(
        config_dir,
        payload_sha,
        trust_policy_path,
    ):
        raise ReleaseTrustError("build attestation input locks differ from controlled inputs")
    expected_signing = {
        "algorithm": "Ed25519",
        "keyId": policy["buildAttestation"]["keyId"],
        "publicKeySha256": policy["buildAttestation"]["publicKeySha256"],
    }
    if value.get("signing") != expected_signing:
        raise ReleaseTrustError("build attestation signing identity differs from policy")
    if value.get("verification") != {
        "independentCandidateFiles": True,
        "candidateRawByteForByteMatch": True,
        "candidateManifestByteForByteMatch": True,
        "externalImageAuditPassed": True,
        "sourceWorktreeClean": True,
    }:
        raise ReleaseTrustError("build attestation lacks required independent verification")
    candidates = value.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise ReleaseTrustError("build attestation must contain exactly two candidates")
    expected_roles = ("independent-a", "independent-b")
    for candidate, role in zip(candidates, expected_roles):
        if not isinstance(candidate, dict):
            raise ReleaseTrustError("build attestation candidate is malformed")
        _require_exact_keys(
            candidate,
            {"role", "rawImageBytes", "rawImageSha256", "manifestSha256"},
            "build attestation candidate",
        )
        if (
            candidate["role"] != role
            or isinstance(candidate["rawImageBytes"], bool)
            or not isinstance(candidate["rawImageBytes"], int)
            or candidate["rawImageBytes"] <= 0
            or not isinstance(candidate["rawImageSha256"], str)
            or not HEX_64.fullmatch(candidate["rawImageSha256"])
            or not isinstance(candidate["manifestSha256"], str)
            or not HEX_64.fullmatch(candidate["manifestSha256"])
        ):
            raise ReleaseTrustError("build attestation candidate identity is malformed")
    if any(
        candidate[field] != candidates[0][field]
        for candidate in candidates[1:]
        for field in ("rawImageBytes", "rawImageSha256", "manifestSha256")
    ):
        raise ReleaseTrustError("build attestation candidates are not byte-identical")
    expectations = (
        (expected_release_id, value["releaseId"], "release ID"),
        (expected_version, value["version"], "version"),
        (expected_git_commit, value["gitCommit"], "Git commit"),
    )
    for expected, actual, label in expectations:
        if expected is not None and expected != actual:
            raise ReleaseTrustError(f"build attestation {label} differs from expected")
    if expected_candidate_path is not None:
        if (
            expected_candidate_path.stat().st_size != candidates[0]["rawImageBytes"]
            or sha256_file(expected_candidate_path) != candidates[0]["rawImageSha256"]
        ):
            raise ReleaseTrustError("candidate raw image differs from build attestation")
    if expected_manifest_path is not None:
        _validate_attested_candidate_manifest(expected_manifest_path, value)
    return value


def verify_seal_evidence(
    *,
    config_dir: Path,
    trust_policy_path: Path,
    evidence_path: Path,
    signature_path: Path,
    public_key_path: Path,
    build_attestation_path: Path,
    sealed_image_path: Path,
    expected_release_id: str,
    expected_version: str,
    expected_git_commit: str,
) -> dict[str, Any]:
    policy = load_policy(trust_policy_path)
    key_id = policy["sealEvidence"]["keyId"]
    check_key(
        trust_policy_path,
        "sealEvidence",
        public_key_path,
        private=False,
        key_id=key_id,
    )
    payload = _read_regular(evidence_path, "seal evidence", maximum=MAX_JSON_BYTES)
    signature = _read_regular(signature_path, "seal evidence signature", maximum=4096)
    _verify_signature(payload, signature, public_key_path)
    value = _load_json_bytes(payload, "seal evidence")
    _require_exact_keys(
        value,
        {
            "$schema", "schemaVersion", "artifactClass", "releaseId", "version",
            "gitCommit", "buildAttestationSha256", "candidateRawImageSha256",
            "sealedRawImageSha256", "rawImageBytes", "protectedFiles",
            "verification", "signing",
        },
        "seal evidence",
    )
    if (
        value["$schema"] != "./schemas/seal-evidence.schema.json"
        or value["schemaVersion"] != 1
        or value["artifactClass"] != "AUTHENTICATED_FILE_LEVEL_SEAL_EVIDENCE"
    ):
        raise ReleaseTrustError("seal evidence header is malformed")
    _validate_identity(value)
    if (
        value["releaseId"] != expected_release_id
        or value["version"] != expected_version
        or value["gitCommit"] != expected_git_commit
        or value["buildAttestationSha256"] != sha256_file(build_attestation_path)
        or value["sealedRawImageSha256"] != sha256_file(sealed_image_path)
        or value["rawImageBytes"] != sealed_image_path.stat().st_size
    ):
        raise ReleaseTrustError("seal evidence is not bound to the release artifacts")
    attestation = _load_json_bytes(
        _read_regular(
            build_attestation_path,
            "build attestation",
            maximum=MAX_JSON_BYTES,
        ),
        "build attestation",
    )
    candidates = attestation.get("candidates")
    if (
        not isinstance(candidates, list)
        or len(candidates) != 2
        or value["candidateRawImageSha256"]
        != candidates[0].get("rawImageSha256")
    ):
        raise ReleaseTrustError("seal evidence candidate differs from build attestation")
    expected_files = [
        {
            "path": "/etc/ecobin/enrollment.key",
            "type": "regular-file",
            "uid": 0,
            "gid": 0,
            "mode": "0600",
            "present": True,
            "contentDigestDisclosed": False,
        },
        {
            "path": "/etc/ecobin/setup-ap.key",
            "type": "regular-file",
            "uid": 0,
            "gid": 0,
            "mode": "0600",
            "present": True,
            "contentDigestDisclosed": False,
        },
    ]
    if value.get("protectedFiles") != expected_files or value.get("verification") != {
        "candidateAuditPassed": True,
        "sealedAuditPassed": True,
        "onlyDeclaredSecretsInjected": True,
    }:
        raise ReleaseTrustError("seal evidence lacks exact file-level verification facts")
    expected_signing = {
        "algorithm": "Ed25519",
        "keyId": policy["sealEvidence"]["keyId"],
        "publicKeySha256": policy["sealEvidence"]["publicKeySha256"],
    }
    if value.get("signing") != expected_signing:
        raise ReleaseTrustError("seal evidence signing identity differs from policy")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    policy = subparsers.add_parser("validate-policy")
    policy.add_argument("--trust-policy", required=True, type=Path)
    policy.add_argument("--repository-root", type=Path)
    policy.add_argument("--allow-unlocked", action="store_true")
    key = subparsers.add_parser("check-key")
    key.add_argument("--trust-policy", required=True, type=Path)
    key.add_argument("--role", choices=SIGNING_ROLES, required=True)
    key.add_argument("--key", required=True, type=Path)
    key.add_argument("--key-id", required=True)
    key.add_argument("--private", action="store_true")
    sign = subparsers.add_parser("sign")
    sign.add_argument("--trust-policy", required=True, type=Path)
    sign.add_argument("--role", choices=SIGNING_ROLES, required=True)
    sign.add_argument("--private-key", required=True, type=Path)
    sign.add_argument("--key-id", required=True)
    sign.add_argument("--payload", required=True, type=Path)
    sign.add_argument("--output", required=True, type=Path)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--source", required=True, type=Path)
    publish.add_argument("--destination", required=True, type=Path)
    publish.add_argument("--mode", choices=("0600", "0644"), required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--source", required=True, type=Path)
    snapshot.add_argument("--destination", required=True, type=Path)
    snapshot.add_argument("--mode", choices=("0600", "0644", "0755"), required=True)
    snapshot.add_argument("--expected-sha256")
    snapshot.add_argument("--maximum-bytes", type=int)
    detached = subparsers.add_parser("verify-signature")
    detached.add_argument("--trust-policy", required=True, type=Path)
    detached.add_argument("--role", choices=SIGNING_ROLES, required=True)
    detached.add_argument("--public-key", required=True, type=Path)
    detached.add_argument("--key-id", required=True)
    detached.add_argument("--payload", required=True, type=Path)
    detached.add_argument("--signature", required=True, type=Path)
    controlled = subparsers.add_parser("validate-directory")
    controlled.add_argument("--directory", required=True, type=Path)
    build = subparsers.add_parser("verify-build")
    build.add_argument("--config-dir", required=True, type=Path)
    build.add_argument("--trust-policy", required=True, type=Path)
    build.add_argument("--attestation", required=True, type=Path)
    build.add_argument("--signature", required=True, type=Path)
    build.add_argument("--public-key", required=True, type=Path)
    build.add_argument("--release-id")
    build.add_argument("--version")
    build.add_argument("--git-commit")
    build.add_argument("--software-payload-sha256")
    build.add_argument("--candidate", type=Path)
    build.add_argument("--candidate-manifest", type=Path)
    build.add_argument("--emit-identities", action="store_true")
    seal = subparsers.add_parser("verify-seal")
    seal.add_argument("--config-dir", required=True, type=Path)
    seal.add_argument("--trust-policy", required=True, type=Path)
    seal.add_argument("--evidence", required=True, type=Path)
    seal.add_argument("--signature", required=True, type=Path)
    seal.add_argument("--public-key", required=True, type=Path)
    seal.add_argument("--build-attestation", required=True, type=Path)
    seal.add_argument("--sealed-image", required=True, type=Path)
    seal.add_argument("--release-id", required=True)
    seal.add_argument("--version", required=True)
    seal.add_argument("--git-commit", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-policy":
            policy_path = args.trust_policy
            if args.repository_root is not None:
                policy_path = validate_external_policy_path(
                    policy_path,
                    args.repository_root,
                )
            policy = load_policy(
                policy_path,
                require_locked=not args.allow_unlocked,
            )
            print(f"formal-release-policy={policy['lockState']}")
        elif args.command == "check-key":
            fingerprint = check_key(
                args.trust_policy,
                args.role,
                args.key,
                private=args.private,
                key_id=args.key_id,
            )
            print(fingerprint)
        elif args.command == "sign":
            sign_detached(
                policy_path=args.trust_policy,
                role=args.role,
                private_key_path=args.private_key,
                key_id=args.key_id,
                payload_path=args.payload,
                output_path=args.output,
            )
            print("detached-signature=PASS")
        elif args.command == "publish":
            publish_new(
                args.source,
                args.destination,
                int(args.mode, 8),
            )
            print("controlled-publication=PASS")
        elif args.command == "snapshot":
            digest = snapshot_new(
                args.source,
                args.destination,
                int(args.mode, 8),
                args.expected_sha256,
                args.maximum_bytes,
            )
            print(digest)
        elif args.command == "verify-signature":
            verify_detached(
                policy_path=args.trust_policy,
                role=args.role,
                public_key_path=args.public_key,
                key_id=args.key_id,
                payload_path=args.payload,
                signature_path=args.signature,
            )
            print("detached-signature-verification=PASS")
        elif args.command == "validate-directory":
            print(validate_controlled_directory(args.directory))
        elif args.command == "verify-build":
            value = verify_build_attestation(
                config_dir=args.config_dir,
                trust_policy_path=args.trust_policy,
                attestation_path=args.attestation,
                signature_path=args.signature,
                public_key_path=args.public_key,
                expected_release_id=args.release_id,
                expected_version=args.version,
                expected_git_commit=args.git_commit,
                expected_software_payload_sha256=args.software_payload_sha256,
                expected_candidate_path=args.candidate,
                expected_manifest_path=args.candidate_manifest,
            )
            if args.emit_identities:
                print(value["releaseId"])
                print(value["version"])
                print(value["gitCommit"])
                print(value["locks"]["softwarePayloadLockSha256"])
                print(value["sourceDateEpoch"])
                print(value["candidates"][0]["rawImageSha256"])
            else:
                print("build-attestation=PASS")
        else:
            verify_seal_evidence(
                config_dir=args.config_dir,
                trust_policy_path=args.trust_policy,
                evidence_path=args.evidence,
                signature_path=args.signature,
                public_key_path=args.public_key,
                build_attestation_path=args.build_attestation,
                sealed_image_path=args.sealed_image,
                expected_release_id=args.release_id,
                expected_version=args.version,
                expected_git_commit=args.git_commit,
            )
            print("seal-evidence=PASS")
    except (OSError, ReleaseTrustError) as error:
        print(f"formal-release-trust=FAIL: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
