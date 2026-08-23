"""Generate deterministic EcoBin F-10 contract artifacts.

Run from the repository root:

    python contracts/tools/generate_contracts.py
    python contracts/tools/generate_contracts.py --check
    python contracts/tools/generate_contracts.py --include-hardware-mcu

Only authoritative files under contracts/onenet and contracts/uart are edited by
hand.  Everything under generated/ or examples/ is rebuilt here.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import pprint
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from contractlib import (  # noqa: E402
    CONTRACTS_ROOT,
    JsonSchemaSubsetValidator,
    canonical_json_bytes,
    compute_uart_command_digest,
    crc16_ccitt_false,
    decode_uart_frame,
    encode_uart_frame,
    encode_uart_payload,
    flag_bitmap,
    load_json,
    load_uart_registry,
    onenet_command_canonical_preimage,
    onenet_command_canonical_projection,
    onenet_command_canonical_sha256,
    onenet_event_canonical_preimage,
    onenet_event_canonical_projection,
    onenet_event_canonical_sha256,
    payload_sha256,
    source_sha256,
    uart_command_digest_preimage,
    uart_message_specs,
    validate_uart_registry,
)


GENERATED_UART_ROOT = CONTRACTS_ROOT / "uart" / "generated"
GENERATED_ONENET_ROOT = CONTRACTS_ROOT / "onenet" / "generated"
GENERATED_EXAMPLES_ROOT = CONTRACTS_ROOT / "examples"
GENERATED_DOC_ROOT = CONTRACTS_ROOT / "generated"
HARDWARE_UART_PROTOCOL = CONTRACTS_ROOT.parent / "hardware" / "uart_protocol.py"
HARDWARE_ONENET_PROJECTION_MODEL = (
    CONTRACTS_ROOT.parent / "hardware" / "onenet_projection_model.json"
)
HARDWARE_MCU_UART_HEADER = (
    CONTRACTS_ROOT.parent
    / "hardware_mcu"
    / "USER"
    / "uar"
    / "ecobin_uart_protocol.h"
)
HARDWARE_MCU_UART_GOLDEN_TEST = (
    CONTRACTS_ROOT.parent
    / "hardware_mcu"
    / "USER"
    / "uar"
    / "ecobin_uart_golden_test.c"
)


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def onenet_import_json_text(value: Any) -> str:
    """Keep the human-imported OneNet artifact readable with safe size headroom."""
    return json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def macro_name(value: str) -> str:
    separated = re.sub(r"(?<!^)(?=[A-Z])", "_", value)
    return re.sub(r"[^A-Za-z0-9]+", "_", separated).upper().strip("_")


def _message_by_name(registry: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    for message in registry["messages"]:
        if message["name"] == name:
            return message
    raise ValueError(f"unknown message {name!r}")


def build_uart_vectors(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    vectors: list[dict[str, Any]] = []
    frames_by_name: dict[str, bytes] = {}
    for source in registry["goldenVectors"]:
        name = source["name"]
        if "sourceVector" in source:
            original = frames_by_name[source["sourceVector"]]
            frame = bytearray(original)
            if source.get("mutate") == "FLIP_LAST_CRC_BIT":
                frame[-1] ^= 0x01
            else:
                raise ValueError(f"{name}: unsupported mutation")
            frame_bytes = bytes(frame)
            decoded_message_name = None
            message_type = frame_bytes[4]
            flags = frame_bytes[5]
            tx_sequence = int.from_bytes(frame_bytes[8:12], "big")
            payload = frame_bytes[12:-2]
        else:
            flags = flag_bitmap(source.get("flags", []))
            tx_sequence = source["txSequence"]
            decoded_message_name = source.get("message")
            if decoded_message_name:
                message = _message_by_name(registry, decoded_message_name)
                message_type = message["id"]
                source_payload = dict(source["payload"])
                if source_payload.get("commandDigestSha256") == "AUTO":
                    source_payload["commandDigestSha256"] = compute_uart_command_digest(
                        registry,
                        decoded_message_name,
                        {
                            **source_payload,
                            "commandDigestSha256": "0" * 64,
                        },
                    )
                payload = encode_uart_payload(
                    registry,
                    decoded_message_name,
                    source_payload,
                )
                if bool(flags & 0x01) != message["ackRequired"]:
                    raise ValueError(f"{name}: golden vector flags disagree with registry")
            else:
                message_type = source["messageTypeValue"]
                if "rawPayloadHex" in source:
                    payload = bytes.fromhex(source["rawPayloadHex"])
                else:
                    fill = bytes.fromhex(source["fillByteHex"])
                    if len(fill) != 1:
                        raise ValueError(f"{name}: fillByteHex must be one byte")
                    payload = fill * source["payloadLength"]
            frame_bytes = encode_uart_frame(
                registry,
                message_type,
                flags,
                tx_sequence,
                payload,
            )

        expected = source["expected"]
        if expected == "CRC_INVALID":
            try:
                decode_uart_frame(registry, frame_bytes)
            except ValueError:
                pass
            else:
                raise ValueError(f"{name}: mutated vector unexpectedly has valid CRC")
        else:
            decoded = decode_uart_frame(registry, frame_bytes)
            if expected == "VALID" and decoded["messageName"] != decoded_message_name:
                raise ValueError(f"{name}: known message did not decode")
            if (
                expected == "FRAME_VALID_MESSAGE_UNSUPPORTED"
                and decoded["messageName"] is not None
            ):
                raise ValueError(f"{name}: unsupported vector uses a registered message")

        frames_by_name[name] = frame_bytes
        vectors.append(
            {
                "name": name,
                "expected": expected,
                "messageName": decoded_message_name,
                "messageType": message_type,
                "flags": flags,
                "txSequence": tx_sequence,
                "payloadLength": len(payload),
                "payloadHex": payload.hex(),
                "frameLength": len(frame_bytes),
                "frameHex": frame_bytes.hex(),
                "wireCrcHex": frame_bytes[-2:].hex(),
            }
        )
    return vectors


def build_uart_stream_traces(
    registry: Mapping[str, Any],
    vectors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_name = {vector["name"]: bytes.fromhex(vector["frameHex"]) for vector in vectors}
    hello = by_name["hello_edge"]
    ack = by_name["ack_duplicate"]
    config = by_name["payload_contains_magic"]
    clean = by_name["clean_completion_confirmed"]
    crc_invalid = by_name["crc_invalid"]
    unknown = by_name["empty_payload_unknown_message"]

    invalid_length = bytes.fromhex("ec420100010000f300000001")
    wrong_ack = bytearray(hello)
    wrong_ack[5] = 0x01
    wrong_ack[-2:] = crc16_ccitt_false(wrong_ack[2:-2]).to_bytes(2, "big")

    return [
        {
            "name": "bytewise_split",
            "senderRole": "EDGE",
            "chunks": [
                {"atMs": index, "hex": bytes([byte]).hex()}
                for index, byte in enumerate(hello)
            ],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": [],
        },
        {
            "name": "sticky_frames",
            "senderRole": "EDGE",
            "chunks": [{"atMs": 0, "hex": (hello + ack).hex()}],
            "expectedMessageNames": ["HELLO", "ACK"],
            "expectedDiagnostics": [],
        },
        {
            "name": "noise_false_magic_invalid_length",
            "senderRole": "EDGE",
            "chunks": [
                {
                    "atMs": 0,
                    "hex": (bytes.fromhex("00ffec00") + invalid_length + hello).hex(),
                }
            ],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": [
                "NOISE_DISCARDED",
                "INVALID_LENGTH",
                "NOISE_DISCARDED",
            ],
        },
        {
            "name": "crc_failure_resynchronizes",
            "senderRole": "EDGE",
            "chunks": [{"atMs": 0, "hex": (crc_invalid + hello).hex()}],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": ["CRC_INVALID", "NOISE_DISCARDED"],
        },
        {
            "name": "assembly_timeout_resynchronizes",
            "senderRole": "EDGE",
            "chunks": [
                {"atMs": 0, "hex": hello[:8].hex()},
                {"atMs": 100, "hex": ""},
                {"atMs": 101, "hex": hello.hex()},
            ],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": ["FRAME_TIMEOUT"],
        },
        {
            "name": "payload_contains_magic",
            "senderRole": "EDGE",
            "chunks": [{"atMs": 0, "hex": config.hex()}],
            "expectedMessageNames": ["CONFIG_COMMIT"],
            "expectedDiagnostics": [],
        },
        {
            "name": "unknown_type_consumes_crc_valid_frame",
            "senderRole": "EDGE",
            "chunks": [{"atMs": 0, "hex": (unknown + hello).hex()}],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": [
                "SEMANTIC_REJECTED:unsupported UART message type"
            ],
        },
        {
            "name": "ack_flag_checked_after_crc",
            "senderRole": "EDGE",
            "chunks": [{"atMs": 0, "hex": (bytes(wrong_ack) + hello).hex()}],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": [
                "SEMANTIC_REJECTED:ACK_REQUIRED flag differs from the message Registry"
            ],
        },
        {
            "name": "direction_checked_after_crc",
            "senderRole": "EDGE",
            "chunks": [{"atMs": 0, "hex": (clean + hello).hex()}],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": [
                "SEMANTIC_REJECTED:message direction differs from the sender role"
            ],
        },
        {
            "name": "bounded_noise_buffer",
            "senderRole": "EDGE",
            "chunks": [{"atMs": 0, "hex": (bytes(600) + hello).hex()}],
            "expectedMessageNames": ["HELLO"],
            "expectedDiagnostics": ["BUFFER_OVERFLOW", "NOISE_DISCARDED"],
        },
    ]


def _digest_scalar(
    registry: Mapping[str, Any],
    field_type: str,
    value: Any,
    enum_name: str | None = None,
) -> bytes:
    if enum_name is not None:
        value = registry["enums"][enum_name]["values"][value]
    if field_type in {"u8", "u16", "u32", "u64"}:
        size = registry["wireTypes"][field_type]["minimumSize"]
        return int(value).to_bytes(size, "big")
    if field_type == "i32":
        return int(value).to_bytes(4, "big", signed=True)
    if field_type == "bool":
        return bytes([1 if value else 0])
    if field_type == "uuid":
        return uuid.UUID(str(value)).bytes
    if field_type == "sha256":
        return bytes.fromhex(str(value))
    raise ValueError(f"unsupported digest scalar {field_type}")


def _digest_fields(
    registry: Mapping[str, Any],
    specs: Mapping[str, Any],
    message_name: str,
    values: Mapping[str, Any],
    field_names: list[str],
) -> bytes:
    by_name = {
        field["name"]: field for field in specs[message_name]["fields"]
    }
    output = bytearray()
    for name in field_names:
        field = by_name[name]
        output.extend(
            _digest_scalar(
                registry,
                field["type"],
                values[name],
                field.get("enum"),
            )
        )
    return bytes(output)


def build_uart_digest_vectors(
    registry: Mapping[str, Any],
    specs: Mapping[str, Any],
) -> list[dict[str, Any]]:
    authorize_source = next(
        vector
        for vector in registry["goldenVectors"]
        if vector["name"] == "authorize_delivery_first_open"
    )
    authorize_payload = dict(authorize_source["payload"])
    authorize_payload["commandDigestSha256"] = "0" * 64
    command_preimage = uart_command_digest_preimage(
        registry,
        authorize_source["message"],
        authorize_payload,
    )

    config_version = 8
    content_sha = "a1" * 32
    device_values = {
        "continueDeliveryWaitMs": 30000,
        "negativeWeightThresholdGrams": 500,
        "deliveryAutoCloseMs": 120000,
        "weightMeasurementTimeoutMs": 6000,
        "deliveryDoorTravelWaitMs": 30000,
        "cleanSolenoidPulseMs": 1000,
        "smokeMonitoringEnabled": True,
    }
    port_values = [
        {
            "portNo": port_no,
            "enabled": True,
            "unitPriceTenThousandths": 4500 + port_no,
            "fullnessMode": "SENSOR_OR_WEIGHT",
            "configuredFullWeightGrams": 50000,
            "fullnessSettleWaitMs": 5000,
            "fullnessSensorKind": "ULTRASONIC",
            "fullnessDistanceThresholdMm": 600,
            "fullnessSampleCount": 5,
            "fullnessMinimumValidSampleCount": 3,
            "fullnessEchoTimeoutUs": 30000,
            "weightStableWindowMs": 1500,
            "weightMaximumFluctuationGrams": 20,
            "weightRequiredSampleCount": 10,
            "weightMeasurementTimeoutMs": 6000,
            "weightMinimumGrams": -5000,
            "weightMaximumGrams": 100000,
            "calibrationVersion": 4,
        }
        for port_no in (1, 2)
    ]
    device_field_names = [
        "continueDeliveryWaitMs",
        "negativeWeightThresholdGrams",
        "deliveryAutoCloseMs",
        "weightMeasurementTimeoutMs",
        "deliveryDoorTravelWaitMs",
        "cleanSolenoidPulseMs",
        "smokeMonitoringEnabled",
    ]
    port_field_names = [
        "portNo",
        "enabled",
        "unitPriceTenThousandths",
        "fullnessMode",
        "configuredFullWeightGrams",
        "fullnessSettleWaitMs",
        "fullnessSensorKind",
        "fullnessDistanceThresholdMm",
        "fullnessSampleCount",
        "fullnessMinimumValidSampleCount",
        "fullnessEchoTimeoutUs",
        "weightStableWindowMs",
        "weightMaximumFluctuationGrams",
        "weightRequiredSampleCount",
        "weightMeasurementTimeoutMs",
        "weightMinimumGrams",
        "weightMaximumGrams",
        "calibrationVersion",
    ]
    config_preimage = bytearray(
        bytes.fromhex(
            registry["digestProfiles"]["mcuPayloadSha256"]["domainHex"]
        )
    )
    config_preimage.extend(_digest_scalar(registry, "u64", config_version))
    config_preimage.extend(_digest_scalar(registry, "sha256", content_sha))
    config_preimage.extend(_digest_scalar(registry, "u8", len(port_values)))
    config_preimage.extend(
        _digest_fields(
            registry,
            specs,
            "CONFIG_DEVICE_BLOCK",
            device_values,
            device_field_names,
        )
    )
    for port in port_values:
        config_preimage.extend(
            _digest_fields(
                registry,
                specs,
                "CONFIG_PORT_BLOCK",
                port,
                port_field_names,
            )
        )
    config_digest = hashlib.sha256(config_preimage).hexdigest()

    snapshot_uid = "90909090-9090-4090-8090-909090909090"
    begin = {
        "mcuBootId": 101,
        "mcuEventSequence": 1,
        "uptimeMs": 1000,
        "snapshotUid": snapshot_uid,
        "queryCommandUid": "91919191-9191-4191-8191-919191919191",
        "protocolMajor": 1,
        "protocolMinor": 0,
        "firmwareVersionCode": 10000,
        "activeWorkType": "NONE",
        "activeWorkUid": "00000000-0000-0000-0000-000000000000",
        "activePortNo": 0,
        "activeWorkPhase": "IDLE",
        "latestMcuCommandUid": "00000000-0000-0000-0000-000000000000",
        "appliedConfigVersion": config_version,
        "appliedContentSha256": content_sha,
        "appliedMcuPayloadSha256": config_digest,
        "stagingValid": False,
        "stagingApplicationUid": "00000000-0000-0000-0000-000000000000",
        "stagingConfigVersion": 0,
        "stagingMcuPayloadSha256": "0" * 64,
        "stagingPartCount": 0,
        "stagingReceivedPartBitmap": 0,
        "portCount": 2,
        "partIndex": 1,
        "partCount": 4,
        "resetReason": "POWER_ON",
    }
    port_snapshots = []
    for port_no, weight in ((1, 12000), (2, -500)):
        port_snapshots.append(
            {
                "mcuBootId": 101,
                "mcuEventSequence": 1 + port_no,
                "uptimeMs": 1000 + port_no,
                "snapshotUid": snapshot_uid,
                "partIndex": 1 + port_no,
                "partCount": 4,
                "portNo": port_no,
                "lastDeliveryDoorCommand": "NONE",
                "lastDeliveryDoorOutputStatus": "NOT_DISPATCHED",
                "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
                "cleanLockPowerState": "DEENERGIZED",
                "cleanSolenoidHealth": "OK",
                "cleanDoorStateBasis": "NOT_OBSERVABLE",
                "cleanerPhysicalCloseConfirmed": False,
                "measurementStatus": "STABLE",
                "weightValuePresent": True,
                "reportedWeightGrams": weight,
                "weightValueKind": "STABLE_WINDOW_MEAN",
                "measurementElapsedMs": 1000,
                "sampleCount": 10,
                "calibrationVersion": 4,
                "weightSensorHealth": "OK",
                "faultCode": "NONE",
                "fullnessSensorKind": "ULTRASONIC",
                "fullnessSensorValue": "CLEAR",
                "fullnessSampleBasis": "MEASURED_MEDIAN",
                "representativeDistancePresent": True,
                "representativeDistanceMm": 800,
                "fullnessValidSampleCount": 5,
                "smokeState": "NORMAL",
                "smokeSensorHealth": "OK",
                "faultBitmap": 0,
            }
        )
    end = {
        "mcuBootId": 101,
        "mcuEventSequence": 4,
        "uptimeMs": 1004,
        "snapshotUid": snapshot_uid,
        "partIndex": 4,
        "partCount": 4,
        "pendingCriticalEventCount": 0,
        "oldestPendingEventBootId": 0,
        "oldestPendingEventSequence": 0,
        "latestPendingEventBootId": 0,
        "latestPendingEventSequence": 0,
        "snapshotSha256": "0" * 64,
    }
    begin_fields = [
        field["name"]
        for field in specs["STATE_SNAPSHOT_BEGIN"]["fields"]
        if field["name"]
        not in {
            "mcuBootId",
            "mcuEventSequence",
            "uptimeMs",
            "partIndex",
            "partCount",
        }
    ]
    port_fields = [
        field["name"]
        for field in specs["STATE_SNAPSHOT_PORT"]["fields"]
        if field["name"]
        not in {
            "mcuBootId",
            "mcuEventSequence",
            "uptimeMs",
            "snapshotUid",
            "partIndex",
            "partCount",
        }
    ]
    end_fields = [
        "pendingCriticalEventCount",
        "oldestPendingEventBootId",
        "oldestPendingEventSequence",
        "latestPendingEventBootId",
        "latestPendingEventSequence",
    ]
    snapshot_preimage = bytearray(
        bytes.fromhex(registry["digestProfiles"]["snapshotSha256"]["domainHex"])
    )
    snapshot_preimage.extend(
        _digest_fields(
            registry,
            specs,
            "STATE_SNAPSHOT_BEGIN",
            begin,
            begin_fields,
        )
    )
    for port in port_snapshots:
        snapshot_preimage.extend(
            _digest_fields(
                registry,
                specs,
                "STATE_SNAPSHOT_PORT",
                port,
                port_fields,
            )
        )
    snapshot_preimage.extend(
        _digest_fields(
            registry,
            specs,
            "STATE_SNAPSHOT_END",
            end,
            end_fields,
        )
    )
    snapshot_digest = hashlib.sha256(snapshot_preimage).hexdigest()
    end["snapshotSha256"] = snapshot_digest
    encode_uart_payload(registry, "STATE_SNAPSHOT_BEGIN", begin)
    for port in port_snapshots:
        encode_uart_payload(registry, "STATE_SNAPSHOT_PORT", port)
    encode_uart_payload(registry, "STATE_SNAPSHOT_END", end)

    source_vectors = [
        (
            "command_authorize_first_open",
            "commandDigestSha256",
            command_preimage,
            {
                "message": authorize_source["message"],
                "payload": authorize_payload,
            },
        ),
        (
            "mcu_configuration_two_ports",
            "mcuPayloadSha256",
            bytes(config_preimage),
            {
                "configVersion": config_version,
                "contentSha256": content_sha,
                "device": device_values,
                "ports": port_values,
            },
        ),
        (
            "state_snapshot_two_ports",
            "snapshotSha256",
            bytes(snapshot_preimage),
            {
                "begin": begin,
                "ports": port_snapshots,
                "end": end,
            },
        ),
    ]
    return [
        {
            "name": name,
            "profile": profile,
            "components": components,
            "preimageHex": preimage.hex(),
            "sha256": hashlib.sha256(preimage).hexdigest(),
        }
        for name, profile, preimage, components in source_vectors
    ]


def render_python_module(
    registry: Mapping[str, Any],
    specs: Mapping[str, Any],
    registry_digest: str,
) -> str:
    registry_literal = pprint.pformat(dict(registry), width=100, sort_dicts=True)
    specs_literal = pprint.pformat(dict(specs), width=100, sort_dicts=True)
    return f'''"""Generated from contracts/uart/uart-registry.yaml.

DO NOT EDIT. Registry SHA-256: {registry_digest}
Compatible with Python 3.11+.
"""

from __future__ import annotations

import struct
import hashlib
import uuid
from typing import Any, Mapping

REGISTRY_SHA256 = "{registry_digest}"
BAUD_RATE = {registry["physicalLink"]["baudRate"]}
DATA_BITS = {registry["physicalLink"]["dataBits"]}
PARITY = "{registry["physicalLink"]["parity"]}"
STOP_BITS = {registry["physicalLink"]["stopBits"]}
FLOW_CONTROL = "{registry["physicalLink"]["flowControl"]}"
PROTOCOL_MAJOR = {registry["protocol"]["major"]}
PROTOCOL_MINOR = {registry["protocol"]["minor"]}
MAGIC = bytes.fromhex("{registry["protocol"]["magicHex"]}")
MAXIMUM_FRAME_LENGTH = {registry["protocol"]["maximumFrameLength"]}
MAXIMUM_PAYLOAD_LENGTH = {registry["protocol"]["maximumPayloadLength"]}
ACK_REQUIRED = 0x01

REGISTRY = {registry_literal}
MESSAGE_SPECS = {specs_literal}
MESSAGE_TYPE = {{message["name"]: message["id"] for message in REGISTRY["messages"]}}
MESSAGE_NAME = {{value: key for key, value in MESSAGE_TYPE.items()}}


class ProtocolError(ValueError):
    pass


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = (((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)) & 0xFFFF
    return crc


def _enum_value(field: Mapping[str, Any], value: Any) -> Any:
    enum_name = field.get("enum")
    if not enum_name:
        return value
    values = REGISTRY["enums"][enum_name]["values"]
    if isinstance(value, str):
        if value not in values:
            raise ProtocolError(f"{{field['name']}}: unknown enum symbol {{value!r}}")
        return values[value]
    if value not in values.values():
        raise ProtocolError(f"{{field['name']}}: invalid enum value {{value!r}}")
    return value


def _check_int(field: Mapping[str, Any], value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{{field['name']}}: integer required")
    wire = REGISTRY["wireTypes"][field["type"]]
    minimum = field.get("minimum", wire.get("minimum"))
    maximum = field.get("maximum", wire.get("maximum"))
    if minimum is not None and value < minimum:
        raise ProtocolError(f"{{field['name']}}: below minimum")
    if maximum is not None and value > maximum:
        raise ProtocolError(f"{{field['name']}}: above maximum")
    if "const" in field and value != field["const"]:
        raise ProtocolError(f"{{field['name']}}: constant differs")
    return value


ZERO_UUID = "00000000-0000-0000-0000-000000000000"
ZERO_SHA256 = "0" * 64


def _zero_slot(field: Mapping[str, Any], value: Any) -> bool:
    field_type = field["type"]
    if field_type in {{"u8", "u16", "u32", "u64", "i32"}}:
        return value == 0
    if field_type == "bool":
        return value is False
    if field_type == "uuid":
        return uuid.UUID(str(value)).int == 0
    if field_type == "sha256":
        return (value.hex() if isinstance(value, bytes) else str(value)) == ZERO_SHA256
    return False


def compute_command_digest(message_name: str, values: Mapping[str, Any]) -> str:
    fields = MESSAGE_SPECS[message_name]["fields"]
    if [field["name"] for field in fields[:2]] != [
        "mcuCommandUid",
        "commandDigestSha256",
    ]:
        raise ProtocolError(message_name + " has no commandIdentity prefix")
    normalized = dict(values)
    normalized["commandDigestSha256"] = ZERO_SHA256
    payload = encode_payload(message_name, normalized, validate_semantics=False)
    semantic = payload[48:]
    profile = REGISTRY["digestProfiles"]["commandDigestSha256"]
    preimage = (
        bytes.fromhex(profile["domainHex"])
        + bytes([MESSAGE_TYPE[message_name]])
        + len(semantic).to_bytes(2, "big")
        + semantic
    )
    return hashlib.sha256(preimage).hexdigest()


def _validate_measurement(message_name: str, values: Mapping[str, Any]) -> None:
    if "measurementStatus" not in values:
        return
    status = values["measurementStatus"]
    value_present = values["weightValuePresent"]
    value_kind = values["weightValueKind"]
    health = values["weightSensorHealth"]
    fault = values["faultCode"]
    if status == "STABLE":
        if (
            not value_present
            or value_kind != "STABLE_WINDOW_MEAN"
            or health != "OK"
            or fault != "NONE"
            or values["sampleCount"] < 1
        ):
            raise ProtocolError(message_name + ": invalid STABLE measurement")
        return
    if value_present == (value_kind == "NONE"):
        raise ProtocolError(message_name + ": inconsistent weight value presence")
    if status == "UNSTABLE" and (
        not value_present
        or value_kind not in ("LAST_FOUR_MEAN", "AVAILABLE_SAMPLES_MEAN")
    ):
        raise ProtocolError(message_name + ": UNSTABLE needs a fallback mean")
    expected = {{
        "UNSTABLE": (("OK",), "WEIGHT_UNSTABLE"),
        "TIMEOUT": (("TIMEOUT",), "WEIGHT_TIMEOUT"),
        "SENSOR_FAULT": (("SENSOR_FAULT", "UNKNOWN"), "WEIGHT_SENSOR"),
        "OVERLOAD": (("OVERLOAD",), "WEIGHT_OVERLOAD"),
        "PROTOCOL_ERROR": (("PROTOCOL_ERROR",), "WEIGHT_PROTOCOL"),
        "CONFIG_ERROR": (("CONFIG_ERROR",), "WEIGHT_CONFIG"),
        "DISCONNECTED": (("DISCONNECTED",), "WEIGHT_DISCONNECTED"),
    }}
    allowed_health, expected_fault = expected[status]
    if health not in allowed_health or fault != expected_fault:
        raise ProtocolError(message_name + ": invalid failed measurement")


def _validate_manual_close(message_name: str, values: Mapping[str, Any]) -> None:
    if message_name not in ("STATE_SNAPSHOT_PORT", "CLEAN_COMPLETION_CONFIRMED"):
        return
    expected = (
        "CLEANER_CONFIRMATION"
        if values["cleanerPhysicalCloseConfirmed"]
        else "NOT_OBSERVABLE"
    )
    if values["cleanDoorStateBasis"] != expected:
        raise ProtocolError(message_name + ": invalid cleaner confirmation basis")


def validate_payload_semantics(
    message_name: str,
    values: Mapping[str, Any],
    *,
    verify_command_digest: bool = True,
) -> None:
    for field in MESSAGE_SPECS[message_name]["fields"]:
        condition = field.get("invalidWhen")
        if condition and condition.endswith("=false"):
            controller = condition[:-6]
            if values[controller] is False and not _zero_slot(
                field, values[field["name"]]
            ):
                raise ProtocolError(field["name"] + ": invalid slot is not zero")
    _validate_measurement(message_name, values)
    _validate_manual_close(message_name, values)
    if message_name == "NACK" and values["errorCode"] == "NONE":
        raise ProtocolError("NACK cannot use NONE")
    if message_name == "SAFE_CLOSE":
        if values["scope"] == "ALL_DELIVERY_DOORS" and values["portNo"] != 0:
            raise ProtocolError("ALL_DELIVERY_DOORS requires portNo=0")
        if values["scope"] == "SINGLE_DELIVERY_DOOR" and values["portNo"] == 0:
            raise ProtocolError("SINGLE_DELIVERY_DOOR requires a port")
    if message_name in ("DELIVERY_DOOR_COMMAND_RESULT", "SAFE_CLOSE_RESULT"):
        if values["physicalDoorStateBasis"] != "NOT_OBSERVABLE":
            raise ProtocolError("delivery-door physical state is not observable")
        if values["command"] == "NONE":
            raise ProtocolError("door command NONE is snapshot-only")
        status = values["outputStatus"]
        if status == "COMMAND_DISPATCHED":
            if values["faultCode"] != "NONE":
                raise ProtocolError("successful door output has a fault")
        elif status == "COALESCED_WITH_EXISTING_CLOSE":
            if values["faultCode"] != "NONE":
                raise ProtocolError("coalesced close has a fault")
        elif status == "COMMAND_SUPERSEDED_BEFORE_DISPATCH":
            if values["faultCode"] != "NONE":
                raise ProtocolError("superseded door command has a fault")
        elif status == "OUTPUT_REJECTED":
            if values["faultCode"] not in (
                "DELIVERY_DOOR_OUTPUT_REJECTED",
                "DELIVERY_DOOR_HIL_NOT_QUALIFIED",
            ):
                raise ProtocolError("rejected door output has the wrong fault")
        else:
            raise ProtocolError("NOT_DISPATCHED is reserved for snapshots")
    if message_name == "CONFIG_PORT_BLOCK":
        if (
            values["fullnessMinimumValidSampleCount"]
            > values["fullnessSampleCount"]
        ):
            raise ProtocolError("minimum valid fullness samples exceed total")
    if message_name in ("FULLNESS_SAMPLE_RESULT", "STATE_SNAPSHOT_PORT"):
        measured = values["fullnessSampleBasis"] == "MEASURED_MEDIAN"
        if measured != values["representativeDistancePresent"]:
            raise ProtocolError("fullness sample basis/distance mismatch")
        if not measured and values["fullnessSensorValue"] != "CLEAR":
            raise ProtocolError("fullness fallback must be CLEAR")
        if (
            message_name == "FULLNESS_SAMPLE_RESULT"
            and values["validSampleCount"] > values["requestedSampleCount"]
        ):
            raise ProtocolError("valid fullness samples exceed requested samples")
    if message_name == "STATE_SNAPSHOT_PORT":
        if values["deliveryDoorPhysicalStateBasis"] != "NOT_OBSERVABLE":
            raise ProtocolError("delivery-door physical state is not observable")
        no_command = values["lastDeliveryDoorCommand"] == "NONE"
        no_result = values["lastDeliveryDoorOutputStatus"] == "NOT_DISPATCHED"
        if no_command != no_result:
            raise ProtocolError("inconsistent last door command/result tuple")
    if message_name == "CLEAN_COMPLETION_CONFIRMED":
        if (
            values["lockPowerState"] != "DEENERGIZED"
            or values["cleanDoorStateBasis"] != "CLEANER_CONFIRMATION"
            or not values["cleanerPhysicalCloseConfirmed"]
        ):
            raise ProtocolError("invalid clean completion confirmation")
    if message_name == "STATE_SNAPSHOT_END":
        queue_fields = (
            "oldestPendingEventBootId",
            "oldestPendingEventSequence",
            "latestPendingEventBootId",
            "latestPendingEventSequence",
        )
        if values["pendingCriticalEventCount"] == 0:
            if any(values[name] != 0 for name in queue_fields):
                raise ProtocolError(
                    "empty pending-event queue requires zero range"
                )
        elif any(values[name] == 0 for name in queue_fields):
            raise ProtocolError(
                "nonempty pending-event queue requires complete range"
            )
    if message_name == "BOOT_RECONCILIATION_RESULT":
        no_work = values["decision"] == "CONFIRM_NO_ACTIVE_WORK"
        if no_work:
            valid_context = (
                values["activeWorkType"] == "NONE"
                and values["activePortNo"] == 0
                and values["recoveryGeneration"] == 0
                and values["nextCleanActionSequence"] == 0
            )
        else:
            valid_context = (
                values["activeWorkType"] == "CLEAN_OPERATION"
                and values["activePortNo"] > 0
                and values["recoveryGeneration"] > 0
                and values["nextCleanActionSequence"] > 0
            )
        if not valid_context:
            raise ProtocolError("invalid boot reconciliation context")
        if (values["status"] == "ACCEPTED") != (values["faultCode"] == "NONE"):
            raise ProtocolError("boot reconciliation status/fault mismatch")
    if verify_command_digest and "commandDigestSha256" in values:
        actual = values["commandDigestSha256"]
        if isinstance(actual, bytes):
            actual = actual.hex()
        if actual != compute_command_digest(message_name, values):
            raise ProtocolError(message_name + ": command digest mismatch")


def encode_payload(
    message_name: str,
    values: Mapping[str, Any],
    *,
    validate_semantics: bool = True,
) -> bytes:
    if message_name not in MESSAGE_SPECS:
        raise ProtocolError(f"unknown message {{message_name!r}}")
    fields = MESSAGE_SPECS[message_name]["fields"]
    expected = {{field["name"] for field in fields}}
    if set(values) != expected:
        raise ProtocolError(
            f"payload keys differ: missing={{sorted(expected - set(values))}}, "
            f"extra={{sorted(set(values) - expected)}}"
        )
    output = bytearray()
    for field in fields:
        value = _enum_value(field, values[field["name"]])
        field_type = field["type"]
        wire = REGISTRY["wireTypes"][field_type]
        if field_type in {{"u8", "u16", "u32", "u64", "i32"}}:
            number = _check_int(field, value)
            output.extend(
                number.to_bytes(
                    wire["minimumSize"],
                    "big",
                    signed=field_type == "i32",
                )
            )
        elif field_type == "bool":
            if not isinstance(value, bool):
                raise ProtocolError(f"{{field['name']}}: boolean required")
            output.append(1 if value else 0)
        elif field_type == "uuid":
            output.extend(uuid.UUID(str(value)).bytes)
        elif field_type == "sha256":
            raw = value if isinstance(value, bytes) else bytes.fromhex(str(value))
            if len(raw) != 32:
                raise ProtocolError(f"{{field['name']}}: SHA-256 must be 32 bytes")
            output.extend(raw)
        elif field_type == "string_u8":
            if not isinstance(value, str):
                raise ProtocolError(f"{{field['name']}}: string required")
            raw = value.encode("utf-8")
            if len(raw) > field["maxLength"]:
                raise ProtocolError(f"{{field['name']}}: UTF-8 value too long")
            output.append(len(raw))
            output.extend(raw)
        else:
            raise ProtocolError(f"unsupported field type {{field_type}}")
    if len(output) > MAXIMUM_PAYLOAD_LENGTH:
        raise ProtocolError("payload exceeds protocol maximum")
    if validate_semantics:
        validate_payload_semantics(message_name, values)
    return bytes(output)


def decode_payload(message_name: str, payload: bytes) -> dict[str, Any]:
    if message_name not in MESSAGE_SPECS:
        raise ProtocolError(f"unknown message {{message_name!r}}")
    offset = 0
    result: dict[str, Any] = {{}}
    for field in MESSAGE_SPECS[message_name]["fields"]:
        field_type = field["type"]
        wire = REGISTRY["wireTypes"][field_type]
        if field_type == "string_u8":
            if offset >= len(payload):
                raise ProtocolError(f"{{field['name']}}: missing length")
            length = payload[offset]
            offset += 1
            if length > field["maxLength"] or offset + length > len(payload):
                raise ProtocolError(f"{{field['name']}}: invalid length")
            value: Any = payload[offset:offset + length].decode("utf-8")
            offset += length
        else:
            size = wire["minimumSize"]
            if offset + size > len(payload):
                raise ProtocolError(f"{{field['name']}}: truncated")
            raw = payload[offset:offset + size]
            offset += size
            if field_type in {{"u8", "u16", "u32", "u64", "i32"}}:
                value = int.from_bytes(raw, "big", signed=field_type == "i32")
            elif field_type == "bool":
                if raw[0] not in (0, 1):
                    raise ProtocolError(f"{{field['name']}}: invalid boolean")
                value = raw[0] == 1
            elif field_type == "uuid":
                value = str(uuid.UUID(bytes=raw))
            elif field_type == "sha256":
                value = raw.hex()
            else:
                raise ProtocolError(f"unsupported field type {{field_type}}")
        enum_name = field.get("enum")
        if enum_name:
            by_value = {{
                number: symbol
                for symbol, number in REGISTRY["enums"][enum_name]["values"].items()
            }}
            if value not in by_value:
                raise ProtocolError(f"{{field['name']}}: unknown enum value")
            value = by_value[value]
        elif field_type in {{"u8", "u16", "u32", "u64", "i32"}}:
            _check_int(field, value)
        result[field["name"]] = value
    if offset != len(payload):
        raise ProtocolError("trailing payload bytes")
    validate_payload_semantics(message_name, result)
    return result


def encode_frame(message_name: str, tx_sequence: int, payload: bytes) -> bytes:
    if message_name not in MESSAGE_TYPE:
        raise ProtocolError(f"unknown message {{message_name!r}}")
    if not 1 <= tx_sequence <= 0xFFFFFFFF:
        raise ProtocolError("txSequence must be positive uint32")
    if len(payload) > MAXIMUM_PAYLOAD_LENGTH:
        raise ProtocolError("payload too long")
    flags = ACK_REQUIRED if MESSAGE_SPECS[message_name]["ackRequired"] else 0
    body = struct.pack(
        ">BBBBHI",
        PROTOCOL_MAJOR,
        PROTOCOL_MINOR,
        MESSAGE_TYPE[message_name],
        flags,
        len(payload),
        tx_sequence,
    ) + payload
    return MAGIC + body + struct.pack(">H", crc16_ccitt_false(body))


def decode_frame(
    frame: bytes,
    require_known_message: bool = True,
    sender_role: str | None = None,
) -> dict[str, Any]:
    if not 14 <= len(frame) <= MAXIMUM_FRAME_LENGTH:
        raise ProtocolError("invalid frame length")
    if frame[:2] != MAGIC:
        raise ProtocolError("invalid magic")
    major, minor, message_type, flags, payload_length, tx_sequence = struct.unpack(
        ">BBBBHI", frame[2:12]
    )
    if payload_length > MAXIMUM_PAYLOAD_LENGTH or len(frame) != 14 + payload_length:
        raise ProtocolError("invalid payload length")
    if crc16_ccitt_false(frame[2:-2]) != int.from_bytes(frame[-2:], "big"):
        raise ProtocolError("CRC mismatch")
    if (major, minor) != (PROTOCOL_MAJOR, PROTOCOL_MINOR):
        raise ProtocolError("unsupported protocol version")
    if flags & ~ACK_REQUIRED:
        raise ProtocolError("unsupported flags")
    if tx_sequence == 0:
        raise ProtocolError("txSequence zero is reserved")
    message_name = MESSAGE_NAME.get(message_type)
    if require_known_message and message_name is None:
        raise ProtocolError("unsupported UART message type")
    if message_name is not None:
        spec = MESSAGE_SPECS[message_name]
        if bool(flags & ACK_REQUIRED) != spec["ackRequired"]:
            raise ProtocolError(
                "ACK_REQUIRED flag differs from the message Registry"
            )
        if sender_role is not None:
            if sender_role not in ("EDGE", "MCU"):
                raise ProtocolError("sender_role must be EDGE or MCU")
            direction = "EDGE_TO_MCU" if sender_role == "EDGE" else "MCU_TO_EDGE"
            if spec["direction"] not in ("BIDIRECTIONAL", direction):
                raise ProtocolError("message direction differs from the sender role")
    payload = frame[12:-2]
    return {{
        "messageName": message_name,
        "messageType": message_type,
        "flags": flags,
        "txSequence": tx_sequence,
        "payload": payload,
    }}


class StreamParser:
    def __init__(self, *, sender_role: str) -> None:
        if sender_role not in ("EDGE", "MCU"):
            raise ProtocolError("sender_role must be EDGE or MCU")
        self.sender_role = sender_role
        self.buffer = bytearray()
        self.candidate_started_ms: int | None = None
        self.diagnostics: list[str] = []

    @property
    def buffered_bytes(self) -> int:
        return len(self.buffer)

    def _discard_candidate(self, diagnostic: str) -> None:
        self.diagnostics.append(diagnostic)
        del self.buffer[0]
        self.candidate_started_ms = None

    def feed(self, data: bytes, *, now_ms: int) -> list[dict[str, Any]]:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ProtocolError("now_ms must be non-negative")
        self.buffer.extend(data)
        limit = REGISTRY["streamParser"]["inputBufferLimit"]
        if len(self.buffer) > limit:
            del self.buffer[:len(self.buffer) - limit]
            self.candidate_started_ms = None
            self.diagnostics.append("BUFFER_OVERFLOW")
        deadline = REGISTRY["streamParser"]["frameAssemblyDeadlineMs"]
        frames: list[dict[str, Any]] = []
        while True:
            index = self.buffer.find(MAGIC)
            if index < 0:
                keep = 1 if self.buffer.endswith(MAGIC[:1]) else 0
                if len(self.buffer) > keep:
                    del self.buffer[:len(self.buffer) - keep]
                self.candidate_started_ms = None
                break
            if index:
                del self.buffer[:index]
                self.candidate_started_ms = None
                self.diagnostics.append("NOISE_DISCARDED")
            if self.candidate_started_ms is None:
                self.candidate_started_ms = now_ms
            if len(self.buffer) < 12:
                if now_ms - self.candidate_started_ms >= deadline:
                    self._discard_candidate("FRAME_TIMEOUT")
                    continue
                break
            payload_length = int.from_bytes(self.buffer[6:8], "big")
            if payload_length > MAXIMUM_PAYLOAD_LENGTH:
                self._discard_candidate("INVALID_LENGTH")
                continue
            frame_length = 14 + payload_length
            if len(self.buffer) < frame_length:
                if now_ms - self.candidate_started_ms >= deadline:
                    self._discard_candidate("FRAME_TIMEOUT")
                    continue
                break
            candidate = bytes(self.buffer[:frame_length])
            if crc16_ccitt_false(candidate[2:-2]) != int.from_bytes(
                candidate[-2:], "big"
            ):
                self._discard_candidate("CRC_INVALID")
                continue
            try:
                decoded = decode_frame(
                    candidate,
                    require_known_message=True,
                    sender_role=self.sender_role,
                )
            except ProtocolError as error:
                self.diagnostics.append("SEMANTIC_REJECTED:" + str(error))
                del self.buffer[:frame_length]
                self.candidate_started_ms = None
                continue
            frames.append(decoded)
            del self.buffer[:frame_length]
            self.candidate_started_ms = None
        return frames
'''


def render_c_header(
    registry: Mapping[str, Any],
    specs: Mapping[str, Any],
    registry_digest: str,
) -> str:
    lines = [
        "/* Generated from contracts/uart/uart-registry.yaml.",
        " * DO NOT EDIT.",
        f" * Registry SHA-256: {registry_digest}",
        " */",
        "#ifndef ECOBIN_UART_PROTOCOL_H",
        "#define ECOBIN_UART_PROTOCOL_H",
        "",
        "#include <stddef.h>",
        "#include <stdint.h>",
        "#include <string.h>",
        "",
        "/* ARM Compiler 5 uses __inline in C mode. */",
        "#if defined(__CC_ARM) && !defined(__cplusplus)",
        "#define inline __inline",
        "#endif",
        "",
        f'#define ECOBIN_UART_REGISTRY_SHA256 "{registry_digest}"',
        f"#define ECOBIN_UART_BAUD_RATE {registry['physicalLink']['baudRate']}u",
        f"#define ECOBIN_UART_DATA_BITS {registry['physicalLink']['dataBits']}u",
        f"#define ECOBIN_UART_STOP_BITS {registry['physicalLink']['stopBits']}u",
        "#define ECOBIN_UART_PARITY_NONE 1u",
        "#define ECOBIN_UART_FLOW_CONTROL_NONE 1u",
        "#define ECOBIN_UART_MAGIC_0 0xECu",
        "#define ECOBIN_UART_MAGIC_1 0x42u",
        f"#define ECOBIN_UART_PROTOCOL_MAJOR {registry['protocol']['major']}u",
        f"#define ECOBIN_UART_PROTOCOL_MINOR {registry['protocol']['minor']}u",
        f"#define ECOBIN_UART_MAX_FRAME_LENGTH {registry['protocol']['maximumFrameLength']}u",
        f"#define ECOBIN_UART_MAX_PAYLOAD_LENGTH {registry['protocol']['maximumPayloadLength']}u",
        "#define ECOBIN_UART_FLAG_ACK_REQUIRED 0x01u",
        "",
        "typedef enum ecobin_uart_message_type {",
    ]
    for message in registry["messages"]:
        lines.append(
            f"    ECOBIN_UART_MESSAGE_{message['name']} = 0x{message['id']:02X}u,"
        )
    lines.extend(["} ecobin_uart_message_type_t;", ""])

    for enum_name, enum in registry["enums"].items():
        prefix = f"ECOBIN_UART_{macro_name(enum_name)}"
        c_type = {"u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t"}[
            enum["wireType"]
        ]
        lines.append(f"typedef {c_type} ecobin_uart_{macro_name(enum_name).lower()}_t;")
        for symbol, value in enum["values"].items():
            suffix = "u"
            lines.append(f"#define {prefix}_{symbol} {value}{suffix}")
        lines.append("")

    for capability, bit in registry["capabilities"].items():
        lines.append(
            f"#define ECOBIN_UART_CAPABILITY_{capability} (UINT64_C(1) << {bit})"
        )
    lines.append("")

    for name, spec in specs.items():
        lines.append(
            f"#define ECOBIN_UART_{name}_PAYLOAD_MIN_LENGTH "
            f"{spec['minimumPayloadLength']}u"
        )
        lines.append(
            f"#define ECOBIN_UART_{name}_PAYLOAD_MAX_LENGTH "
            f"{spec['maximumPayloadLength']}u"
        )
        for field in spec["fields"]:
            if field["offset"] is not None:
                lines.append(
                    f"#define ECOBIN_UART_{name}_{macro_name(field['name'])}_OFFSET "
                    f"{field['offset']}u"
                )
        lines.append("")

    lines.extend(
        [
            "static inline uint16_t ecobin_uart_crc16_ccitt_false(",
            "    const uint8_t *data, size_t length) {",
            "    uint16_t crc = UINT16_C(0xFFFF);",
            "    size_t index;",
            "    for (index = 0; index < length; ++index) {",
            "        uint8_t bit;",
            "        crc ^= (uint16_t)((uint16_t)data[index] << 8);",
            "        for (bit = 0; bit < 8u; ++bit) {",
            "            crc = (crc & UINT16_C(0x8000))",
            "                ? (uint16_t)((crc << 1) ^ UINT16_C(0x1021))",
            "                : (uint16_t)(crc << 1);",
            "        }",
            "    }",
            "    return crc;",
            "}",
            "",
            "static inline uint16_t ecobin_uart_read_u16_be(const uint8_t *data) {",
            "    return (uint16_t)(((uint16_t)data[0] << 8) | data[1]);",
            "}",
            "",
            "static inline uint32_t ecobin_uart_read_u32_be(const uint8_t *data) {",
            "    return ((uint32_t)data[0] << 24) | ((uint32_t)data[1] << 16)",
            "        | ((uint32_t)data[2] << 8) | (uint32_t)data[3];",
            "}",
            "",
            "static inline void ecobin_uart_write_u16_be(uint8_t *data, uint16_t value) {",
            "    data[0] = (uint8_t)(value >> 8);",
            "    data[1] = (uint8_t)value;",
            "}",
            "",
            "static inline void ecobin_uart_write_u32_be(uint8_t *data, uint32_t value) {",
            "    data[0] = (uint8_t)(value >> 24);",
            "    data[1] = (uint8_t)(value >> 16);",
            "    data[2] = (uint8_t)(value >> 8);",
            "    data[3] = (uint8_t)value;",
            "}",
            "",
            "static inline uint64_t ecobin_uart_read_u64_be(const uint8_t *data) {",
            "    return ((uint64_t)ecobin_uart_read_u32_be(data) << 32)",
            "        | (uint64_t)ecobin_uart_read_u32_be(data + 4u);",
            "}",
            "",
            "static inline int32_t ecobin_uart_read_i32_be(const uint8_t *data) {",
            "    uint32_t raw = ecobin_uart_read_u32_be(data);",
            "    return raw <= UINT32_C(0x7FFFFFFF)",
            "        ? (int32_t)raw",
            "        : (int32_t)((int64_t)raw - INT64_C(4294967296));",
            "}",
            "",
            "static inline void ecobin_uart_write_u64_be(uint8_t *data, uint64_t value) {",
            "    ecobin_uart_write_u32_be(data, (uint32_t)(value >> 32));",
            "    ecobin_uart_write_u32_be(data + 4u, (uint32_t)value);",
            "}",
            "",
            "static inline void ecobin_uart_write_i32_be(uint8_t *data, int32_t value) {",
            "    ecobin_uart_write_u32_be(data, (uint32_t)value);",
            "}",
            "",
            "static inline void ecobin_uart_copy_uuid(uint8_t *target, const uint8_t source[16]) {",
            "    memcpy(target, source, 16u);",
            "}",
            "",
            "static inline void ecobin_uart_copy_sha256(uint8_t *target, const uint8_t source[32]) {",
            "    memcpy(target, source, 32u);",
            "}",
            "",
        ]
    )

    lines.extend(
        [
            "typedef struct ecobin_uart_frame_view {",
            "    uint8_t message_type;",
            "    uint8_t flags;",
            "    uint16_t payload_length;",
            "    uint32_t tx_sequence;",
            "    const uint8_t *payload;",
            "    uint16_t crc16;",
            "} ecobin_uart_frame_view_t;",
            "",
            "static inline int ecobin_uart_message_ack_required(uint8_t message_type) {",
            "    switch (message_type) {",
        ]
    )
    for message in registry["messages"]:
        lines.append(
            f"    case ECOBIN_UART_MESSAGE_{message['name']}: "
            f"return {1 if message['ackRequired'] else 0};"
        )
    lines.extend(
        [
            "    default: return -1;",
            "    }",
            "}",
            "",
            "static inline int ecobin_uart_message_direction(uint8_t message_type) {",
            "    switch (message_type) {",
        ]
    )
    direction_value = {"BIDIRECTIONAL": 0, "EDGE_TO_MCU": 1, "MCU_TO_EDGE": 2}
    for message in registry["messages"]:
        lines.append(
            f"    case ECOBIN_UART_MESSAGE_{message['name']}: "
            f"return {direction_value[message['direction']]};"
        )
    lines.extend(
        [
            "    default: return -1;",
            "    }",
            "}",
            "",
            "static inline int ecobin_uart_validate_frame(",
            "    const uint8_t *frame, size_t length, ecobin_uart_sender_role_t sender,",
            "    ecobin_uart_frame_view_t *view) {",
            "    uint16_t payload_length;",
            "    uint16_t expected_crc;",
            "    uint16_t actual_crc;",
            "    uint8_t message_type;",
            "    uint8_t flags;",
            "    uint32_t tx_sequence;",
            "    int ack_required;",
            "    int direction;",
            "    if (frame == NULL || view == NULL || length < 14u",
            "        || length > ECOBIN_UART_MAX_FRAME_LENGTH) return -1;",
            "    if (frame[0] != ECOBIN_UART_MAGIC_0 || frame[1] != ECOBIN_UART_MAGIC_1) return -2;",
            "    payload_length = ecobin_uart_read_u16_be(frame + 6u);",
            "    if (payload_length > ECOBIN_UART_MAX_PAYLOAD_LENGTH",
            "        || length != (size_t)payload_length + 14u) return -3;",
            "    expected_crc = ecobin_uart_read_u16_be(frame + length - 2u);",
            "    actual_crc = ecobin_uart_crc16_ccitt_false(frame + 2u, length - 4u);",
            "    if (actual_crc != expected_crc) return -4;",
            "    if (frame[2] != ECOBIN_UART_PROTOCOL_MAJOR",
            "        || frame[3] != ECOBIN_UART_PROTOCOL_MINOR) return -5;",
            "    message_type = frame[4];",
            "    flags = frame[5];",
            "    tx_sequence = ecobin_uart_read_u32_be(frame + 8u);",
            "    if ((flags & (UINT8_MAX ^ ECOBIN_UART_FLAG_ACK_REQUIRED)) != 0u",
            "        || tx_sequence == 0u) return -6;",
            "    ack_required = ecobin_uart_message_ack_required(message_type);",
            "    direction = ecobin_uart_message_direction(message_type);",
            "    if (ack_required < 0 || direction < 0) return -7;",
            "    if ((((flags & ECOBIN_UART_FLAG_ACK_REQUIRED) != 0u) ? 1 : 0)",
            "        != ack_required) return -8;",
            "    if (direction != 0 && direction != (int)sender) return -9;",
            "    view->message_type = message_type;",
            "    view->flags = flags;",
            "    view->payload_length = payload_length;",
            "    view->tx_sequence = tx_sequence;",
            "    view->payload = frame + 12u;",
            "    view->crc16 = actual_crc;",
            "    return 0;",
            "}",
            "",
            "static inline int ecobin_uart_encode_frame(",
            "    uint8_t message_type, uint8_t flags, uint32_t tx_sequence,",
            "    const uint8_t *payload, uint16_t payload_length,",
            "    uint8_t *output, size_t output_capacity, size_t *output_length) {",
            "    size_t length = (size_t)payload_length + 14u;",
            "    uint16_t crc;",
            "    if (output == NULL || output_length == NULL",
            "        || payload_length > ECOBIN_UART_MAX_PAYLOAD_LENGTH",
            "        || output_capacity < length || tx_sequence == 0u) return -1;",
            "    if ((flags & (UINT8_MAX ^ ECOBIN_UART_FLAG_ACK_REQUIRED)) != 0u) return -2;",
            "    output[0] = ECOBIN_UART_MAGIC_0;",
            "    output[1] = ECOBIN_UART_MAGIC_1;",
            "    output[2] = ECOBIN_UART_PROTOCOL_MAJOR;",
            "    output[3] = ECOBIN_UART_PROTOCOL_MINOR;",
            "    output[4] = message_type;",
            "    output[5] = flags;",
            "    ecobin_uart_write_u16_be(output + 6u, payload_length);",
            "    ecobin_uart_write_u32_be(output + 8u, tx_sequence);",
            "    if (payload_length > 0u && payload != NULL)",
            "        memcpy(output + 12u, payload, payload_length);",
            "    crc = ecobin_uart_crc16_ccitt_false(output + 2u, 10u + payload_length);",
            "    ecobin_uart_write_u16_be(output + 12u + payload_length, crc);",
            "    *output_length = length;",
            "    return 0;",
            "}",
            "",
            "typedef struct ecobin_uart_sha256_context {",
            "    uint8_t data[64];",
            "    uint32_t data_length;",
            "    uint64_t bit_length;",
            "    uint32_t state[8];",
            "} ecobin_uart_sha256_context_t;",
            "",
            "static inline uint32_t ecobin_uart_sha256_rotr(uint32_t value, uint32_t count) {",
            "    return (value >> count) | (value << (32u - count));",
            "}",
            "",
            "static inline void ecobin_uart_sha256_transform(",
            "    ecobin_uart_sha256_context_t *context, const uint8_t data[64]) {",
            "    static const uint32_t constants[64] = {",
            "        UINT32_C(0x428a2f98), UINT32_C(0x71374491), UINT32_C(0xb5c0fbcf), UINT32_C(0xe9b5dba5),",
            "        UINT32_C(0x3956c25b), UINT32_C(0x59f111f1), UINT32_C(0x923f82a4), UINT32_C(0xab1c5ed5),",
            "        UINT32_C(0xd807aa98), UINT32_C(0x12835b01), UINT32_C(0x243185be), UINT32_C(0x550c7dc3),",
            "        UINT32_C(0x72be5d74), UINT32_C(0x80deb1fe), UINT32_C(0x9bdc06a7), UINT32_C(0xc19bf174),",
            "        UINT32_C(0xe49b69c1), UINT32_C(0xefbe4786), UINT32_C(0x0fc19dc6), UINT32_C(0x240ca1cc),",
            "        UINT32_C(0x2de92c6f), UINT32_C(0x4a7484aa), UINT32_C(0x5cb0a9dc), UINT32_C(0x76f988da),",
            "        UINT32_C(0x983e5152), UINT32_C(0xa831c66d), UINT32_C(0xb00327c8), UINT32_C(0xbf597fc7),",
            "        UINT32_C(0xc6e00bf3), UINT32_C(0xd5a79147), UINT32_C(0x06ca6351), UINT32_C(0x14292967),",
            "        UINT32_C(0x27b70a85), UINT32_C(0x2e1b2138), UINT32_C(0x4d2c6dfc), UINT32_C(0x53380d13),",
            "        UINT32_C(0x650a7354), UINT32_C(0x766a0abb), UINT32_C(0x81c2c92e), UINT32_C(0x92722c85),",
            "        UINT32_C(0xa2bfe8a1), UINT32_C(0xa81a664b), UINT32_C(0xc24b8b70), UINT32_C(0xc76c51a3),",
            "        UINT32_C(0xd192e819), UINT32_C(0xd6990624), UINT32_C(0xf40e3585), UINT32_C(0x106aa070),",
            "        UINT32_C(0x19a4c116), UINT32_C(0x1e376c08), UINT32_C(0x2748774c), UINT32_C(0x34b0bcb5),",
            "        UINT32_C(0x391c0cb3), UINT32_C(0x4ed8aa4a), UINT32_C(0x5b9cca4f), UINT32_C(0x682e6ff3),",
            "        UINT32_C(0x748f82ee), UINT32_C(0x78a5636f), UINT32_C(0x84c87814), UINT32_C(0x8cc70208),",
            "        UINT32_C(0x90befffa), UINT32_C(0xa4506ceb), UINT32_C(0xbef9a3f7), UINT32_C(0xc67178f2)",
            "    };",
            "    uint32_t words[16];",
            "    uint32_t a, b, c, d, e, f, g, h;",
            "    uint32_t index;",
            "    for (index = 0u; index < 16u; ++index)",
            "        words[index] = ecobin_uart_read_u32_be(data + index * 4u);",
            "    a = context->state[0]; b = context->state[1];",
            "    c = context->state[2]; d = context->state[3];",
            "    e = context->state[4]; f = context->state[5];",
            "    g = context->state[6]; h = context->state[7];",
            "    for (index = 0u; index < 64u; ++index) {",
            "        uint32_t word_index;",
            "        uint32_t s1;",
            "        uint32_t choice;",
            "        uint32_t temporary1;",
            "        uint32_t s0;",
            "        uint32_t majority;",
            "        uint32_t temporary2;",
            "        word_index = index & 15u;",
            "        if (index >= 16u) {",
            "            uint32_t schedule_s0;",
            "            uint32_t schedule_s1;",
            "            schedule_s0 = ecobin_uart_sha256_rotr(words[(index + 1u) & 15u], 7u)",
            "                ^ ecobin_uart_sha256_rotr(words[(index + 1u) & 15u], 18u)",
            "                ^ (words[(index + 1u) & 15u] >> 3u);",
            "            schedule_s1 = ecobin_uart_sha256_rotr(words[(index + 14u) & 15u], 17u)",
            "                ^ ecobin_uart_sha256_rotr(words[(index + 14u) & 15u], 19u)",
            "                ^ (words[(index + 14u) & 15u] >> 10u);",
            "            words[word_index] = words[word_index] + schedule_s0",
            "                + words[(index + 9u) & 15u] + schedule_s1;",
            "        }",
            "        s1 = ecobin_uart_sha256_rotr(e, 6u)",
            "            ^ ecobin_uart_sha256_rotr(e, 11u)",
            "            ^ ecobin_uart_sha256_rotr(e, 25u);",
            "        choice = (e & f) ^ ((~e) & g);",
            "        temporary1 = h + s1 + choice + constants[index]",
            "            + words[word_index];",
            "        s0 = ecobin_uart_sha256_rotr(a, 2u)",
            "            ^ ecobin_uart_sha256_rotr(a, 13u)",
            "            ^ ecobin_uart_sha256_rotr(a, 22u);",
            "        majority = (a & b) ^ (a & c) ^ (b & c);",
            "        temporary2 = s0 + majority;",
            "        h = g; g = f; f = e; e = d + temporary1;",
            "        d = c; c = b; b = a; a = temporary1 + temporary2;",
            "    }",
            "    context->state[0] += a; context->state[1] += b;",
            "    context->state[2] += c; context->state[3] += d;",
            "    context->state[4] += e; context->state[5] += f;",
            "    context->state[6] += g; context->state[7] += h;",
            "}",
            "",
            "static inline void ecobin_uart_sha256_init(ecobin_uart_sha256_context_t *context) {",
            "    context->data_length = 0u;",
            "    context->bit_length = 0u;",
            "    context->state[0] = UINT32_C(0x6a09e667);",
            "    context->state[1] = UINT32_C(0xbb67ae85);",
            "    context->state[2] = UINT32_C(0x3c6ef372);",
            "    context->state[3] = UINT32_C(0xa54ff53a);",
            "    context->state[4] = UINT32_C(0x510e527f);",
            "    context->state[5] = UINT32_C(0x9b05688c);",
            "    context->state[6] = UINT32_C(0x1f83d9ab);",
            "    context->state[7] = UINT32_C(0x5be0cd19);",
            "}",
            "",
            "static inline void ecobin_uart_sha256_update(",
            "    ecobin_uart_sha256_context_t *context,",
            "    const uint8_t *data, size_t length) {",
            "    size_t index;",
            "    for (index = 0u; index < length; ++index) {",
            "        context->data[context->data_length++] = data[index];",
            "        if (context->data_length == 64u) {",
            "            ecobin_uart_sha256_transform(context, context->data);",
            "            context->bit_length += UINT64_C(512);",
            "            context->data_length = 0u;",
            "        }",
            "    }",
            "}",
            "",
            "static inline void ecobin_uart_sha256_final(",
            "    ecobin_uart_sha256_context_t *context, uint8_t digest[32]) {",
            "    uint32_t index = context->data_length;",
            "    context->data[index++] = 0x80u;",
            "    if (index > 56u) {",
            "        while (index < 64u) context->data[index++] = 0u;",
            "        ecobin_uart_sha256_transform(context, context->data);",
            "        index = 0u;",
            "    }",
            "    while (index < 56u) context->data[index++] = 0u;",
            "    context->bit_length += (uint64_t)context->data_length * UINT64_C(8);",
            "    ecobin_uart_write_u64_be(context->data + 56u, context->bit_length);",
            "    ecobin_uart_sha256_transform(context, context->data);",
            "    for (index = 0u; index < 8u; ++index)",
            "        ecobin_uart_write_u32_be(digest + index * 4u, context->state[index]);",
            "}",
            "",
            "static inline void ecobin_uart_sha256(",
            "    const uint8_t *data, size_t length, uint8_t digest[32]) {",
            "    ecobin_uart_sha256_context_t context;",
            "    ecobin_uart_sha256_init(&context);",
            "    ecobin_uart_sha256_update(&context, data, length);",
            "    ecobin_uart_sha256_final(&context, digest);",
            "}",
            "",
            "#define ECOBIN_UART_DIAG_NOISE_DISCARDED UINT32_C(0x0001)",
            "#define ECOBIN_UART_DIAG_INVALID_LENGTH UINT32_C(0x0002)",
            "#define ECOBIN_UART_DIAG_CRC_INVALID UINT32_C(0x0004)",
            "#define ECOBIN_UART_DIAG_FRAME_TIMEOUT UINT32_C(0x0008)",
            "#define ECOBIN_UART_DIAG_BUFFER_OVERFLOW UINT32_C(0x0010)",
            "#define ECOBIN_UART_DIAG_SEMANTIC_REJECTED UINT32_C(0x0020)",
            "",
            "typedef struct ecobin_uart_stream_parser {",
            "    uint8_t buffer[512];",
            "    size_t length;",
            "    uint64_t candidate_started_ms;",
            "    int candidate_active;",
            "    uint32_t diagnostics;",
            "    ecobin_uart_sender_role_t sender;",
            "} ecobin_uart_stream_parser_t;",
            "",
            "typedef void (*ecobin_uart_frame_callback_t)(",
            "    const uint8_t *frame, size_t length,",
            "    const ecobin_uart_frame_view_t *view, void *context);",
            "",
            "static inline void ecobin_uart_stream_parser_init(",
            "    ecobin_uart_stream_parser_t *parser,",
            "    ecobin_uart_sender_role_t sender) {",
            "    memset(parser, 0, sizeof(*parser));",
            "    parser->sender = sender;",
            "}",
            "",
            "static inline void ecobin_uart_stream_drop_first(",
            "    ecobin_uart_stream_parser_t *parser, uint32_t diagnostic) {",
            "    if (parser->length > 0u) {",
            "        memmove(parser->buffer, parser->buffer + 1u, parser->length - 1u);",
            "        parser->length -= 1u;",
            "    }",
            "    parser->candidate_active = 0;",
            "    parser->diagnostics |= diagnostic;",
            "}",
            "",
            "static inline size_t ecobin_uart_stream_find_magic(",
            "    const ecobin_uart_stream_parser_t *parser) {",
            "    size_t index;",
            "    for (index = 0u; index + 1u < parser->length; ++index) {",
            "        if (parser->buffer[index] == ECOBIN_UART_MAGIC_0",
            "            && parser->buffer[index + 1u] == ECOBIN_UART_MAGIC_1) return index;",
            "    }",
            "    return parser->length;",
            "}",
            "",
            "static inline size_t ecobin_uart_stream_parser_feed(",
            "    ecobin_uart_stream_parser_t *parser,",
            "    const uint8_t *data, size_t data_length, uint64_t now_ms,",
            "    ecobin_uart_frame_callback_t callback, void *context) {",
            "    size_t input_index;",
            "    size_t emitted = 0u;",
            "    for (input_index = 0u; input_index < data_length; ++input_index) {",
            "        if (parser->length == sizeof(parser->buffer)) {",
            "            memmove(parser->buffer, parser->buffer + 1u, parser->length - 1u);",
            "            parser->length -= 1u;",
            "            parser->candidate_active = 0;",
            "            parser->diagnostics |= ECOBIN_UART_DIAG_BUFFER_OVERFLOW;",
            "        }",
            "        parser->buffer[parser->length++] = data[input_index];",
            "    }",
            "    for (;;) {",
            "        size_t magic_index = ecobin_uart_stream_find_magic(parser);",
            "        uint16_t payload_length;",
            "        size_t frame_length;",
            "        uint16_t expected_crc;",
            "        uint16_t actual_crc;",
            "        ecobin_uart_frame_view_t view;",
            "        int status;",
            "        if (magic_index == parser->length) {",
            "            size_t keep = parser->length > 0u",
            "                && parser->buffer[parser->length - 1u] == ECOBIN_UART_MAGIC_0",
            "                ? 1u : 0u;",
            "            if (keep == 1u) parser->buffer[0] = ECOBIN_UART_MAGIC_0;",
            "            parser->length = keep;",
            "            parser->candidate_active = 0;",
            "            break;",
            "        }",
            "        if (magic_index > 0u) {",
            "            memmove(parser->buffer, parser->buffer + magic_index,",
            "                parser->length - magic_index);",
            "            parser->length -= magic_index;",
            "            parser->candidate_active = 0;",
            "            parser->diagnostics |= ECOBIN_UART_DIAG_NOISE_DISCARDED;",
            "        }",
            "        if (!parser->candidate_active) {",
            "            parser->candidate_started_ms = now_ms;",
            "            parser->candidate_active = 1;",
            "        }",
            "        if (parser->length < 12u) {",
            "            if (now_ms - parser->candidate_started_ms >= 100u) {",
            "                ecobin_uart_stream_drop_first(parser,",
            "                    ECOBIN_UART_DIAG_FRAME_TIMEOUT);",
            "                continue;",
            "            }",
            "            break;",
            "        }",
            "        payload_length = ecobin_uart_read_u16_be(parser->buffer + 6u);",
            "        if (payload_length > ECOBIN_UART_MAX_PAYLOAD_LENGTH) {",
            "            ecobin_uart_stream_drop_first(parser,",
            "                ECOBIN_UART_DIAG_INVALID_LENGTH);",
            "            continue;",
            "        }",
            "        frame_length = (size_t)payload_length + 14u;",
            "        if (parser->length < frame_length) {",
            "            if (now_ms - parser->candidate_started_ms >= 100u) {",
            "                ecobin_uart_stream_drop_first(parser,",
            "                    ECOBIN_UART_DIAG_FRAME_TIMEOUT);",
            "                continue;",
            "            }",
            "            break;",
            "        }",
            "        expected_crc = ecobin_uart_read_u16_be(",
            "            parser->buffer + frame_length - 2u);",
            "        actual_crc = ecobin_uart_crc16_ccitt_false(",
            "            parser->buffer + 2u, frame_length - 4u);",
            "        if (expected_crc != actual_crc) {",
            "            ecobin_uart_stream_drop_first(parser, ECOBIN_UART_DIAG_CRC_INVALID);",
            "            continue;",
            "        }",
            "        status = ecobin_uart_validate_frame(",
            "            parser->buffer, frame_length, parser->sender, &view);",
            "        if (status != 0) {",
            "            parser->diagnostics |= ECOBIN_UART_DIAG_SEMANTIC_REJECTED;",
            "        } else {",
            "            if (callback != NULL)",
            "                callback(parser->buffer, frame_length, &view, context);",
            "            emitted += 1u;",
            "        }",
            "        memmove(parser->buffer, parser->buffer + frame_length,",
            "            parser->length - frame_length);",
            "        parser->length -= frame_length;",
            "        parser->candidate_active = 0;",
            "    }",
            "    return emitted;",
            "}",
            "",
            "#endif /* ECOBIN_UART_PROTOCOL_H */",
            "",
        ]
    )
    return "\n".join(lines)


def render_java_protocol(
    registry: Mapping[str, Any],
    specs: Mapping[str, Any],
    registry_digest: str,
) -> str:
    message_constants = "\n".join(
        f"    public static final int MESSAGE_{message['name']} = "
        f"0x{message['id']:02X};"
        for message in registry["messages"]
    )
    layout_constants: list[str] = []
    for name, spec in specs.items():
        layout_constants.append(
            f"    public static final int {name}_PAYLOAD_MIN_LENGTH = "
            f"{spec['minimumPayloadLength']};"
        )
        layout_constants.append(
            f"    public static final int {name}_PAYLOAD_MAX_LENGTH = "
            f"{spec['maximumPayloadLength']};"
        )
        for field in spec["fields"]:
            if field["offset"] is not None:
                layout_constants.append(
                    f"    public static final int {name}_{macro_name(field['name'])}_OFFSET = "
                    f"{field['offset']};"
                )
    ack_cases = "\n".join(
        f"            case MESSAGE_{message['name']} -> "
        f"{str(message['ackRequired']).lower()};"
        for message in registry["messages"]
    )
    direction_cases = "\n".join(
        f"            case MESSAGE_{message['name']} -> Direction."
        + {
            "BIDIRECTIONAL": "BIDIRECTIONAL",
            "EDGE_TO_MCU": "EDGE_TO_MCU",
            "MCU_TO_EDGE": "MCU_TO_EDGE",
        }[message["direction"]]
        + ";"
        for message in registry["messages"]
    )
    return f"""// Generated from contracts/uart/uart-registry.yaml.
// DO NOT EDIT. Registry SHA-256: {registry_digest}

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.UUID;

public final class EcobinUartProtocol {{
    public static final String REGISTRY_SHA256 = "{registry_digest}";
    public static final int BAUD_RATE = {registry["physicalLink"]["baudRate"]};
    public static final int DATA_BITS = {registry["physicalLink"]["dataBits"]};
    public static final int STOP_BITS = {registry["physicalLink"]["stopBits"]};
    public static final String PARITY = "{registry["physicalLink"]["parity"]}";
    public static final String FLOW_CONTROL = "{registry["physicalLink"]["flowControl"]}";
    public static final int PROTOCOL_MAJOR = {registry["protocol"]["major"]};
    public static final int PROTOCOL_MINOR = {registry["protocol"]["minor"]};
    public static final int MAXIMUM_FRAME_LENGTH = {registry["protocol"]["maximumFrameLength"]};
    public static final int MAXIMUM_PAYLOAD_LENGTH = {registry["protocol"]["maximumPayloadLength"]};
    public static final int ACK_REQUIRED = 0x01;
{message_constants}
{chr(10).join(layout_constants)}

    public enum SenderRole {{ EDGE, MCU }}
    public enum Direction {{ BIDIRECTIONAL, EDGE_TO_MCU, MCU_TO_EDGE }}

    private EcobinUartProtocol() {{}}

    public static Boolean ackRequired(int messageType) {{
        return switch (messageType) {{
{ack_cases}
            default -> null;
        }};
    }}

    public static Direction direction(int messageType) {{
        return switch (messageType) {{
{direction_cases}
            default -> null;
        }};
    }}

    public static int crc16CcittFalse(byte[] data) {{
        return crc16CcittFalse(data, 0, data.length);
    }}

    public static int crc16CcittFalse(byte[] data, int offset, int length) {{
        int crc = 0xFFFF;
        for (int index = offset; index < offset + length; index++) {{
            crc ^= Byte.toUnsignedInt(data[index]) << 8;
            for (int bit = 0; bit < 8; bit++) {{
                crc = ((crc & 0x8000) != 0)
                    ? ((crc << 1) ^ 0x1021) & 0xFFFF
                    : (crc << 1) & 0xFFFF;
            }}
        }}
        return crc;
    }}

    public static byte[] sha256(byte[] data) {{
        try {{
            return MessageDigest.getInstance("SHA-256").digest(data);
        }} catch (NoSuchAlgorithmException error) {{
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }}
    }}

    public static long readU64(byte[] data, int offset) {{
        long value = ByteBuffer.wrap(data, offset, 8)
            .order(ByteOrder.BIG_ENDIAN)
            .getLong();
        if (value < 0) {{
            throw new IllegalArgumentException("uint64 exceeds signed Java long");
        }}
        return value;
    }}

    public static int readI32(byte[] data, int offset) {{
        return ByteBuffer.wrap(data, offset, 4)
            .order(ByteOrder.BIG_ENDIAN)
            .getInt();
    }}

    public static void writeU64(byte[] data, int offset, long value) {{
        if (value < 0) {{
            throw new IllegalArgumentException("uint64 value must be non-negative");
        }}
        ByteBuffer.wrap(data, offset, 8)
            .order(ByteOrder.BIG_ENDIAN)
            .putLong(value);
    }}

    public static void writeI32(byte[] data, int offset, int value) {{
        ByteBuffer.wrap(data, offset, 4)
            .order(ByteOrder.BIG_ENDIAN)
            .putInt(value);
    }}

    public static UUID readUuid(byte[] data, int offset) {{
        ByteBuffer buffer = ByteBuffer.wrap(data, offset, 16)
            .order(ByteOrder.BIG_ENDIAN);
        return new UUID(buffer.getLong(), buffer.getLong());
    }}

    public static void writeUuid(byte[] data, int offset, UUID value) {{
        ByteBuffer buffer = ByteBuffer.wrap(data, offset, 16)
            .order(ByteOrder.BIG_ENDIAN);
        buffer.putLong(value.getMostSignificantBits());
        buffer.putLong(value.getLeastSignificantBits());
    }}

    public static byte[] encodeFrame(
        int messageType,
        int flags,
        long txSequence,
        byte[] payload
    ) {{
        if (messageType < 0 || messageType > 0xFF) {{
            throw new IllegalArgumentException("messageType must fit uint8");
        }}
        if ((flags & ~ACK_REQUIRED) != 0) {{
            throw new IllegalArgumentException("unsupported flags");
        }}
        if (txSequence < 1 || txSequence > 0xFFFF_FFFFL) {{
            throw new IllegalArgumentException("txSequence must be positive uint32");
        }}
        if (payload.length > MAXIMUM_PAYLOAD_LENGTH) {{
            throw new IllegalArgumentException("payload too long");
        }}
        ByteBuffer buffer = ByteBuffer
            .allocate(14 + payload.length)
            .order(ByteOrder.BIG_ENDIAN);
        buffer.put((byte) 0xEC);
        buffer.put((byte) 0x42);
        buffer.put((byte) PROTOCOL_MAJOR);
        buffer.put((byte) PROTOCOL_MINOR);
        buffer.put((byte) messageType);
        buffer.put((byte) flags);
        buffer.putShort((short) payload.length);
        buffer.putInt((int) txSequence);
        buffer.put(payload);
        byte[] frame = buffer.array();
        int crc = crc16CcittFalse(frame, 2, 10 + payload.length);
        buffer.putShort((short) crc);
        return frame;
    }}

    public static Frame decodeFrame(byte[] frame) {{
        return decodeFrame(frame, false, null);
    }}

    public static Frame decodeFrame(
        byte[] frame,
        boolean requireKnownMessage,
        SenderRole senderRole
    ) {{
        if (frame.length < 14 || frame.length > MAXIMUM_FRAME_LENGTH) {{
            throw new IllegalArgumentException("invalid frame length");
        }}
        if (Byte.toUnsignedInt(frame[0]) != 0xEC || Byte.toUnsignedInt(frame[1]) != 0x42) {{
            throw new IllegalArgumentException("invalid magic");
        }}
        ByteBuffer buffer = ByteBuffer.wrap(frame).order(ByteOrder.BIG_ENDIAN);
        buffer.position(2);
        int major = Byte.toUnsignedInt(buffer.get());
        int minor = Byte.toUnsignedInt(buffer.get());
        int messageType = Byte.toUnsignedInt(buffer.get());
        int flags = Byte.toUnsignedInt(buffer.get());
        int payloadLength = Short.toUnsignedInt(buffer.getShort());
        long txSequence = Integer.toUnsignedLong(buffer.getInt());
        if (payloadLength > MAXIMUM_PAYLOAD_LENGTH || frame.length != 14 + payloadLength) {{
            throw new IllegalArgumentException("invalid payload length");
        }}
        int expected = (Byte.toUnsignedInt(frame[frame.length - 2]) << 8)
            | Byte.toUnsignedInt(frame[frame.length - 1]);
        int actual = crc16CcittFalse(frame, 2, frame.length - 4);
        if (actual != expected) {{
            throw new IllegalArgumentException("CRC mismatch");
        }}
        if (major != PROTOCOL_MAJOR || minor != PROTOCOL_MINOR) {{
            throw new IllegalArgumentException("unsupported version");
        }}
        if ((flags & ~ACK_REQUIRED) != 0 || txSequence == 0) {{
            throw new IllegalArgumentException("invalid flags or sequence");
        }}
        Boolean expectedAck = ackRequired(messageType);
        Direction messageDirection = direction(messageType);
        if (requireKnownMessage && expectedAck == null) {{
            throw new IllegalArgumentException("unsupported UART message type");
        }}
        if (expectedAck != null
            && ((flags & ACK_REQUIRED) != 0) != expectedAck.booleanValue()) {{
            throw new IllegalArgumentException(
                "ACK_REQUIRED flag differs from the message Registry"
            );
        }}
        if (senderRole != null && messageDirection != null) {{
            Direction expectedDirection = senderRole == SenderRole.EDGE
                ? Direction.EDGE_TO_MCU
                : Direction.MCU_TO_EDGE;
            if (messageDirection != Direction.BIDIRECTIONAL
                && messageDirection != expectedDirection) {{
                throw new IllegalArgumentException(
                    "message direction differs from the sender role"
                );
            }}
        }}
        byte[] payload = Arrays.copyOfRange(frame, 12, frame.length - 2);
        return new Frame(messageType, flags, txSequence, payload, actual);
    }}

    public static final class StreamParser {{
        private final SenderRole senderRole;
        private byte[] buffer = new byte[0];
        private Long candidateStartedMs;
        private final List<String> diagnostics = new ArrayList<>();

        public StreamParser(SenderRole senderRole) {{
            this.senderRole = senderRole;
        }}

        public List<String> diagnostics() {{
            return List.copyOf(diagnostics);
        }}

        public int bufferedBytes() {{
            return buffer.length;
        }}

        private void discardFirst(String diagnostic) {{
            diagnostics.add(diagnostic);
            buffer = Arrays.copyOfRange(buffer, 1, buffer.length);
            candidateStartedMs = null;
        }}

        public List<Frame> feed(byte[] data, long nowMs) {{
            if (nowMs < 0) {{
                throw new IllegalArgumentException("nowMs must be non-negative");
            }}
            byte[] combined = Arrays.copyOf(buffer, buffer.length + data.length);
            System.arraycopy(data, 0, combined, buffer.length, data.length);
            buffer = combined;
            if (buffer.length > 512) {{
                buffer = Arrays.copyOfRange(buffer, buffer.length - 512, buffer.length);
                candidateStartedMs = null;
                diagnostics.add("BUFFER_OVERFLOW");
            }}
            List<Frame> frames = new ArrayList<>();
            while (true) {{
                int magicIndex = -1;
                for (int index = 0; index + 1 < buffer.length; index++) {{
                    if (Byte.toUnsignedInt(buffer[index]) == 0xEC
                        && Byte.toUnsignedInt(buffer[index + 1]) == 0x42) {{
                        magicIndex = index;
                        break;
                    }}
                }}
                if (magicIndex < 0) {{
                    boolean keep = buffer.length > 0
                        && Byte.toUnsignedInt(buffer[buffer.length - 1]) == 0xEC;
                    buffer = keep ? new byte[] {{(byte) 0xEC}} : new byte[0];
                    candidateStartedMs = null;
                    break;
                }}
                if (magicIndex > 0) {{
                    buffer = Arrays.copyOfRange(buffer, magicIndex, buffer.length);
                    candidateStartedMs = null;
                    diagnostics.add("NOISE_DISCARDED");
                }}
                if (candidateStartedMs == null) {{
                    candidateStartedMs = nowMs;
                }}
                if (buffer.length < 12) {{
                    if (nowMs - candidateStartedMs >= 100) {{
                        discardFirst("FRAME_TIMEOUT");
                        continue;
                    }}
                    break;
                }}
                int payloadLength = Short.toUnsignedInt(
                    ByteBuffer.wrap(buffer, 6, 2)
                        .order(ByteOrder.BIG_ENDIAN)
                        .getShort()
                );
                if (payloadLength > MAXIMUM_PAYLOAD_LENGTH) {{
                    discardFirst("INVALID_LENGTH");
                    continue;
                }}
                int frameLength = 14 + payloadLength;
                if (buffer.length < frameLength) {{
                    if (nowMs - candidateStartedMs >= 100) {{
                        discardFirst("FRAME_TIMEOUT");
                        continue;
                    }}
                    break;
                }}
                byte[] candidate = Arrays.copyOfRange(buffer, 0, frameLength);
                int expected = (Byte.toUnsignedInt(candidate[frameLength - 2]) << 8)
                    | Byte.toUnsignedInt(candidate[frameLength - 1]);
                int actual = crc16CcittFalse(candidate, 2, frameLength - 4);
                if (expected != actual) {{
                    discardFirst("CRC_INVALID");
                    continue;
                }}
                try {{
                    frames.add(decodeFrame(candidate, true, senderRole));
                }} catch (IllegalArgumentException error) {{
                    diagnostics.add("SEMANTIC_REJECTED:" + error.getMessage());
                }}
                buffer = Arrays.copyOfRange(buffer, frameLength, buffer.length);
                candidateStartedMs = null;
            }}
            return frames;
        }}
    }}

    public record Frame(
        int messageType,
        int flags,
        long txSequence,
        byte[] payload,
        int crc16
    ) {{}}
}}
"""


def _java_byte_array(hex_value: str) -> str:
    raw = bytes.fromhex(hex_value)
    return ", ".join(f"(byte) 0x{byte:02X}" for byte in raw)


def render_java_golden_test(
    registry: Mapping[str, Any],
    vectors: list[dict[str, Any]],
    stream_traces: list[dict[str, Any]],
    digest_vectors: list[dict[str, Any]],
) -> str:
    entries = []
    for vector in vectors:
        entries.append(
            "        new Vector("
            + json.dumps(vector["name"])
            + ", new byte[] {"
            + _java_byte_array(vector["frameHex"])
            + "}, "
            + ("true" if vector["expected"] == "CRC_INVALID" else "false")
            + ")"
        )
    joined = ",\n".join(entries)
    message_ids = {
        message["name"]: message["id"] for message in registry["messages"]
    }
    trace_entries: list[str] = []
    for trace in stream_traces:
        chunks = ", ".join(
            "new Chunk("
            + str(chunk["atMs"])
            + "L, new byte[] {"
            + _java_byte_array(chunk["hex"])
            + "})"
            for chunk in trace["chunks"]
        )
        expected_types = ", ".join(
            f"0x{message_ids[name]:02X}" for name in trace["expectedMessageNames"]
        )
        expected_diagnostics = ", ".join(
            json.dumps(value) for value in trace["expectedDiagnostics"]
        )
        sender = (
            "EcobinUartProtocol.SenderRole.EDGE"
            if trace["senderRole"] == "EDGE"
            else "EcobinUartProtocol.SenderRole.MCU"
        )
        trace_entries.append(
            "        new Trace("
            + json.dumps(trace["name"])
            + ", "
            + sender
            + ", new Chunk[] {"
            + chunks
            + "}, new int[] {"
            + expected_types
            + "}, List.of("
            + expected_diagnostics
            + "))"
        )
    joined_traces = ",\n".join(trace_entries)
    digest_entries = ",\n".join(
        "        new DigestVector("
        + json.dumps(vector["name"])
        + ", new byte[] {"
        + _java_byte_array(vector["preimageHex"])
        + "}, new byte[] {"
        + _java_byte_array(vector["sha256"])
        + "})"
        for vector in digest_vectors
    )
    return f"""// Generated UART golden-vector test. DO NOT EDIT.

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

public final class EcobinUartGoldenTest {{
    private record Vector(String name, byte[] frame, boolean crcInvalid) {{}}
    private record Chunk(long atMs, byte[] data) {{}}
    private record Trace(
        String name,
        EcobinUartProtocol.SenderRole senderRole,
        Chunk[] chunks,
        int[] expectedMessageTypes,
        List<String> expectedDiagnostics
    ) {{}}
    private record DigestVector(
        String name,
        byte[] preimage,
        byte[] expectedSha256
    ) {{}}

    private static final Vector[] VECTORS = new Vector[] {{
{joined}
    }};

    private static final Trace[] TRACES = new Trace[] {{
{joined_traces}
    }};

    private static final DigestVector[] DIGEST_VECTORS = new DigestVector[] {{
{digest_entries}
    }};

    public static void main(String[] args) {{
        int check = EcobinUartProtocol.crc16CcittFalse(
            "123456789".getBytes(StandardCharsets.US_ASCII)
        );
        if (check != 0x29B1) {{
            throw new AssertionError("CRC check value differs: " + check);
        }}
        byte[] scalar = new byte[32];
        EcobinUartProtocol.writeU64(scalar, 0, 9_007_199_254_740_991L);
        if (EcobinUartProtocol.readU64(scalar, 0) != 9_007_199_254_740_991L) {{
            throw new AssertionError("safe uint64 did not round-trip");
        }}
        EcobinUartProtocol.writeI32(scalar, 0, -500);
        if (EcobinUartProtocol.readI32(scalar, 0) != -500) {{
            throw new AssertionError("signed int32 did not round-trip");
        }}
        UUID uuid = UUID.fromString("10111213-1415-1617-1819-1a1b1c1d1e1f");
        EcobinUartProtocol.writeUuid(scalar, 0, uuid);
        if (!EcobinUartProtocol.readUuid(scalar, 0).equals(uuid)) {{
            throw new AssertionError("UUID network bytes did not round-trip");
        }}
        for (Vector vector : VECTORS) {{
            try {{
                EcobinUartProtocol.Frame decoded =
                    EcobinUartProtocol.decodeFrame(vector.frame());
                if (vector.crcInvalid()) {{
                    throw new AssertionError(vector.name() + " should fail CRC");
                }}
                byte[] encoded = EcobinUartProtocol.encodeFrame(
                    decoded.messageType(),
                    decoded.flags(),
                    decoded.txSequence(),
                    decoded.payload()
                );
                if (!Arrays.equals(encoded, vector.frame())) {{
                    throw new AssertionError(vector.name() + " did not round-trip");
                }}
            }} catch (IllegalArgumentException error) {{
                if (!vector.crcInvalid()) {{
                    throw error;
                }}
            }}
        }}
        for (Trace trace : TRACES) {{
            EcobinUartProtocol.StreamParser parser =
                new EcobinUartProtocol.StreamParser(trace.senderRole());
            List<Integer> observed = new ArrayList<>();
            for (Chunk chunk : trace.chunks()) {{
                for (EcobinUartProtocol.Frame frame :
                    parser.feed(chunk.data(), chunk.atMs())) {{
                    observed.add(frame.messageType());
                }}
            }}
            int[] actual = observed.stream().mapToInt(Integer::intValue).toArray();
            if (!Arrays.equals(actual, trace.expectedMessageTypes())
                || !parser.diagnostics().equals(trace.expectedDiagnostics())) {{
                throw new AssertionError(trace.name() + " stream result differs");
            }}
        }}
        for (DigestVector vector : DIGEST_VECTORS) {{
            if (!Arrays.equals(
                EcobinUartProtocol.sha256(vector.preimage()),
                vector.expectedSha256()
            )) {{
                throw new AssertionError(vector.name() + " SHA-256 differs");
            }}
        }}
        System.out.println(
            "Java UART golden vectors: " + VECTORS.length
            + " frames, " + TRACES.length + " stream traces, "
            + DIGEST_VECTORS.length + " digest profiles passed"
        );
    }}
}}
"""


def _c_byte_array(hex_value: str) -> str:
    raw = bytes.fromhex(hex_value)
    return ", ".join(f"0x{byte:02X}u" for byte in raw)


def render_c_golden_test(
    registry: Mapping[str, Any],
    vectors: list[dict[str, Any]],
    stream_traces: list[dict[str, Any]],
    digest_vectors: list[dict[str, Any]],
) -> str:
    declarations = []
    rows = []
    for index, vector in enumerate(vectors):
        symbol = f"vector_{index}"
        declarations.append(
            f"static const uint8_t {symbol}[] = {{{_c_byte_array(vector['frameHex'])}}};"
        )
        rows.append(
            "    {"
            f'"{vector["name"]}", {symbol}, sizeof({symbol}), '
            f"{1 if vector['expected'] == 'CRC_INVALID' else 0}"
            "},"
        )
    message_ids = {
        message["name"]: message["id"] for message in registry["messages"]
    }
    diagnostic_macro = {
        "NOISE_DISCARDED": "ECOBIN_UART_DIAG_NOISE_DISCARDED",
        "INVALID_LENGTH": "ECOBIN_UART_DIAG_INVALID_LENGTH",
        "CRC_INVALID": "ECOBIN_UART_DIAG_CRC_INVALID",
        "FRAME_TIMEOUT": "ECOBIN_UART_DIAG_FRAME_TIMEOUT",
        "BUFFER_OVERFLOW": "ECOBIN_UART_DIAG_BUFFER_OVERFLOW",
    }
    trace_declarations: list[str] = []
    trace_rows: list[str] = []
    for trace_index, trace in enumerate(stream_traces):
        chunk_rows: list[str] = []
        for chunk_index, chunk in enumerate(trace["chunks"]):
            symbol = f"trace_{trace_index}_chunk_{chunk_index}"
            raw = bytes.fromhex(chunk["hex"])
            values = _c_byte_array(chunk["hex"]) if raw else "0x00u"
            trace_declarations.append(
                f"static const uint8_t {symbol}[] = {{{values}}};"
            )
            chunk_rows.append(
                f"    {{{symbol}, {len(raw)}u, UINT64_C({chunk['atMs']})}},"
            )
        chunks_symbol = f"trace_{trace_index}_chunks"
        trace_declarations.append(
            "static const stream_chunk_t "
            + chunks_symbol
            + "[] = {\n"
            + "\n".join(chunk_rows)
            + "\n};"
        )
        expected_values = [
            f"0x{message_ids[name]:02X}u" for name in trace["expectedMessageNames"]
        ]
        expected_symbol = f"trace_{trace_index}_expected"
        trace_declarations.append(
            f"static const uint8_t {expected_symbol}[] = "
            + "{"
            + (", ".join(expected_values) if expected_values else "0x00u")
            + "};"
        )
        diagnostic_terms = []
        for diagnostic in trace["expectedDiagnostics"]:
            base = diagnostic.split(":", 1)[0]
            diagnostic_terms.append(
                diagnostic_macro.get(base, "ECOBIN_UART_DIAG_SEMANTIC_REJECTED")
            )
        diagnostics = " | ".join(sorted(set(diagnostic_terms))) or "UINT32_C(0)"
        sender = (
            "ECOBIN_UART_SENDER_ROLE_EDGE"
            if trace["senderRole"] == "EDGE"
            else "ECOBIN_UART_SENDER_ROLE_MCU"
        )
        trace_rows.append(
            "    {"
            f'"{trace["name"]}", {sender}, {chunks_symbol}, '
            f"sizeof({chunks_symbol}) / sizeof({chunks_symbol}[0]), "
            f"{expected_symbol}, {len(expected_values)}u, {diagnostics}"
            "},"
        )
    digest_declarations: list[str] = []
    digest_rows: list[str] = []
    for digest_index, vector in enumerate(digest_vectors):
        preimage_symbol = f"digest_{digest_index}_preimage"
        expected_symbol = f"digest_{digest_index}_expected"
        digest_declarations.append(
            f"static const uint8_t {preimage_symbol}[] = "
            + "{"
            + _c_byte_array(vector["preimageHex"])
            + "};"
        )
        digest_declarations.append(
            f"static const uint8_t {expected_symbol}[] = "
            + "{"
            + _c_byte_array(vector["sha256"])
            + "};"
        )
        digest_rows.append(
            "    {"
            f'"{vector["name"]}", {preimage_symbol}, sizeof({preimage_symbol}), '
            f"{expected_symbol}"
            "},"
        )
    return f"""/* Generated UART golden-vector test. DO NOT EDIT. */
#include "ecobin_uart_protocol.h"

#include <stdio.h>
#include <string.h>

{chr(10).join(declarations)}

typedef struct golden_vector {{
    const char *name;
    const uint8_t *frame;
    size_t length;
    int crc_invalid;
}} golden_vector_t;

typedef struct stream_chunk {{
    const uint8_t *data;
    size_t length;
    uint64_t at_ms;
}} stream_chunk_t;

typedef struct stream_trace {{
    const char *name;
    ecobin_uart_sender_role_t sender;
    const stream_chunk_t *chunks;
    size_t chunk_count;
    const uint8_t *expected_types;
    size_t expected_count;
    uint32_t expected_diagnostics;
}} stream_trace_t;

typedef struct stream_capture {{
    uint8_t message_types[16];
    size_t count;
}} stream_capture_t;

typedef struct digest_vector {{
    const char *name;
    const uint8_t *preimage;
    size_t preimage_length;
    const uint8_t *expected;
}} digest_vector_t;

static const golden_vector_t vectors[] = {{
{chr(10).join(rows)}
}};

{chr(10).join(trace_declarations)}

static const stream_trace_t traces[] = {{
{chr(10).join(trace_rows)}
}};

{chr(10).join(digest_declarations)}

static const digest_vector_t digest_vectors[] = {{
{chr(10).join(digest_rows)}
}};

static void capture_frame(
    const uint8_t *frame,
    size_t length,
    const ecobin_uart_frame_view_t *view,
    void *context
) {{
    stream_capture_t *capture = (stream_capture_t *)context;
    (void)frame;
    (void)length;
    if (capture->count < sizeof(capture->message_types)) {{
        capture->message_types[capture->count++] = view->message_type;
    }}
}}

int main(void) {{
    static const uint8_t check_text[] = "123456789";
    static const uint8_t uuid_source[16] = {{
        0x10u, 0x11u, 0x12u, 0x13u, 0x14u, 0x15u, 0x16u, 0x17u,
        0x18u, 0x19u, 0x1Au, 0x1Bu, 0x1Cu, 0x1Du, 0x1Eu, 0x1Fu
    }};
    static const uint8_t sha_source[32] = {{
        0x00u, 0x01u, 0x02u, 0x03u, 0x04u, 0x05u, 0x06u, 0x07u,
        0x08u, 0x09u, 0x0Au, 0x0Bu, 0x0Cu, 0x0Du, 0x0Eu, 0x0Fu,
        0x10u, 0x11u, 0x12u, 0x13u, 0x14u, 0x15u, 0x16u, 0x17u,
        0x18u, 0x19u, 0x1Au, 0x1Bu, 0x1Cu, 0x1Du, 0x1Eu, 0x1Fu
    }};
    uint8_t scalar_buffer[32];
    size_t index;
    if (ecobin_uart_crc16_ccitt_false(check_text, 9u) != UINT16_C(0x29B1)) {{
        return 1;
    }}
    ecobin_uart_write_u64_be(scalar_buffer, UINT64_C(9007199254740991));
    if (ecobin_uart_read_u64_be(scalar_buffer) != UINT64_C(9007199254740991)) return 10;
    ecobin_uart_write_i32_be(scalar_buffer, INT32_C(-500));
    if (ecobin_uart_read_i32_be(scalar_buffer) != INT32_C(-500)) return 11;
    ecobin_uart_copy_uuid(scalar_buffer, uuid_source);
    if (memcmp(scalar_buffer, uuid_source, 16u) != 0) return 12;
    ecobin_uart_copy_sha256(scalar_buffer, sha_source);
    if (memcmp(scalar_buffer, sha_source, 32u) != 0) return 13;
    for (index = 0; index < sizeof(vectors) / sizeof(vectors[0]); ++index) {{
        const golden_vector_t *vector = &vectors[index];
        uint16_t expected;
        uint16_t actual;
        int invalid;
        if (vector->length < 14u) {{
            return 2;
        }}
        expected = ecobin_uart_read_u16_be(vector->frame + vector->length - 2u);
        actual = ecobin_uart_crc16_ccitt_false(
            vector->frame + 2u,
            vector->length - 4u
        );
        invalid = actual != expected;
        if (invalid != vector->crc_invalid) {{
            fprintf(stderr, "CRC result differs for %s\\n", vector->name);
            return 3;
        }}
    }}
    for (index = 0; index < sizeof(traces) / sizeof(traces[0]); ++index) {{
        const stream_trace_t *trace = &traces[index];
        ecobin_uart_stream_parser_t parser;
        stream_capture_t capture = {{{{0u}}, 0u}};
        size_t chunk_index;
        ecobin_uart_stream_parser_init(&parser, trace->sender);
        for (chunk_index = 0; chunk_index < trace->chunk_count; ++chunk_index) {{
            const stream_chunk_t *chunk = &trace->chunks[chunk_index];
            (void)ecobin_uart_stream_parser_feed(
                &parser,
                chunk->data,
                chunk->length,
                chunk->at_ms,
                capture_frame,
                &capture
            );
        }}
        if (capture.count != trace->expected_count
            || memcmp(capture.message_types, trace->expected_types, capture.count) != 0
            || parser.diagnostics != trace->expected_diagnostics) {{
            fprintf(stderr, "stream trace differs for %s\\n", trace->name);
            return 20;
        }}
    }}
    for (index = 0; index < sizeof(digest_vectors) / sizeof(digest_vectors[0]); ++index) {{
        const digest_vector_t *vector = &digest_vectors[index];
        uint8_t actual[32];
        ecobin_uart_sha256(vector->preimage, vector->preimage_length, actual);
        if (memcmp(actual, vector->expected, sizeof(actual)) != 0) {{
            fprintf(stderr, "SHA-256 differs for %s\\n", vector->name);
            return 30;
        }}
    }}
    printf("C UART golden vectors: %zu frames, %zu stream traces, %zu digests passed\\n",
        sizeof(vectors) / sizeof(vectors[0]),
        sizeof(traces) / sizeof(traces[0]),
        sizeof(digest_vectors) / sizeof(digest_vectors[0]));
    return 0;
}}
"""


def _measurement(
    uid: str,
    weight: int,
    mcu_boot: int,
    sequence: int,
) -> dict[str, Any]:
    return {
        "measurementUid": uid,
        "status": "STABLE",
        "weightValueAvailable": True,
        "reportedWeightGrams": weight,
        "weightValueKind": "STABLE_WINDOW_MEAN",
        "measurementElapsedMs": 1200,
        "sampleCount": 12,
        "calibrationVersion": 4,
        "sensorHealth": "OK",
        "faultCode": None,
        "mcuBootId": mcu_boot,
        "mcuEventSequence": sequence,
    }


def _pending_photo(slot: str) -> dict[str, Any]:
    return {
        "slot": slot,
        "status": "UPLOAD_PENDING",
        "photoUid": None,
        "url": None,
        "sha256": None,
        "sizeBytes": None,
        "capturedAt": None,
        "missingReason": "CAMERA_NOT_READY",
    }


def _config_identity() -> dict[str, Any]:
    return {
        "version": 8,
        "contentSha256": "a" * 64,
        "mcuPayloadSha256": "b" * 64,
    }


def _command(
    uid: str,
    command_type: str,
    target_type: str,
    target_uid: str,
    payload: dict[str, Any],
    *,
    cos_grant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "commandUid": uid,
        "commandType": command_type,
        "targetDeviceName": "SN-CONTRACT-0001",
        "target": {"type": target_type, "uid": target_uid},
        "issuedAt": "2026-07-24T01:00:00.000Z",
        "expiresAt": "2026-07-24T01:01:00.000Z",
        "payloadSchemaVersion": 2,
        "payloadSha256": payload_sha256(payload),
        "payload": payload,
        "cosGrant": cos_grant,
    }


def _event(
    uid: str,
    sequence: int,
    event_type: str,
    delivery_class: str,
    target_type: str,
    target_uid: str,
    payload: dict[str, Any],
    *,
    command_uid: str | None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "eventUid": uid,
        "edgeEventSequence": sequence,
        "eventType": event_type,
        "deliveryClass": delivery_class,
        "target": {"type": target_type, "uid": target_uid},
        "commandUid": command_uid,
        "occurredAt": "2026-07-24T01:00:30.000Z",
        "clockQuality": "SYNCED",
        "payloadSha256": payload_sha256(payload),
        "payload": payload,
    }


def build_onenet_examples() -> dict[str, Any]:
    config = _config_identity()
    application_uid = "10000000-0000-4000-8000-000000000001"
    apply_payload = {
        "applicationUid": application_uid,
        "config": config,
        "deviceConfig": {
            "edgeHeartbeatIntervalMs": 3600000,
            "edgeHeartbeatMissThreshold": 3,
            "continueDeliveryWaitMs": 30000,
            "negativeWeightThresholdGrams": 500,
            "deliveryAutoCloseMs": 120000,
            "weightMeasurementTimeoutMs": 6000,
            "deliveryDoorTravelWaitMs": 30000,
            "cleanSolenoidPulseMs": 1000,
            "smokeMonitoringEnabled": True,
        },
        "ports": [
            {
                "portNo": port_no,
                "displayName": f"投口{port_no}",
                "enabled": True,
                "unitPriceTenThousandths": 4500,
                "fullnessMode": "SENSOR_OR_WEIGHT",
                "configuredFullWeightGrams": 50000,
                "fullnessSettleWaitMs": 5000,
                "fullnessSensorKind": "ULTRASONIC",
                "fullnessDistanceThresholdMm": 600,
                "fullnessSampleCount": 5,
                "fullnessMinimumValidSampleCount": 3,
                "fullnessEchoTimeoutUs": 30000,
                "weightStableWindowMs": 1500,
                "weightMaximumFluctuationGrams": 20,
                "weightRequiredSampleCount": 10,
                "weightMeasurementTimeoutMs": 6000,
                "weightMinimumGrams": -5000,
                "weightMaximumGrams": 100000,
                "calibrationVersion": 4,
            }
            for port_no in (1, 2)
        ],
    }
    apply_command_uid = "20000000-0000-4000-8000-000000000001"
    apply_command = _command(
        apply_command_uid,
        "APPLY_CONFIGURATION",
        "CONFIGURATION_APPLICATION",
        application_uid,
        apply_payload,
    )

    session_uid = "30000000-0000-4000-8000-000000000001"
    start_delivery_payload = {
        "sessionUid": session_uid,
        "portNo": 2,
        "bagUid": "30000000-0000-4000-8000-000000000002",
        "config": config,
        "unitPriceTenThousandths": 4500,
        "continueDeliveryWaitMs": 30000,
        "negativeWeightThresholdGrams": 500,
        "deliveryAutoCloseMs": 120000,
    }
    start_delivery_uid = "30000000-0000-4000-8000-000000000003"
    start_delivery_command = _command(
        start_delivery_uid,
        "START_DELIVERY_SESSION",
        "DELIVERY_SESSION",
        session_uid,
        start_delivery_payload,
    )

    delivery_payload = {
        "sessionUid": session_uid,
        "portNo": 2,
        "firstPreOpenMeasurement": _measurement(
            "30000000-0000-4000-8000-000000000004", 12000, 101, 11
        ),
        "finalPostCloseMeasurement": _measurement(
            "30000000-0000-4000-8000-000000000005", 13250, 101, 29
        ),
        "deliveryNetWeightGrams": 1250,
        "finalDoorCommand": {
            "command": "CLOSE",
            "outputStatus": "COMMAND_DISPATCHED",
            "physicalStateBasis": "NOT_OBSERVABLE",
        },
        "completionReason": "USER_ENDED",
        "manualReviewRequired": False,
        "negativeWeightAnomaly": False,
        "frozenConfig": config,
        "unitPriceTenThousandths": 4500,
        "photos": [
            _pending_photo("BEFORE_INNER"),
            _pending_photo("BEFORE_OUTER"),
            _pending_photo("AFTER_INNER"),
            _pending_photo("AFTER_OUTER"),
        ],
    }
    delivery_event_uid = "30000000-0000-4000-8000-000000000006"
    delivery_event = _event(
        delivery_event_uid,
        1042,
        "DELIVERY_COMPLETE",
        "RELIABLE_FACT",
        "DELIVERY_SESSION",
        session_uid,
        delivery_payload,
        command_uid=start_delivery_uid,
    )

    operation_uid = "40000000-0000-4000-8000-000000000001"
    clean_payload = {
        "operationUid": operation_uid,
        "portNo": 2,
        "oldBagUid": None,
        "newBagUid": "40000000-0000-4000-8000-000000000002",
        "preUnlockMeasurement": _measurement(
            "40000000-0000-4000-8000-000000000003", 20000, 101, 31
        ),
        "cleanerConfirmedFinalMeasurement": _measurement(
            "40000000-0000-4000-8000-000000000004", 1200, 101, 42
        ),
        "removedNetWeightGrams": 18800,
        "newBaselineWeightGrams": 1200,
        "cleanerCompletionConfirmed": True,
        "cleanActionSequence": 3,
        "cleanLockAndManualDoorConfirmation": {
            "lockPowerState": "DEENERGIZED",
            "solenoidHealth": "OK",
            "physicalDoorStateBasis": "CLEANER_CONFIRMATION",
            "cleanerPhysicalCloseConfirmed": True,
        },
        "frozenConfig": config,
        "photos": [
            _pending_photo("FIRST_OPEN_INNER"),
            _pending_photo("FIRST_OPEN_OUTER"),
            _pending_photo("FINAL_CLOSE_INNER"),
            _pending_photo("FINAL_CLOSE_OUTER"),
        ],
    }
    clean_command_uid = "40000000-0000-4000-8000-000000000005"
    clean_event = _event(
        "40000000-0000-4000-8000-000000000006",
        1043,
        "CLEAN_COMPLETE",
        "RELIABLE_FACT",
        "CLEAN_OPERATION",
        operation_uid,
        clean_payload,
        command_uid=clean_command_uid,
    )

    detection_uid = "50000000-0000-4000-8000-000000000001"
    fullness_payload = {
        "detectionUid": detection_uid,
        "portNo": 2,
        "sampleRole": "INITIAL",
        "triggerType": "DELIVERY_COMPLETE",
        "fullnessMode": "SENSOR_OR_WEIGHT",
        "fullnessSensorKind": "ULTRASONIC",
        "fullnessSensorValue": "CLEAR",
        "fullnessSampleBasis": "MEASURED_MEDIAN",
        "representativeDistanceMm": 720,
        "requestedSampleCount": 5,
        "validSampleCount": 5,
        "totalWeightMeasurement": _measurement(
            "50000000-0000-4000-8000-000000000002", 13250, 101, 45
        ),
        "frozenConfig": config,
    }
    fullness_event = _event(
        "50000000-0000-4000-8000-000000000003",
        1044,
        "FULLNESS_SAMPLE_COMPLETE",
        "RELIABLE_FACT",
        "FULLNESS_DETECTION",
        detection_uid,
        fullness_payload,
        command_uid="50000000-0000-4000-8000-000000000004",
    )
    fullness_state_change_uid = (
        "51000000-0000-4000-8000-000000000001"
    )
    fullness_state_event = _event(
        "51000000-0000-4000-8000-000000000002",
        1045,
        "FULLNESS_STATE_CHANGED",
        "RELIABLE_FACT",
        "PORT_FULLNESS_STATE",
        fullness_state_change_uid,
        {
            "stateChangeUid": fullness_state_change_uid,
            "portNo": 2,
            "bagUid": start_delivery_payload["bagUid"],
            "state": "FULL",
            "sourceWorkType": "DELIVERY_SESSION",
            "sourceWorkUid": session_uid,
            "fullnessMode": "SENSOR_OR_WEIGHT",
            "fullnessSensorKind": "DIGITAL_INFRARED",
            "fullnessSensorValue": "BLOCKED",
            "confirmationBasis": (
                "FIXED_FRAME_CACHED_FINAL_OBSERVATION"
            ),
            "totalWeightMeasurement": _measurement(
                "51000000-0000-4000-8000-000000000003",
                51200,
                101,
                46,
            ),
            "baselineWeightGrams": 1200,
            "configuredFullWeightGrams": 50000,
            "fullnessPercentHundredths": 10000,
            "weightFull": True,
            "frozenConfig": config,
        },
        command_uid=None,
    )

    confirmation_uid = "60000000-0000-4000-8000-000000000001"
    confirmation_payload = {
        "confirmationUid": confirmation_uid,
        "originalEventUid": delivery_event_uid,
        "originalPayloadSha256": delivery_event["payloadSha256"],
        "outcome": "BUSINESS_APPLIED",
        "effectKind": "CREATED",
        "processedAt": "2026-07-24T01:00:31.000Z",
        "resultReferences": [
            {"type": "DELIVERY_ORDER", "key": "DO202607240001"}
        ],
        "errorCode": None,
        "quarantineUid": None,
    }
    confirm_command = _command(
        "60000000-0000-4000-8000-000000000002",
        "CONFIRM_EDGE_EVENT",
        "EDGE_EVENT",
        delivery_event_uid,
        confirmation_payload,
    )
    receipt_payload = {
        "confirmationUid": confirmation_uid,
        "originalEventUid": delivery_event_uid,
        "originalPayloadSha256": delivery_event["payloadSha256"],
        "outcome": "BUSINESS_APPLIED",
    }
    receipt_event = _event(
        "60000000-0000-4000-8000-000000000003",
        1045,
        "BUSINESS_CONFIRMATION_RECEIPT",
        "CONTROL_RECEIPT",
        "BUSINESS_CONFIRMATION",
        confirmation_uid,
        receipt_payload,
        command_uid=confirm_command["commandUid"],
    )
    command_receipt = {
        "schemaVersion": 2,
        "commandUid": start_delivery_uid,
        "receiptState": "ACCEPTED",
        "errorCode": None,
        "edgeBootId": 9001,
    }

    start_clean_uid = "81000000-0000-4000-8000-000000000001"
    start_clean_payload = {
        "operationUid": operation_uid,
        "portNo": 2,
        "oldBagUid": None,
        "oldBaselineWeightGrams": None,
        "newBagUid": clean_payload["newBagUid"],
        "config": config,
        "operationWindowMs": 1800000,
        "recoveryGeneration": 0,
    }
    start_clean_command = _command(
        start_clean_uid,
        "START_CLEAN_OPERATION",
        "CLEAN_OPERATION",
        operation_uid,
        start_clean_payload,
        cos_grant=_fake_cos_grant(
            tag="3",
            work_type="CLEAN_OPERATION",
            work_uid=operation_uid,
        ),
    )
    end_clean_command = _command(
        "81000000-0000-4000-8000-000000000002",
        "END_CLEAN_BEFORE_UNLOCK",
        "CLEAN_OPERATION",
        operation_uid,
        {
            "operationUid": operation_uid,
            "portNo": 2,
            "reason": "CLEANER_CANCELLED",
        },
    )
    resume_clean_command = _command(
        "81000000-0000-4000-8000-000000000003",
        "RESUME_CLEAN_OPERATION",
        "CLEAN_OPERATION",
        operation_uid,
        {
            "operationUid": operation_uid,
            "portNo": 2,
            "newBagUid": clean_payload["newBagUid"],
            "config": config,
            "operationWindowMs": 1800000,
            "recoveryGeneration": 1,
            "originalCleanerConfirmedOnsite": True,
        },
        cos_grant=_fake_cos_grant(
            tag="4",
            work_type="CLEAN_OPERATION",
            work_uid=operation_uid,
        ),
    )
    sample_fullness_command_uid = "81000000-0000-4000-8000-000000000004"
    sample_fullness_command = _command(
        sample_fullness_command_uid,
        "SAMPLE_FULLNESS",
        "FULLNESS_DETECTION",
        detection_uid,
        {
            "detectionUid": detection_uid,
            "portNo": 2,
            "sampleRole": "INITIAL",
            "triggerType": "DELIVERY_COMPLETE",
            "fullnessMode": "SENSOR_OR_WEIGHT",
            "currentBaselineWeightGrams": 1200,
            "configuredFullWeightGrams": 50000,
            "settleWaitMs": 5000,
            "measurementTimeoutMs": 6000,
            "config": config,
        },
    )
    baseline_uid = "82000000-0000-4000-8000-000000000001"
    measure_baseline_command_uid = "82000000-0000-4000-8000-000000000002"
    measure_baseline_command = _command(
        measure_baseline_command_uid,
        "MEASURE_EMPTY_BAG_BASELINE",
        "BASELINE_MEASUREMENT",
        baseline_uid,
        {
            "measurementUid": baseline_uid,
            "portNo": 2,
            "bagUid": clean_payload["newBagUid"],
            "emptyBagConfirmed": True,
            "measurementTimeoutMs": 6000,
            "config": config,
        },
    )
    photo_grant_request_uid = "83000000-0000-4000-8000-000000000001"
    provide_photo_grant_command = _command(
        "83000000-0000-4000-8000-000000000002",
        "PROVIDE_PHOTO_UPLOAD_GRANT",
        "PHOTO_GRANT_REQUEST",
        photo_grant_request_uid,
        {
            "grantRequestEventUid": photo_grant_request_uid,
            "workType": "DELIVERY_SESSION",
            "workUid": session_uid,
            "authorizedSlots": [
                "BEFORE_INNER",
                "BEFORE_OUTER",
                "AFTER_INNER",
                "AFTER_OUTER",
            ],
        },
        cos_grant=_fake_cos_grant(
            tag="5",
            work_type="DELIVERY_SESSION",
            work_uid=session_uid,
        ),
    )

    command_observed_event = _event(
        "84000000-0000-4000-8000-000000000001",
        1046,
        "DEVICE_COMMAND_OBSERVED",
        "RELIABLE_FACT",
        "DEVICE_COMMAND",
        start_delivery_uid,
        {
            "observedCommandType": "START_DELIVERY_SESSION",
            "stage": "MCU_ACCEPTED",
            "mcuCommandUid": "84000000-0000-4000-8000-000000000002",
            "errorCode": None,
        },
        command_uid=start_delivery_uid,
    )
    configuration_progress_event = _event(
        "85000000-0000-4000-8000-000000000001",
        1047,
        "CONFIGURATION_PROGRESS",
        "RELIABLE_FACT",
        "CONFIGURATION_APPLICATION",
        application_uid,
        {
            "applicationUid": application_uid,
            "stage": "APPLIED",
            "version": config["version"],
            "contentSha256": config["contentSha256"],
            "mcuPayloadSha256": config["mcuPayloadSha256"],
            "mcuCommandUid": "85000000-0000-4000-8000-000000000002",
            "errorCode": None,
        },
        command_uid=apply_command_uid,
    )
    baseline_event = _event(
        "86000000-0000-4000-8000-000000000001",
        1048,
        "BASELINE_MEASUREMENT_COMPLETE",
        "RELIABLE_FACT",
        "BASELINE_MEASUREMENT",
        baseline_uid,
        {
            "measurementUid": baseline_uid,
            "portNo": 2,
            "bagUid": clean_payload["newBagUid"],
            "emptyBagConfirmed": True,
            "totalWeightMeasurement": _measurement(
                "86000000-0000-4000-8000-000000000002",
                1180,
                101,
                47,
            ),
            "frozenConfig": config,
        },
        command_uid=measure_baseline_command_uid,
    )
    fault_uid = "87000000-0000-4000-8000-000000000001"
    fault_payload = {
        "faultUid": fault_uid,
        "portNo": 2,
        "component": "WEIGHT_SENSOR",
        "severity": "BLOCK_PORT",
        "faultCode": "WEIGHT_SENSOR",
        "mcuBootId": 101,
        "mcuEventSequence": 48,
    }
    fault_observed_event = _event(
        "87000000-0000-4000-8000-000000000002",
        1049,
        "DEVICE_FAULT_OBSERVED",
        "RELIABLE_FACT",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        fault_payload,
        command_uid=None,
    )
    recovered_payload = copy.deepcopy(fault_payload)
    recovered_payload["mcuEventSequence"] = 49
    fault_recovered_event = _event(
        "87000000-0000-4000-8000-000000000003",
        1050,
        "DEVICE_FAULT_RECOVERED",
        "RELIABLE_FACT",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        recovered_payload,
        command_uid=None,
    )
    safety_sensor_event = _event(
        "87500000-0000-4000-8000-000000000001",
        1054,
        "SAFETY_SENSOR_STATE_CHANGED",
        "RELIABLE_FACT",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "portNo": 2,
            "smokeState": "ALARM",
            "smokeSensorHealth": "OK",
            "faultCode": None,
            "workType": "DELIVERY_SESSION",
            "workUid": session_uid,
            "mcuBootId": 101,
            "mcuEventSequence": 50,
        },
        command_uid=None,
    )
    photo_uid = "88000000-0000-4000-8000-000000000001"
    photo_status_event = _event(
        "88000000-0000-4000-8000-000000000002",
        1051,
        "PHOTO_STATUS_REPORTED",
        "RELIABLE_FACT",
        "DELIVERY_SESSION",
        session_uid,
        {
            "workType": "DELIVERY_SESSION",
            "workUid": session_uid,
            "photo": {
                "slot": "AFTER_INNER",
                "status": "AVAILABLE",
                "photoUid": photo_uid,
                "url": (
                    "https://ecobin-contract-1250000000.cos.ap-guangzhou."
                    f"myqcloud.com/ecobin/delivery-session/{session_uid}/"
                    f"AFTER_INNER/{photo_uid}.jpg"
                ),
                "sha256": "c" * 64,
                "sizeBytes": 483220,
                "capturedAt": "2026-07-24T01:00:29.000Z",
                "missingReason": None,
            },
        },
        command_uid=None,
    )
    photo_grant_request_event = _event(
        photo_grant_request_uid,
        1052,
        "PHOTO_UPLOAD_GRANT_REQUESTED",
        "RELIABLE_FACT",
        "DELIVERY_SESSION",
        session_uid,
        {
            "workType": "DELIVERY_SESSION",
            "workUid": session_uid,
            "requestedSlots": ["AFTER_INNER", "AFTER_OUTER"],
            "reason": "GRANT_EXPIRED",
        },
        command_uid=None,
    )
    runtime_snapshot_event = _event(
        "89000000-0000-4000-8000-000000000001",
        1053,
        "DEVICE_RUNTIME_SNAPSHOT",
        "TELEMETRY_SNAPSHOT",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "edgeBootId": 9001,
            "edgeVersion": "1.0.0-rc.3",
            "mcuBootId": 101,
            "mcuFirmwareVersion": "1.0.0-rc.3",
            "mcuFirmwareIdentity": {
                "queryStatus": "OK",
                "statusCode": 0,
                "fixedFrameRevision": 2,
                "firmwareVersionCode": 10003,
                "firmwareVersion": "1.0.0-rc.3",
                "firmwareIdentityHex": "0123456789abcdef",
            },
            "uartState": "READY",
            "uartProtocolMajor": 1,
            "uartProtocolMinor": 0,
            "capabilityBitmapHex": "0000000000001fff",
            "appliedConfig": config,
            "localStorageState": "HEALTHY",
            "clockState": "SYNCED",
            "pendingReliableEventCount": 2,
            "ports": [
                {
                    "portNo": port_no,
                    "lastDeliveryDoorCommand": "CLOSE",
                    "lastDeliveryDoorOutputStatus": "COMMAND_DISPATCHED",
                    "deliveryDoorPhysicalStateBasis": "NOT_OBSERVABLE",
                    "cleanLockPowerState": "DEENERGIZED",
                    "solenoidHealth": "OK",
                    "cleanDoorStateBasis": "NOT_OBSERVABLE",
                    "cleanerPhysicalCloseConfirmed": False,
                    "weightMeasurementUid": (
                        f"89500000-0000-4000-8000-{port_no:012d}"
                    ),
                    "weightMeasurementStatus": "STABLE",
                    "weightValueAvailable": True,
                    "reportedWeightGrams": 13250,
                    "weightValueKind": "STABLE_WINDOW_MEAN",
                    "measurementElapsedMs": 1200,
                    "weightSampleCount": 12,
                    "calibrationVersion": 4,
                    "weightSensorHealth": "OK",
                    "weightFaultCode": None,
                    "weightMcuBootId": 101,
                    "weightMcuEventSequence": 50 + port_no,
                    "fullnessSensorKind": "ULTRASONIC",
                    "fullnessSensorValue": "CLEAR",
                    "fullnessSampleBasis": "MEASURED_MEDIAN",
                    "representativeDistanceMm": 720,
                    "fullnessValidSampleCount": 5,
                    "smokeState": "NORMAL",
                    "smokeSensorHealth": "OK",
                    "faultBitmap": 0,
                }
                for port_no in (1, 2)
            ],
        },
        command_uid=None,
    )
    acceptance_challenge_uid = (
        "8a000000-0000-4000-8000-000000000003"
    )
    acceptance_command_uid = (
        "8a000000-0000-4000-8000-000000000004"
    )
    device_entry_url = (
        "https://www.jinshoubao.com/device-entry/"
        "?deviceCode=Dv_contract000000000000000000000000"
    )
    device_entry_url_sha256 = hashlib.sha256(
        device_entry_url.encode("ascii")
    ).hexdigest()
    factory_bag_revision = 2
    factory_bag_set_sha256 = "a" * 64
    request_acceptance_command = _command(
        acceptance_command_uid,
        "REQUEST_DEVICE_ACCEPTANCE",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "challengeUid": acceptance_challenge_uid,
            "expectedPortCount": 2,
            "factoryBagRevision": factory_bag_revision,
            "factoryBagSetSha256": factory_bag_set_sha256,
            "deviceEntryUrl": device_entry_url,
            "deviceEntryUrlSha256": device_entry_url_sha256,
        },
        cos_grant=_fake_cos_grant(
            tag="6",
            work_type="DEVICE_ACCEPTANCE",
            work_uid=acceptance_challenge_uid,
        ),
    )
    acceptance_evidence_event = _event(
        "8a000000-0000-4000-8000-000000000001",
        1055,
        "DEVICE_ACCEPTANCE_EVIDENCE",
        "RELIABLE_FACT",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "evidenceSchemaVersion": 3,
            "challengeUid": acceptance_challenge_uid,
            "factoryBagRevision": factory_bag_revision,
            "factoryBagSetSha256": factory_bag_set_sha256,
            "edgeSoftwareVersion": "0.1.0",
            "edgeProtocolVersion": "2",
            "edgeStoreInstanceUid": (
                "8a000000-0000-4000-8000-000000000002"
            ),
            "mcuFirmwareVersion": "fixed-frame-1.0.0",
            "persistentStoreHealthy": True,
            "trustedTimeHealthy": True,
            "configurationPersistenceHealthy": True,
            "mcuCommunicationHealthy": True,
            "sensorsHealthy": True,
            "camerasCaptureHealthy": True,
            "cameraUploadHealthy": True,
            "deviceEntryUrlStored": True,
            "deviceEntryUrlSha256": device_entry_url_sha256,
            "mcuSimulated": False,
            "camerasSimulated": False,
            "verifiedPortCount": 2,
            "verifiedCameraCount": 2,
            "sensorSampleSha256": "d" * 64,
            "cameraCaptureSha256": "e" * 64,
            "cameraUploadSha256": "f" * 64,
        },
        command_uid=acceptance_command_uid,
    )
    factory_seal_command_uid = "8a000000-0000-4000-8000-000000000007"
    factory_seal_command = _command(
        factory_seal_command_uid,
        "AUTHORIZE_FACTORY_SEAL",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "sealAuthorizationSchemaVersion": 1,
            "hardwareSn": "SN-CONTRACT-0001",
            "acceptanceGeneration": 1,
            "acceptanceEvidenceUid": acceptance_evidence_event["eventUid"],
            "acceptanceChallengeUid": acceptance_challenge_uid,
            "acceptanceEvidenceSha256": (
                acceptance_evidence_event["payloadSha256"]
            ),
            "factoryBagRevision": factory_bag_revision,
            "factoryBagSetSha256": factory_bag_set_sha256,
        },
    )
    factory_seal_command["expiresAt"] = "2027-07-24T01:00:00.000Z"
    image_release_id = "ecobin-opiz3-2026.08.22.1"
    image_release_sha256 = "1" * 64
    factory_report_sha256 = "2" * 64
    factory_seal_binding_values = {
        "commandUid": factory_seal_command_uid,
        "hardwareSn": "SN-CONTRACT-0001",
        "acceptanceGeneration": 1,
        "acceptanceEvidenceUid": acceptance_evidence_event["eventUid"],
        "acceptanceChallengeUid": acceptance_challenge_uid,
        "acceptanceEvidenceSha256": (
            acceptance_evidence_event["payloadSha256"]
        ),
        "factoryBagRevision": factory_bag_revision,
        "factoryBagSetSha256": factory_bag_set_sha256,
        "imageReleaseId": image_release_id,
        "imageReleaseSha256": image_release_sha256,
        "factoryReportSha256": factory_report_sha256,
    }
    factory_seal_completed_event = _event(
        "8a000000-0000-4000-8000-00000000000a",
        1058,
        "FACTORY_SEAL_COMPLETED",
        "RELIABLE_FACT",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "sealCompletionSchemaVersion": 1,
            "hardwareSn": "SN-CONTRACT-0001",
            "authorizationCommandUid": factory_seal_command_uid,
            "acceptanceGeneration": 1,
            "acceptanceEvidenceUid": acceptance_evidence_event["eventUid"],
            "acceptanceEvidenceSha256": (
                acceptance_evidence_event["payloadSha256"]
            ),
            "acceptanceChallengeUid": acceptance_challenge_uid,
            "factoryBagRevision": factory_bag_revision,
            "factoryBagSetSha256": factory_bag_set_sha256,
            "imageReleaseId": image_release_id,
            "imageReleaseSha256": image_release_sha256,
            "factoryReportSha256": factory_report_sha256,
            "authorizationBindingSha256": payload_sha256(
                factory_seal_binding_values
            ),
            "operatorConfirmationUid": (
                "8a000000-0000-4000-8000-00000000000b"
            ),
            "sealedAt": "2026-07-24T01:00:20.000Z",
            "cleanupCompletedAt": "2026-07-24T01:00:30.000Z",
        },
        command_uid=factory_seal_command_uid,
    )
    sync_device_entry_url_command = _command(
        "8a000000-0000-4000-8000-000000000005",
        "SYNC_DEVICE_ENTRY_URL",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "deviceEntryUrl": device_entry_url,
            "deviceEntryUrlSha256": device_entry_url_sha256,
        },
    )
    firmware_release_uid = "8c000000-0000-4000-8000-000000000001"
    firmware_deployment_uid = "8c000000-0000-4000-8000-000000000002"
    firmware_command_uid = "8c000000-0000-4000-8000-000000000003"
    firmware_update_uid = "8c000000-0000-4000-8000-000000000004"
    firmware_package_sha256 = "c" * 64
    start_mcu_firmware_update_command = _command(
        firmware_command_uid,
        "START_MCU_FIRMWARE_UPDATE",
        "MCU_FIRMWARE_DEPLOYMENT",
        firmware_deployment_uid,
        {
            "deploymentUid": firmware_deployment_uid,
            "releaseUid": firmware_release_uid,
            "firmwareVersion": "2.1.0",
            "firmwareVersionCode": 20100,
            "firmwareIdentityHex": "0123456789abcdef",
            "objectKey": (
                f"ecobin/mcu-firmware/{firmware_release_uid}/"
                f"{firmware_package_sha256}.efw"
            ),
            "packageSha256": firmware_package_sha256,
            "packageSize": 65536,
            "reason": "single-device validation",
        },
        cos_grant=_fake_cos_grant(
            tag="7",
            work_type="MCU_FIRMWARE_RELEASE",
            work_uid=firmware_release_uid,
        ),
    )
    mcu_firmware_progress_event = _event(
        "8c000000-0000-4000-8000-000000000005",
        1057,
        "MCU_FIRMWARE_UPDATE_PROGRESS",
        "RELIABLE_FACT",
        "MCU_FIRMWARE_DEPLOYMENT",
        firmware_deployment_uid,
        {
            "deploymentUid": firmware_deployment_uid,
            "updateUid": firmware_update_uid,
            "releaseUid": firmware_release_uid,
            "source": "CLOUD",
            "stage": "SUCCEEDED",
            "firmwareVersion": "2.1.0",
            "firmwareVersionCode": 20100,
            "firmwareIdentityHex": "0123456789abcdef",
            "fixedFrameRevision": 2,
            "targetAttemptCount": 1,
            "rollbackAttemptCount": 0,
            "legacyPreflight": False,
            "downgradeAuthorized": False,
            "installedFirmwareVersion": "2.1.0",
            "installedFirmwareVersionCode": 20100,
            "installedFirmwareIdentityHex": "0123456789abcdef",
            "errorCode": None,
        },
        command_uid=firmware_command_uid,
    )
    remote_support_session_uid = (
        "8b000000-0000-4000-8000-000000000001"
    )
    open_remote_support_command_uid = (
        "8b000000-0000-4000-8000-000000000002"
    )
    open_remote_support_command = _command(
        open_remote_support_command_uid,
        "OPEN_REMOTE_SUPPORT_TUNNEL",
        "REMOTE_SUPPORT_SESSION",
        remote_support_session_uid,
        {
            "sessionUid": remote_support_session_uid,
            "remotePort": 22011,
            "expiresAt": "2026-07-24T01:01:00.000Z",
        },
    )
    close_remote_support_command = _command(
        "8b000000-0000-4000-8000-000000000003",
        "CLOSE_REMOTE_SUPPORT_TUNNEL",
        "REMOTE_SUPPORT_SESSION",
        remote_support_session_uid,
        {"sessionUid": remote_support_session_uid},
    )
    remote_support_status_event = _event(
        "8b000000-0000-4000-8000-000000000004",
        1056,
        "REMOTE_SUPPORT_TUNNEL_STATUS",
        "RELIABLE_FACT",
        "DEVICE_ASSET",
        "SN-CONTRACT-0001",
        {
            "sessionUid": remote_support_session_uid,
            "state": "OPEN",
            "remotePort": 22011,
            "failureCode": None,
        },
        command_uid=open_remote_support_command_uid,
    )

    return {
        "apply-configuration.command.json": (
            apply_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "start-delivery-session.command.json": (
            start_delivery_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "delivery-complete.event.json": (
            delivery_event,
            "../../onenet/events/events.schema.json",
        ),
        "clean-complete.event.json": (
            clean_event,
            "../../onenet/events/events.schema.json",
        ),
        "fullness-sample-complete.event.json": (
            fullness_event,
            "../../onenet/events/events.schema.json",
        ),
        "fullness-state-changed.event.json": (
            fullness_state_event,
            "../../onenet/events/events.schema.json",
        ),
        "confirm-edge-event.command.json": (
            confirm_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "business-confirmation-receipt.event.json": (
            receipt_event,
            "../../onenet/events/events.schema.json",
        ),
        "command-receipt.json": (
            command_receipt,
            "../../onenet/command-receipt.schema.json",
        ),
        "start-clean-operation.command.json": (
            start_clean_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "end-clean-before-unlock.command.json": (
            end_clean_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "resume-clean-operation.command.json": (
            resume_clean_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "sample-fullness.command.json": (
            sample_fullness_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "measure-empty-bag-baseline.command.json": (
            measure_baseline_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "provide-photo-upload-grant.command.json": (
            provide_photo_grant_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "request-device-acceptance.command.json": (
            request_acceptance_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "authorize-factory-seal.command.json": (
            factory_seal_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "sync-device-entry-url.command.json": (
            sync_device_entry_url_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "start-mcu-firmware-update.command.json": (
            start_mcu_firmware_update_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "open-remote-support-tunnel.command.json": (
            open_remote_support_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "close-remote-support-tunnel.command.json": (
            close_remote_support_command,
            "../../onenet/commands/commands.schema.json",
        ),
        "device-command-observed.event.json": (
            command_observed_event,
            "../../onenet/events/events.schema.json",
        ),
        "configuration-progress.event.json": (
            configuration_progress_event,
            "../../onenet/events/events.schema.json",
        ),
        "baseline-measurement-complete.event.json": (
            baseline_event,
            "../../onenet/events/events.schema.json",
        ),
        "device-fault-observed.event.json": (
            fault_observed_event,
            "../../onenet/events/events.schema.json",
        ),
        "device-fault-recovered.event.json": (
            fault_recovered_event,
            "../../onenet/events/events.schema.json",
        ),
        "safety-sensor-state-changed.event.json": (
            safety_sensor_event,
            "../../onenet/events/events.schema.json",
        ),
        "photo-status-reported.event.json": (
            photo_status_event,
            "../../onenet/events/events.schema.json",
        ),
        "photo-upload-grant-requested.event.json": (
            photo_grant_request_event,
            "../../onenet/events/events.schema.json",
        ),
        "device-runtime-snapshot.event.json": (
            runtime_snapshot_event,
            "../../onenet/events/events.schema.json",
        ),
        "device-acceptance-evidence.event.json": (
            acceptance_evidence_event,
            "../../onenet/events/events.schema.json",
        ),
        "factory-seal-completed.event.json": (
            factory_seal_completed_event,
            "../../onenet/events/events.schema.json",
        ),
        "remote-support-tunnel-status.event.json": (
            remote_support_status_event,
            "../../onenet/events/events.schema.json",
        ),
        "mcu-firmware-update-progress.event.json": (
            mcu_firmware_progress_event,
            "../../onenet/events/events.schema.json",
        ),
    }


def build_canonicalization_vectors(examples: Mapping[str, Any]) -> list[dict[str, Any]]:
    selected = [
        "start-delivery-session.command.json",
        "delivery-complete.event.json",
        "clean-complete.event.json",
    ]
    vectors = []
    for filename in selected:
        instance = examples[filename][0]
        canonical = canonical_json_bytes(instance["payload"])
        vectors.append(
            {
                "name": filename.removesuffix(".json").replace(".", "_").replace("-", "_"),
                "payload": instance["payload"],
                "canonicalUtf8Hex": canonical.hex(),
                "sha256": payload_sha256(instance["payload"]),
            }
        )
    tricky_payload = {
        "\ue000": "private-use",
        "😀": "surrogate-pair-sorts-first",
        "controls": "\b\t\n\f\r",
        "nested": {
            "safeMaximum": 9007199254740991,
            "safeMinimum": -9007199254740991,
            "values": [None, False, True, "雪"],
        },
    }
    tricky_canonical = canonical_json_bytes(tricky_payload)
    vectors.append(
        {
            "name": "utf16_order_controls_and_safe_integers",
            "payload": tricky_payload,
            "canonicalUtf8Hex": tricky_canonical.hex(),
            "sha256": payload_sha256(tricky_payload),
        }
    )
    return vectors


def _fake_cos_grant(
    *,
    tag: str,
    work_type: str,
    work_uid: str,
) -> dict[str, Any]:
    path_type = {
        "DELIVERY_SESSION": "delivery-session",
        "CLEAN_OPERATION": "clean-operation",
        "DEVICE_ACCEPTANCE": "device-acceptance",
        "MCU_FIRMWARE_RELEASE": "mcu-firmware",
    }[work_type]
    return {
        "grantUid": f"71000000-0000-4000-8000-00000000000{tag}",
        "tmpSecretId": f"TMP_SECRET_ID_{tag}",
        "tmpSecretKey": f"TMP_SECRET_KEY_{tag}",
        "sessionTokenParts": [f"TOKEN_{tag}_PART_1", f"TOKEN_{tag}_PART_2"],
        "bucket": "ecobin-contract-1250000000",
        "region": "ap-guangzhou",
        "baseUrl": "https://ecobin-contract-1250000000.cos.ap-guangzhou.myqcloud.com",
        "keyPrefix": f"ecobin/{path_type}/{work_uid}/",
        "expiresAt": "2026-07-24T01:30:00.000Z",
    }


def build_onenet_identity_digest_vectors(
    examples: Mapping[str, Any],
) -> list[dict[str, Any]]:
    base_command = copy.deepcopy(
        examples["start-delivery-session.command.json"][0]
    )
    work_uid = base_command["payload"]["sessionUid"]
    refreshed = copy.deepcopy(base_command)
    refreshed["cosGrant"] = _fake_cos_grant(
        tag="1",
        work_type="DELIVERY_SESSION",
        work_uid=work_uid,
    )
    refreshed_again = copy.deepcopy(refreshed)
    refreshed_again["cosGrant"] = _fake_cos_grant(
        tag="2",
        work_type="DELIVERY_SESSION",
        work_uid=work_uid,
    )
    target_changed = copy.deepcopy(base_command)
    target_changed["target"]["uid"] = "30000000-0000-4000-8000-000000000099"
    expiry_changed = copy.deepcopy(base_command)
    expiry_changed["expiresAt"] = "2026-07-24T01:02:00.000Z"

    base_event = copy.deepcopy(examples["delivery-complete.event.json"][0])
    trusted_source = {
        "productId": "ecobin-product-contract",
        "deviceName": "SN-CONTRACT-0001",
    }
    source_changed = {
        "productId": trusted_source["productId"],
        "deviceName": "SN-CONTRACT-0002",
    }

    vectors: list[dict[str, Any]] = []
    for name, command in (
        ("command_without_cos_grant", base_command),
        ("command_with_cos_grant_refresh_1", refreshed),
        ("command_with_cos_grant_refresh_2", refreshed_again),
        ("command_target_changed", target_changed),
        ("command_expiry_changed", expiry_changed),
    ):
        vectors.append(
            {
                "name": name,
                "kind": "COMMAND",
                "input": command,
                "projection": onenet_command_canonical_projection(command),
                "preimageHex": onenet_command_canonical_preimage(command).hex(),
                "sha256": onenet_command_canonical_sha256(command),
            }
        )
    for name, source in (
        ("event_authenticated_source", trusted_source),
        ("event_authenticated_device_changed", source_changed),
    ):
        vectors.append(
            {
                "name": name,
                "kind": "EVENT",
                "trustedSource": source,
                "input": base_event,
                "projection": onenet_event_canonical_projection(
                    base_event,
                    trusted_product_id=source["productId"],
                    trusted_device_name=source["deviceName"],
                ),
                "preimageHex": onenet_event_canonical_preimage(
                    base_event,
                    trusted_product_id=source["productId"],
                    trusted_device_name=source["deviceName"],
                ).hex(),
                "sha256": onenet_event_canonical_sha256(
                    base_event,
                    trusted_product_id=source["productId"],
                    trusted_device_name=source["deviceName"],
                ),
            }
        )
    return vectors


def _java_json_value(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return f"{value}L"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, list):
        return "list(" + ", ".join(_java_json_value(item) for item in value) + ")"
    if isinstance(value, dict):
        entries: list[str] = []
        for key, item in value.items():
            entries.extend((_java_json_value(key), _java_json_value(item)))
        return "map(" + ", ".join(entries) + ")"
    raise TypeError(f"unsupported Java vector value: {type(value).__name__}")


def render_java_canonical_json() -> str:
    return """// Generated by contracts/tools/generate_contracts.py. Do not edit.
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class EcobinCanonicalJson {
    public static final long SAFE_INTEGER_MAX = 9_007_199_254_740_991L;
    private static final byte[] COMMAND_DOMAIN =
        domain("ECOBIN:ONENET:COMMAND:v2");
    private static final byte[] EVENT_DOMAIN =
        domain("ECOBIN:ONENET:EVENT:v2");

    private EcobinCanonicalJson() {}

    private static byte[] domain(String value) {
        byte[] text = value.getBytes(StandardCharsets.UTF_8);
        byte[] result = new byte[text.length + 1];
        System.arraycopy(text, 0, result, 0, text.length);
        return result;
    }

    public static byte[] canonicalBytes(Object value) {
        StringBuilder output = new StringBuilder();
        appendValue(output, value, "$");
        return output.toString().getBytes(StandardCharsets.UTF_8);
    }

    public static String payloadSha256(Object payload) {
        return sha256Hex(canonicalBytes(payload));
    }

    public static Map<String, Object> commandProjection(Map<String, Object> command) {
        String[] fields = {
            "schemaVersion", "commandUid", "commandType", "targetDeviceName",
            "target", "issuedAt", "expiresAt", "payloadSchemaVersion",
            "payloadSha256"
        };
        Map<String, Object> result = new LinkedHashMap<>();
        for (String field : fields) {
            result.put(field, require(command, field));
        }
        return result;
    }

    public static byte[] commandCanonicalPreimage(Map<String, Object> command) {
        return concat(COMMAND_DOMAIN, canonicalBytes(commandProjection(command)));
    }

    public static String commandCanonicalSha256(Map<String, Object> command) {
        return sha256Hex(commandCanonicalPreimage(command));
    }

    public static Map<String, Object> eventProjection(
        Map<String, Object> event,
        String trustedProductId,
        String trustedDeviceName
    ) {
        if (trustedProductId == null || trustedProductId.isEmpty()
            || trustedDeviceName == null || trustedDeviceName.isEmpty()) {
            throw new IllegalArgumentException("trusted OneNet source is required");
        }
        String[] fields = {
            "schemaVersion", "eventUid", "edgeEventSequence",
            "eventType", "deliveryClass", "target", "commandUid", "occurredAt",
            "clockQuality", "payloadSha256"
        };
        Map<String, Object> source = new LinkedHashMap<>();
        source.put("productId", trustedProductId);
        source.put("deviceName", trustedDeviceName);
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("trustedSource", source);
        for (String field : fields) {
            result.put(field, require(event, field));
        }
        return result;
    }

    public static byte[] eventCanonicalPreimage(
        Map<String, Object> event,
        String trustedProductId,
        String trustedDeviceName
    ) {
        return concat(
            EVENT_DOMAIN,
            canonicalBytes(eventProjection(event, trustedProductId, trustedDeviceName))
        );
    }

    public static String eventCanonicalSha256(
        Map<String, Object> event,
        String trustedProductId,
        String trustedDeviceName
    ) {
        return sha256Hex(
            eventCanonicalPreimage(event, trustedProductId, trustedDeviceName)
        );
    }

    private static Object require(Map<String, Object> value, String key) {
        if (!value.containsKey(key)) {
            throw new IllegalArgumentException("missing canonical field " + key);
        }
        return value.get(key);
    }

    private static void appendValue(StringBuilder out, Object value, String path) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof Boolean) {
            out.append(((Boolean) value).booleanValue() ? "true" : "false");
        } else if (value instanceof Byte || value instanceof Short
            || value instanceof Integer || value instanceof Long) {
            long number = ((Number) value).longValue();
            if (number < -SAFE_INTEGER_MAX || number > SAFE_INTEGER_MAX) {
                throw new IllegalArgumentException(path + ": unsafe JCS integer");
            }
            out.append(number);
        } else if (value instanceof Number) {
            throw new IllegalArgumentException(path + ": floating point is forbidden");
        } else if (value instanceof String) {
            appendString(out, (String) value, path);
        } else if (value instanceof List<?>) {
            out.append('[');
            List<?> values = (List<?>) value;
            for (int index = 0; index < values.size(); index++) {
                if (index != 0) {
                    out.append(',');
                }
                appendValue(out, values.get(index), path + "[" + index + "]");
            }
            out.append(']');
        } else if (value instanceof Map<?, ?>) {
            List<Map.Entry<String, Object>> entries = new ArrayList<>();
            for (Map.Entry<?, ?> entry : ((Map<?, ?>) value).entrySet()) {
                if (!(entry.getKey() instanceof String)) {
                    throw new IllegalArgumentException(path + ": object key is not text");
                }
                @SuppressWarnings("unchecked")
                Map.Entry<String, Object> typed =
                    (Map.Entry<String, Object>) (Map.Entry<?, ?>) entry;
                entries.add(typed);
            }
            entries.sort(Comparator.comparing(Map.Entry::getKey));
            out.append('{');
            for (int index = 0; index < entries.size(); index++) {
                if (index != 0) {
                    out.append(',');
                }
                Map.Entry<String, Object> entry = entries.get(index);
                appendString(out, entry.getKey(), path + ".<key>");
                out.append(':');
                appendValue(out, entry.getValue(), path + "." + entry.getKey());
            }
            out.append('}');
        } else {
            throw new IllegalArgumentException(
                path + ": unsupported canonical value " + value.getClass().getName()
            );
        }
    }

    private static void appendString(StringBuilder out, String value, String path) {
        out.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (Character.isHighSurrogate(character)) {
                if (index + 1 >= value.length()
                    || !Character.isLowSurrogate(value.charAt(index + 1))) {
                    throw new IllegalArgumentException(path + ": unpaired surrogate");
                }
                out.append(character);
                out.append(value.charAt(++index));
                continue;
            }
            if (Character.isLowSurrogate(character)) {
                throw new IllegalArgumentException(path + ": unpaired surrogate");
            }
            switch (character) {
                case '"': out.append("\\\\\\""); break;
                case '\\\\': out.append("\\\\\\\\"); break;
                case '\\b': out.append("\\\\b"); break;
                case '\\t': out.append("\\\\t"); break;
                case '\\n': out.append("\\\\n"); break;
                case '\\f': out.append("\\\\f"); break;
                case '\\r': out.append("\\\\r"); break;
                default:
                    if (character < 0x20) {
                        out.append(String.format("\\\\u%04x", (int) character));
                    } else {
                        out.append(character);
                    }
            }
        }
        out.append('"');
    }

    public static String sha256Hex(byte[] value) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(value);
            StringBuilder output = new StringBuilder(digest.length * 2);
            for (byte item : digest) {
                output.append(String.format("%02x", item & 0xff));
            }
            return output.toString();
        } catch (NoSuchAlgorithmException exception) {
            throw new IllegalStateException("SHA-256 unavailable", exception);
        }
    }

    public static String hex(byte[] value) {
        StringBuilder output = new StringBuilder(value.length * 2);
        for (byte item : value) {
            output.append(String.format("%02x", item & 0xff));
        }
        return output.toString();
    }

    private static byte[] concat(byte[] left, byte[] right) {
        byte[] result = new byte[left.length + right.length];
        System.arraycopy(left, 0, result, 0, left.length);
        System.arraycopy(right, 0, result, left.length, right.length);
        return result;
    }
}
"""


def render_java_canonical_golden_test(
    payload_vectors: list[dict[str, Any]],
    identity_vectors: list[dict[str, Any]],
) -> str:
    lines = [
        "// Generated by contracts/tools/generate_contracts.py. Do not edit.",
        "import java.util.ArrayList;",
        "import java.util.LinkedHashMap;",
        "import java.util.List;",
        "import java.util.Map;",
        "",
        "public final class EcobinCanonicalJsonGoldenTest {",
        "    private EcobinCanonicalJsonGoldenTest() {}",
        "",
        "    private static Map<String, Object> map(Object... entries) {",
        "        Map<String, Object> result = new LinkedHashMap<>();",
        "        for (int index = 0; index < entries.length; index += 2) {",
        "            result.put((String) entries[index], entries[index + 1]);",
        "        }",
        "        return result;",
        "    }",
        "",
        "    private static List<Object> list(Object... values) {",
        "        List<Object> result = new ArrayList<>();",
        "        for (Object value : values) { result.add(value); }",
        "        return result;",
        "    }",
        "",
        "    private static void require(boolean condition, String message) {",
        "        if (!condition) { throw new AssertionError(message); }",
        "    }",
        "",
        "    public static void main(String[] args) {",
    ]
    for vector in payload_vectors:
        expression = _java_json_value(vector["payload"])
        name = vector["name"]
        lines.extend(
            [
                f"        Object payload_{macro_name(name)} = {expression};",
                "        require(",
                "            EcobinCanonicalJson.hex(EcobinCanonicalJson.canonicalBytes("
                f"payload_{macro_name(name)})).equals(\"{vector['canonicalUtf8Hex']}\"),",
                f"            \"canonical bytes differ: {name}\"",
                "        );",
                "        require(",
                f"            EcobinCanonicalJson.payloadSha256(payload_{macro_name(name)})"
                f".equals(\"{vector['sha256']}\"),",
                f"            \"payload digest differs: {name}\"",
                "        );",
            ]
        )
    for vector in identity_vectors:
        name = vector["name"]
        variable = f"identity_{macro_name(name)}"
        lines.append(
            f"        Map<String, Object> {variable} = "
            f"{_java_json_value(vector['input'])};"
        )
        if vector["kind"] == "COMMAND":
            lines.extend(
                [
                    "        require(",
                    f"            EcobinCanonicalJson.hex("
                    f"EcobinCanonicalJson.commandCanonicalPreimage({variable}))"
                    f".equals(\"{vector['preimageHex']}\"),",
                    f"            \"command preimage differs: {name}\"",
                    "        );",
                    "        require(",
                    f"            EcobinCanonicalJson.commandCanonicalSha256({variable})"
                    f".equals(\"{vector['sha256']}\"),",
                    f"            \"command digest differs: {name}\"",
                    "        );",
                ]
            )
        else:
            source = vector["trustedSource"]
            lines.extend(
                [
                    "        require(",
                    f"            EcobinCanonicalJson.hex("
                    f"EcobinCanonicalJson.eventCanonicalPreimage({variable}, "
                    f"\"{source['productId']}\", \"{source['deviceName']}\"))"
                    f".equals(\"{vector['preimageHex']}\"),",
                    f"            \"event preimage differs: {name}\"",
                    "        );",
                    "        require(",
                    f"            EcobinCanonicalJson.eventCanonicalSha256({variable}, "
                    f"\"{source['productId']}\", \"{source['deviceName']}\")"
                    f".equals(\"{vector['sha256']}\"),",
                    f"            \"event digest differs: {name}\"",
                    "        );",
                ]
            )
    command_vectors = [item for item in identity_vectors if item["kind"] == "COMMAND"]
    event_vectors = [item for item in identity_vectors if item["kind"] == "EVENT"]
    lines.extend(
        [
            "        require(",
            f"            \"{command_vectors[0]['sha256']}\".equals("
            f"\"{command_vectors[1]['sha256']}\")",
            "            && "
            f"\"{command_vectors[1]['sha256']}\".equals("
            f"\"{command_vectors[2]['sha256']}\"),",
            '            "COS credential refresh changed stable command digest"',
            "        );",
            "        require(",
            f"            !\"{command_vectors[0]['sha256']}\".equals("
            f"\"{command_vectors[3]['sha256']}\")",
            "            && "
            f"!\"{command_vectors[0]['sha256']}\".equals("
            f"\"{command_vectors[4]['sha256']}\"),",
            '            "target or expiry did not change stable command digest"',
            "        );",
            "        require(",
            f"            !\"{event_vectors[0]['sha256']}\".equals("
            f"\"{event_vectors[1]['sha256']}\"),",
            '            "trusted device identity did not change event digest"',
            "        );",
            "        boolean unsafeRejected = false;",
            "        try { EcobinCanonicalJson.canonicalBytes(9007199254740992L); }",
            "        catch (IllegalArgumentException expected) { unsafeRejected = true; }",
            '        require(unsafeRejected, "unsafe integer was accepted");',
            "        boolean floatRejected = false;",
            "        try { EcobinCanonicalJson.canonicalBytes(0.5d); }",
            "        catch (IllegalArgumentException expected) { floatRejected = true; }",
            '        require(floatRejected, "floating point was accepted");',
            "        System.out.println(",
            f'            "Java OneNet canonical vectors: {len(payload_vectors)} payloads, "',
            f'            + "{len(identity_vectors)} stable identities passed"',
            "        );",
            "    }",
            "}",
            "",
        ]
    )
    return "\n".join(lines)


def _resolve_schema_reference(base_path: Path, ref: str) -> tuple[Path, Any]:
    file_part, separator, fragment = ref.partition("#")
    target_path = (
        (base_path.parent / file_part).resolve()
        if file_part
        else base_path.resolve()
    )
    target = load_json(target_path)
    if separator and fragment:
        if not fragment.startswith("/"):
            raise ValueError(f"unsupported schema fragment {ref!r}")
        for raw_token in fragment[1:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            target = target[token]
    return target_path, target


def _merge_schema_constraints(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any]:
    if left.get("x-impossible") or right.get("x-impossible"):
        return {"x-impossible": True}
    result = copy.deepcopy(dict(left))
    for key, value in right.items():
        if key in {"$schema", "$id", "title", "description"}:
            continue
        if key not in result:
            result[key] = copy.deepcopy(value)
            continue
        current = result[key]
        if key == "type":
            current_types = set(current if isinstance(current, list) else [current])
            value_types = set(value if isinstance(value, list) else [value])
            intersection = current_types & value_types
            if not intersection:
                return {"x-impossible": True}
            result[key] = (
                next(iter(intersection))
                if len(intersection) == 1
                else sorted(intersection)
            )
        elif key == "const":
            if current != value:
                return {"x-impossible": True}
        elif key == "enum":
            allowed = [item for item in current if item in value]
            if not allowed:
                return {"x-impossible": True}
            result[key] = allowed
        elif key == "properties":
            merged_properties = copy.deepcopy(current)
            for property_name, property_schema in value.items():
                if property_name in merged_properties:
                    merged_properties[property_name] = {
                        "allOf": [
                            merged_properties[property_name],
                            copy.deepcopy(property_schema),
                        ]
                    }
                else:
                    merged_properties[property_name] = copy.deepcopy(property_schema)
            result[key] = merged_properties
        elif key == "required":
            result[key] = list(dict.fromkeys([*current, *value]))
        elif key in {"minimum", "minLength", "minItems"}:
            result[key] = max(current, value)
        elif key in {"maximum", "maxLength", "maxItems"}:
            result[key] = min(current, value)
        elif key == "items":
            result[key] = {"allOf": [current, copy.deepcopy(value)]}
        elif current != value:
            result[key] = copy.deepcopy(value)
    minimum = result.get("minimum")
    maximum = result.get("maximum")
    if minimum is not None and maximum is not None and minimum > maximum:
        return {"x-impossible": True}
    return result


def _inline_schema_references(
    schema: Any,
    base_path: Path,
    stack: tuple[tuple[Path, str], ...] = (),
) -> Any:
    if isinstance(schema, list):
        return [
            _inline_schema_references(item, base_path, stack)
            for item in schema
        ]
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    if "$ref" in schema:
        ref = schema["$ref"]
        key = (base_path.resolve(), ref)
        if key in stack:
            raise ValueError(f"recursive schema reference is not supported: {ref}")
        target_path, target = _resolve_schema_reference(base_path, ref)
        inlined_target = _inline_schema_references(
            target,
            target_path,
            (*stack, key),
        )
        siblings = {
            key_name: key_value
            for key_name, key_value in schema.items()
            if key_name != "$ref"
        }
        inlined_siblings = _inline_schema_references(
            siblings,
            base_path,
            stack,
        )
        return _merge_schema_constraints(inlined_target, inlined_siblings)
    return {
        key: _inline_schema_references(value, base_path, stack)
        for key, value in schema.items()
    }


def _schema_alternatives(schema: Mapping[str, Any]) -> list[dict[str, Any]]:
    base = {
        key: copy.deepcopy(value)
        for key, value in schema.items()
        if key not in {"allOf", "oneOf"}
    }
    alternatives = [base]
    for child in schema.get("allOf", []):
        child_alternatives = _schema_alternatives(child)
        alternatives = [
            _merge_schema_constraints(current, candidate)
            for current in alternatives
            for candidate in child_alternatives
        ]
        alternatives = [
            item for item in alternatives if not item.get("x-impossible")
        ]
    if "oneOf" in schema:
        branch_alternatives = [
            candidate
            for branch in schema["oneOf"]
            for candidate in _schema_alternatives(branch)
        ]
        alternatives = [
            _merge_schema_constraints(current, candidate)
            for current in alternatives
            for candidate in branch_alternatives
        ]
        alternatives = [
            item for item in alternatives if not item.get("x-impossible")
        ]

    expanded: list[dict[str, Any]] = []
    for alternative in alternatives:
        raw_type = alternative.get("type")
        if not isinstance(raw_type, list):
            expanded.append(alternative)
            continue
        for type_name in raw_type:
            typed = copy.deepcopy(alternative)
            typed["type"] = type_name
            if "enum" in typed:
                typed["enum"] = [
                    value
                    for value in typed["enum"]
                    if _json_value_matches_schema_type(value, type_name)
                ]
                if not typed["enum"]:
                    continue
            expanded.append(typed)
    return expanded


def _json_value_matches_schema_type(value: Any, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    return False


def _alternative_type(alternative: Mapping[str, Any]) -> str:
    if "type" in alternative:
        return str(alternative["type"])
    if "const" in alternative:
        value = alternative["const"]
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, str):
            return "string"
    if "properties" in alternative:
        return "object"
    if "items" in alternative:
        return "array"
    if "enum" in alternative and alternative["enum"]:
        return _alternative_type({"const": alternative["enum"][0]})
    raise ValueError(f"cannot infer schema type from {alternative!r}")


def _schema_summary(schema: Mapping[str, Any]) -> dict[str, Any]:
    alternatives = _schema_alternatives(schema)
    nullable = any(_alternative_type(item) == "null" for item in alternatives)
    concrete = [
        item for item in alternatives if _alternative_type(item) != "null"
    ]
    if not concrete:
        return {"kind": "null", "nullable": True}
    kinds = {_alternative_type(item) for item in concrete}
    if len(kinds) != 1:
        raise ValueError(f"OneNet cannot project mixed JSON types {sorted(kinds)}")
    kind = next(iter(kinds))
    result: dict[str, Any] = {
        "kind": kind,
        "nullable": nullable,
    }
    enum_values: list[Any] = []
    enum_constrained = True
    for item in concrete:
        if "const" in item:
            values = [item["const"]]
        elif "enum" in item:
            values = item["enum"]
        else:
            enum_constrained = False
            break
        for value in values:
            if value not in enum_values:
                enum_values.append(value)
    if enum_constrained and enum_values:
        result["enum"] = enum_values

    if kind == "object":
        property_names: list[str] = []
        for item in concrete:
            for name in item.get("properties", {}):
                if name not in property_names:
                    property_names.append(name)
        properties: dict[str, Any] = {}
        for name in property_names:
            variants = []
            for item in concrete:
                property_schema = item.get("properties", {}).get(name)
                if property_schema is None:
                    variants.append({"type": "null"})
                else:
                    variants.append(property_schema)
            properties[name] = (
                variants[0] if len(variants) == 1 else {"oneOf": variants}
            )
        result["properties"] = properties
    elif kind == "array":
        item_variants = [item["items"] for item in concrete]
        result["items"] = (
            item_variants[0]
            if len(item_variants) == 1
            else {"oneOf": item_variants}
        )
        result["minItems"] = min(item.get("minItems", 0) for item in concrete)
        result["maxItems"] = max(item.get("maxItems", 32) for item in concrete)
    elif kind == "integer":
        result["minimum"] = min(
            item.get("minimum", -9007199254740991) for item in concrete
        )
        result["maximum"] = max(
            item.get("maximum", 9007199254740991) for item in concrete
        )
    elif kind == "string":
        result["maxLength"] = max(item.get("maxLength", 1024) for item in concrete)
    return result


def _upper_camel(value: str) -> str:
    return value[:1].upper() + value[1:]


def _safe_id(identifier: str, max_len: int = 32) -> str:
    return identifier[:max_len]

def _thing_name(identifier: str) -> str:
    name = identifier[:30]
    return name if name else "field"


def _one_net_primitive_data_type(
    summary: Mapping[str, Any],
    enum_display: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    kind = summary["kind"]
    if kind == "boolean":
        return {
            "type": "bool",
            "specs": {"false": "false", "true": "true"},
        }
    if kind == "integer":
        if "enum" in summary:
            values = summary["enum"]
            display = enum_display or {}
            return {
                "type": "enum",
                "specs": {
                    str(index + 1): display.get(str(value), str(value))
                    for index, value in enumerate(values)
                },
            }
        minimum = int(summary["minimum"])
        maximum = int(summary["maximum"])
        wire_type = (
            "int32"
            if -2147483648 <= minimum and maximum <= 2147483647
            else "int64"
        )
        return {
            "type": wire_type,
            "specs": {
                "min": str(minimum),
                "max": str(maximum),
                "unit": "",
                "step": "1",
            },
        }
    if kind == "string":
        if "enum" in summary:
            display = enum_display or {}
            return {
                "type": "enum",
                "specs": {
                    str(index + 1): display.get(str(value), str(value))
                    for index, value in enumerate(summary["enum"])
                },
            }
        return {
            "type": "string",
            "specs": {"length": min(int(summary["maxLength"]), 512)},
        }
    raise ValueError(f"{kind} is not a OneNet primitive")


def _presence_parameter(identifier: str) -> dict[str, Any]:
    return {
        "identifier": _safe_id(identifier, 25) + "Present",
        "name": _thing_name(f"{_safe_id(identifier, 25)}Present"),
        "dataType": {
            "type": "bool",
            "specs": {"false": "null", "true": "present"},
        },
    }


def _flatten_struct_members(
    properties: Mapping[str, Any],
    *,
    identifier_prefix: str = "",
    enum_display: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for property_name, property_schema in properties.items():
        identifier = _safe_id(
            f"{identifier_prefix}{_upper_camel(property_name)}"
            if identifier_prefix
            else property_name
        , 32)
        summary = _schema_summary(property_schema)
        if summary["nullable"]:
            members.append(_presence_parameter(identifier))
        if summary["kind"] == "null":
            continue
        if summary["kind"] == "object":
            members.extend(
                _flatten_struct_members(
                    summary["properties"],
                    identifier_prefix=identifier,
                    enum_display=enum_display,
                )
            )
            continue
        if summary["kind"] == "array":
            raise ValueError(
                f"OneNet struct member {identifier} cannot contain an array"
            )
        members.append(
            {
                "identifier": identifier,
                "name": _thing_name(identifier),
                "dataType": _one_net_primitive_data_type(summary, enum_display),
            }
        )
    return members


def _one_net_parameter(
    identifier: str,
    schema: Mapping[str, Any],
    enum_display: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    summary = _schema_summary(schema)
    parameters: list[dict[str, Any]] = []
    id_max = 25 if summary["nullable"] else 32
    safe_id = _safe_id(identifier, id_max)
    if summary["nullable"]:
        parameters.append(_presence_parameter(safe_id))
    if summary["kind"] == "null":
        return parameters
    if summary["kind"] == "object":
        members = _flatten_struct_members(summary["properties"], enum_display=enum_display)
        parameters.append(
            {
                "identifier": safe_id,
                "name": _thing_name(safe_id),
                "dataType": {"type": "struct", "specs": members},
            }
        )
        return parameters
    if summary["kind"] == "array":
        item_summary = _schema_summary(summary["items"])
        if item_summary["nullable"]:
            raise ValueError(f"OneNet array {identifier} cannot contain null items")
        if item_summary["kind"] == "object":
            item_specs: Any = _flatten_struct_members(item_summary["properties"], enum_display=enum_display)
            item_descriptor = {"type": "struct", "specs": item_specs}
        else:
            item_data_type = _one_net_primitive_data_type(item_summary, enum_display)
            item_descriptor = {
                "type": item_data_type["type"],
                **{k: v for k, v in item_data_type["specs"].items()},
            }
        array_specs: dict[str, Any] = {
            **item_descriptor,
            "length": int(summary["maxItems"]),
        }
        parameters.append(
            {
                "identifier": safe_id,
                "name": _thing_name(safe_id),
                "dataType": {
                    "type": "array",
                    "specs": array_specs,
                },
            }
        )
        return parameters
    parameters.append(
        {
            "identifier": safe_id,
            "name": _thing_name(safe_id),
            "dataType": _one_net_primitive_data_type(summary, enum_display),
        }
    )
    return parameters


def _object_contains_array(schema: Mapping[str, Any]) -> bool:
    summary = _schema_summary(schema)
    return summary["kind"] == "object" and any(
        _schema_summary(child)["kind"] == "array"
        for child in summary["properties"].values()
    )


def _project_root_parameters(
    schema: Mapping[str, Any],
    enum_display: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary = _schema_summary(schema)
    if summary["kind"] != "object":
        raise ValueError("OneNet function schema root must be an object")
    parameters: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
    seen_identifiers: set[str] = set()

    def append_parameter(
        identifier: str,
        property_schema: Mapping[str, Any],
        json_path: str,
    ) -> None:
        safe_identifier = _safe_id(identifier, 32)
        emitted = _one_net_parameter(safe_identifier, property_schema, enum_display)
        for parameter in emitted:
            wire_identifier = parameter["identifier"]
            if wire_identifier in seen_identifiers:
                raise ValueError(f"duplicate OneNet parameter {wire_identifier}")
            seen_identifiers.add(wire_identifier)
            parameters.append(parameter)
        nullable = _schema_summary(property_schema)["nullable"]
        mappings.append(
            {
                "wireIdentifier": _safe_id(safe_identifier, 25) if nullable else safe_identifier,
                "jsonPath": json_path,
                "encoding": "FLAT_TYPED",
                "nullable": nullable,
            }
        )

    for property_name, property_schema in summary["properties"].items():
        property_summary = _schema_summary(property_schema)
        if property_name == "payload":
            if property_summary["kind"] != "object":
                raise ValueError("typed payload must be an object")
            for payload_name, payload_schema in property_summary["properties"].items():
                wire_name = payload_name
                if _safe_id(wire_name, 32) in seen_identifiers:
                    wire_name = f"payload{_upper_camel(payload_name)}"
                append_parameter(
                    wire_name,
                    payload_schema,
                    f"$.payload.{payload_name}",
                )
            continue
        if property_name == "cosGrant" and (
            property_summary["kind"] == "null"
            or _object_contains_array(property_schema)
        ):
            if property_summary["nullable"]:
                presence = _presence_parameter("cosGrant")
                parameters.append(presence)
                seen_identifiers.add(presence["identifier"])
            mappings.append(
                {
                    "wireIdentifier": "cosGrant",
                    "jsonPath": "$.cosGrant",
                    "encoding": "FLATTENED_OPTIONAL_OBJECT",
                    "nullable": True,
                }
            )
            if property_summary["kind"] == "object":
                for grant_name, grant_schema in property_summary["properties"].items():
                    append_parameter(
                        f"cosGrant{_upper_camel(grant_name)}",
                        grant_schema,
                        f"$.cosGrant.{grant_name}",
                    )
            continue
        append_parameter(property_name, property_schema, f"$.{property_name}")
    return parameters, mappings


def _group_scalar_parameters_to_limit(
    parameters: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
    *,
    maximum_top_level_parameters: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Losslessly group primitive parameters when OneNet's top-level limit is exceeded."""

    if len(parameters) <= maximum_top_level_parameters:
        return parameters, mappings
    scalar_types = {"int32", "int64", "string", "bool", "enum"}
    scalar_parameters = [
        parameter
        for parameter in parameters
        if parameter["dataType"]["type"] in scalar_types
    ]
    composite_parameters = [
        parameter
        for parameter in parameters
        if parameter["dataType"]["type"] not in scalar_types
    ]
    if not scalar_parameters:
        raise ValueError("OneNet parameter limit cannot be met without nested composites")
    mapping_by_identifier = {
        mapping["wireIdentifier"]: mapping for mapping in mappings
    }
    scalar_member_mappings: list[dict[str, Any]] = []
    scalar_source_identifiers: set[str] = set()
    for parameter in scalar_parameters:
        identifier = parameter["identifier"]
        source_identifier = (
            identifier.removesuffix("Present")
            if identifier.endswith("Present")
            else identifier
        )
        source_mapping = mapping_by_identifier[source_identifier]
        scalar_source_identifiers.add(source_identifier)
        scalar_member_mappings.append(
            {
                "wireIdentifier": identifier,
                "jsonPath": source_mapping["jsonPath"],
                "presenceFlag": identifier.endswith("Present"),
            }
        )
    maximum_struct_members = 20
    parameter_chunks = [
        scalar_parameters[index : index + maximum_struct_members]
        for index in range(0, len(scalar_parameters), maximum_struct_members)
    ]
    mapping_chunks = [
        scalar_member_mappings[index : index + maximum_struct_members]
        for index in range(0, len(scalar_member_mappings), maximum_struct_members)
    ]
    grouped_identifiers = [
        (
            "scalarFields"
            if len(parameter_chunks) == 1
            else f"scalarFields{index + 1}"
        )
        for index in range(len(parameter_chunks))
    ]
    composite_identifiers = {
        parameter["identifier"] for parameter in composite_parameters
    }
    if composite_identifiers & set(grouped_identifiers):
        raise ValueError("reserved OneNet scalar group identifier is already used")
    grouped_parameters = [
        {
            "identifier": identifier,
            "name": _thing_name(identifier),
            "dataType": {
                "type": "struct",
                "specs": chunk,
            },
        }
        for identifier, chunk in zip(grouped_identifiers, parameter_chunks)
    ] + composite_parameters
    if len(grouped_parameters) > maximum_top_level_parameters:
        raise ValueError(
            "OneNet parameter limit remains exceeded after scalar grouping"
        )
    composite_source_identifiers = {
        parameter["identifier"].removesuffix("Present")
        if parameter["identifier"].endswith("Present")
        else parameter["identifier"]
        for parameter in composite_parameters
    }
    grouped_mappings = [
        {
            "wireIdentifier": identifier,
            "encoding": "GROUPED_SCALARS",
            "members": chunk,
        }
        for identifier, chunk in zip(grouped_identifiers, mapping_chunks)
    ] + [
        mapping
        for mapping in mappings
        if (
            mapping["wireIdentifier"] not in scalar_source_identifiers
            or mapping["wireIdentifier"] in composite_source_identifiers
        )
    ]
    return grouped_parameters, grouped_mappings


def build_onenet_thing_model(
    mapping: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    command_schema_path = (
        CONTRACTS_ROOT / "onenet" / "commands" / "commands.schema.json"
    )
    event_schema_path = (
        CONTRACTS_ROOT / "onenet" / "events" / "events.schema.json"
    )
    receipt_schema_path = CONTRACTS_ROOT / "onenet" / "command-receipt.schema.json"
    command_schema = load_json(command_schema_path)
    event_schema = load_json(event_schema_path)
    receipt_schema = _inline_schema_references(
        load_json(receipt_schema_path),
        receipt_schema_path,
    )
    enum_display = mapping.get("enumDisplay", {})
    receipt_output, receipt_mappings = _project_root_parameters(receipt_schema, enum_display)

    services: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    wire_functions: dict[str, Any] = {}
    for identifier, definition in mapping["services"].items():
        definition_name = definition["schemaRef"].rsplit("/", 1)[-1]
        typed_schema = command_schema["$defs"][definition_name]
        inlined = _inline_schema_references(typed_schema, command_schema_path)
        input_parameters, field_mappings = _project_root_parameters(inlined, enum_display)
        input_parameters, field_mappings = _group_scalar_parameters_to_limit(
            input_parameters,
            field_mappings,
            maximum_top_level_parameters=20,
        )
        services.append(
            {
                "identifier": identifier,
                "name": _thing_name(identifier),
                "functionType": "u",
                "callType": "sync",
                "desc": (
                    "同步回复仅证明香橙派已可靠受理；物理与业务结果走可靠事件"
                ),
                "input": input_parameters,
                "output": copy.deepcopy(receipt_output),
            }
        )
        wire_functions[identifier] = {
            "kind": "SERVICE",
            "commandType": definition["commandType"],
            "schemaRef": definition["schemaRef"],
            "inputMappings": field_mappings,
            "outputSchemaRef": "command-receipt.schema.json",
            "outputMappings": receipt_mappings,
        }

    for identifier, definition in mapping["events"].items():
        definition_name = definition["schemaRef"].rsplit("/", 1)[-1]
        typed_schema = event_schema["$defs"][definition_name]
        inlined = _inline_schema_references(typed_schema, event_schema_path)
        output_parameters, field_mappings = _project_root_parameters(inlined, enum_display)
        output_parameters, field_mappings = _group_scalar_parameters_to_limit(
            output_parameters,
            field_mappings,
            maximum_top_level_parameters=50,
        )
        events.append(
            {
                "identifier": identifier,
                "name": _thing_name(identifier),
                "functionType": "u",
                "eventType": (
                    "error"
                    if definition["eventType"] == "DEVICE_FAULT_OBSERVED"
                    else "info"
                ),
                "desc": "EcoBin 可靠边缘事件；以 JSON Schema 和语义校验为准",
                "outputData": output_parameters,
            }
        )
        wire_functions[identifier] = {
            "kind": "EVENT",
            "eventType": definition["eventType"],
            "deliveryClass": definition["deliveryClass"],
            "schemaRef": definition["schemaRef"],
            "outputMappings": field_mappings,
        }

    thing_model = {
        "properties": [],
        "services": services,
        "events": events,
    }
    wire_mapping = {
        "mappingVersion": mapping["mappingVersion"],
        "generated": True,
        "thingModelFile": "onenet-thing-model.candidate.json",
        "projection": mapping["oneNetProjection"],
        "functions": wire_functions,
    }
    return thing_model, wire_mapping


def _json_path_value(instance: Any, json_path: str) -> Any:
    if not json_path.startswith("$."):
        raise ValueError(f"unsupported generated JSON path {json_path!r}")
    current = instance
    for token in json_path[2:].split("."):
        if current is None:
            return None
        current = current[token]
    return current


def _flatten_json_members(
    value: Mapping[str, Any],
    *,
    prefix: str = "",
) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, child in value.items():
        identifier = f"{prefix}{_upper_camel(key)}" if prefix else key
        if isinstance(child, dict):
            flattened.update(_flatten_json_members(child, prefix=identifier))
        else:
            flattened[identifier] = child
    return flattened


def _one_net_placeholder(data_type: Mapping[str, Any]) -> Any:
    type_name = data_type["type"]
    specs = data_type["specs"]
    if type_name == "bool":
        return False
    if type_name == "string":
        return ""
    if type_name in {"int32", "int64"}:
        return int(specs["min"])
    if type_name == "enum":
        return int(next(iter(specs)))
    if type_name == "array":
        return []
    if type_name == "struct":
        return {
            member["identifier"]: _one_net_placeholder(member["dataType"])
            for member in specs
        }
    raise ValueError(f"unsupported placeholder type {type_name}")


def _encode_one_net_value(data_type: Mapping[str, Any], value: Any, enum_display: Mapping[str, str] | None = None) -> Any:
    if value is None:
        return _one_net_placeholder(data_type)
    type_name = data_type["type"]
    specs = data_type["specs"]
    if type_name == "enum":
        expected = str(value)
        for wire_value, symbol in specs.items():
            if symbol == expected:
                return int(wire_value)
        display = enum_display or {}
        abbreviated = display.get(expected)
        if abbreviated is not None:
            for wire_value, symbol in specs.items():
                if symbol == abbreviated:
                    return int(wire_value)
        raise ValueError(f"enum symbol {value!r} is absent from OneNet model")
    if type_name == "bool":
        return bool(value)
    if type_name in {"int32", "int64"}:
        return int(value)
    if type_name == "string":
        return str(value)
    if type_name == "struct":
        if not isinstance(value, dict):
            raise ValueError("OneNet struct source must be an object")
        flattened = _flatten_json_members(value)
        encoded: dict[str, Any] = {}
        for member in specs:
            identifier = member["identifier"]
            if identifier.endswith("Present"):
                source_name = identifier.removesuffix("Present")
                encoded[identifier] = flattened.get(source_name) is not None
            else:
                encoded[identifier] = _encode_one_net_value(
                    member["dataType"],
                    flattened.get(identifier),
                    enum_display,
                )
        return encoded
    if type_name == "array":
        if not isinstance(value, list):
            raise ValueError("OneNet array source must be a list")
        item_type = specs["type"]
        if item_type == "struct":
            item_descriptor = {"type": "struct", "specs": specs["specs"]}
        else:
            item_descriptor = {
                "type": item_type,
                "specs": {k: v for k, v in specs.items() if k not in ("length", "type")},
            }
        return [
            _encode_one_net_value(item_descriptor, item, enum_display)
            for item in value
        ]
    raise ValueError(f"unsupported OneNet data type {type_name}")


def _encode_function_parameters(
    descriptors: list[Mapping[str, Any]],
    field_mappings: list[Mapping[str, Any]],
    instance: Mapping[str, Any],
    enum_display: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    mapping_by_identifier = {
        item["wireIdentifier"]: item for item in field_mappings
    }
    encoded: dict[str, Any] = {}
    for descriptor in descriptors:
        identifier = descriptor["identifier"]
        direct_mapping = mapping_by_identifier.get(identifier)
        if direct_mapping and direct_mapping.get("encoding") == "GROUPED_SCALARS":
            member_mappings = {
                member["wireIdentifier"]: member
                for member in direct_mapping["members"]
            }
            encoded_members: dict[str, Any] = {}
            for member_descriptor in descriptor["dataType"]["specs"]:
                member_identifier = member_descriptor["identifier"]
                member_mapping = member_mappings[member_identifier]
                source_value = _json_path_value(
                    instance,
                    member_mapping["jsonPath"],
                )
                if member_mapping["presenceFlag"]:
                    encoded_members[member_identifier] = source_value is not None
                else:
                    encoded_members[member_identifier] = _encode_one_net_value(
                        member_descriptor["dataType"],
                        source_value,
                        enum_display,
                    )
            encoded[identifier] = encoded_members
            continue
        source_identifier = (
            identifier.removesuffix("Present")
            if identifier.endswith("Present")
            else identifier
        )
        field_mapping = mapping_by_identifier[source_identifier]
        source_value = _json_path_value(instance, field_mapping["jsonPath"])
        if identifier.endswith("Present"):
            encoded[identifier] = source_value is not None
        else:
            encoded[identifier] = _encode_one_net_value(
                descriptor["dataType"],
                source_value,
                enum_display,
            )
    return encoded


def build_onenet_wire_examples(
    examples: Mapping[str, Any],
    thing_model: Mapping[str, Any],
    wire_mapping: Mapping[str, Any],
    enum_display: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    services = {
        service["identifier"]: service for service in thing_model["services"]
    }
    events = {
        event["identifier"]: event for event in thing_model["events"]
    }
    result: dict[str, dict[str, Any]] = {}
    event_message_id = 100
    for filename, (instance, _schema_ref) in examples.items():
        if "commandType" in instance:
            identifier = next(
                function_identifier
                for function_identifier, definition in wire_mapping["functions"].items()
                if definition.get("commandType") == instance["commandType"]
            )
            service = services[identifier]
            definition = wire_mapping["functions"][identifier]
            params = _encode_function_parameters(
                service["input"],
                definition["inputMappings"],
                instance,
                enum_display,
            )
            receipt = {
                "schemaVersion": 2,
                "commandUid": instance["commandUid"],
                "receiptState": "ACCEPTED",
                "errorCode": None,
                "edgeBootId": 9001,
            }
            reply_data = _encode_function_parameters(
                service["output"],
                definition["outputMappings"],
                receipt,
                enum_display,
            )
            result[filename.replace(".command.json", ".service-wire.json")] = {
                "kind": "SYNC_SERVICE",
                "identifier": identifier,
                "callServiceApiBodyTemplate": {
                    "product_id": "<PRODUCT_ID>",
                    "device_name": "<HARDWARE_SN>",
                    "identifier": identifier,
                    "params": params,
                },
                "deviceInvokeTopicTemplate": (
                    f"$sys/{{pid}}/{{device-name}}/thing/service/{identifier}/invoke"
                ),
                "deviceReplyTopicTemplate": (
                    f"$sys/{{pid}}/{{device-name}}/thing/service/{identifier}/"
                    "invoke_reply"
                ),
                "deviceAcceptedReplyTemplate": {
                    "id": "<COPY_REQUEST_ID>",
                    "code": 200,
                    "msg": "accepted",
                    "data": reply_data,
                },
            }
        elif "eventType" in instance:
            identifier = next(
                function_identifier
                for function_identifier, definition in wire_mapping["functions"].items()
                if definition.get("eventType") == instance["eventType"]
            )
            event = events[identifier]
            definition = wire_mapping["functions"][identifier]
            value = _encode_function_parameters(
                event["outputData"],
                definition["outputMappings"],
                instance,
                enum_display,
            )
            result[filename.replace(".event.json", ".event-wire.json")] = {
                "kind": "EVENT",
                "identifier": identifier,
                "topicTemplate": "$sys/{pid}/{device-name}/thing/event/post",
                "replyTopicTemplate": (
                    "$sys/{pid}/{device-name}/thing/event/post/reply"
                ),
                "oneJsonPayload": {
                    "id": str(event_message_id),
                    "version": "1.0",
                    "params": {
                        identifier: {
                            "value": value,
                        }
                    },
                },
            }
            event_message_id += 1
    return result


def _strip_onenet_array_specs(model: dict[str, Any]) -> dict[str, Any]:
    """Remove non-length/type keys from non-struct array specs for OneNet console."""
    for collection in ("services", "events"):
        for function in model.get(collection, []):
            for direction in ("input", "outputData"):
                for param in function.get(direction, []):
                    _strip_param(param)
    return model


_ARRAY_ALLOWED_TYPES = {"int32", "int64", "float", "double", "string", "struct", "date"}

def _strip_param(param: dict[str, Any]) -> None:
    dt = param.get("dataType")
    if not dt:
        return
    if dt["type"] == "array":
        specs = dt["specs"]
        if specs.get("type") == "struct":
            for member in specs["specs"]:
                _strip_param(member)
        elif specs.get("type") is not None:
            item_type = specs["type"]
            # OneNet arrays only allow: int32 int64 float double string struct date
            if item_type not in _ARRAY_ALLOWED_TYPES:
                item_type = "int32"
            # Build item specs: strings need {length: N}, others need {}
            if item_type == "string":
                item_specs = {"length": 512}
            elif item_type in ("int32", "int64"):
                item_specs = {"min": "0", "max": "2147483647", "step": "1", "unit": ""}
            elif item_type in ("float", "double"):
                item_specs = {"min": "0", "max": "3.4e38", "step": "0.1", "unit": ""}
            else:
                item_specs = {}
            # Replace with clean {length, type, specs}
            dt["specs"] = {
                "length": specs["length"],
                "type": item_type,
                "specs": item_specs,
            }
    elif dt["type"] == "struct":
        for member in dt.get("specs", []):
            _strip_param(member)


def render_catalog(
    registry: Mapping[str, Any],
    specs: Mapping[str, Any],
    mapping: Mapping[str, Any],
    registry_digest: str,
) -> str:
    def target_label(definition: Mapping[str, Any]) -> str:
        if "targetTypes" in definition:
            return " / ".join(definition["targetTypes"])
        return str(definition["targetType"])

    lines = [
        "# EcoBin F-10 机器契约目录（生成）",
        "",
        "> 本文件由 `contracts/tools/generate_contracts.py` 生成，请勿直接编辑。",
        "",
        f"- UART Registry：`{registry['registryVersion']}`",
        f"- UART Registry SHA-256：`{registry_digest}`",
        f"- UART 状态：`{registry['status']}`",
        f"- UART 物理链路：`{registry['physicalLink']['baudRate']} baud / "
        f"{registry['physicalLink']['dataBits']}{registry['physicalLink']['parity'][0]}"
        f"{registry['physicalLink']['stopBits']} / no flow control`",
        f"- OneNet Mapping：`{mapping['mappingVersion']}` / `{mapping['status']}`",
        "",
        "## UART 消息",
        "",
        "| ID | 消息 | 方向 | ACK | payload 字节 |",
        "|---:|---|---|---|---:|",
    ]
    for message in registry["messages"]:
        spec = specs[message["name"]]
        size = (
            str(spec["minimumPayloadLength"])
            if spec["minimumPayloadLength"] == spec["maximumPayloadLength"]
            else f"{spec['minimumPayloadLength']}..{spec['maximumPayloadLength']}"
        )
        lines.append(
            f"| `0x{message['id']:02X}` | `{message['name']}` | "
            f"`{message['direction']}` | "
            f"{'是' if message['ackRequired'] else '否'} | `{size}` |"
        )
    lines.extend(
        [
            "",
            "## OneNet 下行",
            "",
            "| OneNet identifier | commandType | 目标 | 类型 |",
            "|---|---|---|---|",
        ]
    )
    for identifier, service in mapping["services"].items():
        lines.append(
            f"| `{identifier}` | `{service['commandType']}` | "
            f"`{target_label(service)}` | `{service['deliveryKind']}` |"
        )
    lines.extend(
        [
            "",
            "## OneNet 上行",
            "",
            "| OneNet identifier | eventType | deliveryClass | 目标 |",
            "|---|---|---|---|",
        ]
    )
    for identifier, event in mapping["events"].items():
        lines.append(
            f"| `{identifier}` | `{event['eventType']}` | "
            f"`{event['deliveryClass']}` | `{target_label(event)}` |"
        )
    lines.extend(
        [
            "",
            "## MCU 人工确认清单",
            "",
            "- [ ] 消息号、方向、逐字段顺序和固定/最大 payload 长度。",
            "- [ ] capability bit 与当前 MCU 硬件能力一致。",
            "- [ ] `CLEAN_FINAL_WEIGHT_READY` 作为人工完成请求后的独立称重结果可实现。",
            "- [ ] 清运只存在电磁阀通断；没有门磁、自动关门或清运 `SAFE_CLOSE`。",
            "- [ ] 投递门事件只报告命令输出，物理门位始终 `NOT_OBSERVABLE`。",
            "- [ ] `UNSTABLE` 和带数据的故障测量保留 `reportedWeightGrams` 与质量标志。",
            "- [ ] 配置 staging/COMMIT 在 RAM 中原子切换；重启后由香橙派重新同步。",
            "- [ ] 启动对账可显式确认无旧作业或续接原清运，且不重启清运窗口。",
            "- [ ] 未实现时不得宣称持久命令去重、持久事件队列或门控 HIL 能力。",
            "- [ ] C 工具链编译并通过同一份 `ecobin_uart_golden_test.c`。",
            "- [ ] 真机对 CRC、ACK 丢失、重发、重启和投递门独立超时关门留存证据。",
            "",
        ]
    )
    return "\n".join(lines)


def build_outputs(*, include_hardware_mcu: bool = False) -> dict[Path, str]:
    schema_validator = JsonSchemaSubsetValidator()
    registry = load_uart_registry()
    validate_uart_registry(registry, schema_validator)
    specs = uart_message_specs(registry)
    registry_digest = source_sha256(registry)
    vectors = build_uart_vectors(registry)
    stream_traces = build_uart_stream_traces(registry, vectors)
    digest_vectors = build_uart_digest_vectors(registry, specs)
    mapping_path = CONTRACTS_ROOT / "onenet" / "thing-model.mapping.yaml"
    mapping = load_json(mapping_path)
    examples = build_onenet_examples()
    canonicalization_vectors = build_canonicalization_vectors(examples)
    identity_digest_vectors = build_onenet_identity_digest_vectors(examples)
    thing_model, onenet_wire_mapping = build_onenet_thing_model(mapping)
    onenet_wire_examples = build_onenet_wire_examples(
        examples,
        thing_model,
        onenet_wire_mapping,
        mapping.get("enumDisplay", {}),
    )
    event_descriptors = {
        event["identifier"]: {
            "eventType": onenet_wire_mapping["functions"][event["identifier"]][
                "eventType"
            ],
            "outputData": event["outputData"],
            "outputMappings": onenet_wire_mapping["functions"][
                event["identifier"]
            ]["outputMappings"],
        }
        for event in thing_model["events"]
    }

    expanded_layout = {
        "generatedFrom": "contracts/uart/uart-registry.yaml",
        "registryVersion": registry["registryVersion"],
        "registrySha256": registry_digest,
        "physicalLink": registry["physicalLink"],
        "protocol": registry["protocol"],
        "capabilities": registry["capabilities"],
        "enums": registry["enums"],
        "messages": specs,
    }
    generated_python = render_python_module(registry, specs, registry_digest)
    generated_c_header = render_c_header(registry, specs, registry_digest)
    generated_c_golden_test = render_c_golden_test(
        registry,
        vectors,
        stream_traces,
        digest_vectors,
    )
    outputs: dict[Path, str] = {
        GENERATED_UART_ROOT / "uart-layout.json": json_text(expanded_layout),
        GENERATED_UART_ROOT / "python" / "ecobin_uart_protocol.py": generated_python,
        HARDWARE_UART_PROTOCOL: generated_python,
        HARDWARE_ONENET_PROJECTION_MODEL: json_text(
            {
                "generated": True,
                "mappingVersion": mapping["mappingVersion"],
                "enumDisplay": mapping.get("enumDisplay", {}),
                "events": event_descriptors,
            }
        ),
        GENERATED_UART_ROOT / "c" / "ecobin_uart_protocol.h": generated_c_header,
        GENERATED_UART_ROOT / "c" / "ecobin_uart_golden_test.c": generated_c_golden_test,
        GENERATED_UART_ROOT / "java" / "EcobinUartProtocol.java": render_java_protocol(
            registry, specs, registry_digest
        ),
        GENERATED_UART_ROOT / "java" / "EcobinUartGoldenTest.java": render_java_golden_test(
            registry,
            vectors,
            stream_traces,
            digest_vectors,
        ),
        GENERATED_EXAMPLES_ROOT / "uart" / "golden-vectors.json": json_text(
            {
                "registryVersion": registry["registryVersion"],
                "registrySha256": registry_digest,
                "crcCheck": {
                    "inputAscii": "123456789",
                    "expectedHex": "29b1",
                },
                "vectors": vectors,
            }
        ),
        GENERATED_EXAMPLES_ROOT / "uart" / "stream-traces.json": json_text(
            {
                "registryVersion": registry["registryVersion"],
                "registrySha256": registry_digest,
                "parserAlgorithm": registry["streamParser"]["algorithm"],
                "traces": stream_traces,
            }
        ),
        GENERATED_EXAMPLES_ROOT / "uart" / "digest-vectors.json": json_text(
            {
                "registryVersion": registry["registryVersion"],
                "registrySha256": registry_digest,
                "algorithm": "SHA-256",
                "vectors": digest_vectors,
            }
        ),
        GENERATED_DOC_ROOT / "contract-catalog.md": render_catalog(
            registry, specs, mapping, registry_digest
        ),
        GENERATED_ONENET_ROOT / "java" / "EcobinCanonicalJson.java":
            render_java_canonical_json(),
        GENERATED_ONENET_ROOT / "java" / "EcobinCanonicalJsonGoldenTest.java":
            render_java_canonical_golden_test(
                canonicalization_vectors,
                identity_digest_vectors,
        ),
        GENERATED_ONENET_ROOT / "onenet-thing-model.candidate.json":
            onenet_import_json_text(
                _strip_onenet_array_specs(copy.deepcopy(thing_model))
            ),
        GENERATED_ONENET_ROOT / "onenet-wire-mapping.json":
            json_text(onenet_wire_mapping),
    }
    if include_hardware_mcu:
        outputs[HARDWARE_MCU_UART_HEADER] = generated_c_header
        outputs[HARDWARE_MCU_UART_GOLDEN_TEST] = generated_c_golden_test

    manifest_entries = []
    for filename, (instance, schema_ref) in examples.items():
        target = GENERATED_EXAMPLES_ROOT / "onenet" / filename
        outputs[target] = json_text(instance)
        manifest_entries.append({"file": filename, "schema": schema_ref})
    outputs[GENERATED_EXAMPLES_ROOT / "onenet" / "manifest.json"] = json_text(
        {
            "generated": True,
            "examples": manifest_entries,
        }
    )
    outputs[
        GENERATED_EXAMPLES_ROOT / "onenet" / "canonicalization-vectors.json"
    ] = json_text(
        {
            "profile": "RFC8785-JCS integer-only EcoBin payload profile",
            "vectors": canonicalization_vectors,
        }
    )
    outputs[
        GENERATED_EXAMPLES_ROOT / "onenet" / "stable-identity-vectors.json"
    ] = json_text(
        {
            "profile": "EcoBin OneNet stable command/event identity v2",
            "commandDomainUtf8WithNullHex":
                "ECOBIN:ONENET:COMMAND:v2".encode("utf-8").hex() + "00",
            "eventDomainUtf8WithNullHex":
                "ECOBIN:ONENET:EVENT:v2".encode("utf-8").hex() + "00",
            "vectors": identity_digest_vectors,
        }
    )
    for filename, wire_example in onenet_wire_examples.items():
        outputs[
            GENERATED_EXAMPLES_ROOT / "onenet-wire" / filename
        ] = json_text(wire_example)
    outputs[
        GENERATED_EXAMPLES_ROOT / "onenet-wire" / "manifest.json"
    ] = json_text(
        {
            "generated": True,
            "thingModel": (
                "../../onenet/generated/onenet-thing-model.candidate.json"
            ),
            "examples": sorted(onenet_wire_examples),
        }
    )

    source_paths = sorted(
        [
            CONTRACTS_ROOT / "uart" / "uart-registry.yaml",
            CONTRACTS_ROOT / "uart" / "uart-registry.schema.json",
            *(
                path
                for path in (CONTRACTS_ROOT / "onenet").rglob("*")
                if path.is_file() and (
                    path.name.endswith(".schema.json")
                    or path.name == "thing-model.mapping.yaml"
                )
            ),
        ]
    )
    source_manifest = {
        "uartRegistryVersion": registry["registryVersion"],
        "uartRegistrySha256": registry_digest,
        "onenetMappingVersion": mapping["mappingVersion"],
        "sources": {
            path.relative_to(CONTRACTS_ROOT).as_posix(): source_sha256(load_json(path))
            for path in source_paths
        },
    }
    outputs[GENERATED_DOC_ROOT / "source-manifest.json"] = json_text(source_manifest)
    return outputs


def apply_outputs(outputs: Mapping[Path, str], check: bool) -> list[Path]:
    drift: list[Path] = []
    for path, content in outputs.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing == content:
            continue
        drift.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    return drift


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail when committed generated artifacts differ from sources",
    )
    parser.add_argument(
        "--include-hardware-mcu",
        action="store_true",
        help=(
            "also write/check the optional uart-v1 C artifacts under hardware_mcu; "
            "the fixed-frame Edge adapter does not require this"
        ),
    )
    args = parser.parse_args()
    outputs = build_outputs(include_hardware_mcu=args.include_hardware_mcu)
    drift = apply_outputs(outputs, args.check)
    if args.check and drift:
        print("Generated contract drift:")
        for path in drift:
            print(f"  {path.relative_to(CONTRACTS_ROOT.parent)}")
        return 1
    action = "checked" if args.check else "generated"
    print(f"Contract artifacts {action}: {len(outputs)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
