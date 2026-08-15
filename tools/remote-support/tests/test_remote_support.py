#!/usr/bin/env python3

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


TEST_DIRECTORY = Path(__file__).resolve().parent
REMOTE_SUPPORT_ROOT = TEST_DIRECTORY.parent
sys.path.insert(0, str(REMOTE_SUPPORT_ROOT / "lib"))

from ecobin_remote_support import (  # noqa: E402
    FIXED_PORTS,
    LeaseError,
    atomic_json_write,
    authorized_key_line,
    load_policy,
    make_lease,
    public_key_fingerprint,
    read_actual,
    unlink_and_fsync,
)


def create_test_ed25519_public_key(
    seed: bytes = b"ecobin-test-key",
) -> tuple[str, str]:
    raw_key = hashlib.sha256(seed).digest()
    algorithm = b"ssh-ed25519"
    blob = (
        struct.pack(">I", len(algorithm))
        + algorithm
        + struct.pack(">I", len(raw_key))
        + raw_key
    )
    return "ssh-ed25519", base64.b64encode(blob).decode("ascii")


def open_test_listener(port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    return listener


class RemoteSupportLeaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="ecobin-remote-support-test."
        )
        self.root = Path(self.temporary.name)
        self.desired = self.root / "desired"
        self.actual = self.root / "actual"
        self.desired.mkdir(mode=0o750)
        self.actual.mkdir(mode=0o750)
        self.desired.chmod(0o2750)
        self.actual.chmod(0o2750)
        self.policy_path = self.root / "policy.json"
        policy_value = {
            "schemaVersion": 1,
            "backendUid": os.getuid(),
            "backendGid": os.getgid(),
            "leaseReaderUid": os.getuid(),
            "leaseReaderGid": os.getgid(),
            "tunnelUid": os.getuid(),
            "desiredLeaseDirectory": str(self.desired),
            "actualLeaseDirectory": str(self.actual),
            "listenHost": "127.0.0.1",
            "ports": list(FIXED_PORTS),
            "maxLeaseSeconds": 1800,
            "clockSkewSeconds": 60,
            "guardPollSeconds": 1,
            "listenerStartupSeconds": 2,
        }
        self.policy_path.write_text(
            json.dumps(policy_value, sort_keys=True), encoding="utf-8"
        )
        self.policy_path.chmod(0o644)
        self.policy = load_policy(
            self.policy_path, expected_policy_uid=os.getuid()
        )
        self.key_type, self.key_base64 = create_test_ed25519_public_key()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_lease(
        self,
        port: int = 22011,
        *,
        session_uid: str = "11111111-1111-4111-8111-111111111111",
        hardware_sn: str = "ECM0-TESTDEVICE01",
        key_type: str | None = None,
        key_base64: str | None = None,
        lifetime: int = 300,
    ):
        now = int(time.time())
        lease = make_lease(
            self.policy,
            session_uid=session_uid,
            hardware_sn=hardware_sn,
            key_type=key_type or self.key_type,
            key_base64=key_base64 or self.key_base64,
            listen_port=port,
            created_at=now,
            expires_at=now + lifetime,
        )
        atomic_json_write(
            self.desired,
            f"{port}.json",
            lease.as_json(),
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
        )
        return lease

    def run_authorized_keys(
        self, key_type: str, key_base64: str, fingerprint: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(REMOTE_SUPPORT_ROOT / "bin" / "ecobin-authorized-keys"),
                "ecobin-tunnel",
                key_type,
                key_base64,
                fingerprint,
                "--policy",
                str(self.policy_path),
                "--allow-unprivileged-policy",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_authorized_keys_emits_only_the_leased_port_and_forced_guard(self) -> None:
        lease = self.create_lease()
        result = self.run_authorized_keys(
            lease.key_type, lease.key_base64, lease.key_fingerprint
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(authorized_key_line(lease), result.stdout.strip())
        self.assertIn('permitlisten="127.0.0.1:22011"', result.stdout)
        self.assertIn("command=", result.stdout)
        self.assertNotIn("22012", result.stdout)

    def test_unleased_key_produces_no_authorization(self) -> None:
        self.create_lease()
        other_type, other_base64 = create_test_ed25519_public_key(b"another-key")
        result = self.run_authorized_keys(
            other_type,
            other_base64,
            public_key_fingerprint(other_type, other_base64),
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)

    def test_same_key_in_two_active_slots_is_denied(self) -> None:
        first = self.create_lease()
        self.create_lease(
            22012,
            session_uid="22222222-2222-4222-8222-222222222222",
            hardware_sn="ECM0-TESTDEVICE02",
        )
        result = self.run_authorized_keys(
            first.key_type, first.key_base64, first.key_fingerprint
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("more than one active lease", result.stderr)

    def test_group_writable_lease_is_rejected(self) -> None:
        lease = self.create_lease()
        self.policy.desired_path(lease.listen_port).chmod(0o660)
        result = self.run_authorized_keys(
            lease.key_type, lease.key_base64, lease.key_fingerprint
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("writable by group", result.stderr)

    def test_non_loopback_or_out_of_pool_lease_is_rejected(self) -> None:
        now = int(time.time())
        with self.assertRaises(LeaseError):
            make_lease(
                self.policy,
                session_uid="33333333-3333-4333-8333-333333333333",
                hardware_sn="ECM0-TESTDEVICE03",
                key_type=self.key_type,
                key_base64=self.key_base64,
                listen_port=22015,
                created_at=now,
                expires_at=now + 300,
            )

    def test_lifetime_over_thirty_minutes_is_rejected(self) -> None:
        now = int(time.time())
        with self.assertRaises(LeaseError):
            make_lease(
                self.policy,
                session_uid="44444444-4444-4444-8444-444444444444",
                hardware_sn="ECM0-TESTDEVICE04",
                key_type=self.key_type,
                key_base64=self.key_base64,
                listen_port=22011,
                created_at=now,
                expires_at=now + 1801,
            )

    def test_expired_lease_produces_no_authorization(self) -> None:
        now = int(time.time())
        lease = make_lease(
            self.policy,
            session_uid="55555555-5555-4555-8555-555555555555",
            hardware_sn="ECM0-TESTDEVICE05",
            key_type=self.key_type,
            key_base64=self.key_base64,
            listen_port=22011,
            created_at=now - 300,
            expires_at=now - 1,
        )
        atomic_json_write(
            self.desired,
            "22011.json",
            lease.as_json(),
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
        )
        result = self.run_authorized_keys(
            lease.key_type, lease.key_base64, lease.key_fingerprint
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual("", result.stdout)

    def test_guard_rejects_any_other_requested_command(self) -> None:
        lease = self.create_lease()
        environment = os.environ.copy()
        environment["SSH_ORIGINAL_COMMAND"] = "unexpected-command"
        result = subprocess.run(
            [
                sys.executable,
                str(REMOTE_SUPPORT_ROOT / "bin" / "ecobin-lease-guard"),
                "--session-uid",
                lease.session_uid,
                "--port",
                str(lease.listen_port),
                "--policy",
                str(self.policy_path),
                "--allow-unprivileged-policy",
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("required lease guard command", result.stderr)
        self.assertFalse(self.policy.actual_path(lease.listen_port).exists())

    def test_revocation_stops_guard_and_removes_actual_marker(self) -> None:
        listener = None
        for port in FIXED_PORTS:
            try:
                listener = open_test_listener(port)
                selected_port = port
                break
            except OSError:
                continue
        self.assertIsNotNone(listener, "all fixed ports are occupied on the test host")
        assert listener is not None
        lease = self.create_lease(selected_port)
        environment = os.environ.copy()
        environment["SSH_ORIGINAL_COMMAND"] = "ecobin-lease-guard"
        environment.pop("SSH_CONNECTION", None)
        process = subprocess.Popen(
            [
                sys.executable,
                str(REMOTE_SUPPORT_ROOT / "bin" / "ecobin-lease-guard"),
                "--session-uid",
                lease.session_uid,
                "--port",
                str(selected_port),
                "--policy",
                str(self.policy_path),
                "--allow-unprivileged-policy",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not self.policy.actual_path(
                selected_port
            ).exists():
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            if process.poll() is not None:
                _stdout, stderr = process.communicate(timeout=1)
                self.fail(f"guard stopped before publishing actual marker: {stderr}")
            marker = read_actual(self.policy, selected_port)
            self.assertEqual(lease.session_uid, marker["sessionUid"])
            self.assertEqual(0o640, stat.S_IMODE(self.policy.actual_path(selected_port).stat().st_mode))

            unlink_and_fsync(self.desired, f"{selected_port}.json")
            process.wait(timeout=5)
            _stdout, stderr = process.communicate(timeout=1)
            self.assertEqual(0, process.returncode, stderr)
            self.assertFalse(self.policy.actual_path(selected_port).exists())
        finally:
            listener.close()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
                process.communicate(timeout=1)


if __name__ == "__main__":
    unittest.main()
