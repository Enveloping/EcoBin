from __future__ import annotations

import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from business_control import (
    BUSINESS_COMPONENT,
    BUSINESS_PROTOCOL_NAME,
    MCU_MAINTENANCE_EVIDENCE_DOMAIN,
    MCU_MAINTENANCE_EVIDENCE_SCHEMA_VERSION,
    BusinessControlController,
    BusinessControlService,
    McuF1Evidence,
    McuF3Evidence,
    McuFirmwareIdentity,
    McuMaintenanceEvidence,
    McuMaintenancePortError,
    build_business_control_service,
    mcu_firmware_identity_sha256,
    mcu_maintenance_evidence_sha256,
)
from local_control import LocalControlActionError


INSTANCE_UID = uuid.UUID("11111111-1111-4111-8111-111111111111")
UPDATE_UID = "22222222-2222-4222-8222-222222222222"
OTHER_UPDATE_UID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
HANDOFF_UID = "33333333-3333-4333-8333-333333333333"
OTHER_HANDOFF_UID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
FLASH_SHA256 = "c" * 64

FIRMWARE_IDENTITY = McuFirmwareIdentity(
    protocol_revision=2,
    firmware_version_code=17,
    firmware_version="1.2.3",
    firmware_identity_hex="0123456789abcdef",
)
FIRMWARE_IDENTITY_SHA256 = mcu_firmware_identity_sha256(FIRMWARE_IDENTITY)


def _f3(
    *,
    query_status: str = "OK",
    mode: int | None = 1,
    status_code: int | None = 0,
    safe_flags: int | None = 0x0F,
    firmware_identity: McuFirmwareIdentity | None = FIRMWARE_IDENTITY,
) -> McuF3Evidence:
    return McuF3Evidence(
        query_status=query_status,
        mode=mode,
        status_code=status_code,
        safe_flags=safe_flags,
        firmware_identity=firmware_identity,
    )


def _f1(
    *,
    query_status: str = "OK",
    communication_healthy: bool | None = True,
    valid_flags: int | None = 3,
    weight_grams: int | None = 12345,
    infrared_blocked: bool | None = False,
    smoke_state: str = "NORMAL",
    smoke_sensor_health: str = "OK",
) -> McuF1Evidence:
    return McuF1Evidence(
        query_status=query_status,
        communication_healthy=communication_healthy,
        valid_flags=valid_flags,
        weight_grams=weight_grams,
        infrared_blocked=infrared_blocked,
        smoke_state=smoke_state,
        smoke_sensor_health=smoke_sensor_health,
    )


def _not_performed_f1() -> McuF1Evidence:
    return _f1(
        query_status="NOT_PERFORMED",
        communication_healthy=None,
        valid_flags=None,
        weight_grams=None,
        infrared_blocked=None,
        smoke_state="UNKNOWN",
        smoke_sensor_health="NOT_PERFORMED",
    )


def _evidence(
    *,
    f3: McuF3Evidence | None = None,
    f1: McuF1Evidence | None = None,
    uart_handed_off: bool | None = False,
) -> McuMaintenanceEvidence:
    return McuMaintenanceEvidence(
        f3=f3 or _f3(),
        f1=f1 or _f1(),
        uart_handed_off=uart_handed_off,
    )


def _quiesced_evidence() -> McuMaintenanceEvidence:
    return _evidence(
        f3=_f3(mode=2, safe_flags=0x1F),
        f1=_not_performed_f1(),
        uart_handed_off=True,
    )


class FakeMcuMaintenancePort:
    def __init__(self) -> None:
        self.observation = _evidence()
        self.quiesce_evidence = _quiesced_evidence()
        self.verification_evidence = _evidence()
        self.calls: list[tuple[str, dict]] = []

    def observe_mcu_maintenance_state(self) -> McuMaintenanceEvidence:
        self.calls.append(("OBSERVE", {}))
        return self.observation

    def quiesce_mcu_for_update(self, **payload) -> McuMaintenanceEvidence:
        self.calls.append(("QUIESCE", payload))
        return self.quiesce_evidence

    def verify_mcu_after_update(self, **payload) -> McuMaintenanceEvidence:
        self.calls.append(("VERIFY", payload))
        return self.verification_evidence


def _controller() -> BusinessControlController:
    return BusinessControlController(
        "1.2.3",
        utc_now=lambda: datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc),
        instance_uid_factory=lambda: INSTANCE_UID,
    )


def _candidate_controller(
    port: FakeMcuMaintenancePort | None = None,
    *,
    ready: bool = True,
) -> tuple[BusinessControlController, FakeMcuMaintenancePort]:
    candidate_port = port or FakeMcuMaintenancePort()
    controller = BusinessControlController(
        "1.2.3",
        utc_now=lambda: datetime(2026, 9, 2, 5, 0, tzinfo=timezone.utc),
        instance_uid_factory=lambda: INSTANCE_UID,
        mcu_maintenance_port=candidate_port,
        maintenance_handoff_enabled=True,
    )
    if ready:
        controller.mark_ready()
    return controller, candidate_port


def _observe(controller: BusinessControlController) -> str:
    return controller.observe_mcu_maintenance_state({})["evidenceSha256"]


def _quiesce_payload(observation_sha256: str) -> dict:
    return {
        "updateUid": UPDATE_UID,
        "handoffUid": HANDOFF_UID,
        "expectedObservationSha256": observation_sha256,
    }


def _verify_payload(**overrides) -> dict:
    result = {
        "updateUid": UPDATE_UID,
        "handoffUid": HANDOFF_UID,
        "expectedFirmwareIdentitySha256": FIRMWARE_IDENTITY_SHA256,
        "observedFlashEvidenceSha256": FLASH_SHA256,
    }
    result.update(overrides)
    return result


def _prepare_verification(controller: BusinessControlController) -> None:
    observation_sha256 = _observe(controller)
    controller.quiesce_mcu_for_update(_quiesce_payload(observation_sha256))


def test_default_status_does_not_claim_permanent_cutover() -> None:
    controller = _controller()
    starting = controller.health({})
    controller.mark_ready()

    assert starting == {
        "component": BUSINESS_COMPONENT,
        "status": "STARTING",
        "runtimeInstanceUid": str(INSTANCE_UID),
        "releaseVersion": "1.2.3",
        "startedAt": "2026-09-02T05:00:00.000Z",
        "localProtocolName": BUSINESS_PROTOCOL_NAME,
        "localProtocolMajor": 1,
        "localProtocolMinor": 0,
        "managementArchitectureGeneration": "LEGACY_DIRECT",
        "cloudConnectionOwner": "BUSINESS_RUNTIME",
        "jobPermitEnforced": False,
        "maintenanceHandoffEnabled": False,
    }
    assert controller.health({}) == {**starting, "status": "READY"}


def test_stopping_runtime_cannot_be_marked_ready_again() -> None:
    controller = _controller()
    controller.mark_stopping()
    with pytest.raises(RuntimeError, match="cannot become ready"):
        controller.mark_ready()


@pytest.mark.parametrize("version", [None, "", "x\n1", "x" * 65])
def test_release_version_is_strict(version) -> None:
    with pytest.raises(ValueError, match="release version"):
        BusinessControlController(version)


def test_default_builder_registers_only_health_and_status(tmp_path) -> None:
    parent = tmp_path / "business"
    parent.mkdir()
    service = build_business_control_service(
        parent / "control.sock",
        release_version="1.2.3",
        allowed_uids={0, 1234},
        socket_gid=5678,
        mcu_maintenance_port=FakeMcuMaintenancePort(),
        updater_uids={9999},
    )

    assert set(service.server.actions) == {"HEALTH", "GET_STATUS"}
    assert service.server.allowed_uids == frozenset({0, 1234})
    assert service.controller.health({})["maintenanceHandoffEnabled"] is False


def test_candidate_requires_port_and_nonempty_updater_uids(tmp_path) -> None:
    parent = tmp_path / "business"
    parent.mkdir()
    with pytest.raises(ValueError, match="updater_uids"):
        build_business_control_service(
            parent / "control.sock",
            release_version="1.2.3",
            allowed_uids={100},
            socket_gid=5678,
            enable_mcu_maintenance_candidate=True,
            mcu_maintenance_port=FakeMcuMaintenancePort(),
            updater_uids=set(),
        )
    with pytest.raises(ValueError, match="handoff port"):
        build_business_control_service(
            parent / "control.sock",
            release_version="1.2.3",
            allowed_uids={100},
            socket_gid=5678,
            enable_mcu_maintenance_candidate=True,
            updater_uids={200},
        )
    with pytest.raises(ValueError, match="positive non-root"):
        build_business_control_service(
            parent / "control.sock",
            release_version="1.2.3",
            allowed_uids={0, 100},
            socket_gid=5678,
            enable_mcu_maintenance_candidate=True,
            mcu_maintenance_port=FakeMcuMaintenancePort(),
            updater_uids={0},
        )


def test_candidate_actions_are_updater_only_with_exact_fields(tmp_path) -> None:
    parent = tmp_path / "business"
    parent.mkdir()
    service = build_business_control_service(
        parent / "control.sock",
        release_version="1.2.3",
        allowed_uids={0, 100},
        socket_gid=5678,
        enable_mcu_maintenance_candidate=True,
        mcu_maintenance_port=FakeMcuMaintenancePort(),
        updater_uids={200},
    )

    assert service.server.allowed_uids == frozenset({0, 100, 200})
    assert service.server.actions["HEALTH"].allowed_uids == frozenset({0, 100})
    assert service.server.actions[
        "OBSERVE_MCU_MAINTENANCE_STATE"
    ].allowed_uids == frozenset({200})
    assert service.server.actions[
        "QUIESCE_MCU_FOR_UPDATE"
    ].allowed_uids == frozenset({200})
    assert service.server.actions[
        "VERIFY_MCU_AFTER_UPDATE"
    ].allowed_uids == frozenset({200})
    assert service.server.actions[
        "OBSERVE_MCU_MAINTENANCE_STATE"
    ].payload_fields == frozenset()
    assert service.server.actions["QUIESCE_MCU_FOR_UPDATE"].payload_fields == {
        "updateUid",
        "handoffUid",
        "expectedObservationSha256",
    }
    assert service.server.actions["VERIFY_MCU_AFTER_UPDATE"].payload_fields == {
        "updateUid",
        "handoffUid",
        "expectedFirmwareIdentitySha256",
        "observedFlashEvidenceSha256",
    }


@pytest.mark.parametrize(
    ("ready", "stopping", "code"),
    [
        (False, False, "BUSINESS_RUNTIME_NOT_READY"),
        (True, True, "SERVICE_STOPPING"),
    ],
)
def test_maintenance_actions_require_ready_runtime(ready, stopping, code) -> None:
    controller, port = _candidate_controller(ready=ready)
    if stopping:
        controller.mark_stopping()
    with pytest.raises(LocalControlActionError) as raised:
        controller.observe_mcu_maintenance_state({})
    assert raised.value.code == code
    assert port.calls == []


def test_controller_computes_versioned_observation_digest() -> None:
    controller, port = _candidate_controller()
    result = controller.observe_mcu_maintenance_state({})

    assert result["evidenceDomain"] == MCU_MAINTENANCE_EVIDENCE_DOMAIN
    assert result["evidenceSchemaVersion"] == MCU_MAINTENANCE_EVIDENCE_SCHEMA_VERSION
    assert result["evidenceStage"] == "OBSERVE"
    assert result["evidenceSha256"] == mcu_maintenance_evidence_sha256(
        "OBSERVE", port.observation
    )
    assert result["f3FirmwareIdentity"]["mode"] == 1
    assert result["f1SelfTest"]["validFlags"] == 3
    assert result["uartHandedOff"] is False


def test_nonzero_f3_status_remains_diagnostic_but_cannot_authorize_quiesce() -> None:
    port = FakeMcuMaintenancePort()
    port.observation = _evidence(f3=_f3(status_code=2))
    controller, _ = _candidate_controller(port)
    observation_sha256 = _observe(controller)

    with pytest.raises(LocalControlActionError) as raised:
        controller.quiesce_mcu_for_update(_quiesce_payload(observation_sha256))

    assert raised.value.code == "MCU_OBSERVATION_MISMATCH"
    assert [name for name, _ in port.calls] == ["OBSERVE"]


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence(
            f3=_f3(mode=1, safe_flags=0x1F),
            f1=_not_performed_f1(),
            uart_handed_off=True,
        ),
        _evidence(
            f3=_f3(mode=2, status_code=1, safe_flags=0x1F),
            f1=_not_performed_f1(),
            uart_handed_off=True,
        ),
        _evidence(
            f3=_f3(mode=2, safe_flags=0x1E),
            f1=_not_performed_f1(),
            uart_handed_off=True,
        ),
        _evidence(
            f3=_f3(mode=2, safe_flags=0x1F),
            f1=_not_performed_f1(),
            uart_handed_off=False,
        ),
    ],
)
def test_quiesce_requires_exact_mode_status_safe_flags_and_uart(evidence) -> None:
    port = FakeMcuMaintenancePort()
    port.quiesce_evidence = evidence
    controller, _ = _candidate_controller(port)
    observation_sha256 = _observe(controller)

    with pytest.raises(LocalControlActionError) as raised:
        controller.quiesce_mcu_for_update(_quiesce_payload(observation_sha256))

    assert raised.value.code == "MCU_QUIESCE_UNCONFIRMED"


def test_quiesce_digest_binds_ids_and_observation() -> None:
    controller, port = _candidate_controller()
    observation_sha256 = _observe(controller)
    result = controller.quiesce_mcu_for_update(
        _quiesce_payload(observation_sha256)
    )

    assert result["evidenceSha256"] == mcu_maintenance_evidence_sha256(
        "QUIESCE",
        port.quiesce_evidence,
        update_uid=UPDATE_UID,
        handoff_uid=HANDOFF_UID,
        expected_observation_sha256=observation_sha256,
    )
    assert result["f3FirmwareIdentity"]["mode"] == 2
    assert result["f3FirmwareIdentity"]["statusCode"] == 0
    assert result["f3FirmwareIdentity"]["safeFlags"] == 0x1F
    assert result["uartHandedOff"] is True


def test_tampered_expected_observation_is_rejected_before_port_mutation() -> None:
    controller, port = _candidate_controller()
    _observe(controller)

    with pytest.raises(LocalControlActionError) as raised:
        controller.quiesce_mcu_for_update(_quiesce_payload("e" * 64))

    assert raised.value.code == "MCU_OBSERVATION_MISMATCH"
    assert [name for name, _ in port.calls] == ["OBSERVE"]


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence(f3=_f3(mode=2)),
        _evidence(f3=_f3(status_code=1)),
        _evidence(f3=_f3(safe_flags=0x1F)),
        _evidence(
            f1=_f1(
                query_status="TIMEOUT",
                communication_healthy=False,
                valid_flags=0,
                weight_grams=None,
                infrared_blocked=None,
                smoke_state="UNKNOWN",
                smoke_sensor_health="TIMEOUT",
            )
        ),
        _evidence(
            f1=_f1(
                valid_flags=1,
                infrared_blocked=None,
            )
        ),
        _evidence(f1=_f1(weight_grams=350001)),
        _evidence(
            f1=_f1(
                smoke_state="UNKNOWN",
                smoke_sensor_health="SENSOR_FAULT",
            )
        ),
        _evidence(uart_handed_off=True),
    ],
)
def test_verify_requires_exact_application_f3_healthy_f1_and_uart(evidence) -> None:
    port = FakeMcuMaintenancePort()
    port.verification_evidence = evidence
    controller, _ = _candidate_controller(port)
    _prepare_verification(controller)

    with pytest.raises(LocalControlActionError) as raised:
        controller.verify_mcu_after_update(_verify_payload())

    assert raised.value.code == "MCU_VERIFICATION_UNCONFIRMED"


@pytest.mark.parametrize("smoke_state", ["NORMAL", "ALARM"])
@pytest.mark.parametrize("weight_grams", [0, 350000])
@pytest.mark.parametrize("infrared_blocked", [False, True])
def test_verify_accepts_all_explicit_boundary_sensor_facts(
    smoke_state, weight_grams, infrared_blocked
) -> None:
    port = FakeMcuMaintenancePort()
    port.verification_evidence = _evidence(
        f1=_f1(
            smoke_state=smoke_state,
            weight_grams=weight_grams,
            infrared_blocked=infrared_blocked,
        )
    )
    controller, _ = _candidate_controller(port)
    _prepare_verification(controller)

    result = controller.verify_mcu_after_update(_verify_payload())

    assert result["evidenceSha256"] == mcu_maintenance_evidence_sha256(
        "VERIFY",
        port.verification_evidence,
        update_uid=UPDATE_UID,
        handoff_uid=HANDOFF_UID,
        expected_firmware_identity_sha256=FIRMWARE_IDENTITY_SHA256,
        observed_flash_evidence_sha256=FLASH_SHA256,
    )


def test_all_zero_firmware_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="firmware identity"):
        McuFirmwareIdentity(2, 17, "1.2.3", "0" * 16)


@pytest.mark.parametrize(
    "overrides",
    [
        {"status_code": 4},
        {"safe_flags": 0x20},
        {
            "query_status": "TIMEOUT",
            "mode": 1,
            "status_code": 0,
            "safe_flags": 0x0F,
            "firmware_identity": FIRMWARE_IDENTITY,
        },
    ],
)
def test_f3_evidence_rejects_undefined_or_contradictory_wire_facts(
    overrides,
) -> None:
    with pytest.raises(ValueError):
        _f3(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"communication_healthy": False},
        {"valid_flags": 3, "weight_grams": None},
        {"smoke_state": "NORMAL", "smoke_sensor_health": "SENSOR_FAULT"},
        {
            "query_status": "TIMEOUT",
            "communication_healthy": True,
            "valid_flags": 3,
            "weight_grams": 12345,
            "infrared_blocked": False,
            "smoke_state": "NORMAL",
            "smoke_sensor_health": "OK",
        },
    ],
)
def test_f1_evidence_rejects_contradictory_query_facts(overrides) -> None:
    with pytest.raises(ValueError):
        _f1(**overrides)


def test_verify_rejects_f3_identity_that_differs_from_expected_digest() -> None:
    different_identity = McuFirmwareIdentity(
        2,
        18,
        "1.2.4",
        "fedcba9876543210",
    )
    port = FakeMcuMaintenancePort()
    port.verification_evidence = _evidence(
        f3=_f3(firmware_identity=different_identity)
    )
    controller, _ = _candidate_controller(port)
    _prepare_verification(controller)

    with pytest.raises(LocalControlActionError) as raised:
        controller.verify_mcu_after_update(_verify_payload())

    assert raised.value.code == "MCU_IDENTITY_MISMATCH"


def test_verify_requires_matching_quiesce_stage() -> None:
    controller, port = _candidate_controller()

    with pytest.raises(LocalControlActionError) as raised:
        controller.verify_mcu_after_update(_verify_payload())

    assert raised.value.code == "MCU_HANDOFF_NOT_CONFIRMED"
    assert port.calls == []


def test_same_mutation_is_idempotent_and_does_not_touch_port_twice() -> None:
    controller, port = _candidate_controller()
    observation_sha256 = _observe(controller)
    payload = _quiesce_payload(observation_sha256)

    first = controller.quiesce_mcu_for_update(payload)
    second = controller.quiesce_mcu_for_update(payload)

    assert first == second
    assert [name for name, _ in port.calls].count("QUIESCE") == 1


def test_same_verify_identity_retries_after_unconfirmed_observation() -> None:
    class RecoveringPort(FakeMcuMaintenancePort):
        def verify_mcu_after_update(self, **payload) -> McuMaintenanceEvidence:
            self.calls.append(("VERIFY", payload))
            if [name for name, _ in self.calls].count("VERIFY") == 1:
                return _evidence(f3=_f3(safe_flags=0x1F))
            return self.verification_evidence

    port = RecoveringPort()
    controller, _ = _candidate_controller(port)
    _prepare_verification(controller)

    with pytest.raises(LocalControlActionError) as first:
        controller.verify_mcu_after_update(_verify_payload())
    assert first.value.code == "MCU_VERIFICATION_UNCONFIRMED"

    recovered = controller.verify_mcu_after_update(_verify_payload())

    assert recovered["evidenceStage"] == "VERIFY"
    assert [name for name, _ in port.calls].count("VERIFY") == 2


def test_failed_identity_still_rejects_changed_retry_payload() -> None:
    port = FakeMcuMaintenancePort()
    port.verification_evidence = _evidence(f3=_f3(safe_flags=0x1F))
    controller, _ = _candidate_controller(port)
    _prepare_verification(controller)

    with pytest.raises(LocalControlActionError):
        controller.verify_mcu_after_update(_verify_payload())
    with pytest.raises(LocalControlActionError) as changed:
        controller.verify_mcu_after_update(
            _verify_payload(observedFlashEvidenceSha256="e" * 64)
        )

    assert changed.value.code == "MCU_MAINTENANCE_IDEMPOTENCY_CONFLICT"
    assert [name for name, _ in port.calls].count("VERIFY") == 1


def test_same_ids_with_tampered_helper_receipt_are_idempotency_conflict() -> None:
    controller, port = _candidate_controller()
    _prepare_verification(controller)
    controller.verify_mcu_after_update(_verify_payload())

    with pytest.raises(LocalControlActionError) as raised:
        controller.verify_mcu_after_update(
            _verify_payload(observedFlashEvidenceSha256="e" * 64)
        )

    assert raised.value.code == "MCU_MAINTENANCE_IDEMPOTENCY_CONFLICT"
    assert [name for name, _ in port.calls].count("VERIFY") == 1


def test_retry_while_first_call_runs_cannot_start_a_second_port_operation() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingPort(FakeMcuMaintenancePort):
        def quiesce_mcu_for_update(self, **payload) -> McuMaintenanceEvidence:
            self.calls.append(("QUIESCE", payload))
            entered.set()
            assert release.wait(2)
            return self.quiesce_evidence

    port = BlockingPort()
    controller, _ = _candidate_controller(port)
    payload = _quiesce_payload(_observe(controller))
    outcome: list[object] = []
    worker = threading.Thread(
        target=lambda: outcome.append(controller.quiesce_mcu_for_update(payload))
    )
    worker.start()
    assert entered.wait(1)

    with pytest.raises(LocalControlActionError) as raised:
        controller.quiesce_mcu_for_update(payload)
    assert raised.value.code == "MCU_MAINTENANCE_IN_PROGRESS"

    release.set()
    worker.join(2)
    assert not worker.is_alive()
    assert len(outcome) == 1
    assert [name for name, _ in port.calls].count("QUIESCE") == 1


def test_service_stop_waits_for_inflight_maintenance_port_call() -> None:
    entered = threading.Event()
    release = threading.Event()

    class BlockingPort(FakeMcuMaintenancePort):
        def observe_mcu_maintenance_state(self) -> McuMaintenanceEvidence:
            self.calls.append(("OBSERVE", {}))
            entered.set()
            assert release.wait(2)
            return self.observation

    class FakeServer:
        is_running = True
        failure = None

        def __init__(self) -> None:
            self.stopped = threading.Event()

        def start(self) -> None:
            self.is_running = True

        def stop(self) -> None:
            self.is_running = False
            self.stopped.set()

    port = BlockingPort()
    controller, _ = _candidate_controller(port)
    server = FakeServer()
    service = BusinessControlService(controller, server)  # type: ignore[arg-type]
    service.start()
    action = threading.Thread(
        target=lambda: controller.observe_mcu_maintenance_state({})
    )
    action.start()
    assert entered.wait(1)

    stopping = threading.Thread(target=service.stop)
    stopping.start()
    assert not server.stopped.wait(0.05)
    assert controller.health({})["status"] == "STOPPING"

    release.set()
    action.join(2)
    stopping.join(2)
    assert not action.is_alive()
    assert not stopping.is_alive()
    assert server.stopped.is_set()


def test_digest_changes_for_stage_ids_helper_receipt_and_each_fact_group() -> None:
    evidence = _evidence()
    base = mcu_maintenance_evidence_sha256(
        "VERIFY",
        evidence,
        update_uid=UPDATE_UID,
        handoff_uid=HANDOFF_UID,
        expected_firmware_identity_sha256=FIRMWARE_IDENTITY_SHA256,
        observed_flash_evidence_sha256=FLASH_SHA256,
    )
    variants = [
        (OTHER_UPDATE_UID, HANDOFF_UID, FLASH_SHA256, evidence),
        (UPDATE_UID, OTHER_HANDOFF_UID, FLASH_SHA256, evidence),
        (UPDATE_UID, HANDOFF_UID, "e" * 64, evidence),
        (
            UPDATE_UID,
            HANDOFF_UID,
            FLASH_SHA256,
            replace(evidence, uart_handed_off=True),
        ),
        (
            UPDATE_UID,
            HANDOFF_UID,
            FLASH_SHA256,
            replace(evidence, f3=_f3(safe_flags=1)),
        ),
        (
            UPDATE_UID,
            HANDOFF_UID,
            FLASH_SHA256,
            replace(
                evidence,
                f3=_f3(
                    firmware_identity=McuFirmwareIdentity(
                        2, 18, "1.2.4", "fedcba9876543210"
                    )
                ),
            ),
        ),
        (
            UPDATE_UID,
            HANDOFF_UID,
            FLASH_SHA256,
            replace(evidence, f1=_f1(weight_grams=12346)),
        ),
    ]

    assert all(
        mcu_maintenance_evidence_sha256(
            "VERIFY",
            changed,
            update_uid=update_uid,
            handoff_uid=handoff_uid,
            expected_firmware_identity_sha256=FIRMWARE_IDENTITY_SHA256,
            observed_flash_evidence_sha256=flash_sha256,
        )
        != base
        for update_uid, handoff_uid, flash_sha256, changed in variants
    )
    assert mcu_maintenance_evidence_sha256("OBSERVE", evidence) != base


def test_request_fields_uuid_and_sha_are_strict() -> None:
    controller, port = _candidate_controller()
    observation_sha256 = _observe(controller)
    bad = _quiesce_payload(observation_sha256)
    bad["extra"] = True
    with pytest.raises(LocalControlActionError) as raised:
        controller.quiesce_mcu_for_update(bad)
    assert raised.value.code == "REQUEST_INVALID"

    bad = _quiesce_payload("A" * 64)
    with pytest.raises(LocalControlActionError) as raised:
        controller.quiesce_mcu_for_update(bad)
    assert raised.value.code == "REQUEST_INVALID"
    assert [name for name, _ in port.calls] == ["OBSERVE"]


def test_port_errors_are_stable_and_unexpected_details_are_hidden() -> None:
    class BusyPort(FakeMcuMaintenancePort):
        def observe_mcu_maintenance_state(self) -> McuMaintenanceEvidence:
            raise McuMaintenancePortError("MCU_MAINTENANCE_BUSY")

    controller, _ = _candidate_controller(BusyPort())
    with pytest.raises(LocalControlActionError) as raised:
        controller.observe_mcu_maintenance_state({})
    assert raised.value.code == "MCU_MAINTENANCE_BUSY"

    class BrokenPort(FakeMcuMaintenancePort):
        def observe_mcu_maintenance_state(self) -> McuMaintenanceEvidence:
            raise RuntimeError("secret-device-key")

    controller, _ = _candidate_controller(BrokenPort())
    with pytest.raises(LocalControlActionError) as raised:
        controller.observe_mcu_maintenance_state({})
    assert raised.value.code == "MCU_MAINTENANCE_PORT_UNAVAILABLE"
    assert "secret" not in raised.value.message
