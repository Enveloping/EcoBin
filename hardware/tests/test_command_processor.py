import json
import os
from datetime import datetime, timedelta, timezone

from command_processor import CommandProcessor
from edge_store import EdgeStore
from onenet_wire import (
    canonical_payload_sha256,
    decode_service_command,
)
from uart_link import compute_mcu_payload_sha256


class FakeUart:
    def __init__(self):
        self.calls = []

    def apply_configuration(self, command, part_command_uids):
        self.calls.append((command, list(part_command_uids)))
        return {
            "acked": True,
            "commit_mcu_command_uid": part_command_uids[-1],
            "parts": [
                {
                    "message_name": "CONFIG_PART",
                    "mcu_command_uid": part_uid,
                    "acked": True,
                }
                for part_uid in part_command_uids
            ],
        }


def make_store(tmp_path):
    store = EdgeStore(str(tmp_path / "edge.db"))
    store.initialize()
    return store


def valid_configuration_command():
    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "contracts",
        "examples",
        "onenet-wire",
        "apply-configuration.service-wire.json",
    )
    with open(path, encoding="utf-8") as source:
        wire = json.load(source)
    body = wire["callServiceApiBodyTemplate"]
    command = decode_service_command(body["identifier"], body["params"])
    now = datetime.now(timezone.utc)
    command["issuedAt"] = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["expiresAt"] = (
        now + timedelta(minutes=5)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    command["payload"]["config"]["mcuPayloadSha256"] = compute_mcu_payload_sha256(
        command["payload"]
    )
    command["payloadSha256"] = canonical_payload_sha256(command["payload"])
    return command


def test_apply_configuration_consumes_inbox_and_waits_for_mcu_result(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    command = valid_configuration_command()
    store.receive_command(command["commandUid"], command["commandType"], command)

    assert processor.process_next()

    inbox = store.get_command(command["commandUid"])
    assert inbox["state"] == "WAITING_MCU_RESULT"
    configuration = store.get_configuration(command["payload"]["applicationUid"])
    assert configuration["state"] == "WAITING_MCU_RESULT"
    assert len(configuration["part_command_uids"]) == len(command["payload"]["ports"]) + 3
    events = store._conn.execute(
        "SELECT * FROM event_outbox WHERE event_type='CONFIGURATION_PROGRESS'"
    ).fetchall()
    assert len(events) == 1
    edge_saved = json.loads(events[0]["payload_json"])
    assert edge_saved["payload"]["stage"] == "EDGE_SAVED"
    store.close()


def test_configuration_apply_result_completes_command_and_creates_event(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    command = valid_configuration_command()
    store.receive_command(command["commandUid"], command["commandType"], command)
    processor.process_next()
    configuration = store.get_configuration(command["payload"]["applicationUid"])
    commit_uid = configuration["part_command_uids"][-1]

    processor.process_mcu_event({
        "message_name": "CONFIG_APPLY_RESULT",
        "message_type": 20,
        "source_tx_sequence": 9,
        "payload": {
            "mcuBootId": 42,
            "mcuEventSequence": 1,
            "uptimeMs": 1000,
            "mcuCommandUid": commit_uid,
            "applicationUid": command["payload"]["applicationUid"],
            "status": "APPLIED",
            "configVersion": command["payload"]["config"]["version"],
            "contentSha256": command["payload"]["config"]["contentSha256"],
            "mcuPayloadSha256": command["payload"]["config"]["mcuPayloadSha256"],
            "faultCode": "NONE",
        },
    })

    assert store.get_command(command["commandUid"])["state"] == "COMPLETED"
    assert store.get_configuration(command["payload"]["applicationUid"])["state"] == "APPLIED"
    assert store.get_state("applied_config_version") == "8"
    events = store._conn.execute(
        "SELECT payload_json FROM event_outbox "
        "WHERE event_type='CONFIGURATION_PROGRESS' ORDER BY edge_event_sequence"
    ).fetchall()
    assert [json.loads(row["payload_json"])["payload"]["stage"] for row in events] == [
        "EDGE_SAVED",
        "APPLIED",
    ]
    store.close()


def test_invalid_previously_accepted_command_is_failed_without_uart(tmp_path):
    store = make_store(tmp_path)
    uart = FakeUart()
    processor = CommandProcessor(store, uart)
    invalid = valid_configuration_command()
    invalid["commandUid"] = "12"
    store.receive_command("12", "APPLY_CONFIGURATION", invalid)

    processor.process_next()

    command = store.get_command("12")
    assert command["state"] == "FAILED"
    assert command["last_error"] == "INVALID_COMMAND_IDENTITY"
    assert uart.calls == []
    store.close()
