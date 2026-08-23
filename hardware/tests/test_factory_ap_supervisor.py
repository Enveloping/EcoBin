from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

import factory.ap_supervisor as ap_supervisor
from factory.ap_supervisor import (
    AccessPointStartupError,
    RuntimeIdentity,
    access_point_is_ready,
    assert_factory_ap_projection_allowed,
    assert_factory_unsealed,
    configure_interface,
    prepare_runtime_configuration,
)
from factory.network import FactoryNetworkConfig


def test_runtime_configuration_is_atomic_private_and_keeps_secret_out_of_dns(
    tmp_path: Path,
) -> None:
    machine_id = tmp_path / "machine-id"
    release = tmp_path / "image-release.json"
    setup_key = tmp_path / "setup-ap.key"
    runtime = tmp_path / "run"
    machine_id.write_text("0123456789abcdef0123456789abcdef\n", encoding="ascii")
    release.write_text(
        json.dumps({"imageReleaseId": "image-2026.08.22"}), encoding="utf-8"
    )
    setup_key.write_text("Factory-Only-42\n", encoding="ascii")
    os.chmod(setup_key, 0o600)

    config = prepare_runtime_configuration(
        machine_id_path=machine_id,
        image_release_path=release,
        setup_ap_key_path=setup_key,
        runtime_directory=runtime,
    )

    hostapd = runtime / "hostapd" / "hostapd.conf"
    dnsmasq = runtime / "dnsmasq" / "dnsmasq.conf"
    leases = runtime / "dnsmasq-state" / "dnsmasq.leases"
    assert config.ssid.startswith("EcoBin-Factory-")
    assert "wpa_passphrase=Factory-Only-42" in hostapd.read_text(encoding="utf-8")
    assert "Factory-Only-42" not in dnsmasq.read_text(encoding="utf-8")
    if os.name == "posix":
        assert hostapd.stat().st_mode & 0o027 == 0
        assert leases.stat().st_mode & 0o077 == 0
    assert not list(runtime.rglob("*.tmp"))


def test_runtime_configuration_can_be_replaced_idempotently(tmp_path: Path) -> None:
    machine_id = tmp_path / "machine-id"
    release = tmp_path / "image-release.json"
    setup_key = tmp_path / "setup-ap.key"
    runtime = tmp_path / "run"
    machine_id.write_text("0123456789abcdef0123456789abcdef\n", encoding="ascii")
    release.write_text(json.dumps({"releaseId": "release-a"}), encoding="utf-8")
    setup_key.write_text("Factory-Only-42\n", encoding="ascii")
    os.chmod(setup_key, 0o600)

    first = prepare_runtime_configuration(
        machine_id_path=machine_id,
        image_release_path=release,
        setup_ap_key_path=setup_key,
        runtime_directory=runtime,
    )
    second = prepare_runtime_configuration(
        machine_id_path=machine_id,
        image_release_path=release,
        setup_ap_key_path=setup_key,
        runtime_directory=runtime,
    )

    assert second == first
    assert (runtime / "hostapd" / "hostapd.conf").read_text(encoding="utf-8").count(
        "wpa_passphrase="
    ) == 1


def test_dnsmasq_state_is_written_before_ownership_is_handed_to_daemon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine_id = tmp_path / "machine-id"
    release = tmp_path / "image-release.json"
    setup_key = tmp_path / "setup-ap.key"
    runtime = tmp_path / "run"
    machine_id.write_text("0123456789abcdef0123456789abcdef\n", encoding="ascii")
    release.write_text(json.dumps({"releaseId": "release-a"}), encoding="utf-8")
    setup_key.write_text("Factory-Only-42\n", encoding="ascii")
    os.chmod(setup_key, 0o600)

    preparer = RuntimeIdentity(0, 0)
    hostapd = RuntimeIdentity(0, 220)
    dnsmasq = RuntimeIdentity(221, 221)
    owners: dict[Path, RuntimeIdentity] = {}
    events: list[tuple[str, str, RuntimeIdentity]] = []

    def ensure_directory(path: Path, mode: int, identity: RuntimeIdentity) -> None:
        assert mode in (0o700, 0o750)
        owners[path] = identity
        events.append(("directory", path.name, identity))

    def atomic_write(
        path: Path,
        content: str,
        mode: int,
        identity: RuntimeIdentity,
    ) -> None:
        if path.name == "dnsmasq.leases":
            # This models the kernel DAC check for mkstemp in the 0700 parent.
            # The oneshot owns no CAP_DAC_OVERRIDE, so it must still own the
            # directory at the instant the lease file is created.
            assert owners[path.parent] == preparer
        assert isinstance(content, str)
        assert mode in (0o600, 0o640)
        events.append(("write", path.name, identity))

    monkeypatch.setattr(ap_supervisor, "_current_identity", lambda: preparer)
    monkeypatch.setattr(ap_supervisor, "_ensure_directory", ensure_directory)
    monkeypatch.setattr(ap_supervisor, "_atomic_write", atomic_write)

    prepare_runtime_configuration(
        machine_id_path=machine_id,
        image_release_path=release,
        setup_ap_key_path=setup_key,
        runtime_directory=runtime,
        hostapd_identity=hostapd,
        dnsmasq_identity=dnsmasq,
    )

    state_directory = runtime / "dnsmasq-state"
    assert owners[state_directory] == dnsmasq
    assert events[-2:] == [
        ("write", "dnsmasq.leases", dnsmasq),
        ("directory", "dnsmasq-state", dnsmasq),
    ]


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership ordering only")
def test_existing_daemon_directory_is_reclaimed_before_chmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "dnsmasq-state"
    directory.mkdir()
    existing_uid = directory.stat().st_uid
    preparer = RuntimeIdentity(existing_uid + 1, 900)
    events: list[tuple[str, int, int] | tuple[str, int]] = []

    monkeypatch.setattr(ap_supervisor, "_current_identity", lambda: preparer)
    monkeypatch.setattr(
        ap_supervisor.os,
        "chown",
        lambda path, uid, gid: events.append(("chown", uid, gid)),
    )
    monkeypatch.setattr(
        ap_supervisor.os,
        "chmod",
        lambda path, mode: events.append(("chmod", mode)),
    )

    ap_supervisor._ensure_directory(directory, 0o700, preparer)

    assert events == [
        ("chown", preparer.uid, preparer.gid),
        ("chmod", 0o700),
        ("chown", preparer.uid, preparer.gid),
    ]


def test_existing_seal_marker_fails_instead_of_skipping_factory_start(
    tmp_path: Path,
) -> None:
    sealed = tmp_path / "sealed.json"
    sealed.write_text("{}", encoding="utf-8")

    with pytest.raises(AccessPointStartupError, match="disabled after sealing"):
        assert_factory_unsealed(sealed)

    sealed.unlink()
    assert_factory_unsealed(sealed)


def test_missing_marker_still_denies_ap_when_sqlite_is_already_sealing(
    tmp_path: Path,
) -> None:
    sealed = tmp_path / "missing-sealed.json"
    edge_store = tmp_path / "edge.db"
    with sqlite3.connect(edge_store) as database:
        database.execute(
            """CREATE TABLE factory_seal_authorization (
                command_uid TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                acceptance_generation INTEGER NOT NULL,
                authorization_binding_sha256 TEXT NOT NULL
            )"""
        )
        database.execute(
            "INSERT INTO factory_seal_authorization VALUES (?, ?, ?, ?)",
            (
                "12345678-1234-4123-8123-123456789abc",
                "SEALING",
                7,
                "a" * 64,
            ),
        )

    with pytest.raises(AccessPointStartupError, match="SEALED_FACT_MISSING"):
        assert_factory_unsealed(sealed, edge_store)


def test_low_privilege_monitor_accepts_only_root_projection_shape(
    tmp_path: Path,
) -> None:
    projection = tmp_path / "ap-allowed.json"
    projection.write_text(
        json.dumps(
            {"schemaVersion": 1, "allowed": True, "statusCode": "UNSEALED"}
        ),
        encoding="utf-8",
    )

    assert_factory_ap_projection_allowed(projection)

    for value in (
        {"schemaVersion": 1, "allowed": False, "statusCode": "SEALED"},
        {
            "schemaVersion": 1,
            "allowed": True,
            "statusCode": "UNSEALED",
            "hardwareSn": "must-not-be-exposed",
        },
        {"schemaVersion": 1, "allowed": True, "statusCode": "SEALED"},
    ):
        projection.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(AccessPointStartupError):
            assert_factory_ap_projection_allowed(projection)


def test_interface_preparation_detaches_bridge_and_disables_forwarding() -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command: object) -> subprocess.CompletedProcess[bytes]:
        normalized = tuple(command)  # type: ignore[arg-type]
        calls.append(normalized)
        return subprocess.CompletedProcess(normalized, 0, b"", b"")

    configure_interface(FactoryNetworkConfig(factory_id="12AB34CD"), runner=runner)

    assert calls == [
        (
            "/usr/sbin/sysctl",
            "-q",
            "-w",
            "net.ipv4.ip_forward=0",
            "net.ipv6.conf.all.forwarding=0",
            "net.ipv6.conf.default.forwarding=0",
            "net.ipv6.conf.wlan0.disable_ipv6=1",
        ),
        ("/usr/sbin/ip", "link", "set", "dev", "wlan0", "down"),
        ("/usr/sbin/ip", "link", "set", "dev", "wlan0", "nomaster"),
        ("/usr/sbin/ip", "address", "flush", "dev", "wlan0"),
        ("/usr/sbin/ip", "address", "add", "10.42.0.1/24", "dev", "wlan0"),
        ("/usr/sbin/ip", "link", "set", "dev", "wlan0", "up"),
    ]


def test_readiness_requires_exact_address_ap_mode_and_local_tcp_dns() -> None:
    probes: list[tuple[str, int, float]] = []

    def runner(command: object) -> subprocess.CompletedProcess[bytes]:
        normalized = tuple(command)  # type: ignore[arg-type]
        if normalized[1:3] == ("-j", "address"):
            output = json.dumps(
                [
                    {
                        "ifname": "wlan0",
                        "flags": ["BROADCAST", "UP"],
                        "addr_info": [
                            {
                                "family": "inet",
                                "local": "10.42.0.1",
                                "prefixlen": 24,
                            }
                        ],
                    }
                ]
            ).encode("utf-8")
        else:
            output = b"Interface wlan0\n\ttype AP\n"
        return subprocess.CompletedProcess(normalized, 0, output, b"")

    def probe(address: str, port: int, timeout: float) -> None:
        probes.append((address, port, timeout))

    assert access_point_is_ready(runner=runner, tcp_probe=probe) is True
    assert probes == [("10.42.0.1", 53, 1.0)]


def test_readiness_fails_closed_for_wrong_prefix_without_dns_probe() -> None:
    probes: list[tuple[str, int, float]] = []

    def runner(command: object) -> subprocess.CompletedProcess[bytes]:
        normalized = tuple(command)  # type: ignore[arg-type]
        output = json.dumps(
            [
                {
                    "flags": ["UP"],
                    "addr_info": [
                        {
                            "family": "inet",
                            "local": "10.42.0.1",
                            "prefixlen": 16,
                        }
                    ],
                }
            ]
        ).encode("utf-8")
        return subprocess.CompletedProcess(normalized, 0, output, b"")

    def probe(address: str, port: int, timeout: float) -> None:
        probes.append((address, port, timeout))

    assert access_point_is_ready(runner=runner, tcp_probe=probe) is False
    assert probes == []
