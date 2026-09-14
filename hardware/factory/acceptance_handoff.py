"""One-shot UART reset/drain proof before the production runtime may start."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from .acceptance_config import AcceptanceConfiguration
from .acceptance_hardware import (
    AcceptanceHardwareError,
    deny_network_access,
    identities_equal,
    sanitize_firmware_identity,
    sanitize_self_test,
)
from .native_acceptance_mcu import NativeAcceptanceMcu
from .acceptance_storage import AcceptanceLease, AtomicJsonFile
from factory_seal.validation import (
    canonical_factory_report_sha256,
    mcu_remote_update_capability,
    valid_passed_factory_report,
)


IMAGE_RELEASE_PATH = Path("/etc/ecobin/image-release.json")
HANDOFF_FACT_PATH = Path("/var/lib/ecobin/first-boot/handoff-safe.json")
DEVICE_CAPABILITIES_PATH = Path("/var/lib/ecobin/device-capabilities.json")
INSTANCE_LOCK_PATH = Path("/run/lock/ecobin/factory-handoff.lock")
UART_LOCK_PATH = Path("/run/lock/ecobin/uart5.lock")
BUSINESS_RUNTIME_GROUP = "ecobin-business"


def _resolve_business_capability_owner() -> tuple[int, int] | None:
    """Resolve the root-owned, business-group-readable publication owner."""

    if os.name == "nt":  # Unit tests do not have the image's system accounts.
        return None
    try:
        import grp

        group = grp.getgrnam(BUSINESS_RUNTIME_GROUP)
    except (ImportError, KeyError) as error:
        raise AcceptanceHardwareError(
            "BUSINESS_RUNTIME_GROUP_NOT_AVAILABLE"
        ) from error
    return (0, group.gr_gid)


def _read_release_id() -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(IMAGE_RELEASE_PATH, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 64 * 1024:
            raise ValueError("image release file is invalid")
        document = json.loads(os.read(descriptor, 64 * 1024 + 1).decode("utf-8"))
    finally:
        os.close(descriptor)
    value = (
        document.get("releaseId", document.get("imageReleaseId"))
        if isinstance(document, dict)
        else None
    )
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value
    ) is None:
        raise ValueError("image release ID is invalid")
    return value


def run_handoff(config: AcceptanceConfiguration) -> dict:
    report = AtomicJsonFile(config.report_path).read()
    release_id = _read_release_id()
    if not isinstance(report, dict) or not valid_passed_factory_report(
        report,
        release_id=release_id,
        hardware_config_digest=config.digest(),
    ):
        raise AcceptanceHardwareError("FACTORY_REPORT_NOT_VALID_FOR_HANDOFF")
    update_capable = mcu_remote_update_capability(report)
    if update_capable is None:
        raise AcceptanceHardwareError("FACTORY_REPORT_NOT_VALID_FOR_HANDOFF")
    if update_capable:
        raise AcceptanceHardwareError("MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED")
    expected_identity = report["mcuIdentity"]
    mcu = NativeAcceptanceMcu.for_port(
        config.serial_port,
        state_path=config.native_uart_state_path,
    )
    with AcceptanceLease(INSTANCE_LOCK_PATH, UART_LOCK_PATH):
        try:
            mcu.open()
            mcu.clear_input_for_recovery()
            mcu.require_business_quiet(quiet_ms=250)
            marker = mcu.business_input_marker()
            with deny_network_access():
                actual_identity = sanitize_firmware_identity(mcu.query_identity())
                sanitize_self_test(mcu.query_self_test())
            if not identities_equal(expected_identity, actual_identity):
                raise AcceptanceHardwareError("MCU_IDENTITY_CHANGED_AT_HANDOFF")
            mcu.require_business_quiet(quiet_ms=250, invalid_marker=marker)
        finally:
            mcu.close()
        capabilities = {
            "schemaVersion": 1,
            "mcuRemoteUpdateCapable": update_capable,
            "factoryReportSha256": canonical_factory_report_sha256(report),
        }
        # Commit capabilities first.  A power loss before the handoff fact
        # leaves runtime admission closed; retry deterministically replaces
        # the same report-bound document.
        capability_owner = _resolve_business_capability_owner()
        AtomicJsonFile(
            DEVICE_CAPABILITIES_PATH,
            chmod_existing_parent=False,
            file_mode=0o640,
            owner_uid=(capability_owner[0] if capability_owner else None),
            owner_gid=(capability_owner[1] if capability_owner else None),
        ).write(capabilities)
        fact = {
            "schemaVersion": 1,
            "status": "HANDOFF_SAFE",
            "imageReleaseId": release_id,
            "hardwareConfigDigest": config.digest(),
            "mcuIdentity": actual_identity,
        }
        AtomicJsonFile(HANDOFF_FACT_PATH).write(fact)
        return fact


def main() -> int:
    try:
        run_handoff(AcceptanceConfiguration.from_mapping(os.environ))
    except Exception as error:
        code = getattr(error, "code", "FACTORY_HANDOFF_FAILED")
        raise SystemExit(f"factory UART handoff failed: {code}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
