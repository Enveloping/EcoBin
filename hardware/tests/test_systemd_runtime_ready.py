from __future__ import annotations

import pytest

import main


def test_systemd_ready_uses_abstract_notify_socket(monkeypatch):
    sent = []

    class Notifier:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendto(self, message, address):
            sent.append((message, address))

    monkeypatch.setenv("NOTIFY_SOCKET", "@ecobin-hardware-ready")
    monkeypatch.setattr(main.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(
        main.socket,
        "socket",
        lambda *_args: Notifier(),
    )

    main.notify_systemd_ready("READY")

    assert sent == [(b"READY=1", "\0ecobin-hardware-ready")]


@pytest.mark.parametrize("status", ["DEGRADED", "MCU_UPDATE_FAILED_LOCKED"])
def test_recoverable_or_maintenance_locked_runtime_is_process_ready(
    monkeypatch,
    status,
):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)

    main.notify_systemd_ready(status)


def test_safety_locked_runtime_never_reports_ready(monkeypatch):
    created = []
    monkeypatch.setenv("NOTIFY_SOCKET", "@must-not-send")
    monkeypatch.setattr(
        main.socket,
        "socket",
        lambda *_args: created.append(True),
    )

    with pytest.raises(RuntimeError, match="refusing systemd readiness"):
        main.notify_systemd_ready("SAFETY_LOCKED")

    assert created == []
