from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "lib"
    / "stage_signed_runtime_payload.py"
)


def _load_stage_module():
    spec = importlib.util.spec_from_file_location(
        "ecobin_stage_signed_runtime_payload_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_stage_audits_venv_before_writing_private_completion_marker(
    monkeypatch,
    tmp_path,
):
    stage = _load_stage_module()
    destination = tmp_path / "runtime"
    marker = destination / ".venv" / ".ecobin-install-complete"
    calls: list[str] = []

    monkeypatch.setattr(stage.sys, "platform", "linux")
    monkeypatch.setattr(stage.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        stage,
        "verified_archive_stream",
        lambda *_args, **_kwargs: contextlib.nullcontext(io.BytesIO()),
    )

    def extract(_stream, target, **_kwargs):
        (target / ".venv" / "bin").mkdir(parents=True)
        return "runtime-test-1"

    monkeypatch.setattr(stage, "safe_extract_archive_stream", extract)
    monkeypatch.setattr(stage, "validate_release_tree", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        stage, "harden_installed_venv_permissions", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(stage, "_run", lambda *_args, **_kwargs: None)

    def audit(*_args, **_kwargs):
        if marker.exists():
            raise RuntimeError("private completion marker was written before venv audit")
        calls.append("audit")

    def write_marker(*_args, **_kwargs):
        calls.append("write-marker")
        marker.write_text("private\n", encoding="ascii")

    def validate_marker(*_args, **_kwargs):
        assert marker.is_file()
        calls.append("validate-marker")

    monkeypatch.setattr(stage, "audit_installed_venv", audit)
    monkeypatch.setattr(stage, "write_install_complete_marker", write_marker)
    monkeypatch.setattr(
        stage,
        "validate_install_complete_marker",
        validate_marker,
        raising=False,
    )

    result = stage.main(
        [
            "--archive",
            str(tmp_path / "runtime.tar.gz"),
            "--expected-sha256",
            "a" * 64,
            "--signature",
            str(tmp_path / "runtime.sig"),
            "--signing-key-id",
            "runtime_test",
            "--trusted-public-keys-directory",
            str(tmp_path / "trust"),
            "--release-id",
            "runtime-test-1",
            "--destination",
            str(destination),
        ]
    )

    assert result == 0
    assert calls == ["audit", "write-marker", "validate-marker"]
