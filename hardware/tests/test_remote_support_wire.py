from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from command_processor import CommandProcessor
from edge_store import EdgeStore
from onenet_wire import (
    canonical_payload_sha256,
    decode_service_command,
    encode_event_post,
    validate_command_envelope,
)


def _uid() -> str:
    return str(uuid.uuid4())


def _instant(minutes: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _params(payload: dict, *, expires_at: str) -> dict:
    session_uid = payload["sessionUid"]
    params = {
        "commandUid": _uid(),
        "targetDeviceName": "ECM0-TEST",
        "target": {"type": 1, "uid": session_uid},
        "issuedAt": _instant(0),
        "expiresAt": expires_at,
        "payloadSha256": canonical_payload_sha256(payload),
        **{key: value for key, value in payload.items() if key != "expiresAt"},
    }
    if "expiresAt" in payload:
        params["payloadExpiresAt"] = payload["expiresAt"]
    return params


def test_open_and_close_services_decode_to_strict_v2_envelopes():
    session_uid = _uid()
    expiry = _instant(30)
    opened = decode_service_command(
        "openRemoteSupportTunnel",
        _params(
            {
                "sessionUid": session_uid,
                "remotePort": 22011,
                "expiresAt": expiry,
            },
            expires_at=expiry,
        ),
    )
    validate_command_envelope(opened)
    assert opened["commandType"] == "OPEN_REMOTE_SUPPORT_TUNNEL"
    assert opened["target"] == {
        "type": "REMOTE_SUPPORT_SESSION",
        "uid": session_uid,
    }

    close_expiry = _instant(2)
    closed = decode_service_command(
        "closeRemoteSupportTunnel",
        _params({"sessionUid": session_uid}, expires_at=close_expiry),
    )
    validate_command_envelope(closed)
    assert closed["commandType"] == "CLOSE_REMOTE_SUPPORT_TUNNEL"


@pytest.mark.parametrize("port", [22, 22010, 22015, 65535, True])
def test_open_service_rejects_any_port_outside_the_server_pool(port):
    expiry = _instant(30)
    command = decode_service_command(
        "openRemoteSupportTunnel",
        _params(
            {
                "sessionUid": _uid(),
                "remotePort": port,
                "expiresAt": expiry,
            },
            expires_at=expiry,
        ),
    )

    with pytest.raises(ValueError, match="22011"):
        validate_command_envelope(command)


def test_open_service_rejects_payload_and_envelope_expiry_mismatch():
    expiry = _instant(30)
    params = _params(
        {
            "sessionUid": _uid(),
            "remotePort": 22011,
            "expiresAt": expiry,
        },
        expires_at=expiry,
    )
    payload_expiry = _instant(29)
    params["payloadExpiresAt"] = payload_expiry
    params["payloadSha256"] = canonical_payload_sha256({
        "sessionUid": params["sessionUid"],
        "remotePort": params["remotePort"],
        "expiresAt": payload_expiry,
    })
    command = decode_service_command("openRemoteSupportTunnel", params)

    with pytest.raises(ValueError, match="expiry differs"):
        validate_command_envelope(command)


def test_status_event_projects_nullable_failure_code_and_fixed_enum(tmp_path: Path):
    edge = EdgeStore(str(tmp_path / "edge.db"))
    edge.initialize()
    session_uid = _uid()
    edge.request_remote_support_open(
        session_uid=session_uid,
        command_uid=_uid(),
        device_name="ECM0-TEST",
        remote_port=22012,
        expires_at="2099-01-01T00:00:00.000Z",
    )
    row = edge._conn.execute(
        "SELECT payload_json FROM event_outbox ORDER BY edge_event_sequence LIMIT 1"
    ).fetchone()
    envelope = json.loads(row["payload_json"])

    wire = encode_event_post("REMOTE_SUPPORT_TUNNEL_STATUS", envelope)
    value = wire["params"]["remoteSupportTunnelStatus"]["value"]

    assert value["state"] == 1
    assert value["remotePort"] == 22012
    assert value["failureCodePresent"] is False
    assert value["failureCode"] == ""
    assert value["target"] == {"type": 1, "uid": "ECM0-TEST"}
    assert value["occurredAtPresent"] is True
    assert value["clockQuality"] == 1
    edge.close()


def test_persisted_command_processor_delegates_without_touching_uart(tmp_path: Path):
    edge = EdgeStore(str(tmp_path / "edge.db"))
    edge.initialize()
    expiry = _instant(30)
    command = decode_service_command(
        "openRemoteSupportTunnel",
        _params(
            {
                "sessionUid": _uid(),
                "remotePort": 22014,
                "expiresAt": expiry,
            },
            expires_at=expiry,
        ),
    )
    calls = []

    class Remote:
        def open_session(self, **kwargs):
            calls.append(kwargs)
            return "ACCEPTED"

    assert edge.receive_command(
        command["commandUid"],
        command["commandType"],
        command,
    ) == "ACCEPTED"
    processor = CommandProcessor(
        edge,
        object(),
        remote_support_manager=Remote(),
    )

    assert processor.process_next() is True
    assert calls[0]["remote_port"] == 22014
    assert edge.get_command(command["commandUid"])["state"] == "COMPLETED"
    edge.close()
