from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_COMPONENT_NAMES = (
    "hardwareRuntime",
    "enrollment",
    "remoteSupport",
    "factoryTest",
    "firstBoot",
    "communicationAgent",
    "deviceUpdater",
)
_IMAGE_RELEASE_FIELDS = frozenset(
    {
        "schemaVersion",
        "releaseId",
        "version",
        "gitCommit",
        "softwarePayloadId",
        "softwarePayloadLockSha256",
        "softwarePayloadSchemaVersion",
        "components",
        "contracts",
    }
)
_PAYLOAD_LOCK_FIELDS = frozenset(
    {
        "schemaVersion",
        "lockState",
        "payloadId",
        "sourceGitCommit",
        "components",
        "entries",
    }
)


@dataclass(frozen=True)
class ImageManagedLayerPaths:
    private_image_release: Path = Path("/etc/ecobin/image-release.json")
    public_image_release: Path = Path("/usr/share/ecobin/image-release.json")
    software_payload_lock: Path = Path(
        "/usr/share/ecobin/software-payload.lock.json"
    )
    release_environment: Path = Path(
        "/usr/share/ecobin/device-management-release.env"
    )


def image_managed_layer_complete(
    paths: ImageManagedLayerPaths = ImageManagedLayerPaths(),
    *,
    expected_owner: tuple[int, int] | None = (0, 0),
) -> bool:
    """Return whether this image contains its complete permanent layer.

    A field-maintenance installation has a separate, transactional active
    marker.  A newly built image cannot legitimately have that marker, so its
    proof is the immutable image inventory itself: the protected and public
    release documents must match, bind the installed payload lock, and name
    the same permanent component releases as the systemd environment file.
    Every uncertainty fails closed.
    """

    try:
        private_release_bytes = _read_regular_file(
            paths.private_image_release,
            maximum_bytes=64 * 1024,
            expected_owner=expected_owner,
        )
        public_release_bytes = _read_regular_file(
            paths.public_image_release,
            maximum_bytes=64 * 1024,
            expected_owner=expected_owner,
        )
        if private_release_bytes != public_release_bytes:
            return False
        release = _load_object(public_release_bytes)
        if (
            set(release) != _IMAGE_RELEASE_FIELDS
            or type(release.get("schemaVersion")) is not int
            or release["schemaVersion"] != 1
            or type(release.get("softwarePayloadSchemaVersion")) is not int
            or release["softwarePayloadSchemaVersion"] != 2
        ):
            return False

        lock_digest = release.get("softwarePayloadLockSha256")
        if not isinstance(lock_digest, str) or not _HEX_64.fullmatch(lock_digest):
            return False
        lock_bytes = _read_regular_file(
            paths.software_payload_lock,
            maximum_bytes=4 * 1024 * 1024,
            expected_owner=expected_owner,
        )
        if hashlib.sha256(lock_bytes).hexdigest() != lock_digest:
            return False
        lock = _load_object(lock_bytes)
        if (
            set(lock) != _PAYLOAD_LOCK_FIELDS
            or type(lock.get("schemaVersion")) is not int
            or lock["schemaVersion"] != 2
            or lock.get("lockState") != "LOCKED"
            or lock.get("payloadId") != release.get("softwarePayloadId")
            or lock.get("sourceGitCommit") != release.get("gitCommit")
        ):
            return False

        release_ids = _component_release_ids(release.get("components"), exact=True)
        lock_release_ids = _component_release_ids(lock.get("components"), exact=False)
        if release_ids is None or release_ids != lock_release_ids:
            return False
        communication_release = release_ids["communicationAgent"]
        updater_release = release_ids["deviceUpdater"]
        if len(communication_release) > 32 or len(updater_release) > 32:
            return False

        release_environment = _read_regular_file(
            paths.release_environment,
            maximum_bytes=4096,
            expected_owner=expected_owner,
        )
        return release_environment == (
            "ECOBIN_COMMUNICATION_AGENT_VERSION="
            f"{communication_release}\n"
            "ECOBIN_DEVICE_UPDATER_VERSION="
            f"{updater_release}\n"
        ).encode("ascii")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return False


def _read_regular_file(
    path: Path,
    *,
    maximum_bytes: int,
    expected_owner: tuple[int, int] | None,
) -> bytes:
    details = path.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_size <= 0
        or details.st_size > maximum_bytes
    ):
        raise ValueError("managed-layer fact is not a safe regular file")
    if (
        os.name == "posix"
        and (
            stat.S_IMODE(details.st_mode) != 0o644
            or (
                expected_owner is not None
                and (details.st_uid, details.st_gid) != expected_owner
            )
        )
    ):
        raise ValueError("managed-layer fact has unsafe metadata")
    value = path.read_bytes()
    if len(value) != details.st_size:
        raise ValueError("managed-layer fact changed while it was read")
    return value


def _load_object(value: bytes) -> dict[str, Any]:
    document = json.loads(value.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("managed-layer document is not an object")
    return document


def _component_release_ids(
    value: object,
    *,
    exact: bool,
) -> dict[str, str] | None:
    if not isinstance(value, dict) or set(value) != set(_COMPONENT_NAMES):
        return None
    release_ids: dict[str, str] = {}
    for name in _COMPONENT_NAMES:
        component = value.get(name)
        if (
            not isinstance(component, dict)
            or (exact and set(component) != {"releaseId"})
            or not isinstance(component.get("releaseId"), str)
            or not _RELEASE_ID.fullmatch(component["releaseId"])
        ):
            return None
        release_ids[name] = component["releaseId"]
    return release_ids
