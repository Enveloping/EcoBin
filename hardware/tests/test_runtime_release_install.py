from __future__ import annotations

import hashlib
import io
import os
import stat
import subprocess
import sys
import tarfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from install import build_runtime_release, install_runtime_release, runtime_release
from install.build_runtime_release import (
    _copy_runtime_sources,
    _load_signing_private_key,
    _sign_archive,
    _write_deterministic_archive,
    _write_manifest,
)
from edge_store import CURRENT_SCHEMA_VERSION
from install.runtime_release import (
    ACTIVATION_JOURNAL_NAME,
    ARTIFACT_KIND,
    EDGE_SCHEMA_VERSION,
    FORBIDDEN_RUNTIME_NAMES,
    INSTALL_COMPLETE_MARKER,
    PYTHON_SERIES,
    RELEASE_FORMAT_VERSION,
    RUNTIME_APP_FILES,
    ReleaseValidationError,
    activate_with_rollback,
    activation_journal_path,
    audit_installed_venv,
    harden_installed_venv_permissions,
    harden_venv_permissions,
    nonblocking_install_lock,
    recover_pending_activation,
    runtime_allowlist_sha256,
    safe_extract_archive,
    sha256_file,
    validate_release_tree,
    validate_install_complete_marker,
    validate_signing_key_id,
    verified_archive_stream,
    write_install_complete_marker,
    write_sha256sums,
)


def _make_release(
    root: Path,
    release_id: str = "test-1",
    *,
    schema_version: str = EDGE_SCHEMA_VERSION,
) -> Path:
    root.mkdir()
    app = root / "app"
    wheelhouse = root / "wheelhouse"
    app.mkdir()
    wheelhouse.mkdir()
    for name in RUNTIME_APP_FILES:
        target = app / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {name}\n", encoding="utf-8")
    (wheelhouse / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel")
    (root / "requirements-runtime.txt").write_text(
        "demo==1.0\n",
        encoding="utf-8",
    )
    (root / "requirements-offline.txt").write_text(
        "demo==1.0 --hash=sha256:"
        f"{hashlib.sha256(b'wheel').hexdigest()}\n",
        encoding="utf-8",
    )
    (root / "manifest.env").write_text(
        f"ECOBIN_RELEASE_FORMAT_VERSION={RELEASE_FORMAT_VERSION}\n"
        f"ECOBIN_ARTIFACT_KIND={ARTIFACT_KIND}\n"
        f"ECOBIN_RELEASE_ID={release_id}\n"
        f"ECOBIN_GIT_COMMIT={'a' * 40}\n"
        f"ECOBIN_PYTHON_SERIES={PYTHON_SERIES}\n"
        f"ECOBIN_EDGE_SCHEMA_VERSION={schema_version}\n"
        "ECOBIN_SOURCE_DATE_EPOCH=1\n"
        "ECOBIN_RUNTIME_ALLOWLIST_SHA256="
        f"{runtime_allowlist_sha256()}\n",
        encoding="utf-8",
    )
    (root / "release.env").write_text(
        f"ECOBIN_EDGE_VERSION={release_id}\n",
        encoding="utf-8",
    )
    write_sha256sums(root)
    return root


def _write_tar(path: Path, members: list[tarfile.TarInfo]) -> None:
    with tarfile.open(path, "w:gz") as stream:
        for member in members:
            payload = None
            if member.isfile():
                payload = io.BytesIO(b"x" * member.size)
            stream.addfile(member, payload)


def _posix_owner() -> tuple[int, int]:
    if os.name != "posix":
        pytest.skip("ownership and link semantics are verified on POSIX")
    return os.getuid(), os.getgid()


def _make_test_venv(release: Path, trusted_python: Path) -> Path:
    venv = release / ".venv"
    (venv / "bin").mkdir(parents=True)
    trusted_python.write_text("python-3.11\n", encoding="utf-8")
    os.chmod(trusted_python, 0o755)
    launcher = venv / "bin" / "python"
    launcher.write_text("launcher\n", encoding="utf-8")
    os.chmod(launcher, 0o755)
    return venv


def _write_signing_material(
    root: Path,
    archive: Path,
    *,
    key_id: str = "factory_2026",
) -> dict[str, object]:
    root.mkdir()
    private_key = Ed25519PrivateKey.generate()
    private_path = root / "private.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(private_path, 0o600)
    trust = root / "trusted"
    trust.mkdir()
    os.chmod(trust, 0o755)
    public_path = trust / f"{key_id}.pem"
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    os.chmod(public_path, 0o644)
    signature = root / "archive.sig"
    signature.write_bytes(private_key.sign(archive.read_bytes()))
    os.chmod(signature, 0o644)
    uid = os.getuid() if os.name == "posix" else 0
    gid = os.getgid() if os.name == "posix" else 0
    return {
        "signature": signature,
        "signing_key_id": key_id,
        "trusted_public_keys_directory": trust,
        "expected_uid": uid,
        "expected_gid": gid,
        "private_key": private_key,
        "private_path": private_path,
    }


def _verification_kwargs(material: dict[str, object]) -> dict[str, object]:
    return {
        name: material[name]
        for name in (
            "signature",
            "signing_key_id",
            "trusted_public_keys_directory",
            "expected_uid",
            "expected_gid",
        )
    }


def _verified_stream_kwargs(material: dict[str, object]) -> dict[str, object]:
    values = _verification_kwargs(material)
    values["signature_path"] = values.pop("signature")
    return values


def test_runtime_source_allowlist_is_exact_and_excludes_privileged_flows():
    assert len(RUNTIME_APP_FILES) == len(set(RUNTIME_APP_FILES))
    assert "device_credentials.py" in RUNTIME_APP_FILES
    assert "remote_support_control.py" in RUNTIME_APP_FILES
    assert "system/mcu_safe_gpio.py" in RUNTIME_APP_FILES
    assert not FORBIDDEN_RUNTIME_NAMES.intersection(
        Path(name).name for name in RUNTIME_APP_FILES
    )


def test_runtime_payload_imports_real_dependencies_outside_repository(
    tmp_path: Path,
):
    hardware_root = Path(__file__).resolve().parents[1]
    app = tmp_path / "isolated-release" / "app"
    _copy_runtime_sources(hardware_root, app)
    program = r"""
import importlib
import os
from pathlib import Path
import sys

app = Path(sys.argv[1]).resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(app))
os.chdir(app.parent)
for name in (
    "main",
    "cloud_transport",
    "direct_onenet_transport",
    "business_message_handler",
    "business_outbox_relay",
    "device_identity",
    "mqtt_client",
    "command_processor",
    "factory_seal.admission",
    "onenet_wire",
    "fixed_frame_mcu_adapter",
    "simulated_camera",
    "system.mcu_safe_gpio",
    "device_credentials",
):
    importlib.import_module(name)
"""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", program, str(app)],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr


def test_release_manifest_schema_matches_edge_store_current_schema():
    assert EDGE_SCHEMA_VERSION == str(CURRENT_SCHEMA_VERSION)


def test_builder_git_identity_tracks_release_manifest_and_tooling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    hardware_root = repository / "hardware"
    hardware_root.mkdir(parents=True)
    outputs = iter(
        [
            str(repository),
            "",
            "a" * 40,
            "1",
        ]
    )
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=next(outputs))

    monkeypatch.setattr(build_runtime_release, "_run", fake_run)

    assert build_runtime_release._git_metadata(hardware_root) == ("a" * 40, 1)
    status_command = calls[1]
    exact_safe_directory = f"safe.directory={repository.resolve()}"
    for command in calls:
        assert exact_safe_directory in command
        assert "safe.directory=*" not in command
    for name in build_runtime_release.RUNTIME_RELEASE_BUILD_FILES:
        assert str((hardware_root / name).relative_to(repository)) in status_command


def test_builder_manifest_declares_current_edge_schema(tmp_path):
    release = tmp_path / "release"
    release.mkdir()

    _write_manifest(
        release,
        release_id="test-1",
        git_commit="a" * 40,
        source_date_epoch=1,
    )

    assert f"ECOBIN_EDGE_SCHEMA_VERSION={EDGE_SCHEMA_VERSION}\n" in (
        release / "manifest.env"
    ).read_text(encoding="utf-8")


def test_release_tree_requires_exact_files_and_complete_checksums(tmp_path):
    release = _make_release(tmp_path / "release")

    manifest = validate_release_tree(release, expected_release_id="test-1")

    assert manifest["ECOBIN_RELEASE_ID"] == "test-1"
    assert manifest["ECOBIN_EDGE_SCHEMA_VERSION"] == EDGE_SCHEMA_VERSION
    (release / "app" / "device_enrollment.py").write_text(
        "forbidden\n",
        encoding="utf-8",
    )
    write_sha256sums(release)
    with pytest.raises(ReleaseValidationError, match="exact allowlist"):
        validate_release_tree(release)


def test_release_tree_rejects_a_modified_file(tmp_path):
    release = _make_release(tmp_path / "release")
    (release / "app" / "main.py").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ReleaseValidationError, match="checksum mismatch"):
        validate_release_tree(release)


def test_archive_bytes_are_deterministic_for_the_same_release_tree(tmp_path):
    release = _make_release(tmp_path / "release")
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"

    _write_deterministic_archive(
        release,
        first,
        release_id="test-1",
        source_date_epoch=1,
    )
    _write_deterministic_archive(
        release,
        second,
        release_id="test-1",
        source_date_epoch=1,
    )

    assert first.read_bytes() == second.read_bytes()


def test_builder_loads_external_pkcs8_ed25519_key_and_emits_raw_signature(
    tmp_path,
):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"deterministic archive bytes")
    material = _write_signing_material(tmp_path / "signing", archive)

    loaded = _load_signing_private_key(material["private_path"])
    signature = _sign_archive(archive, loaded)

    assert len(signature) == 64
    material["private_key"].public_key().verify(signature, archive.read_bytes())


def test_builder_signing_falls_back_for_legacy_cryptography_mmap_rejection(
    tmp_path,
):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"legacy cryptography compatibility")
    private_key = Ed25519PrivateKey.generate()

    class LegacyPrivateKey:
        def sign(self, message):
            if not isinstance(message, bytes):
                raise TypeError("legacy backend requires bytes")
            return private_key.sign(message)

    signature = _sign_archive(archive, LegacyPrivateKey())

    private_key.public_key().verify(signature, archive.read_bytes())


def test_builder_publishes_detached_sig_without_copying_private_key(
    monkeypatch,
    tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "edge_store.py").write_text(f"CURRENT_SCHEMA_VERSION = {EDGE_SCHEMA_VERSION}\n", encoding="utf-8")
    unsigned = tmp_path / "unsigned-input"
    unsigned.write_bytes(b"seed")
    material = _write_signing_material(tmp_path / "signing", unsigned)
    output = tmp_path / "output"

    monkeypatch.setattr(
        build_runtime_release,
        "_require_arm64_python311_builder",
        lambda: None,
    )
    monkeypatch.setattr(
        build_runtime_release,
        "_git_metadata",
        lambda _source: ("a" * 40, 1),
    )
    monkeypatch.setattr(
        build_runtime_release,
        "_copy_runtime_sources",
        lambda _source, app: app.mkdir(parents=True),
    )
    monkeypatch.setattr(
        build_runtime_release,
        "_export_runtime_requirements",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_runtime_release,
        "_build_wheelhouse",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_runtime_release,
        "_write_offline_requirements",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_runtime_release,
        "validate_release_tree",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        build_runtime_release,
        "_verify_offline_environment",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        build_runtime_release,
        "_write_deterministic_archive",
        lambda _root, archive, **_kwargs: archive.write_bytes(
            b"final deterministic archive"
        ),
    )

    archive, checksum, signature = build_runtime_release.build_release(
        source_root=source,
        output_directory=output,
        release_id="test-1",
        signing_private_key=material["private_path"],
    )

    assert signature.name == "ecobin-hardware-test-1.tar.gz.sig"
    assert len(signature.read_bytes()) == 64
    material["private_key"].public_key().verify(
        signature.read_bytes(),
        archive.read_bytes(),
    )
    assert checksum.read_text(encoding="ascii") == (
        f"{sha256_file(archive)}  {archive.name}\n"
    )
    assert material["private_path"].name not in archive.read_bytes().decode(
        "ascii"
    )
    assert not any(path.name == material["private_path"].name for path in output.iterdir())


def test_private_signing_key_rejects_link_wide_permissions_and_non_ed25519(
    tmp_path,
):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    private_path = material["private_path"]
    linked = tmp_path / "linked-private.pem"
    try:
        linked.symlink_to(private_path)
    except OSError as error:
        pytest.skip(f"directory does not permit symlink tests: {error}")

    with pytest.raises(RuntimeError, match="securely readable") as linked_error:
        _load_signing_private_key(linked)
    assert str(linked) not in str(linked_error.value)

    if os.name == "posix":
        os.chmod(private_path, 0o640)
        with pytest.raises(RuntimeError, match="securely readable"):
            _load_signing_private_key(private_path)
        os.chmod(private_path, 0o600)

    rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    os.chmod(private_path, 0o600)
    with pytest.raises(RuntimeError, match="must be Ed25519"):
        _load_signing_private_key(private_path)


def test_verified_archive_stream_accepts_matching_ed25519_signature(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)

    with verified_archive_stream(
        archive,
        expected_sha256=sha256_file(archive),
        **_verified_stream_kwargs(material),
    ) as verified:
        assert verified.read() == b"signed archive"


def test_verified_archive_stream_falls_back_for_legacy_cryptography(
    tmp_path,
    monkeypatch,
):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"legacy verification compatibility")
    material = _write_signing_material(tmp_path / "signing", archive)
    real_public_key = material["private_key"].public_key()

    class LegacyPublicKey:
        def verify(self, signature, message):
            if not isinstance(message, bytes):
                raise TypeError("legacy backend requires bytes")
            return real_public_key.verify(signature, message)

    monkeypatch.setattr(
        runtime_release.serialization,
        "load_pem_public_key",
        lambda _pem: LegacyPublicKey(),
    )
    monkeypatch.setattr(runtime_release, "Ed25519PublicKey", LegacyPublicKey)

    with verified_archive_stream(
        archive,
        expected_sha256=sha256_file(archive),
        **_verified_stream_kwargs(material),
    ) as verified:
        assert verified.read() == b"legacy verification compatibility"


def test_digest_failure_happens_before_signature_or_release_store_access(
    tmp_path,
):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"tampered archive")
    releases = tmp_path / "must-not-be-created" / "releases"

    with pytest.raises(ReleaseValidationError, match="SHA-256 mismatch"):
        install_runtime_release.stage_archive(
            archive=archive,
            expected_sha256=hashlib.sha256(b"original archive").hexdigest(),
            signature=tmp_path / "signature-must-not-be-opened",
            signing_key_id="factory_2026",
            trusted_public_keys_directory=tmp_path / "trust-must-not-be-opened",
            releases_directory=releases,
            current_link=releases.parent / "current",
        )

    assert not releases.exists()


def test_invalid_signature_is_rejected_before_release_store_write(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    material["signature"].write_bytes(b"x" * 64)
    releases = tmp_path / "must-not-be-created" / "releases"

    with pytest.raises(ReleaseValidationError, match="signature is invalid"):
        install_runtime_release.stage_archive(
            archive=archive,
            expected_sha256=sha256_file(archive),
            releases_directory=releases,
            current_link=releases.parent / "current",
            **_verification_kwargs(material),
        )

    assert not releases.exists()


def test_archive_tamper_with_updated_digest_still_fails_signature(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"original signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    archive.write_bytes(b"tampered archive with a new digest")

    with pytest.raises(ReleaseValidationError, match="signature is invalid"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **_verified_stream_kwargs(material),
        ):
            pytest.fail("tampered archive must not be yielded")


def test_signature_rejects_link_hardlink_and_wrong_size(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    signature = material["signature"]
    linked = tmp_path / "linked.sig"
    try:
        linked.symlink_to(signature)
    except OSError as error:
        pytest.skip(f"directory does not permit symlink tests: {error}")

    kwargs = _verified_stream_kwargs(material)
    kwargs["signature_path"] = linked
    with pytest.raises(ReleaseValidationError, match="no-follow"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **kwargs,
        ):
            pytest.fail("linked signature must not be accepted")

    hardlink = tmp_path / "hardlink.sig"
    os.link(signature, hardlink)
    kwargs["signature_path"] = signature
    with pytest.raises(ReleaseValidationError, match="single-link"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **kwargs,
        ):
            pytest.fail("hard-linked signature must not be accepted")
    hardlink.unlink()

    signature.write_bytes(b"short")
    with pytest.raises(ReleaseValidationError, match="size is invalid"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **kwargs,
        ):
            pytest.fail("short signature must not be accepted")


def test_public_key_rejects_link_hardlink_and_unsafe_permissions(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    trust = material["trusted_public_keys_directory"]
    key_id = material["signing_key_id"]
    public_path = trust / f"{key_id}.pem"
    real_public = tmp_path / "real-public.pem"
    public_path.replace(real_public)
    try:
        public_path.symlink_to(real_public)
    except OSError as error:
        pytest.skip(f"directory does not permit symlink tests: {error}")

    with pytest.raises(ReleaseValidationError, match="no-follow"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **_verified_stream_kwargs(material),
        ):
            pytest.fail("linked public key must not be accepted")

    public_path.unlink()
    os.link(real_public, public_path)
    with pytest.raises(ReleaseValidationError, match="single-link"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **_verified_stream_kwargs(material),
        ):
            pytest.fail("hard-linked public key must not be accepted")
    public_path.unlink()
    real_public.replace(public_path)

    if os.name == "posix":
        os.chmod(public_path, 0o666)
        with pytest.raises(ReleaseValidationError, match="permissions are unsafe"):
            with verified_archive_stream(
                archive,
                expected_sha256=sha256_file(archive),
                **_verified_stream_kwargs(material),
            ):
                pytest.fail("writable public key must not be accepted")


def test_trust_directory_must_be_owned_and_not_writable(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    trust = material["trusted_public_keys_directory"]

    if os.name == "posix":
        os.chmod(trust, 0o777)
        with pytest.raises(ReleaseValidationError, match="permissions are unsafe"):
            with verified_archive_stream(
                archive,
                expected_sha256=sha256_file(archive),
                **_verified_stream_kwargs(material),
            ):
                pytest.fail("writable trust directory must not be accepted")

    kwargs = _verified_stream_kwargs(material)
    kwargs["expected_uid"] = int(kwargs["expected_uid"]) + 1
    with pytest.raises(ReleaseValidationError, match="owner is invalid"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **kwargs,
        ):
            pytest.fail("wrong trust owner must not be accepted")


def test_public_key_size_limit_is_enforced(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    public_path = (
        material["trusted_public_keys_directory"]
        / f"{material['signing_key_id']}.pem"
    )
    public_path.write_bytes(b"x" * 16_385)

    with pytest.raises(ReleaseValidationError, match="size is invalid"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **_verified_stream_kwargs(material),
        ):
            pytest.fail("oversized public key must not be accepted")


def test_wrong_or_non_ed25519_public_key_is_rejected(tmp_path):
    archive = tmp_path / "archive.tar.gz"
    archive.write_bytes(b"signed archive")
    material = _write_signing_material(tmp_path / "signing", archive)
    public_path = (
        material["trusted_public_keys_directory"]
        / f"{material['signing_key_id']}.pem"
    )
    wrong_key = Ed25519PrivateKey.generate()
    public_path.write_bytes(
        wrong_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    with pytest.raises(ReleaseValidationError, match="signature is invalid"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **_verified_stream_kwargs(material),
        ):
            pytest.fail("wrong key must not yield archive")

    rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
    public_path.write_bytes(
        rsa_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    with pytest.raises(ReleaseValidationError, match="not Ed25519"):
        with verified_archive_stream(
            archive,
            expected_sha256=sha256_file(archive),
            **_verified_stream_kwargs(material),
        ):
            pytest.fail("non-Ed25519 key must not yield archive")


@pytest.mark.parametrize(
    "key_id",
    ["", "Factory", "../factory", "factory.pem", "factory/key", "a" * 65],
)
def test_signing_key_id_is_strict(key_id):
    with pytest.raises(ReleaseValidationError, match="key ID"):
        validate_signing_key_id(key_id)


def test_builder_archive_round_trips_through_safe_extraction(tmp_path):
    release = _make_release(tmp_path / "release")
    archive = tmp_path / "release.tar.gz"
    extracted = tmp_path / "extracted"
    _write_deterministic_archive(
        release,
        archive,
        release_id="test-1",
        source_date_epoch=1,
    )

    release_id = safe_extract_archive(
        archive,
        extracted,
        expected_sha256=sha256_file(archive),
    )

    assert release_id == "test-1"
    assert validate_release_tree(extracted)["ECOBIN_RELEASE_ID"] == "test-1"


@pytest.mark.skipif(os.name != "posix", reason="POSIX umask semantics are required")
def test_safe_extraction_canonicalizes_descendants_under_private_umask(tmp_path):
    release = _make_release(tmp_path / "release")
    archive = tmp_path / "release.tar.gz"
    extracted = tmp_path / "extracted"
    _write_deterministic_archive(
        release,
        archive,
        release_id="test-1",
        source_date_epoch=1,
    )

    previous_umask = os.umask(0o077)
    try:
        safe_extract_archive(
            archive,
            extracted,
            expected_sha256=sha256_file(archive),
        )
    finally:
        os.umask(previous_umask)

    # The caller may keep the root private until publication, but every
    # descendant must already be traversable when that root becomes current.
    assert stat.S_IMODE(extracted.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o755
        for path in extracted.rglob("*")
        if path.is_dir() and not path.is_symlink()
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX umask semantics are required")
def test_safe_extraction_canonicalizes_implicit_parent_directories(tmp_path):
    archive = tmp_path / "implicit-directories.tar.gz"
    member = tarfile.TarInfo(
        "ecobin-hardware-test-1/app/implicit/package/module.py"
    )
    member.size = 1
    _write_tar(archive, [member])
    extracted = tmp_path / "extracted"

    previous_umask = os.umask(0o077)
    try:
        safe_extract_archive(
            archive,
            extracted,
            expected_sha256=sha256_file(archive),
        )
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(extracted.stat().st_mode) == 0o700
    assert all(
        stat.S_IMODE((extracted / relative).stat().st_mode) == 0o755
        for relative in ("app", "app/implicit", "app/implicit/package")
    )
    assert stat.S_IMODE(
        (extracted / "app/implicit/package/module.py").stat().st_mode
    ) == 0o644


def test_release_tree_rejects_links_or_special_files(tmp_path):
    release = _make_release(tmp_path / "release")
    link = release / "app" / "linked.py"
    try:
        link.symlink_to(release / "app" / "main.py")
    except OSError as error:
        pytest.skip(f"directory does not permit symlink tests: {error}")

    with pytest.raises(ReleaseValidationError, match="link or special"):
        write_sha256sums(release)


def test_venv_audit_accepts_only_expected_internal_and_trusted_python_links(
    tmp_path,
):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    (venv / "bin" / "python").unlink()
    (venv / "lib").mkdir()
    (venv / "lib64").symlink_to("lib", target_is_directory=True)
    (venv / "bin" / "python3").symlink_to(trusted)
    (venv / "bin" / "python").symlink_to("python3")

    audit_installed_venv(
        release,
        trusted_python_targets=[trusted],
        expected_uid=uid,
        expected_gid=gid,
    )


def test_venv_audit_rejects_a_python_link_to_an_untrusted_path(tmp_path):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    untrusted = tmp_path / "other-python-3.11"
    venv = _make_test_venv(release, trusted)
    untrusted.write_text("other\n", encoding="utf-8")
    os.chmod(untrusted, 0o755)
    (venv / "bin" / "python").unlink()
    (venv / "bin" / "python").symlink_to(untrusted)

    with pytest.raises(ReleaseValidationError, match="trusted Python boundary"):
        audit_installed_venv(
            release,
            trusted_python_targets=[trusted],
            expected_uid=uid,
            expected_gid=gid,
        )


def test_venv_audit_rejects_group_writable_and_hardlinked_files(tmp_path):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    unsafe = venv / "unsafe.py"
    unsafe.write_text("unsafe\n", encoding="utf-8")
    os.chmod(unsafe, 0o664)

    with pytest.raises(ReleaseValidationError, match="unsafe permissions"):
        audit_installed_venv(
            release,
            trusted_python_targets=[trusted],
            expected_uid=uid,
            expected_gid=gid,
        )

    os.chmod(unsafe, 0o644)
    os.link(unsafe, venv / "hardlink.py")
    with pytest.raises(ReleaseValidationError, match="hard-linked"):
        audit_installed_venv(
            release,
            trusted_python_targets=[trusted],
            expected_uid=uid,
            expected_gid=gid,
        )


def test_venv_permission_hardening_removes_wide_write_bits(tmp_path):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    package = venv / "lib" / "package"
    package.mkdir(parents=True)
    module = package / "module.py"
    module.write_text("value = 1\n", encoding="utf-8")
    os.chmod(package, 0o775)
    os.chmod(module, 0o664)

    harden_venv_permissions(
        venv,
        expected_uid=uid,
        expected_gid=gid,
    )

    assert stat.S_IMODE(package.stat().st_mode) == 0o755
    assert stat.S_IMODE(module.stat().st_mode) == 0o644
    audit_installed_venv(
        release,
        trusted_python_targets=[trusted],
        expected_uid=uid,
        expected_gid=gid,
    )


def test_venv_permission_hardening_repairs_restrictive_builder_umask(tmp_path):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    package = venv / "lib" / "package"
    package.mkdir(parents=True)
    module = package / "module.py"
    module.write_text("value = 1\n", encoding="utf-8")
    executable = venv / "bin" / "helper"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(venv, 0o700)
    os.chmod(venv / "bin", 0o700)
    os.chmod(package, 0o700)
    os.chmod(module, 0o600)
    os.chmod(executable, 0o700)

    harden_venv_permissions(
        venv,
        expected_uid=uid,
        expected_gid=gid,
    )

    assert stat.S_IMODE(venv.stat().st_mode) == 0o755
    assert stat.S_IMODE((venv / "bin").stat().st_mode) == 0o755
    assert stat.S_IMODE(package.stat().st_mode) == 0o755
    assert stat.S_IMODE(module.stat().st_mode) == 0o644
    assert stat.S_IMODE(executable.stat().st_mode) == 0o755
    audit_installed_venv(
        release,
        trusted_python_targets=[trusted],
        expected_uid=uid,
        expected_gid=gid,
    )


def test_venv_audit_rejects_restrictive_builder_umask(tmp_path):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    os.chmod(venv, 0o700)

    with pytest.raises(ReleaseValidationError, match="not canonical"):
        audit_installed_venv(
            release,
            trusted_python_targets=[trusted],
            expected_uid=uid,
            expected_gid=gid,
        )


def test_venv_permission_hardening_rejects_hardlinks_before_chmod(tmp_path):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    outside = tmp_path / "outside.py"
    outside.write_text("must not be chmodded\n", encoding="utf-8")
    os.chmod(outside, 0o664)
    os.link(outside, venv / "hardlink.py")

    with pytest.raises(ReleaseValidationError, match="hard-linked"):
        harden_installed_venv_permissions(
            release,
            expected_uid=uid,
            expected_gid=gid,
        )

    assert stat.S_IMODE(outside.stat().st_mode) == 0o664


def test_venv_audit_rejects_special_and_unexpected_link_entries(tmp_path):
    uid, gid = _posix_owner()
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    os.mkfifo(venv / "pipe")

    with pytest.raises(ReleaseValidationError, match="special filesystem"):
        audit_installed_venv(
            release,
            trusted_python_targets=[trusted],
            expected_uid=uid,
            expected_gid=gid,
        )

    (venv / "pipe").unlink()
    (venv / "unexpected").symlink_to("bin")
    with pytest.raises(ReleaseValidationError, match="unexpected symbolic"):
        audit_installed_venv(
            release,
            trusted_python_targets=[trusted],
            expected_uid=uid,
            expected_gid=gid,
        )


def test_venv_audit_rejects_a_hardlinked_symlink(tmp_path):
    uid, gid = _posix_owner()
    release = tmp_path / "release"
    trusted = tmp_path / "trusted-python-3.11"
    venv = _make_test_venv(release, trusted)
    (venv / "bin" / "python").unlink()
    python3 = venv / "bin" / "python3"
    python311 = venv / "bin" / "python3.11"
    python3.symlink_to(trusted)
    try:
        os.link(python3, python311, follow_symlinks=False)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"filesystem cannot hard-link a symbolic link: {error}")

    with pytest.raises(ReleaseValidationError, match="hard-linked symbolic"):
        audit_installed_venv(
            release,
            trusted_python_targets=[trusted],
            expected_uid=uid,
            expected_gid=gid,
        )


@pytest.mark.parametrize("member_type", [tarfile.SYMTYPE, tarfile.FIFOTYPE])
def test_archive_rejects_links_and_special_members(
    tmp_path,
    member_type,
):
    archive = tmp_path / "release.tar.gz"
    top = tarfile.TarInfo("ecobin-hardware-test-1")
    top.type = tarfile.DIRTYPE
    malicious = tarfile.TarInfo("ecobin-hardware-test-1/app/bad")
    malicious.type = member_type
    if member_type == tarfile.SYMTYPE:
        malicious.linkname = "/etc/passwd"
    _write_tar(archive, [top, malicious])

    with pytest.raises(ReleaseValidationError, match="link or special"):
        safe_extract_archive(
            archive,
            tmp_path / "extract",
            expected_sha256=sha256_file(archive),
        )


@pytest.mark.parametrize(
    "member_name",
    [
        "../escape",
        "/absolute",
        "ecobin-hardware-test-1/../../escape",
        "ecobin-hardware-test-1\\escape",
    ],
)
def test_archive_rejects_path_traversal(tmp_path, member_name):
    archive = tmp_path / "release.tar.gz"
    member = tarfile.TarInfo(member_name)
    member.size = 1
    _write_tar(archive, [member])

    with pytest.raises(ReleaseValidationError, match="path|POSIX"):
        safe_extract_archive(
            archive,
            tmp_path / "extract",
            expected_sha256=sha256_file(archive),
        )
    assert not (tmp_path / "escape").exists()


def test_archive_sha_is_verified_before_extraction(tmp_path):
    archive = tmp_path / "release.tar.gz"
    _write_tar(archive, [tarfile.TarInfo("ecobin-hardware-test-1")])

    with pytest.raises(ReleaseValidationError, match="SHA-256 mismatch"):
        safe_extract_archive(
            archive,
            tmp_path / "extract",
            expected_sha256="0" * 64,
        )
    assert not (tmp_path / "extract").exists()


def test_install_complete_marker_is_private_bound_to_release_and_checksums(
    tmp_path,
):
    uid, gid = _posix_owner()
    release = _make_release(tmp_path / "release")
    (release / ".venv").mkdir()

    marker = write_install_complete_marker(
        release,
        "test-1",
        expected_uid=uid,
        expected_gid=gid,
    )

    assert marker.name == INSTALL_COMPLETE_MARKER
    assert marker.stat().st_mode & 0o777 == 0o600
    validate_install_complete_marker(
        release,
        "test-1",
        expected_uid=uid,
        expected_gid=gid,
    )
    marker.write_text("forged\n", encoding="utf-8")
    with pytest.raises(ReleaseValidationError, match="invalid marker"):
        validate_install_complete_marker(
            release,
            "test-1",
            expected_uid=uid,
            expected_gid=gid,
        )


def test_install_complete_marker_rejects_missing_or_hardlinked_marker(tmp_path):
    uid, gid = _posix_owner()
    release = _make_release(tmp_path / "release")
    (release / ".venv").mkdir()

    with pytest.raises(ReleaseValidationError, match="incomplete"):
        validate_install_complete_marker(
            release,
            "test-1",
            expected_uid=uid,
            expected_gid=gid,
        )

    marker = write_install_complete_marker(
        release,
        "test-1",
        expected_uid=uid,
        expected_gid=gid,
    )
    os.link(marker, release / ".venv" / "marker-hardlink")
    with pytest.raises(ReleaseValidationError, match="regular private"):
        validate_install_complete_marker(
            release,
            "test-1",
            expected_uid=uid,
            expected_gid=gid,
        )


def test_failed_health_check_restores_current_without_touching_edge_store(
    tmp_path,
):
    if os.name != "posix":
        pytest.skip("atomic POSIX symlink replacement is verified on Linux")
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "old", "old")
    _make_release(releases / "new", "new")
    current = tmp_path / "opt" / "current"
    try:
        os.symlink(
            os.path.join("releases", "old"),
            current,
            target_is_directory=True,
        )
    except OSError as error:
        pytest.skip(f"directory does not permit symlink tests: {error}")
    edge_store = tmp_path / "var" / "lib" / "ecobin" / "hardware" / "edge.db"
    edge_store.parent.mkdir(parents=True)
    edge_store.write_bytes(b"authoritative-edge-state")
    recovered: list[str] = []

    def fail_health(_: str) -> None:
        raise RuntimeError("new runtime failed")

    with pytest.raises(RuntimeError, match="current rolled back"):
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="new",
            health_check=fail_health,
            rollback_health_check=recovered.append,
            expected_uid=uid,
            expected_gid=gid,
        )

    assert current.resolve() == (releases / "old").resolve()
    assert recovered == ["old"]
    assert edge_store.read_bytes() == b"authoritative-edge-state"
    assert not activation_journal_path(current).exists()


def test_power_loss_after_switch_is_recovered_without_running_target(tmp_path):
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "old", "old")
    _make_release(releases / "new", "new")
    _make_release(releases / "third", "third")
    current = tmp_path / "opt" / "current"
    os.symlink(os.path.join("releases", "old"), current)

    class SimulatedPowerLoss(BaseException):
        pass

    with pytest.raises(SimulatedPowerLoss):
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="new",
            health_check=lambda _release: (_ for _ in ()).throw(
                SimulatedPowerLoss()
            ),
            rollback_health_check=lambda _release: None,
            expected_uid=uid,
            expected_gid=gid,
        )

    assert current.resolve() == (releases / "new").resolve()
    assert activation_journal_path(current).name == ACTIVATION_JOURNAL_NAME
    with pytest.raises(ReleaseValidationError, match="must be recovered"):
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="third",
            health_check=lambda _release: pytest.fail("target must not run"),
            rollback_health_check=lambda _release: None,
            expected_uid=uid,
            expected_gid=gid,
        )
    recovered: list[str | None] = []
    result = recover_pending_activation(
        releases_directory=releases,
        current_link=current,
        health_check=recovered.append,
        expected_uid=uid,
        expected_gid=gid,
    )

    assert result == "old"
    assert recovered == ["old"]
    assert current.resolve() == (releases / "old").resolve()
    assert not activation_journal_path(current).exists()


def test_initial_install_power_loss_recovery_removes_current_and_checks_stop(
    tmp_path,
):
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "new", "new")
    current = tmp_path / "opt" / "current"

    class SimulatedPowerLoss(BaseException):
        pass

    with pytest.raises(SimulatedPowerLoss):
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="new",
            health_check=lambda _release: (_ for _ in ()).throw(
                SimulatedPowerLoss()
            ),
            rollback_health_check=lambda _release: None,
            expected_uid=uid,
            expected_gid=gid,
        )
    assert current.resolve() == (releases / "new").resolve()

    checked: list[str | None] = []
    assert (
        recover_pending_activation(
            releases_directory=releases,
            current_link=current,
            health_check=checked.append,
            expected_uid=uid,
            expected_gid=gid,
        )
        is None
    )
    assert checked == [None]
    assert not os.path.lexists(current)
    assert not activation_journal_path(current).exists()


def test_activation_rejects_schema_change_before_writing_journal(tmp_path):
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "old", "old", schema_version="13")
    _make_release(releases / "new", "new", schema_version="14")
    current = tmp_path / "opt" / "current"
    os.symlink(os.path.join("releases", "old"), current)

    with pytest.raises(ReleaseValidationError, match="across edge schema"):
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="new",
            health_check=lambda _release: pytest.fail("must not start target"),
            rollback_health_check=lambda _release: None,
            expected_uid=uid,
            expected_gid=gid,
        )

    assert current.resolve() == (releases / "old").resolve()
    assert not activation_journal_path(current).exists()


def test_initial_activation_allows_schema_14_and_clears_journal(tmp_path):
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "new", "new")
    current = tmp_path / "opt" / "current"
    started: list[str] = []

    assert (
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="new",
            health_check=started.append,
            rollback_health_check=lambda _release: None,
            expected_uid=uid,
            expected_gid=gid,
        )
        is None
    )
    assert started == ["new"]
    assert current.resolve() == (releases / "new").resolve()
    assert not activation_journal_path(current).exists()


def test_failed_rollback_health_keeps_pending_journal_for_boot_recovery(
    tmp_path,
):
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "old", "old")
    _make_release(releases / "new", "new")
    current = tmp_path / "opt" / "current"
    os.symlink(os.path.join("releases", "old"), current)

    def fail(_release):
        raise RuntimeError("health failed")

    with pytest.raises(RuntimeError, match="previous release did not recover"):
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="new",
            health_check=fail,
            rollback_health_check=fail,
            expected_uid=uid,
            expected_gid=gid,
        )

    assert current.resolve() == (releases / "old").resolve()
    assert activation_journal_path(current).exists()


def test_activation_journal_is_durable_before_current_switch(monkeypatch, tmp_path):
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "old", "old")
    _make_release(releases / "new", "new")
    current = tmp_path / "opt" / "current"
    os.symlink(os.path.join("releases", "old"), current)

    class SimulatedPowerLoss(BaseException):
        pass

    def lose_power_before_switch(*_args):
        journal = activation_journal_path(current)
        assert journal.is_file()
        assert journal.stat().st_mode & 0o777 == 0o600
        raise SimulatedPowerLoss()

    monkeypatch.setattr(
        runtime_release,
        "_atomic_set_current",
        lose_power_before_switch,
    )
    with pytest.raises(SimulatedPowerLoss):
        activate_with_rollback(
            releases_directory=releases,
            current_link=current,
            release_id="new",
            health_check=lambda _release: pytest.fail("target must not run"),
            rollback_health_check=lambda _release: None,
            expected_uid=uid,
            expected_gid=gid,
        )

    assert current.resolve() == (releases / "old").resolve()
    assert activation_journal_path(current).exists()


def test_nonblocking_install_lock_rejects_a_concurrent_installer(tmp_path):
    uid, gid = _posix_owner()
    current = tmp_path / "opt" / "current"

    with nonblocking_install_lock(
        current,
        expected_uid=uid,
        expected_gid=gid,
    ):
        with pytest.raises(RuntimeError, match="another runtime installation"):
            with nonblocking_install_lock(
                current,
                expected_uid=uid,
                expected_gid=gid,
            ):
                pytest.fail("second installer must not acquire the lock")

    lock = current.parent / ".ecobin-runtime-install.lock"
    assert lock.stat().st_mode & 0o777 == 0o600


def test_current_release_without_complete_marker_is_locked(tmp_path):
    _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    _make_release(releases / "current-release", "current-release")
    (releases / "current-release" / ".venv").mkdir()
    current = tmp_path / "opt" / "current"
    os.symlink(os.path.join("releases", "current-release"), current)
    source = _make_release(tmp_path / "source", "candidate")
    archive = tmp_path / "candidate.tar.gz"
    _write_deterministic_archive(
        source,
        archive,
        release_id="candidate",
        source_date_epoch=1,
    )
    signing = _write_signing_material(tmp_path / "signing", archive)

    with pytest.raises(ReleaseValidationError, match="incomplete"):
        install_runtime_release.stage_archive(
            archive=archive,
            expected_sha256=sha256_file(archive),
            releases_directory=releases,
            current_link=current,
            **_verification_kwargs(signing),
        )


def test_noncurrent_incomplete_release_can_be_safely_removed_for_rebuild(
    tmp_path,
):
    uid, gid = _posix_owner()
    releases = tmp_path / "opt" / "releases"
    releases.mkdir(parents=True)
    incomplete = releases / "new"
    (incomplete / ".venv" / "lib").mkdir(parents=True)
    (incomplete / ".venv" / "lib" / "partial").write_text(
        "partial\n",
        encoding="utf-8",
    )

    install_runtime_release._remove_incomplete_final(
        incomplete,
        releases,
        expected_uid=uid,
        expected_gid=gid,
    )

    assert not incomplete.exists()


def test_existing_complete_release_is_audited_and_smoked_from_final_path(
    monkeypatch,
    tmp_path,
):
    source = _make_release(tmp_path / "source")
    archive = tmp_path / "release.tar.gz"
    _write_deterministic_archive(
        source,
        archive,
        release_id="test-1",
        source_date_epoch=1,
    )
    releases = tmp_path / "releases"
    releases.mkdir()
    final = _make_release(releases / "test-1")
    (final / ".venv").mkdir()
    (final / ".venv" / INSTALL_COMPLETE_MARKER).write_text(
        "marker-present\n",
        encoding="utf-8",
    )
    calls: list[tuple[str, Path]] = []
    signing = _write_signing_material(tmp_path / "signing", archive)
    monkeypatch.setattr(
        install_runtime_release,
        "validate_install_complete_marker",
        lambda path, _release: calls.append(("marker", path)),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_audit_installed_environment",
        lambda path: calls.append(("audit", path)),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_pip_check_installed_release",
        lambda path: calls.append(("pip", path)),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_smoke_installed_release",
        lambda path: calls.append(("smoke", path)),
    )

    assert (
        install_runtime_release.stage_archive(
            archive=archive,
            expected_sha256=sha256_file(archive),
            releases_directory=releases,
            current_link=tmp_path / "current",
            **_verification_kwargs(signing),
        )
        == "test-1"
    )
    assert calls == [
        ("marker", final),
        ("audit", final),
        ("pip", final),
        ("smoke", final),
    ]


@pytest.mark.skipif(
    sys.version_info[:2] != (3, 11),
    reason="runtime installer targets Python 3.11",
)
def test_offline_install_hardens_permissions_before_dependency_check(
    monkeypatch,
    tmp_path,
):
    release = tmp_path / "release"
    calls: list[str] = []
    monkeypatch.setattr(
        install_runtime_release,
        "_run",
        lambda *_args, **kwargs: calls.append(kwargs["operation"]),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "harden_installed_venv_permissions",
        lambda path: calls.append(f"harden:{path.name}"),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_pip_check_installed_release",
        lambda path: calls.append(f"check:{path.name}"),
    )

    install_runtime_release._prepare_offline_environment(release)

    assert calls == [
        "Python virtual environment creation",
        "offline dependency installation",
        "harden:release",
        "check:release",
    ]


def test_new_release_is_smoked_before_audit_and_again_before_marker(
    monkeypatch,
    tmp_path,
):
    source = _make_release(tmp_path / "source")
    archive = tmp_path / "release.tar.gz"
    _write_deterministic_archive(
        source,
        archive,
        release_id="test-1",
        source_date_epoch=1,
    )
    releases = tmp_path / "releases"
    calls: list[tuple[str, str]] = []
    signing = _write_signing_material(tmp_path / "signing", archive)

    def prepare(path: Path) -> None:
        (path / ".venv" / "bin").mkdir(parents=True)
        (path / ".venv" / "bin" / "python").write_text(
            "python\n",
            encoding="utf-8",
        )
        calls.append(("prepare", path.name))

    monkeypatch.setattr(install_runtime_release, "_prepare_offline_environment", prepare)
    monkeypatch.setattr(
        install_runtime_release,
        "_smoke_installed_release",
        lambda path: calls.append(("smoke", path.name)),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_audit_installed_environment",
        lambda path: calls.append(("audit", path.name)),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "fsync_release_tree",
        lambda path: calls.append(("fsync", path.name)),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_fsync_directory",
        lambda _path: None,
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_pip_check_installed_release",
        lambda path: calls.append(("pip", path.name)),
    )

    def marker(path: Path, _release: str) -> None:
        calls.append(("marker", path.name))

    monkeypatch.setattr(install_runtime_release, "write_install_complete_marker", marker)
    monkeypatch.setattr(
        install_runtime_release,
        "validate_install_complete_marker",
        lambda path, _release: calls.append(("validate-marker", path.name)),
    )

    assert (
        install_runtime_release.stage_archive(
            archive=archive,
            expected_sha256=sha256_file(archive),
            releases_directory=releases,
            current_link=tmp_path / "current",
            **_verification_kwargs(signing),
        )
        == "test-1"
    )
    assert [name for name, _path in calls] == [
        "prepare",
        "smoke",
        "audit",
        "fsync",
        "audit",
        "pip",
        "smoke",
        "marker",
        "validate-marker",
    ]
    assert calls[-4:] == [
        ("pip", "test-1"),
        ("smoke", "test-1"),
        ("marker", "test-1"),
        ("validate-marker", "test-1"),
    ]


def test_installer_cli_holds_fixed_private_umask_for_the_full_stage(monkeypatch):
    if os.name != "posix" or not sys.platform.startswith("linux"):
        pytest.skip("installer CLI is Linux-only")
    observed: list[int] = []

    def stage(**_kwargs):
        current = os.umask(0o077)
        os.umask(current)
        observed.append(current)
        return "test-1"

    monkeypatch.setattr(install_runtime_release.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        install_runtime_release,
        "nonblocking_install_lock",
        lambda _current: nullcontext(),
    )
    monkeypatch.setattr(install_runtime_release, "stage_archive", stage)

    assert (
        install_runtime_release.main(
            [
                "/trusted/release.tar.gz",
                "--expected-sha256",
                "0" * 64,
                "--signature",
                "/trusted/release.sig",
                "--signing-key-id",
                "factory_2026",
                "--trusted-public-keys-directory",
                "/etc/ecobin/trusted-runtime-keys",
                "--stage-only",
            ]
        )
        == 0
    )
    assert observed == [0o077]


def test_recover_pending_cli_never_requires_or_stages_an_archive(monkeypatch):
    if os.name != "posix" or not sys.platform.startswith("linux"):
        pytest.skip("installer CLI is Linux-only")
    recovered: list[dict] = []
    monkeypatch.setattr(install_runtime_release.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        install_runtime_release,
        "nonblocking_install_lock",
        lambda _current: nullcontext(),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "recover_activation",
        lambda **kwargs: recovered.append(kwargs) or "old",
    )
    monkeypatch.setattr(
        install_runtime_release,
        "stage_archive",
        lambda **_kwargs: pytest.fail("recovery must not stage an archive"),
    )

    assert install_runtime_release.main(["--recover-pending"]) == 0
    assert len(recovered) == 1


def test_recovery_cli_rejects_all_release_and_signing_inputs(monkeypatch):
    if os.name != "posix" or not sys.platform.startswith("linux"):
        pytest.skip("installer CLI is Linux-only")
    monkeypatch.setattr(install_runtime_release.os, "geteuid", lambda: 0)

    with pytest.raises(SystemExit, match="does not accept release or signing"):
        install_runtime_release.main(
            [
                "--recover-pending",
                "--signature",
                "/secret/signature.sig",
            ]
        )


def test_install_cli_requires_detached_signature_and_trust_selection(monkeypatch):
    if os.name != "posix" or not sys.platform.startswith("linux"):
        pytest.skip("installer CLI is Linux-only")
    monkeypatch.setattr(install_runtime_release.os, "geteuid", lambda: 0)

    with pytest.raises(SystemExit, match="requires archive, digest, signature"):
        install_runtime_release.main(
            [
                "/trusted/archive.tar.gz",
                "--expected-sha256",
                "0" * 64,
            ]
        )


def test_installer_source_never_names_or_removes_the_edge_store():
    source = (
        Path(__file__).resolve().parents[1]
        / "install"
        / "install_runtime_release.py"
    ).read_text(encoding="utf-8")

    assert "/var/lib/ecobin/hardware/edge.db" not in source
    assert "ecobin-remote-support.service" not in source


def test_service_restart_accepts_counter_reset_by_explicit_restart(monkeypatch):
    calls = []
    systemd_counter = {"value": 7}

    def run(argv, **kwargs):
        calls.append(("run", argv, kwargs))
        systemd_counter["value"] = 0

    def snapshot(service):
        calls.append(("snapshot", service, systemd_counter["value"]))
        return {
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "4321",
            "NRestarts": str(systemd_counter["value"]),
            "InvocationID": "new-invocation",
        }

    clock = iter([0.0, 0.0, 0.2])
    monkeypatch.setattr(install_runtime_release, "_run", run)
    monkeypatch.setattr(install_runtime_release, "_service_snapshot", snapshot)
    monkeypatch.setattr(
        install_runtime_release.time,
        "monotonic",
        lambda: next(clock),
    )

    install_runtime_release.restart_and_verify_service(
        "ecobin-hardware.service",
        timeout_seconds=1,
        stable_seconds=0.1,
    )

    assert calls[0][0:2] == (
        "run",
        ["systemctl", "restart", "ecobin-hardware.service"],
    )
    assert calls[0][2]["timeout_seconds"] == (
        install_runtime_release.SYSTEMCTL_RESTART_TIMEOUT_SECONDS
    )
    assert calls[1:] == [
        ("snapshot", "ecobin-hardware.service", 0),
        ("snapshot", "ecobin-hardware.service", 0),
    ]


def test_restart_timeout_covers_stop_start_and_scheduler_margin():
    assert install_runtime_release.SYSTEMCTL_RESTART_TIMEOUT_SECONDS >= 225
    help_text = " ".join(
        install_runtime_release.build_parser().format_help().split()
    )
    assert "post-READY stability-window" in help_text
    assert "not the total install" in help_text


def test_service_health_rejects_a_changed_invocation_id(monkeypatch):
    snapshots = iter(
        [
            {
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": "4321",
                "NRestarts": "0",
                "InvocationID": "invocation-a",
            },
            {
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": "8765",
                "NRestarts": "0",
                "InvocationID": "invocation-b",
            },
        ]
    )

    monkeypatch.setattr(
        install_runtime_release,
        "_service_snapshot",
        lambda _service: next(snapshots),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_run",
        lambda _argv, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="different systemd invocation"):
        install_runtime_release.restart_and_verify_service(
            "ecobin-hardware.service",
            timeout_seconds=1,
            stable_seconds=0.1,
        )


def test_service_health_rejects_an_incremented_restart_counter(monkeypatch):
    snapshots = iter(
        [
            {
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": "4321",
                "NRestarts": "0",
                "InvocationID": "invocation-a",
            },
            {
                "ActiveState": "active",
                "SubState": "running",
                "MainPID": "8765",
                "NRestarts": "1",
                "InvocationID": "invocation-a",
            },
        ]
    )

    monkeypatch.setattr(
        install_runtime_release,
        "_service_snapshot",
        lambda _service: next(snapshots),
    )
    monkeypatch.setattr(
        install_runtime_release,
        "_run",
        lambda _argv, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="automatic restart counter"):
        install_runtime_release.restart_and_verify_service(
            "ecobin-hardware.service",
            timeout_seconds=1,
            stable_seconds=0.1,
        )


def test_systemctl_restart_timeout_is_bounded_and_does_not_leak_command(
    monkeypatch,
):
    secret_service = "ecobin-secret-token.service"
    observed = {}

    def timeout(*args, **kwargs):
        observed["timeout"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(install_runtime_release.subprocess, "run", timeout)
    monkeypatch.setattr(
        install_runtime_release,
        "SYSTEMCTL_RESTART_TIMEOUT_SECONDS",
        2.5,
    )

    with pytest.raises(RuntimeError, match="service restart timed out") as caught:
        install_runtime_release.restart_and_verify_service(
            secret_service,
            timeout_seconds=2.5,
            stable_seconds=0.1,
        )

    assert observed["timeout"] == 2.5
    assert secret_service not in str(caught.value)


def test_child_process_failure_does_not_leak_argv_or_captured_output(monkeypatch):
    def fail(argv, **_kwargs):
        raise subprocess.CalledProcessError(
            1,
            argv,
            output="secret-output",
            stderr="secret-error",
        )

    monkeypatch.setattr(install_runtime_release.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="safe operation failed") as caught:
        install_runtime_release._run(
            ["tool", "secret-archive-path"],
            timeout_seconds=1,
            operation="safe operation",
        )

    message = str(caught.value)
    assert "secret-archive-path" not in message
    assert "secret-output" not in message
    assert "secret-error" not in message
