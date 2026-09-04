"""Project the enrollment bundle into permanent least-privilege files."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Callable

try:
    import pwd
except ImportError:  # pragma: no cover - production target is Linux
    pwd = None

from business_identity import (
    DEFAULT_BUSINESS_IDENTITY_PATH,
    validate_business_identity_document,
)
from communication_credentials import (
    DEFAULT_COMMUNICATION_CREDENTIALS_PATH,
    validate_communication_credentials_document,
)
from device_credentials import (
    DEFAULT_CREDENTIALS_PATH,
    DeviceCredentials,
    load_device_credentials,
)


COMMUNICATION_USER = "ecobin-communication"
BUSINESS_USER = "ecobin-business"


def build_communication_credentials_document(
    bundle: DeviceCredentials,
) -> dict:
    one_net = bundle.one_net
    document = {
        "schemaVersion": 1,
        "productId": one_net.product_id,
        "deviceName": one_net.device_name,
        "deviceKey": one_net.device_key,
        "mqttHost": one_net.mqtt_host,
        "mqttPort": one_net.mqtt_port,
    }
    validate_communication_credentials_document(document)
    return document


def build_business_identity_document(bundle: DeviceCredentials) -> dict:
    document = {
        "schemaVersion": 1,
        "assetUid": bundle.asset_uid,
        "deviceName": bundle.hardware_sn,
        "modelCode": bundle.model_code,
        "expectedPortCount": bundle.expected_port_count,
        "deviceEntryUrl": bundle.device_entry_url,
    }
    validate_business_identity_document(document)
    return document


def install_device_runtime_projections(
    *,
    source_path: str | os.PathLike[str] = DEFAULT_CREDENTIALS_PATH,
    communication_target: str | os.PathLike[str] = (
        DEFAULT_COMMUNICATION_CREDENTIALS_PATH
    ),
    business_target: str | os.PathLike[str] = DEFAULT_BUSINESS_IDENTITY_PATH,
    user_lookup: Callable[[str], tuple[int, int] | None] | None = None,
) -> None:
    bundle = load_device_credentials(source_path, required=True)
    if bundle is None:  # required=True already raises
        raise ValueError("device credentials are required")
    lookup = user_lookup or _lookup_user
    communication_owner = _require_non_root_user(lookup, COMMUNICATION_USER)
    business_owner = _require_non_root_user(lookup, BUSINESS_USER)
    _atomic_write_owned_json(
        Path(communication_target),
        build_communication_credentials_document(bundle),
        owner=communication_owner,
        validator=validate_communication_credentials_document,
    )
    _atomic_write_owned_json(
        Path(business_target),
        build_business_identity_document(bundle),
        owner=business_owner,
        validator=validate_business_identity_document,
    )


def _require_non_root_user(
    lookup: Callable[[str], tuple[int, int] | None],
    name: str,
) -> tuple[int, int]:
    identity = lookup(name)
    if (
        identity is None
        or len(identity) != 2
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in identity
        )
        or identity[0] <= 0
        or identity[1] <= 0
    ):
        raise RuntimeError(
            f"required non-root service identity is unavailable: {name}"
        )
    return identity


def _require_private_parent(path: Path, owner: tuple[int, int]) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise RuntimeError(
            f"runtime projection directory is unavailable: {path}"
        ) from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise RuntimeError("runtime projection parent must be a real directory")
    # systemd applies the service unit's IPC Group= to StateDirectory.  The
    # directory remains accessible only to its owning UID because its mode is
    # exactly 0700, so requiring the account's primary GID would reject the
    # same least-privilege directory after an otherwise valid service start.
    if os.name == "posix" and (
        details.st_uid != owner[0]
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise RuntimeError(
            "runtime projection parent ownership or mode is unsafe"
        )


def _atomic_write_owned_json(
    destination: Path,
    document: dict,
    *,
    owner: tuple[int, int],
    validator: Callable[[dict], object],
) -> None:
    validator(document)
    _require_private_parent(destination.parent, owner)
    if os.path.lexists(destination):
        details = destination.lstat()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_nlink != 1
            or (
                os.name == "posix"
                and (
                    details.st_uid != owner[0]
                    or stat.S_IMODE(details.st_mode) != 0o600
                )
            )
        ):
            raise RuntimeError("existing runtime projection file is unsafe")
    raw = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    temporary = destination.with_name(
        f".{destination.name}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        if os.name == "posix":
            os.fchown(descriptor, owner[0], owner[1])
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    details = destination.lstat()
    if (
        not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
        or (
            os.name == "posix"
            and (
                details.st_uid != owner[0]
                or stat.S_IMODE(details.st_mode) != 0o600
            )
        )
    ):
        raise RuntimeError("persisted runtime projection ownership is invalid")
    persisted = json.loads(destination.read_text(encoding="utf-8"))
    if persisted != document:
        raise RuntimeError("persisted runtime projection content differs")
    validator(persisted)


def _fsync_directory(directory: Path) -> None:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError:
        if os.name == "posix":
            raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _lookup_user(name: str) -> tuple[int, int] | None:
    if pwd is None:
        return None
    try:
        record = pwd.getpwnam(name)
    except KeyError:
        return None
    return record.pw_uid, record.pw_gid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_CREDENTIALS_PATH)
    parser.add_argument(
        "--communication-target",
        default=DEFAULT_COMMUNICATION_CREDENTIALS_PATH,
    )
    parser.add_argument(
        "--business-target",
        default=DEFAULT_BUSINESS_IDENTITY_PATH,
    )
    args = parser.parse_args(argv)
    install_device_runtime_projections(
        source_path=args.source,
        communication_target=args.communication_target,
        business_target=args.business_target,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
