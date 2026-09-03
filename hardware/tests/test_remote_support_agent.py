from __future__ import annotations

import sys
from types import SimpleNamespace

import remote_support_agent


def test_systemd_ready_uses_abstract_notify_socket(monkeypatch):
    sent = []

    class Notifier:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendto(self, message, address):
            sent.append((message, address))

    monkeypatch.setenv("NOTIFY_SOCKET", "@ecobin-ready")
    monkeypatch.setattr(
        remote_support_agent.socket,
        "AF_UNIX",
        1,
        raising=False,
    )
    monkeypatch.setattr(
        remote_support_agent.socket,
        "socket",
        lambda *_args: Notifier(),
    )

    remote_support_agent.notify_systemd_ready()

    assert sent == [(b"READY=1", "\0ecobin-ready")]


def test_remote_support_resolves_business_peer_and_socket_group(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "pwd",
        SimpleNamespace(getpwnam=lambda _name: SimpleNamespace(pw_uid=101)),
    )
    monkeypatch.setitem(
        sys.modules,
        "grp",
        SimpleNamespace(getgrnam=lambda _name: SimpleNamespace(gr_gid=202)),
    )

    assert remote_support_agent.resolve_allowed_uids(
        [0], ["ecobin-business"]
    ) == frozenset({0, 101})
    assert remote_support_agent.resolve_socket_gid(
        "ecobin-business-ipc"
    ) == 202
