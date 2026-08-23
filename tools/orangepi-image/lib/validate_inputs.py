#!/usr/bin/env python3
"""Validate EcoBin Orange Pi image locks without third-party dependencies."""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
import urllib.parse
from dataclasses import dataclass
from typing import Any


HEX_64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
UUIDISH = re.compile(r"^[0-9A-Fa-f][0-9A-Fa-f-]{3,63}$")
PACKAGE = re.compile(
    r"^(?P<spec>(?P<name>[a-z0-9][a-z0-9+.-]*)"
    r"(?::(?P<arch>arm64|all))?="
    r"(?P<version>[0-9A-Za-z][0-9A-Za-z.+:~_-]*)) "
    r"sha256=(?P<sha256>[0-9a-f]{64})$"
)
RELEASE_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
UTC_SECONDS = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
MAX_QUALIFICATION_EVIDENCE_BYTES = 1024 * 1024
MINIMUM_PLAUSIBLE_32_GB_MEDIA_BYTES = 30_000_000_000


class ValidationError(RuntimeError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValidationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path} must contain one JSON object")
    return value


def _load_regular_json_bytes(path: pathlib.Path, context: str) -> tuple[bytes, dict[str, Any]]:
    try:
        before = path.lstat()
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > MAX_QUALIFICATION_EVIDENCE_BYTES
        ):
            raise ValidationError(f"{context} must be a bounded regular file with one link")
        payload = path.read_bytes()
        after = path.stat()
    except (OSError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            raise
        raise ValidationError(f"cannot read {context}: {exc}") from exc
    if (
        len(payload) != before.st_size
        or (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        raise ValidationError(f"{context} changed while it was being read")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise ValidationError(f"cannot parse {context}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{context} must contain one JSON object")
    return payload, value


def require_keys(
    value: dict[str, Any], required: set[str], context: str, *, schema: bool = False
) -> None:
    allowed = set(required)
    if schema:
        allowed.add("$schema")
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - allowed)
    if missing:
        raise ValidationError(f"{context} is missing keys: {', '.join(missing)}")
    if extra:
        raise ValidationError(f"{context} has unexpected keys: {', '.join(extra)}")


def require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{context} must be a non-empty string")
    return value


def require_positive_integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError(f"{context} must be a positive integer")
    return value


def require_nonnegative_integer(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValidationError(f"{context} must be a non-negative integer")
    return value


def require_lock_header(value: dict[str, Any], context: str) -> str:
    if value.get("schemaVersion") != 1:
        raise ValidationError(f"{context}.schemaVersion must be 1")
    state = value.get("lockState")
    if state not in {"LOCKED", "UNLOCKED"}:
        raise ValidationError(f"{context}.lockState must be LOCKED or UNLOCKED")
    require_string(value.get("reason"), f"{context}.reason")
    return state


def _reject_mutable_marker(value: str, context: str) -> None:
    lowered = value.lower()
    if "latest" in lowered or lowered == "snapshot" or lowered == "unlocked":
        raise ValidationError(f"{context} contains a mutable or placeholder marker")


def validate_source(source: dict[str, Any]) -> str:
    require_keys(
        source,
        {"schemaVersion", "lockState", "reason", "board", "operatingSystem", "artifact"},
        "source.lock.json",
        schema=True,
    )
    state = require_lock_header(source, "source.lock.json")

    board = source.get("board")
    if not isinstance(board, dict):
        raise ValidationError("source.lock.json.board must be an object")
    require_keys(
        board,
        {"vendor", "model", "hardwareRevision", "soc", "architecture"},
        "source.lock.json.board",
    )
    expected_board = {
        "vendor": "Orange Pi",
        "model": "Orange Pi Zero 3",
        "hardwareRevision": "v1.2",
        "soc": "Allwinner H618",
        "architecture": "arm64",
    }
    if board != expected_board:
        raise ValidationError("source lock does not target Orange Pi Zero 3 v1.2/H618 arm64")

    operating_system = source.get("operatingSystem")
    if not isinstance(operating_system, dict):
        raise ValidationError("source.lock.json.operatingSystem must be an object")
    require_keys(
        operating_system,
        {"distribution", "majorVersion", "variant", "kernelSeries"},
        "source.lock.json.operatingSystem",
    )
    if operating_system != {
        "distribution": "Debian",
        "majorVersion": 12,
        "variant": "Server",
        "kernelSeries": "6.1",
    }:
        raise ValidationError("source lock must use Debian 12 Server and Linux 6.1")

    artifact = source.get("artifact")
    if not isinstance(artifact, dict):
        raise ValidationError("source.lock.json.artifact must be an object")
    artifact_keys = {
        "url",
        "fileName",
        "sha256",
        "downloadBytes",
        "archiveFormat",
        "imageMember",
        "extractedImageBytes",
        "extractedImageSha256",
    }
    require_keys(artifact, artifact_keys, "source.lock.json.artifact")
    if state == "UNLOCKED":
        populated = sorted(key for key in artifact_keys if artifact[key] is not None)
        if populated:
            raise ValidationError(
                "unlocked source artifact must keep all identity fields null; populated: "
                + ", ".join(populated)
            )
        return state

    url = require_string(artifact["url"], "source artifact URL")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValidationError("source artifact URL must be credential-free HTTPS")
    _reject_mutable_marker(url, "source artifact URL")
    file_name = require_string(artifact["fileName"], "source artifact fileName")
    if pathlib.PurePosixPath(file_name).name != file_name:
        raise ValidationError("source artifact fileName must be a plain file name")
    digest = require_string(artifact["sha256"], "source artifact sha256")
    if not HEX_64.fullmatch(digest) or set(digest) == {"0"}:
        raise ValidationError("source artifact sha256 must be a non-zero lowercase SHA-256")
    require_positive_integer(artifact["downloadBytes"], "source artifact downloadBytes")
    extracted_bytes = require_positive_integer(
        artifact["extractedImageBytes"], "source artifact extractedImageBytes"
    )
    if extracted_bytes % 512 != 0:
        raise ValidationError("extracted image size must be a multiple of 512 bytes")
    extracted_digest = require_string(
        artifact["extractedImageSha256"], "source artifact extractedImageSha256"
    )
    if not HEX_64.fullmatch(extracted_digest) or set(extracted_digest) == {"0"}:
        raise ValidationError(
            "source artifact extractedImageSha256 must be a non-zero lowercase SHA-256"
        )
    archive_format = artifact["archiveFormat"]
    if archive_format not in {"raw", "gzip", "xz", "zstd", "7z"}:
        raise ValidationError("unsupported source artifact archiveFormat")
    image_member = artifact["imageMember"]
    if archive_format == "raw":
        if image_member is not None:
            raise ValidationError("raw source artifact must not declare imageMember")
    else:
        member = require_string(image_member, "source artifact imageMember")
        member_path = pathlib.PurePosixPath(member)
        if member_path.is_absolute() or ".." in member_path.parts or not member.endswith(".img"):
            raise ValidationError("archive imageMember must be a safe relative .img path")
    return state


def validate_builder(builder: dict[str, Any]) -> str:
    require_keys(
        builder,
        {
            "schemaVersion",
            "lockState",
            "reason",
            "container",
            "sourceDateEpoch",
            "uvArtifact",
            "tools",
        },
        "builder.lock",
        schema=True,
    )
    state = require_lock_header(builder, "builder.lock")
    container = builder.get("container")
    if not isinstance(container, dict):
        raise ValidationError("builder.lock.container must be an object")
    require_keys(
        container,
        {"reference", "digest", "platform"},
        "builder.lock.container",
    )
    tools = builder.get("tools")
    required_tools = {
        "bash",
        "caCertificates",
        "coreutils",
        "debianArchiveKeyring",
        "e2fsprogs",
        "fdisk",
        "findutils",
        "git",
        "grep",
        "gzip",
        "jq",
        "mawk",
        "mount",
        "openssl",
        "p7zip",
        "python",
        "qemuUserStatic",
        "sed",
        "utilLinux",
        "uv",
        "xz",
        "zstd",
    }
    if not isinstance(tools, dict):
        raise ValidationError("builder.lock.tools must be an object")
    require_keys(tools, required_tools, "builder.lock.tools")
    uv_artifact = builder.get("uvArtifact")
    uv_keys = {"version", "url", "fileName", "downloadBytes", "sha256"}
    if not isinstance(uv_artifact, dict):
        raise ValidationError("builder.lock.uvArtifact must be an object")
    require_keys(uv_artifact, uv_keys, "builder.lock.uvArtifact")

    if state == "UNLOCKED":
        if container != {
            "reference": None,
            "digest": None,
            "platform": None,
        }:
            raise ValidationError("unlocked builder container identity must remain null")
        if builder["sourceDateEpoch"] is not None:
            raise ValidationError("unlocked builder sourceDateEpoch must remain null")
        if any(value is not None for value in uv_artifact.values()):
            raise ValidationError("unlocked builder uv artifact identity must remain null")
        if any(value is not None for value in tools.values()):
            raise ValidationError("unlocked builder tool versions must remain null")
        return state

    reference = require_string(container["reference"], "builder container reference")
    digest = require_string(container["digest"], "builder container digest")
    if not DIGEST.fullmatch(digest) or digest == "sha256:" + ("0" * 64):
        raise ValidationError("builder digest must be a non-zero sha256 digest")
    if not reference.endswith("@" + digest):
        raise ValidationError("builder reference must end with its exact @sha256 digest")
    if container["platform"] != "linux/arm64":
        raise ValidationError("builder container platform must be linux/arm64")
    _reject_mutable_marker(reference, "builder container reference")
    epoch = builder["sourceDateEpoch"]
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ValidationError("builder sourceDateEpoch must be a non-negative integer")
    for tool_name, version in tools.items():
        exact_version = require_string(version, f"builder tool {tool_name}")
        _reject_mutable_marker(exact_version, f"builder tool {tool_name}")
        if any(marker in exact_version for marker in ("*", ">", "<", "^")):
            raise ValidationError(f"builder tool {tool_name} must use an exact version")
    uv_version = require_string(uv_artifact["version"], "builder uv version")
    if tools["uv"] != uv_version or not RELEASE_VERSION.fullmatch(uv_version):
        raise ValidationError("builder uv tool and artifact versions must match")
    uv_url = require_string(uv_artifact["url"], "builder uv artifact URL")
    parsed_uv_url = urllib.parse.urlparse(uv_url)
    if (
        parsed_uv_url.scheme != "https"
        or parsed_uv_url.netloc != "github.com"
        or parsed_uv_url.username
        or parsed_uv_url.password
        or f"/releases/download/{uv_version}/" not in parsed_uv_url.path
    ):
        raise ValidationError("builder uv artifact must use its versioned GitHub release URL")
    _reject_mutable_marker(uv_url, "builder uv artifact URL")
    uv_file_name = require_string(uv_artifact["fileName"], "builder uv fileName")
    if pathlib.PurePosixPath(uv_file_name).name != uv_file_name:
        raise ValidationError("builder uv fileName must be a plain file name")
    if not uv_url.endswith("/" + uv_file_name):
        raise ValidationError("builder uv URL and fileName differ")
    require_positive_integer(uv_artifact["downloadBytes"], "builder uv downloadBytes")
    uv_sha256 = require_string(uv_artifact["sha256"], "builder uv sha256")
    if not HEX_64.fullmatch(uv_sha256) or set(uv_sha256) == {"0"}:
        raise ValidationError("builder uv sha256 must be a non-zero lowercase SHA-256")
    return state


@dataclass(frozen=True)
class AptLock:
    state: str
    repository_snapshot: str | None
    repository_key_fingerprint: str | None
    packages: tuple[str, ...]
    package_sha256: tuple[tuple[str, str], ...]


def validate_apt_lock(path: pathlib.Path) -> AptLock:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValidationError(f"cannot read {path}: {exc}") from exc
    if not lines or lines[0] != "# ecobin-apt-packages-lock-v2":
        raise ValidationError("apt-packages.lock has an invalid format header")
    headers: dict[str, str] = {}
    packages: list[str] = []
    package_sha256: list[tuple[str, str]] = []
    names: set[str] = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        if line.startswith("# ") and ": " in line[2:]:
            key, value = line[2:].split(": ", 1)
            if key in headers:
                raise ValidationError(f"duplicate apt lock header: {key}")
            headers[key] = value
            continue
        if line.startswith("#"):
            continue
        match = PACKAGE.fullmatch(line)
        if line != line.strip() or match is None:
            raise ValidationError(f"invalid exact apt package entry: {line!r}")
        name = match.group("name")
        if name in names:
            raise ValidationError(f"duplicate apt package entry: {name}")
        names.add(name)
        spec = match.group("spec")
        digest = match.group("sha256")
        if set(digest) == {"0"}:
            raise ValidationError("apt package SHA-256 must be non-zero")
        packages.append(spec)
        package_sha256.append((spec, digest))
    required_headers = {
        "lock-state",
        "reason",
        "repository-snapshot",
        "repository-key-fingerprint",
    }
    missing_headers = sorted(required_headers - headers.keys())
    if missing_headers:
        raise ValidationError("apt lock is missing headers: " + ", ".join(missing_headers))
    state = headers["lock-state"]
    if state not in {"LOCKED", "UNLOCKED"}:
        raise ValidationError("apt lock-state must be LOCKED or UNLOCKED")
    require_string(headers["reason"], "apt lock reason")
    if state == "UNLOCKED":
        if packages:
            raise ValidationError("unlocked apt lock must not contain package entries")
        if (
            headers["repository-snapshot"] != "UNLOCKED"
            or headers["repository-key-fingerprint"] != "UNLOCKED"
        ):
            raise ValidationError("unlocked apt repository fields must be UNLOCKED")
        return AptLock(state, None, None, (), ())
    if not packages:
        raise ValidationError("locked apt lock must contain exact package versions")
    repository = headers["repository-snapshot"]
    parsed = urllib.parse.urlparse(repository)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValidationError("apt repository snapshot must be credential-free HTTPS")
    _reject_mutable_marker(repository, "apt repository snapshot")
    fingerprint = headers["repository-key-fingerprint"]
    if not re.fullmatch(r"(?:[0-9A-F]{40}|[0-9A-F]{64})", fingerprint):
        raise ValidationError("apt repository key fingerprint must be 40 or 64 uppercase hex digits")
    return AptLock(
        state,
        repository,
        fingerprint,
        tuple(packages),
        tuple(package_sha256),
    )


def validate_layout(
    layout: dict[str, Any], source: dict[str, Any], builder: dict[str, Any]
) -> str:
    require_keys(
        layout,
        {"schemaVersion", "lockState", "reason", "sourceGeometry", "targetMedia", "compactImage", "rootFilesystem", "firstBootExpansion"},
        "image-layout.json",
        schema=True,
    )
    if layout.get("schemaVersion") != 2:
        raise ValidationError("image-layout.json.schemaVersion must be 2")
    state = layout.get("lockState")
    if state not in {"LOCKED", "UNLOCKED"}:
        raise ValidationError("image-layout.json.lockState must be LOCKED or UNLOCKED")
    require_string(layout.get("reason"), "image-layout.json.reason")
    geometry = layout.get("sourceGeometry")
    target = layout.get("targetMedia")
    compact = layout.get("compactImage")
    root = layout.get("rootFilesystem")
    expansion = layout.get("firstBootExpansion")
    if not all(isinstance(value, dict) for value in (geometry, target, compact, root, expansion)):
        raise ValidationError("image layout sections must be JSON objects")
    require_keys(
        geometry,
        {"lockState", "rawImageBytes", "rawImageSha256", "partitionTableType", "diskIdentifier", "logicalSectorBytes", "bootPrefixBytes", "bootPrefixSha256", "rootPartition"},
        "image-layout.json.sourceGeometry",
    )
    require_keys(
        target,
        {"marketedCapacityGB", "marketedCapacityBytes", "qualificationState", "minimumQualifiedMediaBytes", "evidenceSha256"},
        "image-layout.json.targetMedia",
    )
    require_keys(
        compact,
        {"fixedRawImageBytes", "alignmentBytes", "tailSafetyBytes", "partitionTablePolicy"},
        "image-layout.json.compactImage",
    )
    require_keys(
        root,
        {"partitionNumber", "filesystemType", "filesystemLabel", "filesystemUuid", "partitionUuid", "mustBeFinalPartition", "buildProfile"},
        "image-layout.json.rootFilesystem",
    )
    require_keys(
        expansion,
        {"schemaVersion", "strategy", "minimumGrowthBytes", "failurePolicy", "idempotent", "identityBinding"},
        "image-layout.json.firstBootExpansion",
    )
    if target["marketedCapacityGB"] != 32 or target["marketedCapacityBytes"] != 32_000_000_000:
        raise ValidationError("image layout must target a marketed 32 GB medium")
    alignment = require_positive_integer(compact["alignmentBytes"], "layout alignmentBytes")
    if alignment < 512 or alignment & (alignment - 1):
        raise ValidationError("image alignmentBytes must be a power of two of at least 512")
    tail = require_positive_integer(compact["tailSafetyBytes"], "layout tailSafetyBytes")
    if tail < 16 * 1024 * 1024:
        raise ValidationError("image tailSafetyBytes must be at least 16 MiB")
    if compact["partitionTablePolicy"] != "locked-prefix-and-rebuilt-root":
        raise ValidationError("layout must preserve the locked prefix and rebuild the root filesystem")
    if root["filesystemType"] != "ext4" or root["filesystemLabel"] != "opi_root" or root["mustBeFinalPartition"] is not True:
        raise ValidationError("root filesystem must be final-partition ext4")
    expected_expansion = {
        "schemaVersion": 2,
        "strategy": "growpart-resize2fs",
        "minimumGrowthBytes": expansion.get("minimumGrowthBytes"),
        "failurePolicy": "fail-closed",
        "idempotent": True,
        "identityBinding": "mounted-root-maj-min-sysfs",
    }
    if expansion != expected_expansion:
        raise ValidationError("first-boot expansion contract has unsupported values")
    require_positive_integer(expansion["minimumGrowthBytes"], "minimumGrowthBytes")

    if geometry["lockState"] != "LOCKED":
        raise ValidationError("source geometry must be independently LOCKED")
    geometry_partition = geometry.get("rootPartition")
    if not isinstance(geometry_partition, dict):
        raise ValidationError("sourceGeometry.rootPartition must be an object")
    require_keys(geometry_partition, {"number", "startSector", "sectorCount", "typeCode", "mustBeFinalPartition"}, "sourceGeometry.rootPartition")
    raw_bytes = require_positive_integer(geometry["rawImageBytes"], "sourceGeometry.rawImageBytes")
    prefix_bytes = require_positive_integer(geometry["bootPrefixBytes"], "sourceGeometry.bootPrefixBytes")
    sector_bytes = require_positive_integer(geometry["logicalSectorBytes"], "sourceGeometry.logicalSectorBytes")
    start_sector = require_positive_integer(geometry_partition["startSector"], "sourceGeometry root startSector")
    sector_count = require_positive_integer(geometry_partition["sectorCount"], "sourceGeometry root sectorCount")
    if geometry["partitionTableType"] != "dos" or not re.fullmatch(r"[0-9a-f]{8}", str(geometry["diskIdentifier"])) or sector_bytes != 512:
        raise ValidationError("source DOS partition identity differs from the measured source")
    if geometry_partition["typeCode"] != "83" or geometry_partition["mustBeFinalPartition"] is not True:
        raise ValidationError("source root partition must be final Linux type 83")
    for key in ("rawImageSha256", "bootPrefixSha256"):
        digest = require_string(geometry[key], f"sourceGeometry.{key}")
        if not HEX_64.fullmatch(digest) or set(digest) == {"0"}:
            raise ValidationError(f"sourceGeometry.{key} must be a non-zero lowercase SHA-256")
    if prefix_bytes != start_sector * sector_bytes or raw_bytes != (start_sector + sector_count) * sector_bytes:
        raise ValidationError("locked prefix/root geometry does not cover the raw image exactly")

    fixed_bytes = require_positive_integer(compact["fixedRawImageBytes"], "fixedRawImageBytes")
    if fixed_bytes % alignment:
        raise ValidationError("fixed raw image size must use the declared alignment")
    partition_number = require_positive_integer(root["partitionNumber"], "root partitionNumber")
    if partition_number > 128:
        raise ValidationError("root partitionNumber is implausibly large")
    for key in ("filesystemUuid", "partitionUuid"):
        identifier = require_string(root[key], f"root {key}")
        if not UUIDISH.fullmatch(identifier):
            raise ValidationError(f"root {key} has an invalid format")
    if (
        source["lockState"] == "LOCKED"
        and source["artifact"]["extractedImageBytes"] != fixed_bytes
    ):
        raise ValidationError("source extracted size and fixed raw image size must be identical in P1")
    if raw_bytes != fixed_bytes or geometry_partition["number"] != partition_number:
        raise ValidationError("source geometry and rebuilt image layout differ")
    if source["lockState"] == "LOCKED" and source["artifact"]["extractedImageSha256"] != geometry["rawImageSha256"]:
        raise ValidationError("source raw image SHA-256 and source geometry differ")
    expected_partuuid = f'{geometry["diskIdentifier"]}-{partition_number:02d}'
    if root["partitionUuid"].lower() != expected_partuuid:
        raise ValidationError("root PARTUUID is not derived from the locked DOS disk identifier")
    profile = root.get("buildProfile")
    if not isinstance(profile, dict):
        raise ValidationError("rootFilesystem.buildProfile must be an object")
    profile_keys = {"sourceDateEpoch", "blockSize", "blockCount", "inodeCount", "inodeSize", "blocksPerGroup", "inodesPerGroup", "inodeBlocksPerGroup", "flexBlockGroupSize", "reservedBlockCount", "reservedPercent", "journalSizeBytes", "features", "defaultMountOptions", "directoryHashAlgorithm", "directoryHashSeed", "filesystemFlags", "errorBehavior", "maximumMountCount", "checkIntervalSeconds"}
    require_keys(profile, profile_keys, "rootFilesystem.buildProfile")
    for key in ("blockSize", "blockCount", "inodeCount", "inodeSize", "blocksPerGroup", "inodesPerGroup", "inodeBlocksPerGroup", "flexBlockGroupSize", "journalSizeBytes"):
        require_positive_integer(profile[key], f"rootFilesystem.buildProfile.{key}")
    require_nonnegative_integer(profile["sourceDateEpoch"], "rootFilesystem.buildProfile.sourceDateEpoch")
    reserved_count = require_nonnegative_integer(profile["reservedBlockCount"], "rootFilesystem.buildProfile.reservedBlockCount")
    reserved_percent = require_nonnegative_integer(profile["reservedPercent"], "rootFilesystem.buildProfile.reservedPercent")
    if reserved_percent > 50 or reserved_count != profile["blockCount"] * reserved_percent // 100:
        raise ValidationError("ext4 reserved block count/percent is inconsistent")
    if profile["journalSizeBytes"] > sector_count * sector_bytes:
        raise ValidationError("ext4 journal cannot exceed the root partition")
    if profile["blockCount"] * profile["blockSize"] != sector_count * sector_bytes:
        raise ValidationError("ext4 block geometry does not cover the root partition exactly")
    if profile["sourceDateEpoch"] != builder["sourceDateEpoch"] or profile["inodeSize"] != 256:
        raise ValidationError("ext4 deterministic epoch must match builder.lock and inode size must be 256")
    for key in ("features", "defaultMountOptions", "filesystemFlags"):
        values = profile[key]
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values) or len(set(values)) != len(values):
            raise ValidationError(f"ext4 {key} must contain unique non-empty strings")
    hash_seed = require_string(profile["directoryHashSeed"], "rootFilesystem.buildProfile.directoryHashSeed")
    if not UUIDISH.fullmatch(hash_seed):
        raise ValidationError("ext4 directory hash seed has an invalid format")
    if profile["features"] != ["has_journal", "ext_attr", "resize_inode", "dir_index", "filetype", "extent", "flex_bg", "sparse_super", "large_file", "huge_file", "dir_nlink", "extra_isize"]:
        raise ValidationError("ext4 feature list/order differs from the locked profile")
    if profile["defaultMountOptions"] != ["journal_data_writeback", "user_xattr", "acl"]:
        raise ValidationError("ext4 default mount options differ from the locked profile")
    if profile["directoryHashAlgorithm"] != "half_md4" or profile["filesystemFlags"] != ["signed_directory_hash"] or profile["errorBehavior"] != "Continue" or profile["maximumMountCount"] != -1 or profile["checkIntervalSeconds"] != 0:
        raise ValidationError("ext4 superblock policy differs from the locked profile")

    qualification = target["qualificationState"]
    if qualification == "UNQUALIFIED":
        if target["minimumQualifiedMediaBytes"] is not None or target["evidenceSha256"] is not None or state != "UNLOCKED":
            raise ValidationError("unqualified target media must keep evidence null and the top layout UNLOCKED")
    elif qualification == "QUALIFIED":
        minimum_media = require_positive_integer(target["minimumQualifiedMediaBytes"], "minimumQualifiedMediaBytes")
        evidence = require_string(target["evidenceSha256"], "targetMedia.evidenceSha256")
        if not HEX_64.fullmatch(evidence) or set(evidence) == {"0"}:
            raise ValidationError("target media evidence SHA-256 must be non-zero lowercase hex")
        if fixed_bytes + tail > minimum_media:
            raise ValidationError("fixed raw image does not leave the declared card safety margin")
        if state != "LOCKED":
            raise ValidationError("qualified target media requires the top layout LOCKED")
    else:
        raise ValidationError("targetMedia.qualificationState is invalid")
    return state


def validate_target_media_qualification_evidence(
    evidence_path: pathlib.Path | None,
    layout: dict[str, Any],
) -> dict[str, Any] | None:
    target = layout["targetMedia"]
    if target["qualificationState"] == "UNQUALIFIED":
        if evidence_path is not None:
            raise ValidationError(
                "target media qualification evidence must not be supplied for an UNQUALIFIED layout"
            )
        return None
    if evidence_path is None:
        raise ValidationError(
            "target media qualification evidence is required for a QUALIFIED layout"
        )

    payload, evidence = _load_regular_json_bytes(
        evidence_path,
        "target media qualification evidence",
    )
    actual_digest = hashlib.sha256(payload).hexdigest()
    if actual_digest != target["evidenceSha256"]:
        raise ValidationError(
            "target media qualification evidence digest differs from image layout"
        )
    require_keys(
        evidence,
        {
            "$schema",
            "schemaVersion",
            "artifactClass",
            "qualificationState",
            "method",
            "deviceClass",
            "marketedCapacityGB",
            "marketedCapacityBytes",
            "batchId",
            "measuredAt",
            "measurementTool",
            "sampleSelection",
            "minimumQualifiedMediaBytes",
            "measurements",
        },
        "target media qualification evidence",
    )
    if (
        evidence["$schema"]
        != "./schemas/target-media-qualification-evidence.schema.json"
        or evidence["schemaVersion"] != 1
        or evidence["artifactClass"]
        != "TARGET_MEDIA_BATCH_CAPACITY_QUALIFICATION"
        or evidence["qualificationState"] != "QUALIFIED"
        or evidence["method"] != "BLOCK_DEVICE_CAPACITY_SAMPLE_MINIMUM_V1"
        or evidence["deviceClass"] != "TF_CARD"
        or evidence["measurementTool"] != "blockdev --getsize64"
        or evidence["sampleSelection"]
        != "SAME_PROCUREMENT_BATCH_MULTIPLE_CARDS"
    ):
        raise ValidationError(
            "target media qualification evidence header or measurement method is invalid"
        )
    if (
        evidence["marketedCapacityGB"] != target["marketedCapacityGB"]
        or evidence["marketedCapacityBytes"] != target["marketedCapacityBytes"]
    ):
        raise ValidationError(
            "target media qualification evidence marketed capacity differs from image layout"
        )
    batch_id = require_string(evidence["batchId"], "target media evidence batchId")
    if not EVIDENCE_ID.fullmatch(batch_id):
        raise ValidationError("target media evidence batchId is malformed")
    measured_at = require_string(
        evidence["measuredAt"], "target media evidence measuredAt"
    )
    if not UTC_SECONDS.fullmatch(measured_at):
        raise ValidationError("target media evidence measuredAt must use UTC whole seconds")
    try:
        datetime.datetime.strptime(measured_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValidationError("target media evidence measuredAt is invalid") from exc

    measurements = evidence["measurements"]
    if not isinstance(measurements, list) or not 2 <= len(measurements) <= 256:
        raise ValidationError(
            "target media qualification requires at least two independently identified cards"
        )
    sample_ids: set[str] = set()
    measured_sizes: list[int] = []
    for index, measurement in enumerate(measurements):
        context = f"target media measurement {index}"
        if not isinstance(measurement, dict):
            raise ValidationError(f"{context} must be an object")
        require_keys(
            measurement,
            {"sampleId", "measuredBytes", "logicalSectorBytes", "wholeDevice"},
            context,
        )
        sample_id = require_string(measurement["sampleId"], f"{context}.sampleId")
        if not EVIDENCE_ID.fullmatch(sample_id) or sample_id in sample_ids:
            raise ValidationError(
                "target media sample IDs must be unique safe non-secret identifiers"
            )
        sample_ids.add(sample_id)
        measured_bytes = require_positive_integer(
            measurement["measuredBytes"], f"{context}.measuredBytes"
        )
        if (
            measurement["logicalSectorBytes"] != 512
            or measurement["wholeDevice"] is not True
            or measured_bytes % 512 != 0
        ):
            raise ValidationError(
                "target media measurements must describe whole 512-byte-sector devices"
            )
        if measured_bytes < MINIMUM_PLAUSIBLE_32_GB_MEDIA_BYTES:
            raise ValidationError(
                "target media measurement is implausibly small for marketed 32 GB media"
            )
        measured_sizes.append(measured_bytes)

    minimum = require_positive_integer(
        evidence["minimumQualifiedMediaBytes"],
        "target media evidence minimumQualifiedMediaBytes",
    )
    if minimum != min(measured_sizes):
        raise ValidationError(
            "target media evidence minimum does not equal the smallest sample"
        )
    if minimum != target["minimumQualifiedMediaBytes"]:
        raise ValidationError(
            "target media evidence minimum differs from image layout"
        )
    return evidence


def validate_repository_policy_template(policy: dict[str, Any]) -> None:
    require_keys(
        policy,
        {
            "schemaVersion",
            "lockState",
            "reason",
            "rootfsQualification",
            "builderReceipts",
            "buildAttestation",
            "sealEvidence",
            "releaseSigning",
        },
        "formal-release-policy.json",
        schema=True,
    )
    if (
        policy.get("schemaVersion") != 1
        or policy.get("lockState") != "UNLOCKED"
        or not isinstance(policy.get("reason"), str)
        or not policy["reason"].strip()
    ):
        raise ValidationError(
            "repository formal-release-policy.json is only an UNLOCKED template; "
            "the locked policy must remain external"
        )
    for role in ("buildAttestation", "sealEvidence", "releaseSigning"):
        if policy.get(role) != {"keyId": None, "publicKeySha256": None}:
            raise ValidationError(
                "repository formal release policy template must not contain trust roots"
            )
    if policy.get("rootfsQualification") != {
        "state": "UNQUALIFIED",
        "method": None,
        "evidenceSha256": None,
    }:
        raise ValidationError(
            "repository policy must keep deterministic rootfs formally unqualified"
        )
    if policy.get("builderReceipts") != []:
        raise ValidationError("repository policy must not contain builder receipt trust roots")


@dataclass(frozen=True)
class ValidatedInputs:
    config_dir: pathlib.Path
    source: dict[str, Any]
    builder: dict[str, Any]
    apt: AptLock
    layout: dict[str, Any]
    target_media_evidence: dict[str, Any] | None
    lock_states: tuple[str, str, str, str]


def validate_inputs(
    config_dir: pathlib.Path,
    *,
    require_locked: bool,
    target_media_evidence_path: pathlib.Path | None = None,
) -> ValidatedInputs:
    config_dir = config_dir.resolve()
    source = load_json(config_dir / "source.lock.json")
    builder = load_json(config_dir / "builder.lock")
    layout = load_json(config_dir / "image-layout.json")
    policy_template = load_json(config_dir / "formal-release-policy.json")
    source_state = validate_source(source)
    builder_state = validate_builder(builder)
    apt = validate_apt_lock(config_dir / "apt-packages.lock")
    layout_state = validate_layout(layout, source, builder)
    if target_media_evidence_path is None:
        environment_path = os.environ.get(
            "ECOBIN_TARGET_MEDIA_QUALIFICATION_EVIDENCE", ""
        ).strip()
        if environment_path:
            target_media_evidence_path = pathlib.Path(environment_path)
    target_media_evidence = validate_target_media_qualification_evidence(
        target_media_evidence_path,
        layout,
    )
    validate_repository_policy_template(policy_template)

    for name in (
        "source-lock.schema.json",
        "builder-lock.schema.json",
        "image-layout.schema.json",
        "image-manifest.schema.json",
        "software-payload-lock.schema.json",
        "release-manifest.schema.json",
        "formal-release-policy.schema.json",
        "build-attestation.schema.json",
        "build-receipt.schema.json",
        "rootfs-qualification-evidence.schema.json",
        "target-media-qualification-evidence.schema.json",
        "seal-evidence.schema.json",
    ):
        load_json(config_dir / "schemas" / name)
    states = (source_state, builder_state, apt.state, layout_state)
    if require_locked and any(state != "LOCKED" for state in states):
        labels = ("source", "builder", "apt", "layout")
        unlocked = [label for label, item_state in zip(labels, states) if item_state != "LOCKED"]
        raise ValidationError(
            "production image inputs are not locked: " + ", ".join(unlocked)
        )
    return ValidatedInputs(
        config_dir,
        source,
        builder,
        apt,
        layout,
        target_media_evidence,
        states,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[1],
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--require-locked", action="store_true")
    mode.add_argument("--allow-unlocked", action="store_true")
    parser.add_argument(
        "--target-media-qualification-evidence",
        type=pathlib.Path,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    require_locked = not args.allow_unlocked
    try:
        validated = validate_inputs(
            args.config_dir,
            require_locked=require_locked,
            target_media_evidence_path=args.target_media_qualification_evidence,
        )
    except ValidationError as exc:
        print(f"image-input-validation=FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        "image-input-validation=PASS "
        + " ".join(
            f"{name}={state}"
            for name, state in zip(
                ("source", "builder", "apt", "layout"), validated.lock_states
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
