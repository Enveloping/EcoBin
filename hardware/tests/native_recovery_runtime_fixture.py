"""Cold-open frozen Edge recovery history without executing obsolete commands.

The JSON was captured once from commit
``3178a99455ecbaf936d4468ede81e395afe94fcf``.  That checkpoint still had an
MCU which accepted the historical SAFE_CLOSE path.  The base rows and each
table override are exact SQLite values captured at the named boundary; this
loader only restores and validates them.  It never constructs a dispatcher or
writes the frozen AUTHORIZE/SAFE_CLOSE bytes.

This is a test-checkpoint capture, not a field-device database export.  In
particular it proves compatibility with the historical Edge ``write_claimed``
boundary and saved-output custody, not that every deployed database shape has
been sampled.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from hardware.tests.native_recovery_history_fixture import (
    LoadedRecoveryHistory,
    load_native_recovery_history,
    read_native_recovery_history_fixture,
)


FIXTURE = Path(__file__).with_name("fixtures") / "native-recovery-runtime-edge-v1.json"


@dataclass
class LoadedRecoveryRuntimeHistory:
    store: Any
    safety: Any
    permanent: LoadedRecoveryHistory
    fixture: dict[str, Any]
    variant: dict[str, Any]
    path: Path
    issue: dict[str, Any]
    occupancy: dict[str, Any]
    permit: Any
    binding: dict[str, Any]
    saved: dict[str, bytes]

    def close(self) -> None:
        self.store.close()
        self.permanent.close()


def _decode_row(row: list[Any]) -> list[Any]:
    return [bytes.fromhex(value["hex"]) if isinstance(value, dict) and set(value) == {"hex"}
            else value for value in row]


def _restore_tables(store: Any, fixture: dict[str, Any], variant_name: str) -> None:
    tables = dict(fixture["edgeBaseTables"])
    tables.update(fixture["edgeTableOverrides"].get(variant_name, {}))
    store._conn.execute("PRAGMA foreign_keys=OFF")
    try:
        with store.transaction() as connection:
            for table_name, table in tables.items():
                connection.execute(f'DELETE FROM "{table_name}"')
                columns = ",".join(f'"{column}"' for column in table["columns"])
                placeholders = ",".join("?" for _ in table["columns"])
                connection.executemany(
                    f'INSERT INTO "{table_name}" ({columns}) VALUES ({placeholders})',
                    [_decode_row(row) for row in table["rows"]],
                )
    finally:
        store._conn.execute("PRAGMA foreign_keys=ON")
    if store._conn.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("frozen Edge recovery history violates foreign keys")


def _validate_edge(loaded: LoadedRecoveryRuntimeHistory, variant_name: str) -> None:
    from dataclasses import asdict
    from job_safety import NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS

    expected = loaded.fixture["edgeExpected"]
    if loaded.store.get_native_delivery_issue(loaded.permit.work_uid) != expected["issue"]:
        raise ValueError("frozen Edge recovery issue differs from checkpoint")
    if loaded.store.get_work_slot() != expected["occupancy"]:
        raise ValueError("frozen Edge recovery occupancy differs from checkpoint")
    uid = loaded.variant["targetActionUid"]
    binding = loaded.store.get_native_delivery_recovery_close(uid)
    common = loaded.fixture["common"]
    recovery = common["recovery"]
    expected_action = {
        "action_uid": recovery["actionUid"],
        "receipt_uid": common["retirement"]["receiptUid"],
        "action_key": recovery["actionKey"],
        "action_kind": recovery["actionKind"],
        "action_digest_sha256": recovery["actionDigestSha256"],
    }
    expected_evidence = {key: recovery[key] for key in NATIVE_RECOVERY_CLOSE_EVIDENCE_FIELDS}
    if (binding is None or asdict(binding["permit"]) != expected["issue"]["permit"]
            or asdict(binding["action"]) != expected_action
            or binding["evidence"] != expected_evidence):
        raise ValueError("frozen Edge recovery binding differs from checkpoint")
    record = loaded.store.get_native_command(uid)
    claimed = variant_name in {"claimed-unknown", "claimed-with-output"}
    if bool(record["write_claimed"]) != claimed:
        raise ValueError("frozen Edge write-claim boundary differs from checkpoint")
    output_rows = loaded.store._conn.execute(
        "SELECT mcu_boot_id,event_sequence FROM native_actuator_event WHERE reported_command_uid=?",
        (uid,),
    ).fetchall()
    if len(output_rows) != int(variant_name == "claimed-with-output"):
        raise ValueError("frozen Edge output boundary differs from checkpoint")
    for row in output_rows:
        loaded.store.get_native_actuator_event(row["mcu_boot_id"], row["event_sequence"])


def load_native_recovery_runtime_history(
    tmp_path: Path,
    variant_name: str,
    *,
    fixture_path: Path = FIXTURE,
) -> LoadedRecoveryRuntimeHistory:
    """Restore matching Edge/permanent histories, then cold-open both stores."""

    from edge_store import EdgeStore
    from job_safety import JobPermit, PermanentJobSafety
    from hardware.tests.test_command_processor import StoreBackedUpdaterClient

    fixture = read_native_recovery_history_fixture(fixture_path)
    try:
        variant = fixture["variants"][variant_name]
    except KeyError as error:
        raise ValueError(f"unknown frozen runtime history: {variant_name}") from error
    root = Path(tmp_path) / variant_name
    path = root / "edge.db"
    first_load = not path.exists()
    store = EdgeStore(str(path))
    if first_load:
        store.initialize()
    else:
        store.initialize_existing_recovery()
    permanent = None
    try:
        if first_load:
            _restore_tables(store, fixture, variant_name)
            store.close()
            store = EdgeStore(str(path))
            store.initialize_existing_recovery()
        permanent = load_native_recovery_history(root, variant_name, fixture_path=fixture_path)
        safety = PermanentJobSafety(StoreBackedUpdaterClient(permanent.store))
        permit = JobPermit(**fixture["edgeExpected"]["issue"]["permit"])
        uid = variant["targetActionUid"]
        loaded = LoadedRecoveryRuntimeHistory(
            store=store,
            safety=safety,
            permanent=permanent,
            fixture=fixture,
            variant=variant,
            path=path,
            issue=fixture["edgeExpected"]["issue"],
            occupancy=fixture["edgeExpected"]["occupancy"],
            permit=permit,
            binding=store.get_native_delivery_recovery_close(uid),
            saved={"payload": bytes.fromhex(fixture["edgeExpected"]["lateResultPayloadHex"])},
        )
        _validate_edge(loaded, variant_name)
        return loaded
    except Exception:
        store.close()
        if permanent is not None:
            permanent.close()
        raise


def cold_reopen_runtime_history(loaded: LoadedRecoveryRuntimeHistory) -> None:
    """Reopen mutated custody without replaying or reasserting seed state."""

    from edge_store import EdgeStore
    from job_safety import PermanentJobSafety
    from updater_store import UpdaterStore
    from hardware.tests.test_command_processor import StoreBackedUpdaterClient

    loaded.store.close()
    loaded.permanent.store.close()
    loaded.store = EdgeStore(str(loaded.path))
    loaded.store.initialize_existing_recovery()
    updater = UpdaterStore(
        loaded.permanent.path,
        release_version=loaded.fixture["schema"]["releaseVersion"],
        enable_stage4_candidate=True,
    )
    updater.initialize()
    loaded.permanent.store = updater
    loaded.safety = PermanentJobSafety(StoreBackedUpdaterClient(updater))
    loaded.binding = loaded.store.get_native_delivery_recovery_close(
        loaded.variant["targetActionUid"]
    )


class RecoverySerial:
    """Script only non-actuating recovery traffic around frozen local rows."""

    timeout, write_timeout = 0, 0.1
    _ALLOWED = {
        "BOOT_PROBE",
        "BIND_BOOT",
        "QUERY_COMMAND",
        "QUERY_ACTUATOR_EVENT",
        "ACTUATOR_EVENT_SAVED",
    }

    def __init__(self, *, mcu_boot_id: int | None = 2, query_outcome: str | None = None):
        self.mcu_boot_id = mcu_boot_id
        self.query_outcome = query_outcome
        self.now = 0
        self.is_open = True
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self._inbound = bytearray()
        self._sequence = 1000

    @property
    def in_waiting(self) -> int:
        return len(self._inbound)

    def read(self, size: int) -> bytes:
        result = bytes(self._inbound[:size])
        del self._inbound[:size]
        return result

    def _reply(self, name: str, values: dict[str, Any]) -> None:
        import uart2_protocol as uart

        self._sequence += 1
        self._inbound.extend(uart.encode_frame(name, self._sequence, uart.encode_payload(name, values)))

    def inject_mcu_frame(self, raw: bytes) -> None:
        self._inbound.extend(raw)

    def write(self, raw: bytes) -> int:
        import uart2_protocol as uart

        decoded = uart.decode_frame(raw, sender_role="EDGE")
        name = decoded["messageName"]
        if name not in self._ALLOWED:
            raise AssertionError(f"recovery runtime attempted forbidden command: {name}")
        values = uart.decode_payload(name, decoded["payload"])
        self.sent.append((name, values))
        if self.mcu_boot_id is None:
            return len(raw)
        if name == "BOOT_PROBE":
            self._reply("BOOT_PROBE_REPLY", {
                "probeId": values["probeId"],
                "mcuBootId": self.mcu_boot_id,
            })
        elif name == "BIND_BOOT":
            self.mcu_boot_id = values["proposedMcuBootId"]
            self._reply("BIND_BOOT_REPLY", {
                "probeId": values["probeId"],
                "proposedMcuBootId": values["proposedMcuBootId"],
                "mcuBootId": self.mcu_boot_id,
                "status": "BOUND",
            })
        elif name == "QUERY_COMMAND" and self.query_outcome is not None:
            self._reply("COMMAND_QUERY_RESULT", values | {
                "currentMcuBootId": self.mcu_boot_id,
                "outcome": self.query_outcome,
                "errorCode": "NONE",
                "highestCommandSequence": (0 if self.query_outcome == "NOT_SEEN"
                                           else values["commandSequence"]),
            })
        return len(raw)

    def advance(self, elapsed_ms: int) -> None:
        self.now += elapsed_ms

    def read_clock(self) -> int:
        # Real monotonic time advances across the several reads in one poll;
        # one millisecond prevents artificial lockstep between independent
        # one-second probe and reconciliation deadlines.
        self.now += 1
        return self.now

    def close(self) -> None:
        self.is_open = False


def candidate_loop(case: LoadedRecoveryRuntimeHistory, serial: RecoverySerial):
    from native_recovery_runtime import NativeRecoveryRuntime
    from uart2_transport import NativeUartTransport

    return NativeRecoveryRuntime(
        case.store,
        case.safety,
        NativeUartTransport(serial),
        device_name=case.issue["deviceName"],
        clock=serial.read_clock,
    )


def poll_candidate(candidate: Any, serial: RecoverySerial, count: int = 10, step: int = 200):
    status = None
    for _ in range(count):
        status = candidate.poll()
        serial.advance(step)
    return status


def assert_recovery_only(status: dict[str, Any], serial: RecoverySerial) -> None:
    assert status["admissionAllowed"] is False
    assert {name for name, _ in serial.sent} <= RecoverySerial._ALLOWED
    assert not any(name in {
        "SAFE_CLOSE",
        "AUTHORIZE_DELIVERY_FIRST_OPEN",
        "UNLOCK_CLEAN_DOOR",
    } for name, _ in serial.sent)
