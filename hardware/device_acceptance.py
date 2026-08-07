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
    registered asset and its current OneNet connection state.
    """

    def __init__(
        self,
        store,
        uart_link,
        photo_manager,
        uploader,
        *,
        device_name: str,
        edge_software_version: str = "0.1.0",
        maximum_sensor_age_seconds: int = (
            MAXIMUM_SENSOR_EVIDENCE_AGE_SECONDS
        ),
    ):
        if not device_name:
            raise ValueError("device name is required for acceptance")
        if not edge_software_version:
            raise ValueError("edge software version is required")
        if maximum_sensor_age_seconds <= 0:
            raise ValueError("maximum sensor age must be positive")
        self._store = store
        self._uart = uart_link
        self._photo = photo_manager
        self._uploader = uploader
        self._device_name = device_name
        self._edge_software_version = edge_software_version
        self._maximum_sensor_age_seconds = maximum_sensor_age_seconds

    def run(self, command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        challenge_uid = payload["challengeUid"]
        expected_port_count = payload["expectedPortCount"]
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
        mcu_simulated = bool(
            getattr(self._uart, "is_simulated", True)
        )
        mcu_firmware_version = str(
            getattr(self._uart, "_mcu_firmware_version", "")
            or "UNKNOWN"
        )[:64]
        mcu_communication_healthy = bool(
            getattr(self._uart, "is_open", False)
            and getattr(self._uart, "mcu_session_ready", False)
            and mcu_firmware_version != "UNKNOWN"
        )

        sensor_result = self._sensor_evidence(expected_port_count)
        camera_result = self._camera_evidence(
            challenge_uid,
            grant,
        )
        evidence = {
            "evidenceSchemaVersion": 1,
            "challengeUid": challenge_uid,
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
            "camerasSimulated": camera_result["camerasSimulated"],
            "verifiedPortCount": sensor_result["verifiedPortCount"],
            "verifiedCameraCount": camera_result[
                "verifiedCameraCount"
            ],
            "sensorSampleSha256": sensor_result["sha256"],
            "cameraCaptureSha256": camera_result["captureSha256"],
            "cameraUploadSha256": camera_result["uploadSha256"],
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
                not mcu_simulated,
                not camera_result["camerasSimulated"],
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
            "verifiedPortCount": facts["verifiedPortCount"],
            "sha256": canonical_payload_sha256(facts),
        }

    def _fixed_frame_sensor_facts(
        self,
        expected_port_count: int,
    ) -> dict[str, Any]:
        record = self._store.get_state_record(
            "fixed_frame_latest_observation_json"
        )
        observation = None
        if record:
            try:
                candidate = json.loads(record["state_value"])
                observation = candidate if isinstance(candidate, dict) else None
            except (TypeError, ValueError):
                observation = None
        fresh = bool(record and self._is_fresh(record["updated_at"]))
        port_no = observation.get("portNo") if observation else None
        post_weight = (
            observation.get("postWeightGrams") if observation else None
        )
        infrared = (
            observation.get("infraredBlocked") if observation else None
        )
        sample_real = bool(
            observation
            and port_no == 1
            and isinstance(post_weight, int)
            and not isinstance(post_weight, bool)
            and 0 <= post_weight <= MAXIMUM_WEIGHT_GRAMS
            and isinstance(infrared, bool)
            and isinstance(observation.get("mcuBootId"), int)
            and observation.get("mcuBootId", 0) > 0
            and isinstance(observation.get("mcuEventSequence"), int)
            and observation.get("mcuEventSequence", 0) > 0
        )
        return {
            "mode": "FIXED_FRAME",
            "expectedPortCount": expected_port_count,
            "verifiedPortCount": 1,
            "fresh": fresh,
            "observation": observation,
            "healthy": bool(
                expected_port_count == 1 and fresh and sample_real
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
