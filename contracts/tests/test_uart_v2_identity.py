"""Unpublished UART 2 identity contract; never imports the device runtime."""
from __future__ import annotations

import hashlib
import copy
import json
import sys
import types
import unittest
from unittest.mock import patch
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from contractlib import (  # noqa: E402
    CONTRACTS_ROOT, ContractError, decode_uart_payload, encode_uart_payload,
    compute_uart_command_digest, load_uart_registry, uart_message_specs,
    validate_uart_registry,
)
from generate_contracts import (  # noqa: E402
    HARDWARE_UART_PROTOCOL,
    build_outputs,
)


class RuntimeIsolationTests(unittest.TestCase):
    def test_candidate_cannot_overwrite_running_python_protocol(self) -> None:
        self.assertNotIn(HARDWARE_UART_PROTOCOL, set(build_outputs()))

    def test_candidate_cannot_be_exported_into_current_mcu_tree(self) -> None:
        with self.assertRaisesRegex(ContractError, "candidate"):
            build_outputs(include_hardware_mcu=True)

    def test_existing_runtime_artifacts_are_frozen_not_regenerated(self) -> None:
        manifest = json.loads(
            (CONTRACTS_ROOT / "uart" / "frozen-v1-runtime.json").read_text("utf-8")
        )
        self.assertEqual("1.0.0-rc.3", manifest["registryVersion"])
        self.assertEqual(3, len(manifest["artifacts"]))
        for relative, digest in manifest["artifacts"].items():
            with self.subTest(path=relative):
                content = (CONTRACTS_ROOT.parent / relative).read_text("utf-8")
                self.assertEqual(digest, hashlib.sha256(content.encode()).hexdigest())

    def test_generation_detects_an_accidental_runtime_edit(self) -> None:
        original = Path.read_text
        def changed(path: Path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return value + "# drift\n" if path == HARDWARE_UART_PROTOCOL else value
        with patch.object(Path, "read_text", changed):
            with self.assertRaisesRegex(ContractError, "frozen UART v1 artifact drift"):
                build_outputs()


class CandidateFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_uart_registry()
        cls.specs = uart_message_specs(cls.registry)
        cls.codec = types.ModuleType("candidate_uart2")
        source = build_outputs()[
            CONTRACTS_ROOT / "uart/generated/python/ecobin_uart_protocol.py"
        ]
        exec(compile(source, "candidate_uart2", "exec"), cls.codec.__dict__)

class BootWireTests(CandidateFixture):
    def test_candidate_major_cannot_be_mistaken_for_v1(self) -> None:
        self.assertEqual(2, self.registry["protocol"]["major"])
        # rc.23 is wired into the local MCU/Pi runtime, but it is still an
        # unreleased v2 candidate.  The older NOT_RUNNABLE suffix described
        # pre-integration revisions and must not be used as the release fence.
        self.assertTrue(self.registry["implementationStage"].endswith("_NOT_RELEASED"))

    def test_bootstrap_layouts_are_fixed_and_do_not_use_ack_retry(self) -> None:
        expected = {
            "BOOT_PROBE": (7, 8), "BOOT_PROBE_REPLY": (8, 16),
            "BIND_BOOT": (9, 16), "BIND_BOOT_REPLY": (10, 25),
        }
        for name, (message_id, size) in expected.items():
            with self.subTest(message=name):
                spec = self.specs[name]
                self.assertEqual((message_id, size, size, False), (
                    spec["id"], spec["minimumPayloadLength"],
                    spec["maximumPayloadLength"], spec["ackRequired"],
                ))
        self.assertEqual(1, self.registry["sessionPolicy"]["bootstrapMaximumSends"])

    def test_zero_boot_is_reportable_only_during_bootstrap(self) -> None:
        values = {"probeId": 41, "mcuBootId": 0}
        expected = bytes.fromhex("00000000000000290000000000000000")
        self.assertEqual(expected, encode_uart_payload(self.registry, "BOOT_PROBE_REPLY", values))
        self.assertEqual(expected, self.codec.encode_payload("BOOT_PROBE_REPLY", values))
        self.assertEqual(values, self.codec.decode_payload("BOOT_PROBE_REPLY", expected))
        for spec in self.specs.values():
            if spec["criticalEvent"]:
                boot_field = next(f for f in spec["fields"] if f["name"] == "mcuBootId")
                self.assertEqual(1, boot_field["minimum"])

    def test_binding_reply_matches_the_requested_boot(self) -> None:
        values = {"probeId": 41, "proposedMcuBootId": 42, "mcuBootId": 42, "status": "BOUND"}
        payload = encode_uart_payload(self.registry, "BIND_BOOT_REPLY", values)
        self.assertEqual(values, decode_uart_payload(self.registry, "BIND_BOOT_REPLY", payload))
        self.assertEqual(payload, self.codec.encode_payload("BIND_BOOT_REPLY", values))
        for changes in (
            {"mcuBootId": 0}, {"mcuBootId": 43},
            {"status": "PROBE_MISMATCH"}, {"status": "ALREADY_BOUND", "mcuBootId": 0},
        ):
            bad = values | changes
            with self.subTest(changes=changes):
                with self.assertRaises(ContractError):
                    encode_uart_payload(self.registry, "BIND_BOOT_REPLY", bad)
                with self.assertRaises(self.codec.ProtocolError):
                    self.codec.encode_payload("BIND_BOOT_REPLY", bad)

    def test_crc_valid_zero_probe_is_rejected_before_dispatch(self) -> None:
        frame = self.codec.encode_frame("BOOT_PROBE", 1, bytes(8))
        with self.assertRaises(self.codec.ProtocolError):
            self.codec.decode_frame(frame, sender_role="EDGE")

    def test_numeric_bind_status_has_the_same_semantics_as_symbolic_status(self) -> None:
        for status, valid_boot in ((1, 42), (2, 0), (3, 43)):
            values = {"probeId": 41, "proposedMcuBootId": 42, "mcuBootId": valid_boot, "status": status}
            self.assertEqual(
                encode_uart_payload(self.registry, "BIND_BOOT_REPLY", values),
                self.codec.encode_payload("BIND_BOOT_REPLY", values),
            )
            bad = values | {"mcuBootId": 0 if status in (1, 3) else 42}
            with self.subTest(status=status):
                with self.assertRaises(ContractError):
                    encode_uart_payload(self.registry, "BIND_BOOT_REPLY", bad)
                with self.assertRaises(self.codec.ProtocolError):
                    self.codec.encode_payload("BIND_BOOT_REPLY", bad)

    def test_bootstrap_bounds_truncation_and_extra_bytes(self) -> None:
        for value in (0, -1, True, 9007199254740992):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    encode_uart_payload(self.registry, "BOOT_PROBE", {"probeId": value})
                with self.assertRaises(self.codec.ProtocolError):
                    self.codec.encode_payload("BOOT_PROBE", {"probeId": value})
        payload = self.codec.encode_payload("BOOT_PROBE", {"probeId": 9007199254740991})
        for bad in (payload[:-1], payload + b"\0"):
            with self.assertRaises(self.codec.ProtocolError):
                self.codec.decode_frame(self.codec.encode_frame("BOOT_PROBE", 1, bad), sender_role="EDGE")

    def test_read_only_firmware_identity_is_bound_to_the_queried_boot(self) -> None:
        request = {"queryId": 51, "targetMcuBootId": 42}
        self.assertEqual(
            16,
            len(self.codec.encode_payload("QUERY_DEVICE_IDENTITY", request)),
        )
        reply = request | {
            "currentMcuBootId": 42,
            "status": "AVAILABLE",
            "protocolMajor": 2,
            "protocolMinor": 0,
            "portCount": 1,
            "capabilityBitmap": 0x8100,
            "highestCommandSequence": 37,
            "firmwareVersionCode": 10_004,
            "firmwareIdentityHigh": 0x391CE0B8,
            "firmwareIdentityLow": 0x3076C981,
            "firmwareVersion": "1.0.1-hil.4",
        }
        payload = self.codec.encode_payload("DEVICE_IDENTITY_REPLY", reply)
        self.assertEqual(
            payload,
            encode_uart_payload(self.registry, "DEVICE_IDENTITY_REPLY", reply),
        )
        self.assertEqual(
            reply,
            self.codec.decode_payload("DEVICE_IDENTITY_REPLY", payload),
        )
        self.assertFalse(
            self.specs["QUERY_DEVICE_IDENTITY"]["ackRequired"]
        )
        self.assertFalse(
            self.specs["DEVICE_IDENTITY_REPLY"]["ackRequired"]
        )

        for changed in (
            request | {"queryId": 0},
            request | {"targetMcuBootId": 0},
            reply | {"protocolMajor": 1},
            reply | {"capabilityBitmap": 0},
            reply | {"capabilityBitmap": 0x18100},
            reply | {"firmwareVersion": "x" * 33},
        ):
            name = (
                "QUERY_DEVICE_IDENTITY"
                if set(changed) == set(request)
                else "DEVICE_IDENTITY_REPLY"
            )
            with self.subTest(message=name, changed=changed):
                with self.assertRaises((ContractError, self.codec.ProtocolError)):
                    if name == "QUERY_DEVICE_IDENTITY":
                        self.codec.encode_payload(name, changed)
                    else:
                        encode_uart_payload(self.registry, name, changed)


class CommandIdentityTests(CandidateFixture):
    def test_registry_cannot_drop_the_cross_boot_fence(self) -> None:
        for field_name in ("targetMcuBootId", "commandSequence"):
            bad = copy.deepcopy(self.registry)
            fields = bad["fieldGroups"]["commandIdentity"]
            fields[:] = [field for field in fields if field["name"] != field_name]
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(ContractError, "command identity"):
                    validate_uart_registry(bad)

    def test_registry_cannot_allow_zero_in_normal_critical_events(self) -> None:
        bad = copy.deepcopy(self.registry)
        bad["fieldGroups"]["criticalEventIdentity"][0]["minimum"] = 0
        with self.assertRaisesRegex(ContractError, "nonzero boot"):
            validate_uart_registry(bad)

    def test_every_command_has_a_nonzero_boot_and_monotonic_sequence(self) -> None:
        found = 0
        for name, spec in self.specs.items():
            fields = spec["fields"]
            if spec["direction"] != "EDGE_TO_MCU":
                continue
            if fields[0]["name"] != "mcuCommandUid":
                continue
            if len(fields) < 2 or fields[1]["name"] != "commandDigestSha256":
                continue
            found += 1
            with self.subTest(message=name):
                self.assertEqual([
                    "mcuCommandUid", "commandDigestSha256",
                    "targetMcuBootId", "commandSequence",
                ], [field["name"] for field in fields[:4]])
                self.assertEqual([0, 16, 48, 56], [field["offset"] for field in fields[:4]])
                self.assertEqual(1, fields[2]["minimum"])
                self.assertEqual(1, fields[3]["minimum"])
        self.assertEqual(18, found)

    def test_command_digest_binds_target_boot_and_sequence(self) -> None:
        name = "SAFE_CLOSE"
        values = {
            "mcuCommandUid": "11111111-1111-4111-8111-111111111111",
            "commandDigestSha256": "0" * 64,
            "targetMcuBootId": 42, "commandSequence": 7,
            "scope": "SINGLE_DELIVERY_DOOR", "portNo": 1,
            "executionDeadlineMs": 5000,
        }
        digest = compute_uart_command_digest(self.registry, name, values)
        self.assertEqual(digest, self.codec.compute_command_digest(name, values))
        values["commandDigestSha256"] = digest
        payload = self.codec.encode_payload(name, values)
        self.assertEqual(66, len(payload))
        for key in ("targetMcuBootId", "commandSequence"):
            with self.subTest(field=key):
                changed = values | {key: values[key] + 1}
                self.assertNotEqual(digest, self.codec.compute_command_digest(name, changed))
                with self.assertRaises(self.codec.ProtocolError):
                    self.codec.encode_payload(name, changed)
                with self.assertRaises(ContractError):
                    encode_uart_payload(self.registry, name, changed)

    def test_generated_budget_covers_all_messages_and_frame_overhead(self) -> None:
        outputs = build_outputs()
        budget = json.loads(outputs[CONTRACTS_ROOT / "uart/generated/message-budget.json"])
        self.assertEqual(set(self.specs), set(budget["messages"]))
        for name, spec in self.specs.items():
            row = budget["messages"][name]
            self.assertEqual(spec["maximumPayloadLength"] + 14, row["maximumFrameLength"])
            self.assertEqual(256 - row["maximumFrameLength"], row["remainingBytes"])
            self.assertGreaterEqual(row["remainingBytes"], 0)


if __name__ == "__main__":
    unittest.main()
