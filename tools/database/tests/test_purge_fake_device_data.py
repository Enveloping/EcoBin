from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "purge-fake-device-data.py"
SPEC = importlib.util.spec_from_file_location("purge_fake_device_data", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BackupEvidenceTests(unittest.TestCase):
    def test_empty_backup_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "backup-evidence.txt"
            evidence.touch()
            with self.assertRaisesRegex(RuntimeError, "non-empty"):
                MODULE.validate_backup_evidence(evidence)

    def test_non_empty_readable_backup_evidence_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "backup-evidence.txt"
            evidence.write_text("backup completed\n", encoding="utf-8")
            MODULE.validate_backup_evidence(evidence)


if __name__ == "__main__":
    unittest.main()
