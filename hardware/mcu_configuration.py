"""Immutable native-v2 MCU subset, validated before any command is prepared.

Not a cloud/OneNet decoder, configuration applier, allocator, or UART owner.
The caller must authenticate/validate the full cloud configuration and its
content digest, persist each real command identity, obtain configuration-specific
authorization, and fence each single write. Legacy v1 hashes are never upgraded
by guessing defaults. No existing runtime imports this candidate module.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Mapping, Sequence

import uart2_protocol as uart


def _sha256(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("configuration requires an explicit SHA-256 hex digest")
    try:
        raw = bytes.fromhex(value)
    except ValueError as error:
        raise ValueError("invalid configuration SHA-256") from error
    if len(raw) != 32 or not any(raw):
        raise ValueError("configuration SHA-256 cannot be empty")
    return raw.hex()


def _semantic_fields(name: str) -> tuple[dict, ...]:
    fields = uart.MESSAGE_SPECS[name]["fields"]
    last_identity = next(index for index, field in enumerate(fields) if field["name"] == "partCount")
    return tuple(fields[last_identity + 1:])


def _exact_semantics(name: str, values: Mapping) -> dict:
    expected = {field["name"] for field in _semantic_fields(name)}
    if not isinstance(values, Mapping) or set(values) != expected:
        raise ValueError("native MCU subset fields differ; no implicit legacy projection")
    return dict(values)


@dataclass(frozen=True, init=False)
class NativeMcuConfiguration:
    """Bounded immutable candidate. Constructing/encoding never sends or applies."""

    digest_preimage: bytes
    mcu_payload_sha256: str
    _parts: tuple[tuple[str, bytes], ...]

    def __init__(self, *, config_version: int, content_sha256: str, device: Mapping,
                 ports: Sequence[Mapping], expected_sha256: str):
        content_sha256, expected_sha256 = _sha256(content_sha256), _sha256(expected_sha256)
        maximum = next(field["maximum"] for field in _semantic_fields("CONFIG_BEGIN") if field["name"] == "expectedPortCount")
        if not isinstance(ports, (list, tuple)) or not 1 <= len(ports) <= maximum:
            raise ValueError("native configuration requires 1..6 ordered ports")
        count = len(ports) + 3
        # Local shape-check prototypes ONLY. Never exported for transmission;
        # encode_part requires all real application/command/boot identities.
        common = {"mcuCommandUid": "00000000-0000-0000-0000-000000000001",
            "commandDigestSha256": "00" * 32, "targetMcuBootId": 1, "commandSequence": 1,
            "applicationUid": "00000000-0000-0000-0000-000000000002", "configVersion": config_version,
            "contentSha256": content_sha256, "mcuPayloadSha256": expected_sha256, "partCount": count}
        segments = [("CONFIG_BEGIN", {"expectedPortCount": len(ports)}),
            ("CONFIG_DEVICE_BLOCK", _exact_semantics("CONFIG_DEVICE_BLOCK", device))]
        for port_no, source in enumerate(ports, 1):
            port = _exact_semantics("CONFIG_PORT_BLOCK", source)
            if port["portNo"] != port_no:
                raise ValueError("ports must be ordered and contiguous from 1")
            segments.append(("CONFIG_PORT_BLOCK", port))
        segments.append(("CONFIG_COMMIT", {}))
        encoded = []
        preimage = bytearray(bytes.fromhex(uart.REGISTRY["digestProfiles"]["mcuPayloadSha256"]["domainHex"]))
        for index, (name, semantics) in enumerate(segments, 1):
            values = common | {"partIndex": index} | semantics
            values["commandDigestSha256"] = uart.compute_command_digest(name, values)
            payload = uart.encode_payload(name, values)
            if index == 1:
                # encode_payload has strictly checked type/range of version.
                preimage.extend(config_version.to_bytes(8, "big"))
                preimage.extend(bytes.fromhex(content_sha256))
                preimage.append(len(ports))
            elif name != "CONFIG_COMMIT":
                preimage.extend(payload[_semantic_fields(name)[0]["offset"]:])
            encoded.append((name, payload))
        digest = hashlib.sha256(preimage).hexdigest()
        if digest != expected_sha256:
            raise ValueError("native mcuPayloadSha256 mismatch; v1 fallback is forbidden")
        object.__setattr__(self, "digest_preimage", bytes(preimage))
        object.__setattr__(self, "mcu_payload_sha256", digest)
        object.__setattr__(self, "_parts", tuple(encoded))

    @property
    def part_count(self) -> int:
        return len(self._parts)

    @classmethod
    def from_parts(cls, parts: Sequence[tuple[str, bytes]]) -> NativeMcuConfiguration:
        """Reconstruct original complete bytes; not proof of application/acceptance.

        No latest configuration, invented identity or legacy field defaults.
        The caller independently verifies custody and command witnesses.
        """
        if not isinstance(parts, (tuple, list)) or not 4 <= len(parts) <= 9:
            raise ValueError("native configuration requires one complete original set")
        if any(not isinstance(part, (tuple, list)) or len(part) != 2 for part in parts):
            raise ValueError("native configuration requires named original parts")
        expected_names = ["CONFIG_BEGIN", "CONFIG_DEVICE_BLOCK"] + ["CONFIG_PORT_BLOCK"] * (len(parts) - 3) + ["CONFIG_COMMIT"]
        if [name for name, _ in parts] != expected_names:
            raise ValueError("native configuration part order differs")
        values = []
        for name, raw in parts:
            if type(raw) is not bytes:
                raise ValueError("native configuration requires immutable part bytes")
            values.append(uart.decode_payload(name, raw))
        first = values[0]
        common = ("applicationUid", "targetMcuBootId", "configVersion", "contentSha256", "mcuPayloadSha256", "partCount")
        if first["partCount"] != len(parts) or first["expectedPortCount"] != len(parts) - 3:
            raise ValueError("native configuration part count differs")
        if any(any(value[key] != first[key] for key in common) for value in values):
            raise ValueError("native configuration parts have mixed identities")
        if len({value["mcuCommandUid"] for value in values}) != len(values) or any(
                left["commandSequence"] >= right["commandSequence"] for left, right in zip(values, values[1:])):
            raise ValueError("native configuration commands are not unique and ordered")
        device = {field["name"]: values[1][field["name"]] for field in _semantic_fields("CONFIG_DEVICE_BLOCK")}
        ports = []
        for (name, _), value in zip(parts[2:-1], values[2:-1]):
            if name != "CONFIG_PORT_BLOCK":
                raise ValueError("native configuration requires original port blocks")
            ports.append({field["name"]: value[field["name"]] for field in _semantic_fields(name)})
        candidate = cls(config_version=first["configVersion"], content_sha256=first["contentSha256"],
            expected_sha256=first["mcuPayloadSha256"], device=device, ports=ports)
        for index, ((name, raw), value) in enumerate(zip(parts, values), 1):
            rebuilt = candidate.encode_part(index, application_uid=first["applicationUid"],
                mcu_command_uid=value["mcuCommandUid"], target_mcu_boot_id=first["targetMcuBootId"],
                command_sequence=value["commandSequence"])
            if rebuilt != (name, raw):
                raise ValueError("native configuration original bytes differ from complete set")
        return candidate

    def encode_part(self, part_index: int, *, application_uid: str, mcu_command_uid: str,
                    target_mcu_boot_id: int, command_sequence: int) -> tuple[str, bytes]:
        """Encode one real-identity payload; no ID allocation, write or retry.

        Identical arguments yield identical bytes. This is NOT permission to
        resend: a lost reply must query the original durable command instead.
        """
        if type(part_index) is not int or not 1 <= part_index <= self.part_count:
            raise ValueError("configuration part index out of range")
        name, prototype = self._parts[part_index - 1]
        values = uart.decode_payload(name, prototype) | {"applicationUid": application_uid,
            "mcuCommandUid": mcu_command_uid, "targetMcuBootId": target_mcu_boot_id,
            "commandSequence": command_sequence}
        values["commandDigestSha256"] = uart.compute_command_digest(name, values)
        return name, uart.encode_payload(name, values)
