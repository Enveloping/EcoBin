"""Release builders must reject a stale schema declaration before any build work."""
import pytest

from install import build_business_release, build_runtime_release, runtime_payload_manifest


@pytest.mark.parametrize("builder", [build_runtime_release, build_business_release], ids=["runtime", "business"])
def test_builder_rejects_source_schema_mismatch_before_platform_keys_or_dependencies(tmp_path, builder):
    source = tmp_path / "hardware"
    source.mkdir()
    actual = int(runtime_payload_manifest.EDGE_SCHEMA_VERSION) + 1
    (source / "edge_store.py").write_text(f"CURRENT_SCHEMA_VERSION = {actual}\n", encoding="utf-8")
    output = tmp_path / "output"
    args = dict(source_root=source, output_directory=output,
        release_id="11111111-1111-4111-8111-111111111111",
        signing_private_key=tmp_path / "missing-signing-key.pem")
    if builder is build_business_release:
        args.update(version_name="1.0.0", release_sequence=1)
    with pytest.raises(RuntimeError, match=f"source schema {actual} differs from release manifest"):
        builder.build_release(**args)
    assert not output.exists()


def test_schema_check_rejects_a_second_annotated_declaration(tmp_path):
    expected = runtime_payload_manifest.EDGE_SCHEMA_VERSION
    (tmp_path / "edge_store.py").write_text(
        f"CURRENT_SCHEMA_VERSION = {expected}\nCURRENT_SCHEMA_VERSION: int = {int(expected) + 1}\n",
        encoding="utf-8")
    with pytest.raises(RuntimeError, match="one positive integer literal"):
        runtime_payload_manifest.verify_source_schema_version(tmp_path)


@pytest.mark.parametrize("annotation", ["", ": int"])
def test_schema_check_accepts_the_matching_literal_without_executing_source(tmp_path, annotation):
    expected = runtime_payload_manifest.EDGE_SCHEMA_VERSION
    (tmp_path / "edge_store.py").write_text(
        f"raise AssertionError('source must not execute')\nCURRENT_SCHEMA_VERSION{annotation} = {expected}\n",
        encoding="utf-8")
    assert runtime_payload_manifest.verify_source_schema_version(tmp_path) == int(expected)


@pytest.mark.parametrize("declaration", ["", "True", "0", "-1", "'25'", "25 + 0", "int('25')"])
def test_schema_check_refuses_missing_or_nonliteral_version(tmp_path, declaration):
    source = f"CURRENT_SCHEMA_VERSION = {declaration}\n" if declaration else "# no version\n"
    (tmp_path / "edge_store.py").write_text(source, encoding="utf-8")
    with pytest.raises(RuntimeError, match="one positive integer literal"):
        runtime_payload_manifest.verify_source_schema_version(tmp_path)


@pytest.mark.parametrize("content", [None, b"CURRENT_SCHEMA_VERSION = (", b"\xff"])
def test_schema_check_refuses_unreadable_source(tmp_path, content):
    if content is not None:
        (tmp_path / "edge_store.py").write_bytes(content)
    with pytest.raises(RuntimeError, match="cannot read source schema declaration"):
        runtime_payload_manifest.verify_source_schema_version(tmp_path)
