from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from command_processor import CommandProcessor
from edge_store import EdgeStore
from factory_seal.admission import (
    FACTORY_NOT_SEALED,
    PRESEAL_CONTROL_COMMAND_TYPES,
    FactorySealProductionGate,
)
from factory_seal.errors import FactorySealError
from factory_seal.validation import (
    FactorySealPaths,
    factory_seal_completion_payload,
)
from onenet_wire import (
    build_event_envelope,
    canonical_payload_sha256,
    decode_service_command,
)


_HARDWARE_SN = "SN-FACTORY-GATE-0001"


class _FakeUart:
    pass


def _valid_service_command(example_name: str) -> dict:
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        example_name,
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["payloadSha256"] = canonical_payload_sha256(
        command["payload"]
    )
    return command


def _paths(tmp_path: Path) -> FactorySealPaths:
    return FactorySealPaths(
        edge_store=tmp_path / "edge.db",
        sealed=tmp_path / "sealed.json",
    )


def _write_bound_seal(
    paths: FactorySealPaths,
    *,
    state: str,
) -> str:
    command_uid = str(uuid.uuid4())
    evidence_uid = str(uuid.uuid4())
    challenge_uid = str(uuid.uuid4())
    confirmation_uid = str(uuid.uuid4())
    binding = "6" * 64
    report_sha256 = "5" * 64
    with sqlite3.connect(paths.edge_store) as connection:
        connection.execute(
            """INSERT INTO factory_seal_authorization (
                   command_uid, command_sha256, hardware_sn,
                   acceptance_generation, evidence_event_uid,
                   acceptance_challenge_uid, acceptance_evidence_sha256,
                   factory_bag_revision, factory_bag_set_sha256,
                   image_release_id, image_release_sha256,
                   factory_report_sha256,
                   authorization_binding_sha256, state,
                   operator_confirmation_uid, authorized_at,
                   confirmed_at, completed_at
               ) VALUES (?, ?, ?, 1, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?,
                         ?, ?, ?)""",
            (
                command_uid,
                "3" * 64,
                _HARDWARE_SN,
                evidence_uid,
                challenge_uid,
                "4" * 64,
                "7" * 64,
                "release-1",
                "8" * 64,
                report_sha256,
                binding,
                state,
                confirmation_uid,
                "2026-08-22T11:59:00Z",
                "2026-08-22T12:00:00Z",
                (
                    "2026-08-22T12:01:00Z"
                    if state == "SEALED"
                    else None
                ),
            ),
        )
    document = {
        "schemaVersion": 1,
        "status": "SEALED",
        "imageReleaseId": "release-1",
        "hardwareIdentitySha256": hashlib.sha256(
            _HARDWARE_SN.encode("utf-8")
        ).hexdigest(),
        "factoryReportSha256": report_sha256,
        "authorizationCommandUid": command_uid,
        "acceptanceGeneration": 1,
        "authorizationBindingSha256": binding,
        "operatorConfirmationUid": confirmation_uid,
        "sealedAt": "2026-08-22T12:00:00Z",
    }
    paths.sealed.write_text(json.dumps(document), encoding="utf-8")
    paths.sealed.chmod(0o600)
    if state == "SEALED":
        cleanup_completed_at = "2026-08-22T12:01:00Z"
        completion_event_uid = str(uuid.uuid4())
        with sqlite3.connect(paths.edge_store) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute(
                """UPDATE factory_seal_authorization
                   SET cleanup_completed_at=?, completion_event_uid=?
                   WHERE command_uid=?""",
                (
                    cleanup_completed_at,
                    completion_event_uid,
                    command_uid,
                ),
            )
            row = connection.execute(
                """SELECT * FROM factory_seal_authorization
                   WHERE command_uid=?""",
                (command_uid,),
            ).fetchone()
            connection.execute(
                """UPDATE device_state SET state_value='1'
                   WHERE state_key='edge_event_sequence'"""
            )
            event = build_event_envelope(
                event_uid=completion_event_uid,
                device_name=_HARDWARE_SN,
                edge_event_sequence=1,
                event_type="FACTORY_SEAL_COMPLETED",
                target_type="DEVICE_ASSET",
                target_uid=_HARDWARE_SN,
                command_uid=command_uid,
                payload=factory_seal_completion_payload(
                    document,
                    row,
                    cleanup_completed_at,
                ),
            )
            event["occurredAt"] = cleanup_completed_at
            event["clockQuality"] = "SYNCED"
            connection.execute(
                """INSERT INTO event_outbox
                   (event_uid, edge_event_sequence, event_type,
                    payload_json, work_uid)
                   VALUES (?, 1, 'FACTORY_SEAL_COMPLETED', ?, ?)""",
                (
                    completion_event_uid,
                    json.dumps(event),
                    _HARDWARE_SN,
                ),
            )
    return command_uid


def _store_and_gate(
    tmp_path: Path,
    *,
    state: str | None,
) -> tuple[EdgeStore, FactorySealPaths, FactorySealProductionGate]:
    paths = _paths(tmp_path)
    store = EdgeStore(str(paths.edge_store))
    store.initialize()
    if state is not None:
        _write_bound_seal(paths, state=state)
    return store, paths, FactorySealProductionGate(paths)


def test_only_exact_cleanup_completed_seal_allows_production(
    tmp_path: Path,
) -> None:
    unsealed_store, _, unsealed = _store_and_gate(
        tmp_path / "unsealed",
        state=None,
    )
    authorized_store, _, authorized = _store_and_gate(
        tmp_path / "authorized",
        state="AUTHORIZED",
    )
    sealing_store, _, sealing = _store_and_gate(
        tmp_path / "sealing",
        state="SEALING",
    )
    sealed_store, sealed_paths, sealed = _store_and_gate(
        tmp_path / "sealed",
        state="SEALED",
    )

    assert not unsealed.production_ready()
    assert not authorized.production_ready()
    assert not sealing.production_ready()
    assert sealed.production_ready()

    forged = json.loads(sealed_paths.sealed.read_text(encoding="utf-8"))
    forged["factoryReportSha256"] = "0" * 64
    sealed_paths.sealed.write_text(json.dumps(forged), encoding="utf-8")
    assert not sealed.production_ready()

    for store in (
        unsealed_store,
        authorized_store,
        sealing_store,
        sealed_store,
    ):
        store.close()


def test_sealed_admission_survives_edge_process_restart(tmp_path: Path) -> None:
    store, paths, gate = _store_and_gate(tmp_path, state="SEALED")
    assert gate.production_ready()
    store.close()

    restarted = EdgeStore(str(paths.edge_store))
    restarted.initialize()
    assert FactorySealProductionGate(paths).production_ready()
    restarted.close()


def test_migrated_v15_sealed_row_without_completion_fact_stays_closed(
    tmp_path: Path,
) -> None:
    store, paths, _ = _store_and_gate(tmp_path, state="SEALED")
    store._conn.execute("DROP INDEX idx_factory_seal_completion_event")
    store._conn.execute(
        """ALTER TABLE factory_seal_authorization
           DROP COLUMN completion_event_uid"""
    )
    store._conn.execute(
        """ALTER TABLE factory_seal_authorization
           DROP COLUMN cleanup_completed_at"""
    )
    store._conn.execute("DELETE FROM schema_version WHERE version >= 16")
    store._conn.commit()
    store.close()

    migrated = EdgeStore(str(paths.edge_store))
    migrated.initialize()
    row = migrated._conn.execute(
        """SELECT cleanup_completed_at, completion_event_uid
           FROM factory_seal_authorization"""
    ).fetchone()
    assert row["cleanup_completed_at"] is None
    assert row["completion_event_uid"] is None
    assert not FactorySealProductionGate(paths).production_ready()
    migrated.close()


def test_completion_event_timestamp_or_payload_tampering_closes_gate(
    tmp_path: Path,
) -> None:
    store, paths, gate = _store_and_gate(tmp_path, state="SEALED")
    assert gate.production_ready()
    row = store._conn.execute(
        """SELECT event_uid, payload_json FROM event_outbox
           WHERE event_type='FACTORY_SEAL_COMPLETED'"""
    ).fetchone()
    envelope = json.loads(row["payload_json"])
    envelope["occurredAt"] = "2026-08-22T12:01:00.001Z"
    store._conn.execute(
        "UPDATE event_outbox SET payload_json=? WHERE event_uid=?",
        (json.dumps(envelope), row["event_uid"]),
    )
    store._conn.commit()

    assert not gate.production_ready()
    store.close()


def test_preseal_control_allowlist_and_physical_denylist_are_explicit(
    tmp_path: Path,
) -> None:
    store, _, gate = _store_and_gate(tmp_path, state=None)
    expected_controls = {
        "APPLY_CONFIGURATION",
        "CONFIRM_EDGE_EVENT",
        "PROVIDE_PHOTO_UPLOAD_GRANT",
        "REQUEST_DEVICE_ACCEPTANCE",
        "AUTHORIZE_FACTORY_SEAL",
        "SYNC_DEVICE_ENTRY_URL",
        "OPEN_REMOTE_SUPPORT_TUNNEL",
        "CLOSE_REMOTE_SUPPORT_TUNNEL",
    }
    assert PRESEAL_CONTROL_COMMAND_TYPES == expected_controls
    for command_type in expected_controls:
        gate.require_command_allowed(command_type)

    for command_type in {
        "START_DELIVERY_SESSION",
        "START_CLEAN_OPERATION",
        "END_CLEAN_BEFORE_UNLOCK",
        "RESUME_CLEAN_OPERATION",
        "SAMPLE_FULLNESS",
        "MEASURE_EMPTY_BAG_BASELINE",
        "START_MCU_FIRMWARE_UPDATE",
        "FUTURE_UNKNOWN_PHYSICAL_COMMAND",
    }:
        with pytest.raises(FactorySealError) as captured:
            gate.require_command_allowed(command_type)
        assert captured.value.code == FACTORY_NOT_SEALED
    store.close()


class _NeverDispatchWork:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def start_delivery_command(self, command: dict) -> dict:
        self.calls.append(command["commandUid"])
        raise AssertionError("production work must not be dispatched")


class _NeverUpdateFirmware:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def queue_cloud(self, **values) -> dict:
        self.calls.append(values)
        raise AssertionError("firmware update must not be queued")


class _InvalidateAfterFirstCheck:
    def __init__(
        self,
        delegate: FactorySealProductionGate,
        sealed_path: Path,
    ) -> None:
        self._delegate = delegate
        self._sealed_path = sealed_path
        self.calls = 0

    def require_command_allowed(self, command_type: object) -> None:
        self.calls += 1
        self._delegate.require_command_allowed(command_type)
        if self.calls == 1:
            document = json.loads(
                self._sealed_path.read_text(encoding="utf-8")
            )
            document["authorizationBindingSha256"] = "0" * 64
            self._sealed_path.write_text(
                json.dumps(document),
                encoding="utf-8",
            )


class _BrokenGate:
    def require_command_allowed(self, command_type: object) -> None:
        raise RuntimeError("simulated local seal storage failure")


def test_second_check_blocks_marker_race_before_work_dispatch(
    tmp_path: Path,
) -> None:
    store, paths, delegate = _store_and_gate(tmp_path, state="SEALED")
    command = _valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    gate = _InvalidateAfterFirstCheck(delegate, paths.sealed)
    work = _NeverDispatchWork()
    processor = CommandProcessor(
        store,
        _FakeUart(),
        work,
        factory_seal_gate=gate,
    )

    assert processor.process_next()

    assert gate.calls == 2
    assert work.calls == []
    row = store.get_command(command["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == FACTORY_NOT_SEALED
    observations = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_COMMAND_OBSERVED'"""
    ).fetchall()
    assert len(observations) == 1
    payload = json.loads(observations[0]["payload_json"])["payload"]
    assert payload["stage"] == "REJECTED"
    assert payload["errorCode"] == FACTORY_NOT_SEALED
    store.close()


def test_pending_command_can_be_atomically_rejected_before_claim(
    tmp_path: Path,
) -> None:
    store, _, _ = _store_and_gate(tmp_path, state=None)
    command = _valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"

    assert store.receive_rejected_command(
        command,
        FACTORY_NOT_SEALED,
    ) == "REJECTED"

    row = store.get_command(command["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == FACTORY_NOT_SEALED
    assert store.claim_next_command() is None
    observations = store._conn.execute(
        """SELECT payload_json FROM event_outbox
           WHERE event_type='DEVICE_COMMAND_OBSERVED'"""
    ).fetchall()
    assert len(observations) == 1
    store.close()


def test_restart_rechecks_invalid_marker_before_work_dispatch(
    tmp_path: Path,
) -> None:
    store, paths, _ = _store_and_gate(tmp_path, state="SEALED")
    command = _valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    store.close()
    paths.sealed.unlink()

    restarted = EdgeStore(str(paths.edge_store))
    restarted.initialize()
    work = _NeverDispatchWork()
    processor = CommandProcessor(
        restarted,
        _FakeUart(),
        work,
        factory_seal_gate=FactorySealProductionGate(paths),
    )

    assert processor.process_next()
    assert work.calls == []
    row = restarted.get_command(command["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == FACTORY_NOT_SEALED
    restarted.close()


def test_unexpected_gate_failure_is_stable_fail_closed_rejection(
    tmp_path: Path,
) -> None:
    store, _, _ = _store_and_gate(tmp_path, state="SEALED")
    command = _valid_service_command(
        "start-delivery-session.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    work = _NeverDispatchWork()
    processor = CommandProcessor(
        store,
        _FakeUart(),
        work,
        factory_seal_gate=_BrokenGate(),
    )

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == FACTORY_NOT_SEALED
    assert work.calls == []
    store.close()


def test_unsealed_restart_rejects_firmware_before_volatile_grant_or_updater(
    tmp_path: Path,
) -> None:
    store, paths, gate = _store_and_gate(tmp_path, state=None)
    command = _valid_service_command(
        "start-mcu-firmware-update.service-wire.json"
    )
    assert store.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    updater = _NeverUpdateFirmware()
    processor = CommandProcessor(
        store,
        _FakeUart(),
        mcu_firmware_updater=updater,
        factory_seal_gate=gate,
        device_name=_HARDWARE_SN,
    )

    assert processor.process_next()

    row = store.get_command(command["commandUid"])
    assert row["state"] == "REJECTED"
    assert row["last_error"] == FACTORY_NOT_SEALED
    assert updater.calls == []
    assert store.get_maintenance_lock() is None
    store.close()
