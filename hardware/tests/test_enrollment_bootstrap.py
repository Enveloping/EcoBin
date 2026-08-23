from __future__ import annotations

import json
from pathlib import Path

import pytest

from enrollment_bootstrap import main
from secret_memory_guard import SecretMemoryGuardError
from tests.test_device_credentials import valid_document


def test_verified_credentials_finish_cleanup_without_backend_or_generation_import(
    tmp_path: Path,
):
    credentials = tmp_path / "device-credentials.json"
    credentials.write_text(json.dumps(valid_document()), encoding="utf-8")
    credentials.chmod(0o600)
    global_key = tmp_path / "enrollment.key"
    state = tmp_path / "enrollment-state.json"
    generation_module = tmp_path / "device_enrollment.py"
    legacy_secret = tmp_path / "legacy-onenet-secret"
    proc_swaps = tmp_path / "proc-swaps"
    proc_swaps.write_text(
        "Filename\tType\tSize\tUsed\tPriority\n", encoding="ascii"
    )
    for path in (global_key, state, generation_module, legacy_secret):
        path.write_text("pending bootstrap material", encoding="utf-8")

    assert main([
        "--credentials",
        str(credentials),
        "--global-key",
        str(global_key),
        "--state",
        str(state),
        "--cleanup-file",
        str(generation_module),
        "--enrollment-mode",
        "LEGACY_ADOPTION",
        "--legacy-onenet-secret-file",
        str(legacy_secret),
        "--proc-swaps",
        str(proc_swaps),
    ]) == 0

    assert credentials.exists()
    assert not global_key.exists()
    assert not state.exists()
    assert not generation_module.exists()
    assert not legacy_secret.exists()


def test_active_swap_blocks_cleanup_before_factory_key_is_touched(
    tmp_path: Path,
) -> None:
    credentials = tmp_path / "device-credentials.json"
    credentials.write_text(json.dumps(valid_document()), encoding="utf-8")
    credentials.chmod(0o600)
    global_key = tmp_path / "enrollment.key"
    global_key.write_text("factory-key", encoding="utf-8")
    proc_swaps = tmp_path / "proc-swaps"
    proc_swaps.write_text(
        "Filename Type Size Used Priority\n"
        "/swapfile file 1048572 0 -2\n",
        encoding="ascii",
    )

    with pytest.raises(SecretMemoryGuardError, match="active swap"):
        main([
            "--credentials",
            str(credentials),
            "--global-key",
            str(global_key),
            "--proc-swaps",
            str(proc_swaps),
        ])

    assert global_key.read_text(encoding="utf-8") == "factory-key"
