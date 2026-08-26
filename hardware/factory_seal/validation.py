from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any
from urllib.parse import quote
import uuid

from .errors import FactorySealError


_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class FactorySealPaths:
    edge_store: Path = Path("/var/lib/ecobin/hardware/edge.db")
    image_release: Path = Path("/etc/ecobin/image-release.json")
    factory_report: Path = Path("/var/lib/ecobin/factory-test/report.json")
    factory_state: Path = Path("/var/lib/ecobin/factory-test/state.json")
    factory_photos: Path = Path("/run/ecobin/factory-test/photos")
    sealed: Path = Path("/var/lib/ecobin/first-boot/sealed.json")
    setup_ap_key: Path = Path("/etc/ecobin/setup-ap.key")
    enrollment_key: Path = Path("/etc/ecobin/enrollment.key")
    enrollment_state: Path = Path("/var/lib/ecobin/enrollment-state.json")
    enrollment_implementation: Path = Path(
        "/opt/ecobin/enrollment/device_enrollment.py"
    )
    credentials: Path = Path("/etc/ecobin/device-credentials.json")
    handoff_fact: Path = Path("/var/lib/ecobin/first-boot/handoff-safe.json")
    device_capabilities: Path = Path(
        "/var/lib/ecobin/device-capabilities.json"
    )


@dataclass(frozen=True)
class LocalFactoryFacts:
    image_release_id: str
    image_release_sha256: str
    factory_report_sha256: str

    def as_store_dict(self) -> dict[str, str]:
        return {
            "imageReleaseId": self.image_release_id,
            "imageReleaseSha256": self.image_release_sha256,
            "factoryReportSha256": self.factory_report_sha256,
        }


@dataclass(frozen=True)
class SealedAuthorizationFact:
    exists: bool
    valid: bool
    status_code: str
    authorization_state: str | None = None
    acceptance_generation: int | None = None
    authorization_binding_sha256: str | None = None


def collect_local_factory_facts(
    paths: FactorySealPaths = FactorySealPaths(),
    *,
    expected_hardware_sn: str | None = None,
) -> LocalFactoryFacts:
    if _path_entry_exists(paths.sealed):
        raise FactorySealError("FACTORY_ALREADY_SEALED")
    release = _read_json(paths.image_release, "IMAGE_RELEASE_INVALID")
    release_id = release.get("releaseId")
    if (
        release.get("schemaVersion") != 1
        or not isinstance(release_id, str)
        or not 1 <= len(release_id) <= 128
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in release_id)
    ):
        raise FactorySealError("IMAGE_RELEASE_INVALID")
    report = _read_json(paths.factory_report, "FACTORY_REPORT_INVALID")
    hardware_digest = report.get("hardwareConfigDigest")
    if (
        not isinstance(hardware_digest, str)
        or not valid_passed_factory_report(
            report,
            release_id=release_id,
            hardware_config_digest=hardware_digest,
        )
    ):
        raise FactorySealError("FACTORY_REPORT_INVALID")
    if expected_hardware_sn is not None:
        credentials = _read_json(
            paths.credentials,
            "DEVICE_CREDENTIALS_INVALID",
        )
        if (
            not isinstance(expected_hardware_sn, str)
            or not expected_hardware_sn
            or credentials.get("schemaVersion") != 1
            or credentials.get("hardwareSn") != expected_hardware_sn
        ):
            raise FactorySealError("DEVICE_CREDENTIALS_INVALID")
    return LocalFactoryFacts(
        image_release_id=release_id,
        image_release_sha256=_canonical_sha256(release),
        factory_report_sha256=_canonical_sha256(report),
    )


def authorization_binding_sha256(values: dict[str, Any]) -> str:
    return _canonical_sha256(values)


def inspect_sealed_authorization(
    paths: FactorySealPaths = FactorySealPaths(),
) -> SealedAuthorizationFact:
    """Read-only fact shared by first boot, cellular gating, and cleanup.

    ``valid`` means more than a correctly bound ``sealed.json`` marker.  A
    production-capable caller may trust it only after the authorization row
    and its exact ``FACTORY_SEAL_COMPLETED`` outbox event prove that cleanup
    committed atomically.  This deliberately keeps a migrated v15
    ``SEALED`` row with NULL completion columns fail-closed until the cleanup
    reconciler backfills the durable completion fact.
    """

    if not _path_entry_exists(paths.sealed):
        # A database that truly does not exist is the only state in which the
        # absence of the irreversible marker proves that this is an unsealed
        # first boot.  Once an edge database path exists, an unreadable file,
        # a missing v15 table, or a failed query is uncertainty rather than
        # evidence that sealing never started.  Fail closed so neither the
        # factory AP nor a cellular profile can be reopened after corruption.
        try:
            paths.edge_store.lstat()
        except FileNotFoundError:
            return SealedAuthorizationFact(False, False, "UNSEALED")
        except OSError:
            return SealedAuthorizationFact(
                True,
                False,
                "SEALED_FACT_INVALID",
            )
        try:
            row = _read_authorization_row(paths, command_uid=None)
        except _AuthorizationStoreError:
            return SealedAuthorizationFact(
                True,
                False,
                "SEALED_FACT_INVALID",
            )
        if row is not None and row["state"] in {"SEALING", "SEALED"}:
            return SealedAuthorizationFact(
                True,
                False,
                "SEALED_FACT_MISSING",
                authorization_state=row["state"],
                acceptance_generation=row["acceptance_generation"],
                authorization_binding_sha256=row[
                    "authorization_binding_sha256"
                ],
            )
        return SealedAuthorizationFact(False, False, "UNSEALED")
    try:
        sealed = _read_json(
            paths.sealed,
            "SEALED_FACT_INVALID",
            maximum_bytes=8192,
            required_mode=0o600,
        )
    except FactorySealError:
        return SealedAuthorizationFact(True, False, "SEALED_FACT_INVALID")
    command_uid = sealed.get("authorizationCommandUid")
    try:
        row = _read_authorization_row(paths, command_uid=command_uid)
    except _AuthorizationStoreError:
        return SealedAuthorizationFact(True, False, "SEALED_FACT_INVALID")
    if row is None:
        return SealedAuthorizationFact(True, False, "SEALED_FACT_INVALID")
    if not sealed_document_matches_authorization(sealed, row):
        return SealedAuthorizationFact(True, False, "SEALED_FACT_INVALID")
    cleanup_completed_at = _optional_row_value(
        row,
        "cleanup_completed_at",
    )
    completion_event_uid = _optional_row_value(
        row,
        "completion_event_uid",
    )
    if (
        row["state"] != "SEALED"
        or (
            cleanup_completed_at is None
            and completion_event_uid is None
        )
    ):
        return SealedAuthorizationFact(
            True,
            False,
            "SEALED_CLEANUP_PENDING",
            authorization_state=row["state"],
            acceptance_generation=row["acceptance_generation"],
            authorization_binding_sha256=row[
                "authorization_binding_sha256"
            ],
        )
    try:
        completion_event = _read_completion_event(
            paths,
            completion_event_uid,
        )
    except _AuthorizationStoreError:
        return SealedAuthorizationFact(True, False, "SEALED_FACT_INVALID")
    if not completion_event_matches_authorization(
        sealed,
        row,
        completion_event,
    ):
        return SealedAuthorizationFact(True, False, "SEALED_FACT_INVALID")
    return SealedAuthorizationFact(
        True,
        True,
        "SEALED",
        authorization_state=row["state"],
        acceptance_generation=row["acceptance_generation"],
        authorization_binding_sha256=row[
            "authorization_binding_sha256"
        ],
    )


def _read_authorization_row(
    paths: FactorySealPaths,
    *,
    command_uid: object | None,
) -> sqlite3.Row | None:
    try:
        details = paths.edge_store.lstat()
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise _AuthorizationStoreError
        database_path = paths.edge_store.absolute().as_posix()
        connection = sqlite3.connect(
            f"file:{quote(database_path, safe='/:')}?mode=ro",
            uri=True,
            timeout=2,
        )
        connection.row_factory = sqlite3.Row
        try:
            if command_uid is None:
                return connection.execute(
                    """SELECT * FROM factory_seal_authorization
                       WHERE state IN ('AUTHORIZED', 'SEALING', 'SEALED')
                       ORDER BY acceptance_generation DESC LIMIT 1"""
                ).fetchone()
            return connection.execute(
                """SELECT * FROM factory_seal_authorization
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
        finally:
            connection.close()
    except _AuthorizationStoreError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise _AuthorizationStoreError from error


class _AuthorizationStoreError(Exception):
    """The persisted one-way seal authority cannot be proven readable."""


def _read_completion_event(
    paths: FactorySealPaths,
    event_uid: object,
) -> sqlite3.Row | None:
    if not isinstance(event_uid, str):
        return None
    try:
        database_path = paths.edge_store.absolute().as_posix()
        connection = sqlite3.connect(
            f"file:{quote(database_path, safe='/:')}?mode=ro",
            uri=True,
            timeout=2,
        )
        connection.row_factory = sqlite3.Row
        try:
            return connection.execute(
                """SELECT event_uid, edge_event_sequence, event_type,
                          payload_json, work_uid
                   FROM event_outbox WHERE event_uid=?""",
                (event_uid,),
            ).fetchone()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as error:
        raise _AuthorizationStoreError from error


def sealed_document_matches_authorization(
    sealed: dict[str, Any],
    row: Any,
) -> bool:
    if row is None:
        return False
    v1_fields = {
        "schemaVersion",
        "status",
        "imageReleaseId",
        "hardwareIdentitySha256",
        "factoryReportSha256",
        "authorizationCommandUid",
        "acceptanceGeneration",
        "authorizationBindingSha256",
        "operatorConfirmationUid",
        "sealedAt",
    }
    v2_fields = v1_fields | {"sealedClockQuality"}
    try:
        schema_version = sealed.get("schemaVersion")
        valid_shape = (
            schema_version == 1 and set(sealed) == v1_fields
        ) or (
            schema_version == 2 and set(sealed) == v2_fields
            and sealed.get("sealedClockQuality")
            in {"SYNCED", "ESTIMATED", "UNAVAILABLE"}
        )
        return bool(
            valid_shape
            and type(sealed.get("schemaVersion")) is int
            and sealed.get("status") == "SEALED"
            and sealed.get("imageReleaseId") == row["image_release_id"]
            and sealed.get("hardwareIdentitySha256")
            == hashlib.sha256(
                row["hardware_sn"].encode("utf-8")
            ).hexdigest()
            and sealed.get("factoryReportSha256")
            == row["factory_report_sha256"]
            and sealed.get("authorizationCommandUid") == row["command_uid"]
            and type(sealed.get("acceptanceGeneration")) is int
            and sealed.get("acceptanceGeneration")
            == row["acceptance_generation"]
            and sealed.get("authorizationBindingSha256")
            == row["authorization_binding_sha256"]
            and _optional_row_value(row, "operator_confirmation_uid")
            in {None, sealed.get("operatorConfirmationUid")}
            and _optional_row_value(row, "confirmed_at")
            in {None, sealed.get("sealedAt")}
            and row["state"] in {"AUTHORIZED", "SEALING", "SEALED"}
            and _is_uuid4(sealed.get("authorizationCommandUid"))
            and _is_uuid4(sealed.get("operatorConfirmationUid"))
            and _is_utc_instant(sealed.get("sealedAt"))
        )
    except (KeyError, TypeError, AttributeError):
        return False


def factory_seal_completion_payload(
    sealed: dict[str, Any],
    row: Any,
    cleanup_completed_at: str,
    completion_clock_quality: str | None = None,
) -> dict[str, Any]:
    """Build the immutable payload bound to the accepted authorization."""

    schema_version = sealed.get("schemaVersion", 1)
    clock_quality = (
        completion_clock_quality
        or _optional_row_value(row, "completion_clock_quality")
        or "SYNCED"
    )
    trusted_times = schema_version == 1 or clock_quality == "SYNCED"
    payload = {
        "sealCompletionSchemaVersion": schema_version,
        "hardwareSn": row["hardware_sn"],
        "authorizationCommandUid": row["command_uid"],
        "acceptanceGeneration": row["acceptance_generation"],
        "acceptanceEvidenceUid": row["evidence_event_uid"],
        "acceptanceEvidenceSha256": row[
            "acceptance_evidence_sha256"
        ],
        "acceptanceChallengeUid": row["acceptance_challenge_uid"],
        "factoryBagRevision": row["factory_bag_revision"],
        "factoryBagSetSha256": row["factory_bag_set_sha256"],
        "imageReleaseId": row["image_release_id"],
        "imageReleaseSha256": row["image_release_sha256"],
        "factoryReportSha256": row["factory_report_sha256"],
        "authorizationBindingSha256": row[
            "authorization_binding_sha256"
        ],
        "operatorConfirmationUid": sealed["operatorConfirmationUid"],
        "sealedAt": sealed["sealedAt"] if trusted_times else None,
        "cleanupCompletedAt": (
            cleanup_completed_at if trusted_times else None
        ),
    }
    if schema_version >= 2:
        payload["completionClockQuality"] = clock_quality
    return payload


def completion_event_matches_authorization(
    sealed: dict[str, Any],
    row: Any,
    event_row: Any,
) -> bool:
    """Prove that one stored reliable fact is exactly bound to the seal."""

    if event_row is None:
        return False
    try:
        cleanup_completed_at = row["cleanup_completed_at"]
        completion_event_uid = row["completion_event_uid"]
        envelope = json.loads(event_row["payload_json"])
        payload = factory_seal_completion_payload(
            sealed,
            row,
            cleanup_completed_at,
        )
        schema_version = sealed.get("schemaVersion", 1)
        clock_quality = (
            _optional_row_value(row, "completion_clock_quality")
            or "SYNCED"
        )
        trusted_time = clock_quality == "SYNCED"
        return bool(
            row["state"] == "SEALED"
            and _is_utc_instant(cleanup_completed_at)
            and _is_uuid4(completion_event_uid)
            and row["operator_confirmation_uid"]
            == sealed["operatorConfirmationUid"]
            and row["confirmed_at"] == sealed["sealedAt"]
            and event_row["event_uid"] == completion_event_uid
            and event_row["event_type"] == "FACTORY_SEAL_COMPLETED"
            and event_row["work_uid"] == row["hardware_sn"]
            and isinstance(envelope, dict)
            and set(envelope)
            == {
                "schemaVersion",
                "eventUid",
                "edgeEventSequence",
                "eventType",
                "deliveryClass",
                "target",
                "commandUid",
                "occurredAt",
                "clockQuality",
                "payloadSha256",
                "payload",
            }
            and envelope["schemaVersion"] == 2
            and envelope["eventUid"] == completion_event_uid
            and type(envelope["edgeEventSequence"]) is int
            and envelope["edgeEventSequence"] > 0
            and envelope["edgeEventSequence"]
            == event_row["edge_event_sequence"]
            and envelope["eventType"] == "FACTORY_SEAL_COMPLETED"
            and envelope["deliveryClass"] == "RELIABLE_FACT"
            and envelope["target"]
            == {"type": "DEVICE_ASSET", "uid": row["hardware_sn"]}
            and envelope["commandUid"] == row["command_uid"]
            and envelope["clockQuality"] == clock_quality
            and (
                trusted_time
                and _is_utc_instant(envelope["occurredAt"])
                and envelope["occurredAt"] == cleanup_completed_at
                or not trusted_time
                and schema_version >= 2
                and envelope["occurredAt"] is None
            )
            and envelope["payload"] == payload
            and envelope["payloadSha256"] == _canonical_sha256(payload)
        )
    except (
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        return False


def _optional_row_value(row: Any, name: str) -> Any:
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return None


def valid_passed_factory_report(
    report: dict[str, Any],
    *,
    release_id: str,
    hardware_config_digest: str | None,
) -> bool:
    """One strict P7 PASSED gate shared by first boot and factory seal."""

    schema_version = report.get("schemaVersion")
    if (
        type(schema_version) is not int
        or schema_version not in {1, 2}
        or report.get("status") != "PASSED"
        or report.get("recoveryRequired") is not False
        or report.get("imageReleaseId") != release_id
        or hardware_config_digest is None
        or report.get("hardwareConfigDigest") != hardware_config_digest
        or _HEX_64.fullmatch(hardware_config_digest) is None
    ):
        return False
    identity = report.get("mcuIdentity")
    if (
        not isinstance(identity, dict)
        or identity.get("fixedFrameRevision") != 2
        or not isinstance(identity.get("firmwareVersion"), str)
        or not 5 <= len(identity["firmwareVersion"]) <= 32
        or not isinstance(identity.get("firmwareVersionCode"), int)
        or isinstance(identity.get("firmwareVersionCode"), bool)
        or identity["firmwareVersionCode"] <= 0
        or not isinstance(identity.get("firmwareIdentityHex"), str)
        or re.fullmatch(r"[0-9a-f]{16}", identity["firmwareIdentityHex"])
        is None
        or int(identity["firmwareIdentityHex"], 16) == 0
    ):
        return False
    checks = report.get("checks")
    expected_result_codes = {
        "mcu": "MCU_REVISION_2_AND_F1_HEALTHY",
        "weight": "WEIGHT_500G_WITHIN_490_510_AND_REMOVED",
        "delivery": "DELIVERY_SAFE_VERIFIED",
        "clean": "CLEAN_SAFE_VERIFIED",
    }
    if not isinstance(checks, dict) or any(
        not isinstance(checks.get(name), dict)
        or checks[name].get("status") != "PASSED"
        or checks[name].get("resultCode") != result_code
        for name, result_code in expected_result_codes.items()
    ):
        return False
    if checks["delivery"].get("operatorAreaSafeConfirmed") is not True:
        return False
    if checks["clean"].get("cleanDoorConfirmed") is not True:
        return False
    weight = checks["weight"]
    delta = weight.get("deltaGrams")
    if (
        weight.get("targetDeltaGrams") != 500
        or weight.get("toleranceGrams") != 10
        or not isinstance(delta, int)
        or isinstance(delta, bool)
        or not 490 <= delta <= 510
        or not isinstance(weight.get("emptyWeightGrams"), int)
        or isinstance(weight.get("emptyWeightGrams"), bool)
        or not isinstance(weight.get("loadedWeightGrams"), int)
        or isinstance(weight.get("loadedWeightGrams"), bool)
        or not isinstance(weight.get("removedWeightGrams"), int)
        or isinstance(weight.get("removedWeightGrams"), bool)
        or abs(weight["removedWeightGrams"] - weight["emptyWeightGrams"])
        > 10
        or weight.get("stableSampleCount") != 3
        or weight.get("stableMaxSpreadGrams") != 2
        or weight.get("sampleIntervalMs") != 100
        or weight.get("sampleTimeoutMs") != 3000
    ):
        return False
    upgrade = checks.get("upgradeLine")
    if not isinstance(upgrade, dict):
        return False
    update_capable = mcu_remote_update_capability(report)
    if update_capable is None:
        return False
    if update_capable:
        if (
            upgrade.get("status") != "PASSED"
            or upgrade.get("resultCode")
            != "F2_BOOT0_NRST_ROM_READ_ONLY_AND_APP_RECOVERY_PASSED"
            or (
                schema_version == 2
                and upgrade.get("prepareSendAttempts") != 1
            )
            or upgrade.get("romWritePerformed") is not False
            or upgrade.get("romDeviceId") != "0x0410"
        ):
            return False
    elif (
        schema_version != 2
        or upgrade.get("status") != "NOT_APPLICABLE"
        or upgrade.get("resultCode")
        != "MCU_REMOTE_UPDATE_LINE_NOT_INSTALLED"
        or upgrade.get("prepareSendAttempts") != 0
        or upgrade.get("romWritePerformed") is not False
        or upgrade.get("romDeviceId") is not None
    ):
        return False
    camera = report.get("cameraSummary")
    if (
        not isinstance(camera, dict)
        or camera.get("status") != "PASSED"
        or camera.get("resultCode")
        != "DUAL_CAMERA_FIXED_ROLES_PASSED"
    ):
        return False
    fingerprints: set[str] = set()
    for key, role in (("outside", "OUTSIDE"), ("inside", "INSIDE")):
        value = camera.get(key)
        if (
            not isinstance(value, dict)
            or value.get("role") != role
            or value.get("sourceKind") != "V4L2_BY_ID"
            or value.get("captureNonEmpty") is not True
            or value.get("operatorRoleConfirmed") is not True
            or not isinstance(value.get("sourceFingerprint"), str)
            or re.fullmatch(r"[0-9a-f]{12}", value["sourceFingerprint"])
            is None
        ):
            return False
        fingerprints.add(value["sourceFingerprint"])
    if len(fingerprints) != 2:
        return False
    for name in ("delivery", "clean"):
        action = checks[name]
        if (
            not isinstance(action.get("preWeightGrams"), int)
            or isinstance(action.get("preWeightGrams"), bool)
            or not isinstance(action.get("postWeightGrams"), int)
            or isinstance(action.get("postWeightGrams"), bool)
            or not isinstance(action.get("weightDeltaGrams"), int)
            or isinstance(action.get("weightDeltaGrams"), bool)
            or not isinstance(action.get("infraredBlocked"), bool)
        ):
            return False
    return True


def mcu_remote_update_capability(report: dict[str, Any]) -> bool | None:
    """Return the report-proven capability; schema 1 always proved ROM access."""

    schema_version = report.get("schemaVersion")
    if type(schema_version) is not int:
        return None
    if schema_version == 1:
        return True
    if schema_version != 2 or not isinstance(
        report.get("mcuRemoteUpdateCapable"), bool
    ):
        return None
    identity = report.get("mcuIdentity")
    if not isinstance(identity, dict):
        return None
    simulated_identity_marker = (
        identity.get("firmwareIdentityHex") == "45434f53494d3031"
    )
    simulated_version_marker = str(identity.get("firmwareVersion", "")).startswith(
        "factory-sim-"
    )
    exact_simulated_identity = bool(
        identity.get("fixedFrameRevision") == 2
        and identity.get("firmwareVersion") == "factory-sim-1.0.0"
        and identity.get("firmwareVersionCode") == 1
        and simulated_identity_marker
    )
    if (
        simulated_identity_marker or simulated_version_marker
    ) and not exact_simulated_identity:
        return None
    expected_mode = (
        "SIMULATED_PERIPHERALS" if exact_simulated_identity else "PHYSICAL"
    )
    if report.get("mcuPeripheralEvidenceMode") != expected_mode:
        return None
    return report["mcuRemoteUpdateCapable"]


def canonical_factory_report_sha256(report: dict[str, Any]) -> str:
    return _canonical_sha256(report)


def valid_device_capabilities(
    document: object,
    report: dict[str, Any],
) -> bool:
    capability = mcu_remote_update_capability(report)
    return bool(
        isinstance(document, dict)
        and set(document)
        == {
            "schemaVersion",
            "mcuRemoteUpdateCapable",
            "factoryReportSha256",
        }
        and type(document.get("schemaVersion")) is int
        and document.get("schemaVersion") == 1
        and isinstance(document.get("mcuRemoteUpdateCapable"), bool)
        and capability is not None
        and document.get("mcuRemoteUpdateCapable") is capability
        and document.get("factoryReportSha256")
        == canonical_factory_report_sha256(report)
    )


def _canonical_sha256(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(
    path: Path,
    error_code: str,
    *,
    maximum_bytes: int = 64 * 1024,
    required_mode: int | None = None,
) -> dict[str, Any]:
    try:
        before = path.lstat()
    except OSError:
        raise FactorySealError(error_code) from None
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_size < 2
        or before.st_size > maximum_bytes
        or (
            os.name != "nt"
            and required_mode is not None
            and stat.S_IMODE(before.st_mode) != required_mode
        )
    ):
        raise FactorySealError(error_code)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise FactorySealError(error_code) from None
    try:
        current = os.fstat(descriptor)
        if not stat.S_ISREG(current.st_mode) or not os.path.samestat(before, current):
            raise FactorySealError(error_code)
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            raw = stream.read(maximum_bytes + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(raw) > maximum_bytes:
        raise FactorySealError(error_code)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise FactorySealError(error_code) from None
    if not isinstance(value, dict):
        raise FactorySealError(error_code)
    return value


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except OSError:
        return False


def _is_uuid4(value: object) -> bool:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


def _is_utc_instant(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(
        parsed
    )


__all__ = [
    "FactorySealPaths",
    "LocalFactoryFacts",
    "SealedAuthorizationFact",
    "authorization_binding_sha256",
    "canonical_factory_report_sha256",
    "collect_local_factory_facts",
    "completion_event_matches_authorization",
    "factory_seal_completion_payload",
    "inspect_sealed_authorization",
    "mcu_remote_update_capability",
    "sealed_document_matches_authorization",
    "valid_passed_factory_report",
    "valid_device_capabilities",
]
