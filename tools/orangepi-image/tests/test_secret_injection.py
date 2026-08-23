from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT / "lib"))
MODULE_PATH = TOOL_ROOT / "lib/inject_factory_secrets.py"
SPEC = importlib.util.spec_from_file_location("ecobin_secret_injection", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
secret_injection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secret_injection)


class SecretInjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        temporary = Path(self.temporary.name)
        self.root = (temporary / "rootfs").resolve()
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc/os-release").write_text(
            "ID=debian\nVERSION_ID=12\n",
            encoding="utf-8",
        )
        self.enrollment = temporary / "enrollment.key"
        self.setup = temporary / "setup-ap.key"
        self.enrollment.write_text(
            base64.b64encode(bytes(range(32))).decode("ascii") + "\n",
            encoding="ascii",
        )
        self.setup.write_text("FactoryOnly-2026\n", encoding="ascii")
        os.chmod(self.enrollment, 0o600)
        os.chmod(self.setup, 0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def inject(self, **kwargs) -> None:
        secret_injection.inject_factory_secrets(
            self.root,
            enrollment_key_source=self.enrollment,
            setup_ap_key_source=self.setup,
            chown=kwargs.pop("chown", lambda *_args: None),
            **kwargs,
        )

    def test_injection_writes_only_two_root_mode_secret_files(self) -> None:
        ownership_calls: list[tuple[int, int]] = []

        self.inject(
            chown=lambda _path, uid, gid: ownership_calls.append((uid, gid))
        )

        enrollment = self.root / "etc/ecobin/enrollment.key"
        setup = self.root / "etc/ecobin/setup-ap.key"
        self.assertEqual(
            enrollment.read_text(encoding="ascii").strip(),
            self.enrollment.read_text(encoding="ascii").strip(),
        )
        self.assertEqual(
            setup.read_text(encoding="ascii").strip(),
            self.setup.read_text(encoding="ascii").strip(),
        )
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(enrollment.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(setup.stat().st_mode), 0o600)
        self.assertEqual(ownership_calls, [(0, 0), (0, 0)])
        self.assertEqual(
            sorted(path.name for path in (self.root / "etc/ecobin").iterdir()),
            ["enrollment.key", "setup-ap.key"],
        )

    def test_existing_destination_is_never_overwritten(self) -> None:
        self.inject()
        original = (self.root / "etc/ecobin/enrollment.key").read_bytes()
        self.enrollment.write_text(
            base64.b64encode(b"x" * 32).decode("ascii") + "\n",
            encoding="ascii",
        )
        os.chmod(self.enrollment, 0o600)

        with self.assertRaisesRegex(
            secret_injection.SecretInjectionError,
            "already contains",
        ):
            self.inject()

        self.assertEqual(
            (self.root / "etc/ecobin/enrollment.key").read_bytes(),
            original,
        )

    def test_partial_failure_removes_the_first_injected_secret(self) -> None:
        calls = 0

        def fail_second(_path: Path, _uid: int, _gid: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("fault injection")

        with self.assertRaises(OSError):
            self.inject(chown=fail_second)

        self.assertFalse(
            os.path.lexists(self.root / "etc/ecobin/enrollment.key")
        )
        self.assertFalse(os.path.lexists(self.root / "etc/ecobin/setup-ap.key"))

    def test_invalid_values_fail_without_echoing_secret(self) -> None:
        secret_value = "DO-NOT-ECHO-THIS-VALUE"
        self.enrollment.write_text(secret_value + "\n", encoding="ascii")
        os.chmod(self.enrollment, 0o600)

        with self.assertRaises(secret_injection.SecretInjectionError) as caught:
            self.inject()

        self.assertNotIn(secret_value, str(caught.exception))
        self.assertFalse((self.root / "etc/ecobin").exists())

    @unittest.skipUnless(
        os.name == "posix" and os.geteuid() == 0,
        "secret-source ownership policy is verified under root on Linux/WSL",
    )
    def test_non_root_owned_secret_source_is_rejected_without_echo(self) -> None:
        secret_value = self.enrollment.read_text(encoding="ascii").strip()
        os.chown(self.enrollment, 65534, 65534)

        with self.assertRaisesRegex(
            secret_injection.SecretInjectionError,
            "permissions are unsafe",
        ) as caught:
            self.inject()

        self.assertNotIn(secret_value, str(caught.exception))
        self.assertFalse((self.root / "etc/ecobin").exists())

    @unittest.skipIf(os.name == "nt", "POSIX symlink policy")
    def test_destination_parent_symlink_cannot_escape_rootfs(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.root / "etc/ecobin").symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(
            secret_injection.SecretInjectionError,
            "parent is unsafe",
        ):
            self.inject()

        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
