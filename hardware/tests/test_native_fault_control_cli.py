import json

import native_fault_control_cli as cli


FAULT_UID = "11111111-1111-4111-8111-111111111111"


class Client:
    def __init__(self, socket, *, protocol_name):
        self.socket = socket
        self.protocol_name = protocol_name

    def request(self, action, payload):
        Client.requested = (action, payload)
        return {"disposition": "RECOVERED", "faultUid": FAULT_UID}


def test_status_uses_business_control_socket_as_root(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_effective_uid", lambda: 0)
    monkeypatch.setattr(cli, "LocalControlClient", Client)

    assert cli.main(["--socket", "/run/test.sock", "status"]) == 0

    assert Client.requested == ("GET_NATIVE_FAULT_STATUS", {})
    assert json.loads(capsys.readouterr().out) == {
        "disposition": "RECOVERED",
        "faultUid": FAULT_UID,
    }


def test_recover_requires_explicit_confirmation(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_effective_uid", lambda: 0)

    assert cli.main([
        "recover",
        "--fault-uid",
        FAULT_UID,
        "--reason",
        "串口线已修复",
    ]) == 2

    error = json.loads(capsys.readouterr().err)
    assert error["errorCode"] == "CONFIRMATION_REQUIRED"


def test_recover_sends_exact_fault_reason_and_confirmation(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(cli, "_effective_uid", lambda: 0)
    monkeypatch.setattr(cli, "LocalControlClient", Client)

    assert cli.main([
        "recover",
        "--fault-uid",
        FAULT_UID,
        "--reason",
        "串口线已修复",
        "--confirm-cause-fixed",
    ]) == 0

    assert Client.requested == (
        "RECOVER_NATIVE_COMMUNICATION_FAULT",
        {
            "expectedFaultUid": FAULT_UID,
            "reason": "串口线已修复",
            "causeFixedConfirmed": True,
        },
    )
    assert json.loads(capsys.readouterr().out)["faultUid"] == FAULT_UID


def test_non_root_is_rejected_before_opening_socket(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_effective_uid", lambda: 1000)

    assert cli.main(["status"]) == 2

    assert json.loads(capsys.readouterr().err)["errorCode"] == "ROOT_REQUIRED"
