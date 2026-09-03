from __future__ import annotations

import json
from pathlib import Path

import pytest

import updater_control_cli


class _RecordingClient:
    calls: list[tuple[str, dict[str, object]]] = []

    def __init__(self, socket_path: str, *, protocol_name: str) -> None:
        assert socket_path == "/test/updater.sock"
        assert protocol_name == "ecobin.updater.control"

    def request(
        self,
        action: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append((action, payload))
        return {"action": action, "accepted": True}


@pytest.mark.parametrize(
    ("command", "expected_action"),
    [
        ("activate", "ACTIVATE_STAGE4_JOB_GATE"),
        ("lock", "LOCK_STAGE4_JOB_GATE"),
    ],
)
def test_root_cli_sends_only_exact_evidence_bearing_mutations(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    expected_action: str,
) -> None:
    _RecordingClient.calls = []
    monkeypatch.setattr(updater_control_cli, "_effective_uid", lambda: 0)
    monkeypatch.setattr(
        updater_control_cli,
        "LocalControlClient",
        _RecordingClient,
    )

    result = updater_control_cli.main(
        [
            "--socket",
            "/test/updater.sock",
            command,
            "--operation-uid",
            "00000000-0000-4000-8000-000000000001",
            "--evidence-digest",
            "a" * 64,
            "--expected-management-sequence",
            "7",
        ]
    )

    assert result == 0
    assert _RecordingClient.calls == [
        (
            expected_action,
            {
                "operationUid": (
                    "00000000-0000-4000-8000-000000000001"
                ),
                "evidenceDigest": "a" * 64,
                "expectedManagementStateSequence": 7,
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["accepted"] is True


def test_root_cli_status_uses_socket_without_mutation_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _RecordingClient.calls = []
    monkeypatch.setattr(updater_control_cli, "_effective_uid", lambda: 0)
    monkeypatch.setattr(
        updater_control_cli,
        "LocalControlClient",
        _RecordingClient,
    )

    assert updater_control_cli.main(
        ["--socket", "/test/updater.sock", "status"]
    ) == 0
    assert _RecordingClient.calls == [
        ("GET_STAGE4_RECONCILIATION_STATUS", {})
    ]


def test_cli_refuses_non_root_before_opening_control_socket(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(updater_control_cli, "_effective_uid", lambda: 3102)

    class UnexpectedClient:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("non-root CLI must not open the socket")

    monkeypatch.setattr(
        updater_control_cli,
        "LocalControlClient",
        UnexpectedClient,
    )

    assert updater_control_cli.main(["status"]) == 2
    assert json.loads(capsys.readouterr().err)["errorCode"] == (
        "ROOT_REQUIRED"
    )


def test_cli_has_no_database_or_generic_transition_interface() -> None:
    source = Path(updater_control_cli.__file__).read_text(encoding="utf-8")

    assert "sqlite3" not in source
    assert "updater_store" not in source
    assert "transition" not in source.casefold()
    parser = updater_control_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["transition"])
