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


def test_root_cli_queues_only_fixed_mcu_package_identities(
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
        [
            "--socket",
            "/test/updater.sock",
            "mcu-queue",
            "--update-uid",
            "11111111-1111-4111-8111-111111111111",
            "--command-uid",
            "22222222-2222-4222-8222-222222222222",
            "--target-package-sha256",
            "a" * 64,
            "--rollback-package-sha256",
            "b" * 64,
        ]
    ) == 0
    assert _RecordingClient.calls == [
        (
            "QUEUE_LOCAL_MCU_UPDATE",
            {
                "updateUid": "11111111-1111-4111-8111-111111111111",
                "commandUid": "22222222-2222-4222-8222-222222222222",
                "targetPackageSha256": "a" * 64,
                "rollbackPackageSha256": "b" * 64,
            },
        )
    ]


def test_root_cli_queues_only_fixed_business_package_identity(
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
        [
            "--socket",
            "/test/updater.sock",
            "business-queue",
            "--update-uid",
            "11111111-1111-4111-8111-111111111111",
            "--deployment-uid",
            "22222222-2222-4222-8222-222222222222",
            "--command-uid",
            "33333333-3333-4333-8333-333333333333",
            "--release-id",
            "44444444-4444-4444-8444-444444444444",
            "--version-name",
            "1.2.3",
            "--release-sequence",
            "7",
            "--package-sha256",
            "a" * 64,
            "--package-size",
            "53750778",
            "--signing-key-id",
            "business_2026",
        ]
    ) == 0
    assert _RecordingClient.calls == [
        (
            "QUEUE_LOCAL_BUSINESS_UPDATE",
            {
                "updateUid": "11111111-1111-4111-8111-111111111111",
                "deploymentUid": "22222222-2222-4222-8222-222222222222",
                "commandUid": "33333333-3333-4333-8333-333333333333",
                "releaseId": "44444444-4444-4444-8444-444444444444",
                "versionName": "1.2.3",
                "releaseSequence": 7,
                "packageSha256": "a" * 64,
                "packageSize": 53750778,
                "signingKeyId": "business_2026",
            },
        )
    ]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["mcu-status"], ("GET_STATUS", {})),
        (
            [
                "mcu-status",
                "--update-uid",
                "11111111-1111-4111-8111-111111111111",
            ],
            (
                "GET_MCU_UPDATE",
                {"updateUid": "11111111-1111-4111-8111-111111111111"},
            ),
        ),
    ],
)
def test_root_cli_reads_mcu_candidate_status(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected: tuple[str, dict[str, object]],
) -> None:
    _RecordingClient.calls = []
    monkeypatch.setattr(updater_control_cli, "_effective_uid", lambda: 0)
    monkeypatch.setattr(
        updater_control_cli,
        "LocalControlClient",
        _RecordingClient,
    )

    assert updater_control_cli.main(
        ["--socket", "/test/updater.sock", *arguments]
    ) == 0
    assert _RecordingClient.calls == [expected]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["business-status"], ("GET_STATUS", {})),
        (
            [
                "business-status",
                "--update-uid",
                "11111111-1111-4111-8111-111111111111",
            ],
            (
                "GET_BUSINESS_UPDATE",
                {"updateUid": "11111111-1111-4111-8111-111111111111"},
            ),
        ),
    ],
)
def test_root_cli_reads_business_candidate_status(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    expected: tuple[str, dict[str, object]],
) -> None:
    _RecordingClient.calls = []
    monkeypatch.setattr(updater_control_cli, "_effective_uid", lambda: 0)
    monkeypatch.setattr(
        updater_control_cli,
        "LocalControlClient",
        _RecordingClient,
    )

    assert updater_control_cli.main(
        ["--socket", "/test/updater.sock", *arguments]
    ) == 0
    assert _RecordingClient.calls == [expected]


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
    assert "--package-path" not in source
    assert "--firmware-path" not in source
    parser = updater_control_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["transition"])
