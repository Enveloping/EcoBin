from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import uuid

import pytest

from edge_store import EdgeStore
from factory_seal.controller import FactorySealController, _runtime_healthy
from factory_seal.errors import FactorySealError
from factory_seal.validation import (
    FactorySealPaths,
    collect_local_factory_facts,
    inspect_sealed_authorization,
    valid_passed_factory_report,
)
from onenet_wire import canonical_payload_sha256, encode_event_post


HARDWARE_SN = "SN-FACTORY-SEAL-0001"


def _report(release_id: str) -> dict[str, object]:
    def camera(role: str, fingerprint: str) -> dict[str, object]:
        return {
            "role": role,
            "sourceKind": "V4L2_BY_ID",
            "sourceFingerprint": fingerprint,
            "captureNonEmpty": True,
            "operatorRoleConfirmed": True,
        }

    return {
        "schemaVersion": 1,
        "status": "PASSED",
        "recoveryRequired": False,
        "imageReleaseId": release_id,
        "hardwareConfigDigest": "a" * 64,
        "mcuIdentity": {
            "fixedFrameRevision": 2,
            "firmwareVersion": "1.2.3",
            "firmwareVersionCode": 0x010203,
            "firmwareIdentityHex": "0123456789abcdef",
        },
        "cameraSummary": {
            "status": "PASSED",
            "resultCode": "DUAL_CAMERA_FIXED_ROLES_PASSED",
            "outside": camera("OUTSIDE", "1" * 12),
            "inside": camera("INSIDE", "2" * 12),
        },
        "checks": {
            "mcu": {
                "status": "PASSED",
                "resultCode": "MCU_REVISION_2_AND_F1_HEALTHY",
            },
            "weight": {
                "status": "PASSED",
                "resultCode": "WEIGHT_500G_WITHIN_490_510_AND_REMOVED",
                "emptyWeightGrams": 1000,
                "loadedWeightGrams": 1500,
                "removedWeightGrams": 1000,
                "deltaGrams": 500,
                "targetDeltaGrams": 500,
                "toleranceGrams": 10,
                "stableSampleCount": 3,
                "stableMaxSpreadGrams": 2,
                "sampleIntervalMs": 100,
                "sampleTimeoutMs": 3000,
            },
            "upgradeLine": {
                "status": "PASSED",
                "resultCode": (
                    "F2_BOOT0_NRST_ROM_READ_ONLY_AND_APP_RECOVERY_PASSED"
                ),
                "romWritePerformed": False,
                "romDeviceId": "0x0410",
            },
            "delivery": {
                "status": "PASSED",
                "resultCode": "DELIVERY_SAFE_VERIFIED",
                "preWeightGrams": 1000,
                "postWeightGrams": 1200,
                "weightDeltaGrams": 200,
                "infraredBlocked": False,
            },
            "clean": {
                "status": "PASSED",
                "resultCode": "CLEAN_SAFE_VERIFIED",
                "preWeightGrams": 1200,
                "postWeightGrams": 100,
                "weightDeltaGrams": 1100,
                "infraredBlocked": False,
            },
        },
    }


def _paths(tmp_path: Path) -> FactorySealPaths:
    return FactorySealPaths(
        edge_store=tmp_path / "edge.db",
        image_release=tmp_path / "image-release.json",
        factory_report=tmp_path / "report.json",
        factory_state=tmp_path / "factory-state.json",
        factory_photos=tmp_path / "photos",
        sealed=tmp_path / "sealed.json",
        setup_ap_key=tmp_path / "setup-ap.key",
        enrollment_key=tmp_path / "enrollment.key",
        enrollment_state=tmp_path / "enrollment-state.json",
        enrollment_implementation=tmp_path / "device_enrollment.py",
        credentials=tmp_path / "credentials.json",
        handoff_fact=tmp_path / "handoff.json",
    )


def _authorized(tmp_path: Path) -> tuple[FactorySealPaths, str]:
    paths = _paths(tmp_path)
    release_id = "image-release-1"
    paths.image_release.write_text(
        json.dumps({"schemaVersion": 1, "releaseId": release_id}),
        encoding="utf-8",
    )
    paths.factory_report.write_text(
        json.dumps(_report(release_id)),
        encoding="utf-8",
    )
    paths.credentials.write_text(
        json.dumps({"schemaVersion": 1, "hardwareSn": HARDWARE_SN}),
        encoding="utf-8",
    )
    paths.handoff_fact.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "status": "HANDOFF_SAFE",
                "imageReleaseId": release_id,
            }
        ),
        encoding="utf-8",
    )
    paths.setup_ap_key.write_text("factory-password", encoding="utf-8")
    paths.factory_state.write_text("{}", encoding="utf-8")
    paths.factory_photos.mkdir()
    (paths.factory_photos / "probe.jpg").write_bytes(b"probe")
    store = EdgeStore(str(paths.edge_store))
    store.initialize()
    store.close()
    facts = collect_local_factory_facts(
        paths,
        expected_hardware_sn=HARDWARE_SN,
    )
    command_uid = str(uuid.uuid4())
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute(
            """INSERT INTO factory_seal_authorization (
                   command_uid, command_sha256, hardware_sn,
                   acceptance_generation, evidence_event_uid,
                   acceptance_challenge_uid, acceptance_evidence_sha256,
                   factory_bag_revision, factory_bag_set_sha256,
                   image_release_id, image_release_sha256,
                   factory_report_sha256,
                   authorization_binding_sha256, state, authorized_at
               ) VALUES (?, ?, ?, 1, ?, ?, ?, 2, ?, ?, ?, ?, ?,
                         'AUTHORIZED', ?)""",
            (
                command_uid,
                "3" * 64,
                HARDWARE_SN,
                str(uuid.uuid4()),
                str(uuid.uuid4()),
                "4" * 64,
                "5" * 64,
                facts.image_release_id,
                facts.image_release_sha256,
                facts.factory_report_sha256,
                "6" * 64,
                "2026-08-22T12:00:00Z",
            ),
        )
    return paths, command_uid


def _controller(
    paths: FactorySealPaths,
    *,
    stopped: list[str],
    production: list[str],
    emergency: list[str],
    fault_hook=None,
) -> FactorySealController:
    return FactorySealController(
        paths,
        runtime_healthy=lambda: True,
        stop_factory=lambda: stopped.append("stop") is None,
        apply_production_firewall=(
            lambda: production.append("production") is None
        ),
        apply_emergency_firewall=(
            lambda: emergency.append("emergency") is None
        ),
        fault_hook=fault_hook,
    )


def _completion_rows(
    paths: FactorySealPaths,
) -> tuple[sqlite3.Row, list[sqlite3.Row]]:
    with sqlite3.connect(paths.edge_store) as connection:
        connection.row_factory = sqlite3.Row
        authorization = connection.execute(
            """SELECT * FROM factory_seal_authorization
               ORDER BY acceptance_generation DESC LIMIT 1"""
        ).fetchone()
        events = connection.execute(
            """SELECT * FROM event_outbox
               WHERE event_type='FACTORY_SEAL_COMPLETED'
               ORDER BY edge_event_sequence"""
        ).fetchall()
    assert authorization is not None
    return authorization, events


def test_confirm_is_sealed_first_and_reconcile_finishes_cleanup(
    tmp_path: Path,
) -> None:
    paths, _ = _authorized(tmp_path)
    stopped: list[str] = []
    production: list[str] = []
    emergency: list[str] = []
    controller = _controller(
        paths,
        stopped=stopped,
        production=production,
        emergency=emergency,
    )

    assert controller.status()["statusCode"] == "SEAL_READY"
    controller.confirm(str(uuid.uuid4()))
    assert paths.sealed.is_file()
    pending = inspect_sealed_authorization(paths)
    assert not pending.valid
    assert pending.status_code == "SEALED_CLEANUP_PENDING"
    assert pending.authorization_state == "SEALING"
    with sqlite3.connect(paths.edge_store) as connection:
        assert connection.execute(
            """SELECT COUNT(*) FROM event_outbox
               WHERE event_type='FACTORY_SEAL_COMPLETED'"""
        ).fetchone()[0] == 0

    # Cleanup remains idempotently authoritative even if a residual
    # enrollment artifact appears after operator confirmation.
    paths.enrollment_key.write_text("factory-k1", encoding="utf-8")
    paths.enrollment_state.write_text("{}", encoding="utf-8")
    paths.enrollment_implementation.write_text("# factory", encoding="utf-8")

    assert controller.reconcile_cleanup() == "SEALED"
    assert stopped == ["stop"]
    assert production == ["production"]
    assert emergency == []
    assert not paths.setup_ap_key.exists()
    assert not paths.enrollment_key.exists()
    assert not paths.enrollment_state.exists()
    assert not paths.enrollment_implementation.exists()
    assert not paths.factory_state.exists()
    assert not any(paths.factory_photos.iterdir())
    completed = inspect_sealed_authorization(paths)
    assert completed.valid
    assert completed.authorization_state == "SEALED"


def test_power_loss_after_marker_is_recovered_only_toward_sealed(
    tmp_path: Path,
) -> None:
    paths, _ = _authorized(tmp_path)
    raised = False

    def fault(point: str) -> None:
        nonlocal raised
        if point == "after_sealed_directory_fsync" and not raised:
            raised = True
            raise RuntimeError("simulated power loss")

    first = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
        fault_hook=fault,
    )
    with pytest.raises(RuntimeError, match="simulated power loss"):
        first.confirm(str(uuid.uuid4()))
    assert paths.sealed.exists()

    stopped: list[str] = []
    production: list[str] = []
    recovered = _controller(
        paths,
        stopped=stopped,
        production=production,
        emergency=[],
    )
    assert recovered.reconcile_cleanup() == "SEALED"
    assert stopped == ["stop"]
    assert production == ["production"]


def test_status_does_not_trust_sealed_database_row_without_marker(
    tmp_path: Path,
) -> None:
    paths, _ = _authorized(tmp_path)
    controller = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
    )
    controller.confirm(str(uuid.uuid4()))
    assert controller.reconcile_cleanup() == "SEALED"
    authorization, events = _completion_rows(paths)
    assert authorization["state"] == "SEALED"
    assert len(events) == 1

    paths.sealed.unlink()

    status = controller.status()
    assert not status["authorized"]
    assert status["statusCode"] == "SEALED_FACT_MISSING"


def test_completion_fact_has_exact_binding_and_one_authoritative_instant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, command_uid = _authorized(tmp_path)
    controller = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
    )
    confirmation_uid = str(uuid.uuid4())
    controller.confirm(confirmation_uid)
    cleanup_at = "2026-08-22T12:01:00.123Z"
    monkeypatch.setattr(
        "factory_seal.controller._utc_now",
        lambda: cleanup_at,
    )
    # Deliberately make the generic envelope builder observe another
    # millisecond.  The persisted completion occurrence must still reuse the
    # cleanup transaction's authoritative timestamp exactly.
    monkeypatch.setattr(
        "onenet_wire.utc_now_rfc3339",
        lambda: "2026-08-22T12:01:00.124Z",
    )

    assert controller.reconcile_cleanup() == "SEALED"

    authorization, events = _completion_rows(paths)
    assert len(events) == 1
    event = json.loads(events[0]["payload_json"])
    sealed = json.loads(paths.sealed.read_text(encoding="utf-8"))
    expected_payload = {
        "sealCompletionSchemaVersion": 1,
        "hardwareSn": HARDWARE_SN,
        "authorizationCommandUid": command_uid,
        "acceptanceGeneration": 1,
        "acceptanceEvidenceUid": authorization["evidence_event_uid"],
        "acceptanceEvidenceSha256": authorization[
            "acceptance_evidence_sha256"
        ],
        "acceptanceChallengeUid": authorization[
            "acceptance_challenge_uid"
        ],
        "factoryBagRevision": authorization["factory_bag_revision"],
        "factoryBagSetSha256": authorization["factory_bag_set_sha256"],
        "imageReleaseId": authorization["image_release_id"],
        "imageReleaseSha256": authorization["image_release_sha256"],
        "factoryReportSha256": authorization["factory_report_sha256"],
        "authorizationBindingSha256": authorization[
            "authorization_binding_sha256"
        ],
        "operatorConfirmationUid": confirmation_uid,
        "sealedAt": sealed["sealedAt"],
        "cleanupCompletedAt": cleanup_at,
    }
    assert authorization["state"] == "SEALED"
    assert authorization["cleanup_completed_at"] == cleanup_at
    assert authorization["completion_event_uid"] == event["eventUid"]
    assert event["eventType"] == "FACTORY_SEAL_COMPLETED"
    assert event["deliveryClass"] == "RELIABLE_FACT"
    assert event["target"] == {
        "type": "DEVICE_ASSET",
        "uid": HARDWARE_SN,
    }
    assert event["commandUid"] == command_uid
    assert event["occurredAt"] == cleanup_at
    assert event["payload"] == expected_payload
    assert event["payloadSha256"] == canonical_payload_sha256(
        expected_payload
    )
    assert encode_event_post("FACTORY_SEAL_COMPLETED", event)["id"] == str(
        event["edgeEventSequence"]
    )


@pytest.mark.parametrize(
    "fault_point",
    ["after_completion_state_written", "after_completion_event_written"],
)
def test_completion_transaction_rolls_back_and_restart_emits_once(
    tmp_path: Path,
    fault_point: str,
) -> None:
    paths, _ = _authorized(tmp_path)
    raised = False

    def fault(point: str) -> None:
        nonlocal raised
        if point == fault_point and not raised:
            raised = True
            raise RuntimeError("simulated completion transaction loss")

    interrupted = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
        fault_hook=fault,
    )
    interrupted.confirm(str(uuid.uuid4()))
    with pytest.raises(
        RuntimeError,
        match="simulated completion transaction loss",
    ):
        interrupted.reconcile_cleanup()

    authorization, events = _completion_rows(paths)
    assert authorization["state"] == "SEALING"
    assert authorization["cleanup_completed_at"] is None
    assert authorization["completion_event_uid"] is None
    assert events == []

    restarted = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
    )
    assert restarted.reconcile_cleanup() == "SEALED"
    completed, first_events = _completion_rows(paths)
    assert len(first_events) == 1
    first_uid = completed["completion_event_uid"]
    first_payload = first_events[0]["payload_json"]

    # Any later boot repeats filesystem/firewall reconciliation but must
    # reuse the already committed UID and bytes rather than enqueue another
    # completion fact.
    after_second_restart = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
    )
    assert after_second_restart.reconcile_cleanup() == "SEALED"
    repeated, repeated_events = _completion_rows(paths)
    assert repeated["completion_event_uid"] == first_uid
    assert len(repeated_events) == 1
    assert repeated_events[0]["payload_json"] == first_payload


def test_no_completion_fact_before_stop_cleanup_and_firewall_all_succeed(
    tmp_path: Path,
) -> None:
    paths, _ = _authorized(tmp_path)
    confirmation_uid = str(uuid.uuid4())
    initial = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
    )
    initial.confirm(confirmation_uid)

    stop_failed = FactorySealController(
        paths,
        runtime_healthy=lambda: True,
        stop_factory=lambda: False,
        apply_production_firewall=lambda: True,
        apply_emergency_firewall=lambda: True,
    )
    assert stop_failed.reconcile_cleanup() == "FACTORY_SERVICES_STOP_FAILED"
    authorization, events = _completion_rows(paths)
    assert authorization["state"] == "SEALING"
    assert events == []

    firewall_failed = FactorySealController(
        paths,
        runtime_healthy=lambda: True,
        stop_factory=lambda: True,
        apply_production_firewall=lambda: False,
        apply_emergency_firewall=lambda: True,
    )
    assert firewall_failed.reconcile_cleanup() == "PRODUCTION_FIREWALL_FAILED"
    authorization, events = _completion_rows(paths)
    assert authorization["state"] == "SEALING"
    assert authorization["completion_event_uid"] is None
    assert events == []


def test_cleanup_error_cannot_publish_or_mark_sealed(tmp_path: Path) -> None:
    paths, _ = _authorized(tmp_path)
    controller = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
    )
    controller.confirm(str(uuid.uuid4()))
    # A directory where the root-owned K1 file must be is an invalid cleanup
    # shape.  Unlinking it fails before production firewall or completion DB
    # work, so neither side of the atomic completion pair can appear.
    paths.enrollment_key.mkdir()

    with pytest.raises(OSError):
        controller.reconcile_cleanup()

    authorization, events = _completion_rows(paths)
    assert authorization["state"] == "SEALING"
    assert authorization["cleanup_completed_at"] is None
    assert authorization["completion_event_uid"] is None
    assert events == []


def test_conflicting_operator_binding_is_rejected_before_cleanup(
    tmp_path: Path,
) -> None:
    paths, _ = _authorized(tmp_path)
    stopped: list[str] = []
    production: list[str] = []
    emergency: list[str] = []
    controller = _controller(
        paths,
        stopped=stopped,
        production=production,
        emergency=emergency,
    )
    controller.confirm(str(uuid.uuid4()))
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute(
            """UPDATE factory_seal_authorization
               SET operator_confirmation_uid=?""",
            (str(uuid.uuid4()),),
        )

    assert controller.reconcile_cleanup() == "SEALED_FACT_INVALID"
    assert stopped == ["stop"]
    assert production == []
    assert emergency == ["emergency"]
    authorization, events = _completion_rows(paths)
    assert authorization["state"] == "SEALING"
    assert events == []


def test_invalid_or_missing_seal_never_opens_production_network(
    tmp_path: Path,
) -> None:
    paths, _ = _authorized(tmp_path)
    controller = _controller(
        paths,
        stopped=[],
        production=[],
        emergency=[],
    )
    controller.confirm(str(uuid.uuid4()))
    document = json.loads(paths.sealed.read_text(encoding="utf-8"))
    document["hardwareIdentitySha256"] = "0" * 64
    paths.sealed.write_text(json.dumps(document), encoding="utf-8")
    stopped: list[str] = []
    production: list[str] = []
    emergency: list[str] = []
    damaged = _controller(
        paths,
        stopped=stopped,
        production=production,
        emergency=emergency,
    )

    assert damaged.reconcile_cleanup() == "SEALED_FACT_INVALID"
    assert stopped == ["stop"]
    assert emergency == ["emergency"]
    assert production == []
    assert not inspect_sealed_authorization(paths).valid

    paths.sealed.unlink()
    missing = inspect_sealed_authorization(paths)
    assert missing.exists and not missing.valid
    assert missing.status_code == "SEALED_FACT_MISSING"
    assert damaged.status()["statusCode"] == "SEALED_FACT_MISSING"

    stopped.clear()
    emergency.clear()
    assert damaged.reconcile_cleanup() == "SEALED_FACT_MISSING"
    assert stopped == ["stop"]
    assert emergency == ["emergency"]
    assert production == []


@pytest.mark.parametrize("database_bytes", [b"not-sqlite", b""])
def test_existing_unreadable_store_without_marker_is_not_unsealed(
    tmp_path: Path,
    database_bytes: bytes,
) -> None:
    paths = _paths(tmp_path)
    paths.edge_store.write_bytes(database_bytes)

    fact = inspect_sealed_authorization(paths)

    assert fact.exists
    assert not fact.valid
    assert fact.status_code == "SEALED_FACT_INVALID"

    stopped: list[str] = []
    production: list[str] = []
    emergency: list[str] = []
    controller = _controller(
        paths,
        stopped=stopped,
        production=production,
        emergency=emergency,
    )
    assert controller.reconcile_cleanup() == "SEALED_FACT_INVALID"
    assert stopped == ["stop"]
    assert emergency == ["emergency"]
    assert production == []


def test_minimal_pass_report_and_wrong_local_hardware_identity_are_rejected(
    tmp_path: Path,
) -> None:
    report = _report("release-1")
    assert valid_passed_factory_report(
        report,
        release_id="release-1",
        hardware_config_digest="a" * 64,
    )
    report["checks"] = {
        "mcu": {"status": "PASSED"},
        "weight": {"status": "PASSED"},
    }
    assert not valid_passed_factory_report(
        report,
        release_id="release-1",
        hardware_config_digest="a" * 64,
    )

    paths, _ = _authorized(tmp_path)
    with pytest.raises(FactorySealError) as captured:
        collect_local_factory_facts(
            paths,
            expected_hardware_sn="SN-DIFFERENT",
        )
    assert captured.value.code == "DEVICE_CREDENTIALS_INVALID"


@pytest.mark.parametrize(
    "inactive_unit",
    [
        "ecobin-hardware.service",
        "ecobin-cellular-uplink.service",
        "ecobin-remote-support.service",
    ],
)
def test_every_required_runtime_service_must_be_active(
    monkeypatch: pytest.MonkeyPatch,
    inactive_unit: str,
) -> None:
    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            3 if argv[-1] == inactive_unit else 0,
        )

    monkeypatch.setattr("factory_seal.controller.subprocess.run", run)
    assert not _runtime_healthy()
