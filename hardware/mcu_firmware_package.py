"""Build and verify signed EcoBin STM32F103C8T6 firmware packages.

The ``.efw`` container deliberately has only three entries:

* ``firmware.bin`` -- raw image written at 0x08000000;
* ``manifest.json`` -- canonical UTF-8 JSON;
* ``manifest.sig`` -- raw 64-byte Ed25519 signature of manifest.json.

The private signing key is needed only by the offline ``package`` command.
Runtime devices use :func:`verify_package` with a public-key mapping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

MCU_PART_NUMBER = "STM32F103C8T6"
FLASH_BASE = 0x08000000
MAX_IMAGE_SIZE = 64 * 1024
MAX_PACKAGE_SIZE = 128 * 1024
MAX_MANIFEST_SIZE = 8 * 1024
PACKAGE_ENTRIES = frozenset(
    {"firmware.bin", "manifest.json", "manifest.sig"}
)
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
HEX_16_PATTERN = re.compile(r"^[0-9a-f]{16}$")
HEX_64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
FIXED_FRAME_APPLICATION_PROTOCOL = "FIXED_FRAME"
NATIVE_APPLICATION_PROTOCOL = "ECOBIN_UART"


class FirmwarePackageError(ValueError):
    """The firmware package is malformed, incompatible, or untrusted."""


@dataclass(frozen=True)
class VerifiedFirmwarePackage:
    manifest: dict
    image: bytes
    package_sha256: str
    package_size: int

    @property
    def version(self) -> str:
        return str(self.manifest["firmwareVersion"])

    @property
    def version_code(self) -> int:
        return int(self.manifest["firmwareVersionCode"])


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_plain_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise FirmwarePackageError(f"{name} must be an integer")
    return value


def validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise FirmwarePackageError("manifest must be a JSON object")
    required = {
        "schemaVersion",
        "releaseUid",
        "mcuPartNumber",
        "hardwareCompatibility",
        "firmwareVersion",
        "firmwareVersionCode",
        "fixedFrameRevision",
        "flashBase",
        "imageSize",
        "imageSha256",
        "firmwareIdentityHex",
        "buildCommit",
        "builtAt",
        "signingKeyId",
    }
    if set(manifest) != required:
        missing = sorted(required - set(manifest))
        extra = sorted(set(manifest) - required)
        raise FirmwarePackageError(
            f"manifest fields differ: missing={missing}, extra={extra}"
        )
    if _require_plain_int(manifest["schemaVersion"], "schemaVersion") != 1:
        raise FirmwarePackageError("unsupported manifest schemaVersion")
    try:
        release_uid = uuid.UUID(str(manifest["releaseUid"]))
    except (ValueError, TypeError) as error:
        raise FirmwarePackageError("releaseUid must be a UUID") from error
    if release_uid.version != 4:
        raise FirmwarePackageError("releaseUid must be a UUIDv4")
    if manifest["mcuPartNumber"] != MCU_PART_NUMBER:
        raise FirmwarePackageError("mcuPartNumber is not STM32F103C8T6")
    compatibility = manifest["hardwareCompatibility"]
    if (
        not isinstance(compatibility, str)
        or not 1 <= len(compatibility) <= 64
        or compatibility.strip() != compatibility
    ):
        raise FirmwarePackageError("hardwareCompatibility is invalid")
    version = manifest["firmwareVersion"]
    if (
        not isinstance(version, str)
        or len(version.encode("ascii", errors="ignore")) != len(version)
        or len(version) > 32
        or not SEMVER_PATTERN.fullmatch(version)
    ):
        raise FirmwarePackageError("firmwareVersion must be ASCII SemVer")
    version_code = _require_plain_int(
        manifest["firmwareVersionCode"], "firmwareVersionCode"
    )
    if not 1 <= version_code <= 0xFFFFFFFF:
        raise FirmwarePackageError("firmwareVersionCode is outside uint32")
    if _require_plain_int(
        manifest["fixedFrameRevision"], "fixedFrameRevision"
    ) != 2:
        raise FirmwarePackageError("fixedFrameRevision must be 2")
    if _require_plain_int(manifest["flashBase"], "flashBase") != FLASH_BASE:
        raise FirmwarePackageError("flashBase must be 0x08000000")
    image_size = _require_plain_int(manifest["imageSize"], "imageSize")
    if not 1 <= image_size <= MAX_IMAGE_SIZE:
        raise FirmwarePackageError("imageSize exceeds STM32F103C8T6 flash")
    if not isinstance(manifest["imageSha256"], str) or not HEX_64_PATTERN.fullmatch(
        manifest["imageSha256"]
    ):
        raise FirmwarePackageError("imageSha256 must be lowercase hex")
    if not isinstance(
        manifest["firmwareIdentityHex"], str
    ) or not HEX_16_PATTERN.fullmatch(manifest["firmwareIdentityHex"]):
        raise FirmwarePackageError("firmwareIdentityHex must be 8-byte hex")
    build_commit = manifest["buildCommit"]
    if (
        not isinstance(build_commit, str)
        or not 7 <= len(build_commit) <= 64
        or not re.fullmatch(r"[0-9a-f]+", build_commit)
    ):
        raise FirmwarePackageError("buildCommit must be lowercase git hex")
    try:
        built_at = datetime.fromisoformat(
            str(manifest["builtAt"]).replace("Z", "+00:00")
        )
    except ValueError as error:
        raise FirmwarePackageError("builtAt must be an RFC3339 timestamp") from error
    if built_at.tzinfo is None:
        raise FirmwarePackageError("builtAt must contain an offset")
    key_id = manifest["signingKeyId"]
    if not isinstance(key_id, str) or not KEY_ID_PATTERN.fullmatch(key_id):
        raise FirmwarePackageError("signingKeyId is invalid")
    return dict(manifest)


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise FirmwarePackageError("signing key is not Ed25519")
    return key


def load_public_key(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise FirmwarePackageError("verification key is not Ed25519")
    return key


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if not hasattr(os, "fchmod"):
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def generate_identity(
    *,
    version: str,
    version_code: int,
    header_path: Path,
    metadata_path: Path,
    application_protocol_family: str,
    release_uid: uuid.UUID | None = None,
) -> dict:
    if not SEMVER_PATTERN.fullmatch(version) or len(version) > 32:
        raise FirmwarePackageError("version must be ASCII SemVer up to 32 chars")
    if not 1 <= version_code <= 0xFFFFFFFF:
        raise FirmwarePackageError("version code is outside uint32")
    if application_protocol_family not in {
        FIXED_FRAME_APPLICATION_PROTOCOL,
        NATIVE_APPLICATION_PROTOCOL,
    }:
        raise FirmwarePackageError("application protocol family is invalid")
    release_uid = release_uid or uuid.uuid4()
    if release_uid.version != 4:
        raise FirmwarePackageError("release UID must be UUIDv4")
    identity = hashlib.sha256(release_uid.bytes).digest()[:8]
    byte_list = ", ".join(f"0x{value:02X}" for value in identity)
    header = (
        "#ifndef __ECOBIN_FIRMWARE_IDENTITY_H\n"
        "#define __ECOBIN_FIRMWARE_IDENTITY_H\n\n"
        "/* Generated by hardware/mcu_firmware_package.py. */\n"
        f'#define ECOBIN_MCU_FIRMWARE_VERSION       "{version}"\n'
        f"#define ECOBIN_MCU_FIRMWARE_VERSION_CODE  {version_code}UL\n"
        "#define ECOBIN_MCU_FIRMWARE_IDENTITY_BYTES \\\n"
        f"    {{{byte_list}}}\n\n"
        "#endif /* __ECOBIN_FIRMWARE_IDENTITY_H */\n"
    ).encode("ascii")
    metadata = {
        "releaseUid": str(release_uid),
        "firmwareVersion": version,
        "firmwareVersionCode": version_code,
        "firmwareIdentityHex": identity.hex(),
        "applicationProtocolFamily": application_protocol_family,
    }
    _atomic_write(header_path, header)
    _atomic_write(metadata_path, canonical_json(metadata))
    return metadata


def create_package(
    *,
    image_path: Path,
    identity_metadata_path: Path,
    private_key_path: Path,
    key_id: str,
    hardware_compatibility: str,
    build_commit: str,
    built_at: str,
    output_path: Path,
) -> VerifiedFirmwarePackage:
    image = image_path.read_bytes()
    if not 1 <= len(image) <= MAX_IMAGE_SIZE:
        raise FirmwarePackageError("binary image is empty or exceeds 64 KiB")
    identity = json.loads(identity_metadata_path.read_text(encoding="utf-8"))
    if identity.get("applicationProtocolFamily") != FIXED_FRAME_APPLICATION_PROTOCOL:
        raise FirmwarePackageError(
            "schemaVersion 1 .efw packages support only legacy FIXED_FRAME firmware; "
            "ECOBIN_UART firmware remote rollout is not implemented"
        )
    manifest = validate_manifest(
        {
            "schemaVersion": 1,
            "releaseUid": identity.get("releaseUid"),
            "mcuPartNumber": MCU_PART_NUMBER,
            "hardwareCompatibility": hardware_compatibility,
            "firmwareVersion": identity.get("firmwareVersion"),
            "firmwareVersionCode": identity.get("firmwareVersionCode"),
            "fixedFrameRevision": 2,
            "flashBase": FLASH_BASE,
            "imageSize": len(image),
            "imageSha256": hashlib.sha256(image).hexdigest(),
            "firmwareIdentityHex": identity.get("firmwareIdentityHex"),
            "buildCommit": build_commit,
            "builtAt": built_at,
            "signingKeyId": key_id,
        }
    )
    manifest_bytes = canonical_json(manifest)
    signature = _load_private_key(private_key_path).sign(manifest_bytes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent
    )
    os.close(fd)
    temp_path = Path(temporary)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in (
                ("firmware.bin", image),
                ("manifest.json", manifest_bytes),
                ("manifest.sig", signature),
            ):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o600 << 16
                archive.writestr(info, data)
        if temp_path.stat().st_size > MAX_PACKAGE_SIZE:
            raise FirmwarePackageError("firmware package exceeds 128 KiB")
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    public_key = _load_private_key(private_key_path).public_key()
    return verify_package(
        output_path,
        {key_id: public_key},
        expected_hardware_compatibility=hardware_compatibility,
    )


def verify_package(
    package_path: Path,
    public_keys: Mapping[str, Ed25519PublicKey],
    *,
    expected_hardware_compatibility: str | None = None,
) -> VerifiedFirmwarePackage:
    package_size = package_path.stat().st_size
    if not 1 <= package_size <= MAX_PACKAGE_SIZE:
        raise FirmwarePackageError("firmware package size is invalid")
    package_bytes = package_path.read_bytes()
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != 3 or set(names) != PACKAGE_ENTRIES:
                raise FirmwarePackageError("package must contain exactly three entries")
            for info in infos:
                if info.is_dir() or info.flag_bits & 0x1:
                    raise FirmwarePackageError("directories/encrypted entries are forbidden")
                if info.filename == "firmware.bin" and info.file_size > MAX_IMAGE_SIZE:
                    raise FirmwarePackageError("firmware.bin exceeds 64 KiB")
                if info.filename == "manifest.json" and info.file_size > MAX_MANIFEST_SIZE:
                    raise FirmwarePackageError("manifest.json is too large")
                if info.filename == "manifest.sig" and info.file_size != 64:
                    raise FirmwarePackageError("manifest.sig must be 64 bytes")
            image = archive.read("firmware.bin")
            manifest_bytes = archive.read("manifest.json")
            signature = archive.read("manifest.sig")
    except (zipfile.BadZipFile, KeyError) as error:
        raise FirmwarePackageError("firmware package is not a valid .efw") from error
    try:
        decoded = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FirmwarePackageError("manifest.json is not valid UTF-8 JSON") from error
    manifest = validate_manifest(decoded)
    if canonical_json(manifest) != manifest_bytes:
        raise FirmwarePackageError("manifest.json is not canonical JSON")
    key = public_keys.get(manifest["signingKeyId"])
    if key is None:
        raise FirmwarePackageError("manifest signing key is not trusted")
    try:
        key.verify(signature, manifest_bytes)
    except InvalidSignature as error:
        raise FirmwarePackageError("manifest signature is invalid") from error
    if len(image) != manifest["imageSize"]:
        raise FirmwarePackageError("firmware.bin size differs from manifest")
    if hashlib.sha256(image).hexdigest() != manifest["imageSha256"]:
        raise FirmwarePackageError("firmware.bin SHA-256 differs from manifest")
    if (
        expected_hardware_compatibility is not None
        and manifest["hardwareCompatibility"]
        != expected_hardware_compatibility
    ):
        raise FirmwarePackageError("firmware package targets another board")
    return VerifiedFirmwarePackage(
        manifest=manifest,
        image=image,
        package_sha256=hashlib.sha256(package_bytes).hexdigest(),
        package_size=package_size,
    )


def _parse_public_keys(values: Sequence[str]) -> dict[str, Ed25519PublicKey]:
    keys: dict[str, Ed25519PublicKey] = {}
    for value in values:
        if "=" not in value:
            raise FirmwarePackageError("public key must use KEY_ID=PATH")
        key_id, path = value.split("=", 1)
        if not KEY_ID_PATTERN.fullmatch(key_id) or key_id in keys:
            raise FirmwarePackageError("public key ID is invalid or duplicated")
        keys[key_id] = load_public_key(Path(path))
    return keys


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    identity = subparsers.add_parser("identity")
    identity.add_argument("--version", required=True)
    identity.add_argument("--version-code", required=True, type=int)
    identity.add_argument(
        "--application-protocol-family",
        choices=(FIXED_FRAME_APPLICATION_PROTOCOL, NATIVE_APPLICATION_PROTOCOL),
        required=True,
    )
    identity.add_argument("--release-uid", type=uuid.UUID)
    identity.add_argument(
        "--header",
        type=Path,
        default=Path("hardware_mcu/USER/firmware_identity.h"),
    )
    identity.add_argument("--metadata", type=Path, required=True)

    package = subparsers.add_parser("package")
    package.add_argument("--bin", dest="image", type=Path, required=True)
    package.add_argument("--identity", type=Path, required=True)
    package.add_argument("--private-key", type=Path, required=True)
    package.add_argument("--key-id", required=True)
    package.add_argument("--hardware-compatibility", required=True)
    package.add_argument("--build-commit", required=True)
    package.add_argument("--built-at", required=True)
    package.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("package", type=Path)
    verify.add_argument("--public-key", action="append", required=True)
    verify.add_argument("--hardware-compatibility")

    args = parser.parse_args(argv)
    if args.command == "identity":
        result = generate_identity(
            version=args.version,
            version_code=args.version_code,
            header_path=args.header,
            metadata_path=args.metadata,
            application_protocol_family=args.application_protocol_family,
            release_uid=args.release_uid,
        )
    elif args.command == "package":
        verified = create_package(
            image_path=args.image,
            identity_metadata_path=args.identity,
            private_key_path=args.private_key,
            key_id=args.key_id,
            hardware_compatibility=args.hardware_compatibility,
            build_commit=args.build_commit,
            built_at=args.built_at,
            output_path=args.output,
        )
        result = {
            "packageSha256": verified.package_sha256,
            "packageSize": verified.package_size,
            "manifest": verified.manifest,
        }
    else:
        verified = verify_package(
            args.package,
            _parse_public_keys(args.public_key),
            expected_hardware_compatibility=args.hardware_compatibility,
        )
        result = {
            "packageSha256": verified.package_sha256,
            "packageSize": verified.package_size,
            "manifest": verified.manifest,
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
