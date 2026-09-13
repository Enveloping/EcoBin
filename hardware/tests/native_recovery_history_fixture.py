"""Load frozen recovery-close history without constructing an MCU runtime.

The fixture is a compact list of inputs accepted by today's permanent
``UpdaterStore`` API.  Loading may create or cold-open a SQLite database, but
it never creates a UART writer and never dispatches the frozen command bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


FIXTURE = Path(__file__).with_name("fixtures") / "native-recovery-close-history-v1.json"
SOURCE_COMMIT = "3178a99455ecbaf936d4468ede81e395afe94fcf"


class RecoveryHistoryFixtureError(ValueError):
    """The frozen fixture is corrupt, unsupported, or internally inconsistent."""


@dataclass
class LoadedRecoveryHistory:
    store: Any
    fixture: dict[str, Any]
    variant: dict[str, Any]
    path: Path

    def close(self) -> None:
        self.store.close()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def read_native_recovery_history_fixture(path: Path = FIXTURE) -> dict[str, Any]:
    try:
        fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryHistoryFixtureError("recovery history fixture is unreadable") from error
    digest = fixture.pop("fixtureSha256", None)
    actual = hashlib.sha256(_canonical(fixture)).hexdigest()
    fixture["fixtureSha256"] = digest
    if digest != actual:
        raise RecoveryHistoryFixtureError("recovery history fixture hash mismatch")
    if fixture.get("sourceCommit") != SOURCE_COMMIT:
        raise RecoveryHistoryFixtureError("recovery history source commit mismatch")
    schema = fixture.get("schema")
    if schema != {
        "name": "ecobin-native-recovery-close-history",
        "releaseVersion": "native-recovery-history-v1",
        "store": "UpdaterStore",
        "version": 1,
    }:
        raise RecoveryHistoryFixtureError("recovery history fixture schema mismatch")
    names = fixture.get("variantNames")
    if (not isinstance(names, list) or len(names) != len(set(names))
            or set(names) != set(fixture.get("variants", {}))):
        raise RecoveryHistoryFixtureError("recovery history variant inventory mismatch")
    _validate_wire_evidence(fixture["common"])
    return fixture


def _validate_wire_evidence(common: dict[str, Any]) -> None:
    import uart2_protocol as uart
    from job_safety import action_digest

    source = common["sourceAction"]
    source_raw = bytes.fromhex(source["sourceCommandPayloadHex"])
    source_values = uart.decode_payload("AUTHORIZE_DELIVERY_FIRST_OPEN", source_raw)
    if source_values["mcuCommandUid"] != source["actionUid"]:
        raise RecoveryHistoryFixtureError("source command identity mismatch")
    source_digest = action_digest(
        work_uid=source["workUid"], command_uid=source["commandUid"],
        action_key=source["actionKey"], action_kind=source["actionKind"],
        payload={"nativeUartPayloadHex": source_raw.hex()},
    )
    if source_digest != source["actionDigestSha256"]:
        raise RecoveryHistoryFixtureError("source action digest mismatch")

    for key in ("recovery", "successor"):
        request = common[key]
        raw = bytes.fromhex(request["closeCommandPayloadHex"])
        values = uart.decode_payload("SAFE_CLOSE", raw)
        if values["mcuCommandUid"] != request["actionUid"]:
            raise RecoveryHistoryFixtureError(f"{key} command identity mismatch")
        digest = action_digest(
            work_uid=request["workUid"], command_uid=request["commandUid"],
            action_key=request["actionKey"], action_kind=request["actionKind"],
            payload={"nativeUartPayloadHex": raw.hex()},
        )
        if digest != request["actionDigestSha256"]:
            raise RecoveryHistoryFixtureError(f"{key} action digest mismatch")

    output = common["output"]
    raw = bytes.fromhex(output["payloadHex"])
    values = uart.decode_payload(output["messageName"], raw)
    digest = hashlib.sha256(raw).hexdigest()
    if (digest != output["payloadSha256"]
            or digest != output["confirmation"]["evidenceDigestSha256"]
            or values["mcuCommandUid"] != output["confirmation"]["actionUid"]):
        raise RecoveryHistoryFixtureError("output evidence mismatch")


def _open_store(path: Path, fixture: dict[str, Any]):
    from updater_store import UpdaterStore

    path.parent.mkdir(parents=True, exist_ok=True)
    return UpdaterStore(
        path,
        release_version=fixture["schema"]["releaseVersion"],
        enable_stage4_candidate=True,
        utc_now=lambda: datetime(2026, 9, 2, 8, 30, tzinfo=timezone.utc),
    )


def _seed(store: Any, fixture: dict[str, Any], variant: dict[str, Any]) -> None:
    common = fixture["common"]
    status = store.get_status()
    store.activate_stage4_job_gate(common["activation"] | {
        "expectedManagementStateSequence": status["managementStateSequence"],
    })
    store.request_job_permit(common["permit"])
    store.begin_job(common["begin"])
    store.prepare_physical_action(common["sourceAction"])
    store.arm_physical_action(common["sourceArm"])
    for operation in variant["operations"]:
        if operation == "prepare":
            store.prepare_native_recovery_close(common["recovery"])
        elif operation == "arm":
            store.arm_physical_action({
                "actionUid": common["recovery"]["actionUid"],
                "dispatchAttemptToken": common["recovery"]["dispatchAttemptToken"],
            })
        elif operation == "confirm-output":
            store.confirm_physical_action(common["output"]["confirmation"])
        elif operation == "retire":
            store.retire_native_recovery_close(common["retirement"])
        elif operation == "withdraw":
            store.withdraw_native_recovery_close_dispatch(common["retirement"])
        elif operation == "isolate":
            store.isolate_native_recovery_close_after_reboot(common["isolation"])
        elif operation == "prepare-successor":
            store.prepare_native_recovery_close_successor(common["successor"])
        else:  # Fixture integrity should make this impossible, but fail closed.
            raise RecoveryHistoryFixtureError(f"unknown history operation: {operation}")


def _assert_loaded(store: Any, fixture: dict[str, Any], variant: dict[str, Any]) -> None:
    from updater_store import UpdaterStoreError

    expected = variant["expected"]
    uid = variant["targetActionUid"]
    action = store.get_physical_action({"actionUid": uid})
    for source, target in (("state", "state"), ("dispatchMode", "dispatchMode"),
                           ("confirmedOutcome", "confirmedOutcome")):
        if action[target] != expected[source]:
            raise RecoveryHistoryFixtureError(f"loaded {target} does not match fixture")
    recovery = store.get_native_recovery_close({"actionUid": uid})
    request = fixture["common"]["successor" if "prepare-successor" in variant["operations"] else "recovery"]
    for key in ("actionUid", "actionDigestSha256", "closeCommandPayloadHex",
                "sourceActionUid", "sourceCommandPayloadHex"):
        if recovery[key] != request[key]:
            raise RecoveryHistoryFixtureError(f"loaded recovery {key} does not match fixture")
    try:
        disposition = store.get_native_recovery_close_disposition({"actionUid": uid})
    except UpdaterStoreError as error:
        if error.code != "NATIVE_RECOVERY_DISPOSITION_NOT_FOUND":
            raise
        disposition = None
    actual_disposition = None if disposition is None else disposition["state"]
    if actual_disposition != expected["dispositionState"]:
        raise RecoveryHistoryFixtureError("loaded disposition does not match fixture")
    if expected.get("predecessorState") is not None:
        predecessor = store.get_physical_action({
            "actionUid": fixture["common"]["recovery"]["actionUid"],
        })
        if predecessor["state"] != expected["predecessorState"]:
            raise RecoveryHistoryFixtureError("loaded predecessor does not match fixture")


def load_native_recovery_history(
    tmp_path: Path,
    variant_name: str,
    *,
    fixture_path: Path = FIXTURE,
) -> LoadedRecoveryHistory:
    """Replay one frozen variant once, then return a current cold-open store."""

    fixture = read_native_recovery_history_fixture(fixture_path)
    try:
        variant = fixture["variants"][variant_name]
    except KeyError as error:
        raise RecoveryHistoryFixtureError(f"unknown recovery history variant: {variant_name}") from error
    path = Path(tmp_path) / variant_name / "updater.db"
    first_load = not path.exists()
    store = _open_store(path, fixture)
    store.initialize()
    try:
        if first_load:
            _seed(store, fixture, variant)
        _assert_loaded(store, fixture, variant)
        store.close()
        store = _open_store(path, fixture)
        store.initialize()
        _assert_loaded(store, fixture, variant)
        return LoadedRecoveryHistory(store=store, fixture=fixture, variant=variant, path=path)
    except Exception:
        store.close()
        raise
