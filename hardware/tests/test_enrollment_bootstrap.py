from __future__ import annotations

import json
from pathlib import Path

import pytest

from enrollment_bootstrap import main
from device_enrollment import EnrollmentRejectedError, EnrollmentRetryableError
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
    progress = tmp_path / "enrollment-progress.json"
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
        "--progress",
        str(progress),
    ]) == 0

    assert credentials.exists()
    assert not global_key.exists()
    assert not state.exists()
    assert not generation_module.exists()
    assert not legacy_secret.exists()
    assert json.loads(progress.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "phase": "COMPLETE",
        "lastErrorCode": None,
        "retryable": False,
    }


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
    progress = tmp_path / "enrollment-progress.json"

    with pytest.raises(SecretMemoryGuardError, match="active swap"):
        main([
            "--credentials",
            str(credentials),
            "--global-key",
            str(global_key),
            "--proc-swaps",
            str(proc_swaps),
            "--progress",
            str(progress),
        ])

    assert global_key.read_text(encoding="utf-8") == "factory-key"
    assert json.loads(progress.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "phase": "FAILED",
        "lastErrorCode": "ACTIVE_SWAP_DETECTED",
        "retryable": False,
    }


def test_k1_cleanup_failure_is_projected_without_secret_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials = tmp_path / "device-credentials.json"
    credentials.write_text(json.dumps(valid_document()), encoding="utf-8")
    credentials.chmod(0o600)
    global_key = tmp_path / "enrollment.key"
    global_key.write_text("private-k1-material", encoding="utf-8")
    proc_swaps = tmp_path / "proc-swaps"
    proc_swaps.write_text(
        "Filename\tType\tSize\tUsed\tPriority\n",
        encoding="ascii",
    )
    progress = tmp_path / "enrollment-progress.json"

    def fail_cleanup(_path) -> None:
        raise OSError("private-k1-material cannot be removed")

    monkeypatch.setattr(
        "enrollment_bootstrap.unlink_and_fsync",
        fail_cleanup,
    )

    with pytest.raises(OSError, match="cannot be removed"):
        main([
            "--credentials",
            str(credentials),
            "--global-key",
            str(global_key),
            "--proc-swaps",
            str(proc_swaps),
            "--progress",
            str(progress),
        ])

    content = progress.read_text(encoding="utf-8")
    assert json.loads(content) == {
        "schemaVersion": 1,
        "phase": "FAILED",
        "lastErrorCode": "K1_CLEANUP_FAILED",
        "retryable": False,
    }
    assert "private-k1-material" not in content


@pytest.mark.parametrize(
    ("failure", "phase", "retryable"),
    (
        (
            EnrollmentRetryableError(
                "private backend response",
                code="ENROLLMENT_NETWORK_UNAVAILABLE",
            ),
            "RETRY_WAIT",
            True,
        ),
        (
            EnrollmentRejectedError(
                "private backend response",
                code="ENROLLMENT_BACKEND_REJECTED",
            ),
            "FAILED",
            False,
        ),
    ),
)
def test_enrollment_failure_boundary_projects_only_stable_code(
    tmp_path,
    monkeypatch,
    failure,
    phase,
    retryable,
):
    proc_swaps = tmp_path / "proc-swaps"
    proc_swaps.write_text(
        "Filename\tType\tSize\tUsed\tPriority\n",
        encoding="ascii",
    )
    progress = tmp_path / "enrollment-progress.json"

    def fail(_self):
        raise failure

    monkeypatch.setattr("device_enrollment.DeviceEnrollmentClient.run_once", fail)

    with pytest.raises(type(failure), match="private backend response"):
        main([
            "--backend-url",
            "https://backend.example.com/private",
            "--credentials",
            str(tmp_path / "credentials.json"),
            "--state",
            str(tmp_path / "state.json"),
            "--global-key",
            str(tmp_path / "enrollment.key"),
            "--ssh-host-private-key",
            str(tmp_path / "ssh-host-key"),
            "--ssh-host-public-key",
            str(tmp_path / "ssh-host-key.pub"),
            "--proc-swaps",
            str(proc_swaps),
            "--progress",
            str(progress),
        ])

    document = json.loads(progress.read_text(encoding="utf-8"))
    assert document == {
        "schemaVersion": 1,
        "phase": phase,
        "lastErrorCode": failure.code,
        "retryable": retryable,
    }
    assert "backend.example.com" not in progress.read_text(encoding="utf-8")
