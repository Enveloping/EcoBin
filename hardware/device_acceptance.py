"""Automatic machine acceptance using only locally provable hardware facts."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from edge_boot import _clock_state
from onenet_wire import canonical_payload_sha256

logger = logging.getLogger("device-acceptance")

MAXIMUM_SENSOR_EVIDENCE_AGE_SECONDS = 10 * 60
MAXIMUM_WEIGHT_GRAMS = 350_000


class DeviceAcceptanceRunner:
    """Run a challenge and persist one reliable pass/fail evidence event.

    This class never decides the authoritative acceptance status.  It reports
    concrete hardware facts; the platform evaluates those facts against the
    registered asset and its current OneNet connection state.  Simulation
    provenance remains diagnostic evidence and does not change functional
    health.
    """

    def __init__(
        self,
        store,
        uart_link,
        photo_manager,
        uploader,
        *,
        device_name: str,
        mcu_remote_update_capable: bool,
        edge_software_version: str = "0.1.0",
        maximum_sensor_age_seconds: int = (
            MAXIMUM_SENSOR_EVIDENCE_AGE_SECONDS
        ),
    ):
        if not device_name:
            raise ValueError("device name is required for acceptance")
        if not edge_software_version:
            raise ValueError("edge software version is required")
        if not isinstance(mcu_remote_update_capable, bool):
            raise ValueError("MCU remote-update capability must be boolean")
        if maximum_sensor_age_seconds <= 0:
            raise ValueError("maximum sensor age must be positive")
        self._store = store
        self._uart = uart_link
        self._photo = photo_manager
        self._uploader = uploader
        self._device_name = device_name
        self._mcu_remote_update_capable = mcu_remote_update_capable
        self._edge_software_version = edge_software_version
        self._maximum_sensor_age_seconds = maximum_sensor_age_seconds

    def run(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        challenge_uid = payload["challengeUid"]
        expected_port_count = payload["expectedPortCount"]
        factory_bag_revision = payload["factoryBagRevision"]
        factory_bag_set_sha256 = payload["factoryBagSetSha256"]
        grant = command.get("cosGrant")
        if not isinstance(grant, dict):
            raise ValueError("acceptance grant not available")

        persistent_store_healthy = self._persistent_store_probe(
            challenge_uid
        )
        configuration_persistence_healthy = (
            self._configuration_persistence_probe(
                challenge_uid,
                expected_port_count,
            )
        )
        trusted_time_healthy = _clock_state() == "SYNCED"
        # ``is_simulated`` describes the serial transport (for example a PTY
        # test double).  Factory simulation firmware runs on a real MCU/UART,
        # so its exact, verified F3 identity is a second provenance signal.
        mcu_simulated = bool(
            getattr(self._uart, "is_simulated", True)
            or getattr(self._uart, "mcu_peripherals_simulated", False)
        )
        mcu_firmware_version = str(
            getattr(self._uart, "_mcu_firmware_version", "")
            or "UNKNOWN"
        )[:64]
        session_communication_healthy = bool(
            getattr(self._uart, "is_open", False)
            and getattr(self._uart, "mcu_session_ready", False)
            and mcu_firmware_version != "UNKNOWN"
        )

        sensor_result = self._sensor_evidence(expected_port_count)
        mcu_communication_healthy = (
            sensor_result["communicationHealthy"]
            if getattr(self._uart, "compatibility_mode", False)
            else session_communication_healthy
        )
        camera_result = self._camera_evidence(
            challenge_uid,
            grant,
        )
        device_entry_url = self._store.get_device_entry_url()
        device_entry_url_stored = device_entry_url is not None
        device_entry_url_sha256 = (
            device_entry_url["deviceEntryUrlSha256"]
            if device_entry_url is not None
            else "0" * 64
        )
        evidence = {
            "evidenceSchemaVersion": 4,
            "challengeUid": challenge_uid,
            "factoryBagRevision": factory_bag_revision,
            "factoryBagSetSha256": factory_bag_set_sha256,
            "edgeSoftwareVersion": self._edge_software_version,
            "edgeProtocolVersion": "2",
            "edgeStoreInstanceUid": (
                self._store.get_or_create_edge_store_instance_uid()
            ),
            "mcuFirmwareVersion": mcu_firmware_version,
            "persistentStoreHealthy": persistent_store_healthy,
            "trustedTimeHealthy": trusted_time_healthy,
            "configurationPersistenceHealthy": (
                configuration_persistence_healthy
            ),
            "mcuCommunicationHealthy": mcu_communication_healthy,
            "sensorsHealthy": sensor_result["healthy"],
            "camerasCaptureHealthy": camera_result[
                "captureHealthy"
            ],
            "cameraUploadHealthy": camera_result["uploadHealthy"],
            "mcuSimulated": mcu_simulated,
            "mcuRemoteUpdateCapable": self._mcu_remote_update_capable,
            "camerasSimulated": camera_result["camerasSimulated"],
            "verifiedPortCount": sensor_result["verifiedPortCount"],
            "verifiedCameraCount": camera_result[
                "verifiedCameraCount"
            ],
            "sensorSampleSha256": sensor_result["sha256"],
            "cameraCaptureSha256": camera_result["captureSha256"],
            "cameraUploadSha256": camera_result["uploadSha256"],
            "deviceEntryUrlStored": device_entry_url_stored,
            "deviceEntryUrlSha256": device_entry_url_sha256,
        }
        event = self._store.complete_device_acceptance(
            command,
            evidence,
        )
        logger.info(
            "device acceptance evidence recorded: challenge=%s "
            "hardware_ok=%s",
            challenge_uid,
            all((
                persistent_store_healthy,
                configuration_persistence_healthy,
                trusted_time_healthy,
                mcu_communication_healthy,
                sensor_result["healthy"],
                camera_result["captureHealthy"],
                camera_result["uploadHealthy"],
                device_entry_url_stored,
            )),
        )
        return {
            "challengeUid": challenge_uid,
            "evidenceEventUid": event["eventUid"],
            "disposition": "EVIDENCE_RECORDED",
        }

    def _persistent_store_probe(self, challenge_uid: str) -> bool:
        if not self._store.integrity_check():
            return False
        marker = json.dumps(
            {
                "challengeUid": challenge_uid,
                "probe": "PERSISTENT_STORE",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self._store.set_state("acceptance_storage_probe", marker)
            return self._store.get_state(
                "acceptance_storage_probe"
            ) == marker
        except Exception:
            logger.exception("acceptance persistent-store probe failed")
            return False

    def _configuration_persistence_probe(
        self,
        challenge_uid: str,
        expected_port_count: int,
    ) -> bool:
        marker = json.dumps(
            {
                "challengeUid": challenge_uid,
                "expectedPortCount": expected_port_count,
                "probe": "CONFIGURATION_PERSISTENCE",
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self._store.set_state(
                "acceptance_configuration_probe_json",
                marker,
            )
            stored = self._store.get_state(
                "acceptance_configuration_probe_json"
            )
            return stored == marker and json.loads(stored) == json.loads(
                marker
            )
        except Exception:
            logger.exception(
                "acceptance configuration-persistence probe failed"
            )
            return False

    def _sensor_evidence(self, expected_port_count: int) -> dict[str, Any]:
        if getattr(self._uart, "compatibility_mode", False):
            facts = self._fixed_frame_sensor_facts(
                expected_port_count
            )
        else:
            facts = self._uart_v1_sensor_facts(expected_port_count)
        return {
            "healthy": facts["healthy"],
            "communicationHealthy": facts.get(
                "communicationHealthy",
                True,
            ),
            "verifiedPortCount": facts["verifiedPortCount"],
            "sha256": canonical_payload_sha256(facts),
        }

    def _fixed_frame_sensor_facts(
        self,
        expected_port_count: int,
    ) -> dict[str, Any]:
        try:
            observation = self._uart.query_self_test(
                timeout_ms=3_000,
                on_result=self._store.save_fixed_frame_self_test,
            )
        except Exception as error:
            logger.warning(
                "fixed-frame acceptance self-test failed: %s",
                type(error).__name__,
            )
            observation = {
                "queryStatus": "ADAPTER_ERROR",
                "communicationHealthy": False,
                "portNo": 1,
                "validFlags": 0,
                "weightValid": False,
                "weightGrams": None,
                "weightMeasurementUid": None,
                "infraredValid": False,
                "infraredBlocked": None,
                "smokeCode": None,
                "smokeState": "UNKNOWN",
                "smokeSensorHealth": "PROTOCOL_ERROR",
                "faultCode": "SMOKE_SENSOR",
                "rawFrameHex": None,
                "adapterError": self._bounded_error(error),
            }
            self._store.save_fixed_frame_self_test(observation)
        if observation.get("communicationHealthy") is True:
            active_uart_fault = self._store.get_active_edge_fault(
                "UART",
                "UART_PROTOCOL",
            )
            if active_uart_fault is not None:
                self._store.recover_fault_and_create_event(
                    device_name=self._device_name,
                    fault_uid=active_uart_fault["fault_uid"],
                    component="UART",
                    fault_code="UART_PROTOCOL",
                    port_no=active_uart_fault["port_no"],
                    recovery_evidence="ACCEPTANCE_SELF_TEST_SUCCEEDED",
                )
        else:
            self._store.observe_fault_and_create_event(
                device_name=self._device_name,
                component="UART",
                fault_code="UART_PROTOCOL",
                severity="BLOCK_DEVICE",
                detail={
                    "reasonCode": observation.get(
                        "queryStatus",
                        "SELF_TEST_FAILED",
                    )
                },
            )
        record = self._store.get_state_record(
            "fixed_frame_latest_self_test_json"
        )
        stored_observation = None
        if record:
            try:
                candidate = json.loads(record["state_value"])
                stored_observation = (
                    candidate if isinstance(candidate, dict) else None
                )
            except (TypeError, ValueError):
                stored_observation = None
        fresh = bool(record and self._is_fresh(record["updated_at"]))
        port_no = (
            stored_observation.get("portNo")
            if stored_observation
            else None
        )
        weight = (
            stored_observation.get("weightGrams")
            if stored_observation
            else None
        )
        infrared = (
            stored_observation.get("infraredBlocked")
            if stored_observation
            else None
        )
        sample_healthy = bool(
            stored_observation
            and port_no == 1
            and stored_observation.get("queryStatus") == "OK"
            and stored_observation.get("communicationHealthy") is True
            and stored_observation.get("validFlags") == 3
            and stored_observation.get("weightValid") is True
            and isinstance(weight, int)
            and not isinstance(weight, bool)
            and 0 <= weight <= MAXIMUM_WEIGHT_GRAMS
            and stored_observation.get("infraredValid") is True
            and isinstance(infrared, bool)
            and stored_observation.get("smokeCode") == 0
            and stored_observation.get("smokeState") == "NORMAL"
            and stored_observation.get("smokeSensorHealth") == "OK"
            and stored_observation.get("faultCode") is None
        )
        return {
            "mode": "FIXED_FRAME",
            "expectedPortCount": expected_port_count,
            "verifiedPortCount": 1,
            "fresh": fresh,
            "communicationHealthy": bool(
                stored_observation
                and stored_observation.get("communicationHealthy") is True
            ),
            "observation": stored_observation,
            "healthy": bool(
                expected_port_count == 1 and fresh and sample_healthy
            ),
        }

    def _uart_v1_sensor_facts(
        self,
        expected_port_count: int,
    ) -> dict[str, Any]:
        record = self._store.get_state_record(
            "latest_runtime_ports_json"
        )
        ports: list[dict[str, Any]] = []
        if record:
            try:
                candidate = json.loads(record["state_value"])
                if isinstance(candidate, list):
                    ports = [
                        item for item in candidate
                        if isinstance(item, dict)
                    ]
            except (TypeError, ValueError):
                ports = []
        fresh = bool(record and self._is_fresh(record["updated_at"]))
        expected_numbers = list(range(1, expected_port_count + 1))
        actual_numbers = [item.get("portNo") for item in ports]
        configured_port_count = getattr(
            self._uart,
            "port_count",
            len(set(actual_numbers)) or expected_port_count,
        )
        if (
            isinstance(configured_port_count, bool)
            or not isinstance(configured_port_count, int)
            or not 1 <= configured_port_count <= 6
        ):
            configured_port_count = expected_port_count
        healthy_ports = [
            item
            for item in ports
            if (
                item.get("weightSensorHealth") == "OK"
                and item.get("weightValueAvailable") is True
                and isinstance(item.get("weightSampleCount"), int)
                and item.get("weightSampleCount", 0) > 0
                and item.get("fullnessSampleBasis") != "NOT_SAMPLED"
                and isinstance(
                    item.get("fullnessValidSampleCount"),
                    int,
                )
                and item.get("fullnessValidSampleCount", 0) > 0
                and item.get("smokeSensorHealth") == "OK"
                and item.get("smokeState") == "NORMAL"
            )
        ]
        return {
            "mode": "UART_V1",
            "expectedPortCount": expected_port_count,
            "verifiedPortCount": configured_port_count,
            "fresh": fresh,
            "communicationHealthy": True,
            "ports": ports,
            "healthy": bool(
                fresh
                and actual_numbers == expected_numbers
                and len(healthy_ports) == expected_port_count
            ),
        }

    def _camera_evidence(
        self,
        challenge_uid: str,
        grant: dict[str, Any],
    ) -> dict[str, Any]:
        probe = self._photo.capture_acceptance_probe(challenge_uid)
        captures = probe["captures"]
        capture_facts = [
            {
                "camera": item["camera"],
                "simulated": item["simulated"],
                "contentSha256": item["contentSha256"],
                "sizeBytes": item["sizeBytes"],
                "error": item["error"],
            }
            for item in captures
        ]
        readbacks: list[dict[str, Any]] = []
        try:
            for capture in captures:
                if not capture["path"]:
                    readbacks.append({
                        "camera": capture["camera"],
                        "uploadedSha256": None,
                        "readbackSha256": None,
                        "error": "CAMERA_CAPTURE_FAILED",
                    })
                    continue
                object_key = (
                    "ecobin/device-acceptance/"
                    f"{challenge_uid}/{capture['camera']}/"
                    f"{uuid.uuid4()}.jpg"
                )
                try:
                    result = self._uploader.upload_and_readback(
                        grant,
                        capture["path"],
                        object_key,
                    )
                    readbacks.append({
                        "camera": capture["camera"],
                        "uploadedSha256": result[
                            "uploadedSha256"
                        ],
                        "readbackSha256": result[
                            "readbackSha256"
                        ],
                        "error": None,
                    })
                except Exception as error:
                    logger.warning(
                        "acceptance camera upload/readback failed: "
                        "camera=%s error=%s",
                        capture["camera"],
                        type(error).__name__,
                    )
                    readbacks.append({
                        "camera": capture["camera"],
                        "uploadedSha256": None,
                        "readbackSha256": None,
                        "error": self._bounded_error(error),
                    })
        finally:
            for capture in captures:
                path = capture.get("path")
                if not path:
                    continue
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
                except OSError:
                    logger.warning(
                        "could not remove local acceptance probe: %s",
                        capture["camera"],
                    )
        verified_camera_count = sum(
            1 for item in captures if item.get("contentSha256")
        )
        capture_healthy = bool(
            len(captures) == 2
            and verified_camera_count == 2
            and all(item.get("error") is None for item in captures)
        )
        upload_healthy = bool(
            len(readbacks) == 2
            and all(
                item["error"] is None
                and item["uploadedSha256"]
                == item["readbackSha256"]
                for item in readbacks
            )
        )
        return {
            "captureHealthy": capture_healthy,
            "uploadHealthy": upload_healthy,
            "camerasSimulated": bool(probe["camerasSimulated"]),
            "verifiedCameraCount": verified_camera_count,
            "captureSha256": canonical_payload_sha256(capture_facts),
            "uploadSha256": canonical_payload_sha256(readbacks),
        }

    def _is_fresh(self, updated_at: str) -> bool:
        try:
            observed = datetime.fromisoformat(updated_at).replace(
                tzinfo=timezone.utc
            )
        except (TypeError, ValueError):
            return False
        age = time.time() - observed.timestamp()
        return 0 <= age <= self._maximum_sensor_age_seconds

    @staticmethod
    def _bounded_error(error: Exception) -> str:
        value = str(error).strip().upper()
        normalized = "".join(
            character if character.isalnum() else "_"
            for character in value
        ).strip("_")
        return (normalized or type(error).__name__.upper())[:64]
