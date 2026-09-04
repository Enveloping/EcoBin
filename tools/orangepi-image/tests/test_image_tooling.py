from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOL_ROOT = pathlib.Path(__file__).resolve().parents[1]
VALIDATOR = TOOL_ROOT / "lib" / "validate_inputs.py"
MANIFEST_GENERATOR = TOOL_ROOT / "lib" / "generate_manifest.py"
ROOTFS_SANITIZER = TOOL_ROOT / "lib" / "sanitize_rootfs.py"
RAW_ASSEMBLER = TOOL_ROOT / "lib" / "assemble_raw_image.py"
EXT4_NORMALIZER = TOOL_ROOT / "lib" / "normalize_ext4_metadata.py"
EXT4_INODE_INVENTORY = TOOL_ROOT / "lib" / "capture_ext4_inode_inventory.py"


def run_command(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        cwd=TOOL_ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class ImageToolingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.fixture = pathlib.Path(self.temporary_directory.name) / "config"
        self.fixture.mkdir()
        shutil.copytree(TOOL_ROOT / "schemas", self.fixture / "schemas")
        shutil.copy2(
            TOOL_ROOT / "expand-rootfs.sh",
            self.fixture / "expand-rootfs.sh",
        )
        shutil.copy2(
            TOOL_ROOT / "formal-release-policy.json",
            self.fixture / "formal-release-policy.json",
        )

        source = json.loads((TOOL_ROOT / "source.lock.json").read_text(encoding="utf-8"))
        source["lockState"] = "LOCKED"
        source["reason"] = "Qualified test fixture."
        source["artifact"] = {
            "url": "https://example.invalid/base.img",
            "fileName": "base.img",
            "sha256": "1" * 64,
            "downloadBytes": 1_048_576,
            "archiveFormat": "raw",
            "imageMember": None,
            "extractedImageBytes": 1_048_576,
            "extractedImageSha256": "c" * 64,
        }
        self.write_json("source.lock.json", source)

        builder = json.loads((TOOL_ROOT / "builder.lock").read_text(encoding="utf-8"))
        builder["lockState"] = "LOCKED"
        builder["reason"] = "Qualified test fixture."
        digest = "sha256:" + ("2" * 64)
        builder["container"] = {
            "reference": "example.invalid/ecobin/image-builder@" + digest,
            "digest": digest,
            "platform": "linux/arm64",
        }
        builder["sourceDateEpoch"] = 1_700_000_000
        builder["tools"] = {key: "1.0.0-1" for key in builder["tools"]}
        builder["tools"]["uv"] = "1.0.0"
        builder["uvArtifact"] = {
            "version": "1.0.0",
            "url": "https://github.com/astral-sh/uv/releases/download/1.0.0/uv-aarch64-unknown-linux-gnu.tar.gz",
            "fileName": "uv-aarch64-unknown-linux-gnu.tar.gz",
            "downloadBytes": 1234,
            "sha256": "a" * 64,
        }
        self.write_json("builder.lock", builder)

        (self.fixture / "apt-packages.lock").write_text(
            "\n".join(
                (
                    "# ecobin-apt-packages-lock-v2",
                    "# lock-state: LOCKED",
                    "# reason: Qualified test fixture.",
                    "# repository-snapshot: https://snapshot.debian.org/archive/debian/20260822T000000Z/",
                    "# repository-key-fingerprint: " + ("A" * 40),
                    "python3:arm64=3.11.2-1+deb12u6 sha256=" + "b" * 64,
                    "",
                )
            ),
            encoding="utf-8",
            newline="\n",
        )

        layout = json.loads((TOOL_ROOT / "image-layout.json").read_text(encoding="utf-8"))
        layout["lockState"] = "LOCKED"
        layout["reason"] = "Qualified test fixture."
        layout["targetMedia"]["qualificationState"] = "QUALIFIED"
        layout["targetMedia"]["minimumQualifiedMediaBytes"] = 30_000_000_000
        self.target_media_evidence = (
            pathlib.Path(self.temporary_directory.name)
            / "target-media-qualification-evidence.json"
        )
        self.target_media_evidence_value = {
            "$schema": "./schemas/target-media-qualification-evidence.schema.json",
            "schemaVersion": 1,
            "artifactClass": "TARGET_MEDIA_BATCH_CAPACITY_QUALIFICATION",
            "qualificationState": "QUALIFIED",
            "method": "BLOCK_DEVICE_CAPACITY_SAMPLE_MINIMUM_V1",
            "deviceClass": "TF_CARD",
            "marketedCapacityGB": 32,
            "marketedCapacityBytes": 32_000_000_000,
            "batchId": "fixture-batch-001",
            "measuredAt": "2026-08-22T00:00:00Z",
            "measurementTool": "blockdev --getsize64",
            "sampleSelection": "SAME_PROCUREMENT_BATCH_MULTIPLE_CARDS",
            "minimumQualifiedMediaBytes": 30_000_000_000,
            "measurements": [
                {
                    "sampleId": "fixture-card-a",
                    "measuredBytes": 31_000_000_000,
                    "logicalSectorBytes": 512,
                    "wholeDevice": True,
                },
                {
                    "sampleId": "fixture-card-b",
                    "measuredBytes": 30_000_000_000,
                    "logicalSectorBytes": 512,
                    "wholeDevice": True,
                },
            ],
        }
        self.write_target_media_evidence()
        layout["targetMedia"]["evidenceSha256"] = hashlib.sha256(
            self.target_media_evidence.read_bytes()
        ).hexdigest()
        layout["compactImage"]["fixedRawImageBytes"] = 1_048_576
        layout["sourceGeometry"].update(
            {
                "rawImageBytes": 1_048_576,
                "rawImageSha256": "c" * 64,
                "diskIdentifier": "abcd1234",
                "bootPrefixBytes": 4096,
                "bootPrefixSha256": "e" * 64,
            }
        )
        layout["sourceGeometry"]["rootPartition"].update(
            {"number": 2, "startSector": 8, "sectorCount": 2040}
        )
        layout["rootFilesystem"].update(
            {
                "partitionNumber": 2,
                "filesystemUuid": "11111111-2222-3333-4444-555555555555",
                "partitionUuid": "abcd1234-02",
            }
        )
        layout["rootFilesystem"]["buildProfile"]["blockCount"] = 255
        layout["rootFilesystem"]["buildProfile"]["reservedBlockCount"] = 5
        layout["rootFilesystem"]["buildProfile"]["journalSizeBytes"] = 524_288
        layout["rootFilesystem"]["buildProfile"]["sourceDateEpoch"] = 1_700_000_000
        self.write_json("image-layout.json", layout)
        self.software_payload_lock = (
            pathlib.Path(self.temporary_directory.name)
            / "software-payload.lock.json"
        )
        self.software_payload_lock.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "lockState": "LOCKED",
                    "payloadId": "payload-001",
                    "sourceGitCommit": "a" * 40,
                    "components": {
                        "hardwareRuntime": {
                            "releaseId": "hardwareruntime-001",
                            "root": "components/hardware-runtime",
                        },
                        "enrollment": {
                            "releaseId": "enrollment-001",
                            "venv": "components/enrollment-venv",
                        },
                        "remoteSupport": {
                            "releaseId": "remotesupport-001",
                            "venv": "components/remote-support-venv",
                        },
                        "factoryTest": {
                            "releaseId": "factorytest-001",
                            "venv": "components/factory-test-venv",
                        },
                        "firstBoot": {"releaseId": "firstboot-001"},
                        "communicationAgent": {
                            "releaseId": "communicationagent-001",
                            "root": "components/communication-agent",
                        },
                        "deviceUpdater": {
                            "releaseId": "deviceupdater-001",
                            "root": "components/device-updater",
                        },
                    },
                    "entries": [{"path": "fixture", "type": "directory", "mode": "0755"}],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.software_payload_sha256 = hashlib.sha256(
            self.software_payload_lock.read_bytes()
        ).hexdigest()

    def test_manifest_schemas_keep_exact_v1_and_v2_component_contracts(self) -> None:
        payload_schema = json.loads(
            (TOOL_ROOT / "schemas/software-payload-lock.schema.json").read_text(
                encoding="utf-8"
            )
        )
        candidate_schema = json.loads(
            (TOOL_ROOT / "schemas/image-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        release_schema = json.loads(
            (TOOL_ROOT / "schemas/release-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )

        legacy = {
            "hardwareRuntime",
            "enrollment",
            "remoteSupport",
            "factoryTest",
            "firstBoot",
        }
        current = legacy | {"communicationAgent", "deviceUpdater"}
        for schema in (payload_schema, candidate_schema, release_schema):
            self.assertNotIn("-v1", schema["$id"])
            self.assertEqual(schema["properties"]["schemaVersion"]["enum"], [1, 2])
            self.assertEqual(set(schema["$defs"]["componentsV1"]["required"]), legacy)
            self.assertEqual(set(schema["$defs"]["componentsV2"]["required"]), current)
            self.assertFalse(
                schema["$defs"]["componentsV1"]["additionalProperties"]
            )
            self.assertFalse(
                schema["$defs"]["componentsV2"]["additionalProperties"]
            )
        for schema in (candidate_schema, release_schema):
            self.assertNotIn(
                "payloadSchemaVersion",
                schema["$defs"]["softwareV1"]["properties"],
            )
            self.assertIn(
                "payloadSchemaVersion",
                schema["$defs"]["softwareV2"]["required"],
            )
            self.assertEqual(
                schema["$defs"]["softwareV2"]["properties"][
                    "payloadSchemaVersion"
                ],
                {"const": 2},
            )

    def write_json(self, name: str, value: object) -> None:
        (self.fixture / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def read_json(self, name: str) -> dict[str, object]:
        return json.loads((self.fixture / name).read_text(encoding="utf-8"))

    def write_target_media_evidence(self, *, update_layout_digest: bool = False) -> None:
        self.target_media_evidence.write_text(
            json.dumps(
                self.target_media_evidence_value,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if update_layout_digest and (self.fixture / "image-layout.json").exists():
            layout = self.read_json("image-layout.json")
            layout["targetMedia"]["evidenceSha256"] = hashlib.sha256(
                self.target_media_evidence.read_bytes()
            ).hexdigest()
            self.write_json("image-layout.json", layout)

    def validate_fixture(self) -> subprocess.CompletedProcess[str]:
        return run_command(
            sys.executable,
            str(VALIDATOR),
            "--config-dir",
            str(self.fixture),
            "--require-locked",
            "--target-media-qualification-evidence",
            str(self.target_media_evidence),
        )

    def test_checked_in_locks_are_valid_but_formal_policy_is_blocked(self) -> None:
        checked_in_evidence = TOOL_ROOT / "target-media-qualification-evidence.json"
        structural = run_command(
            sys.executable,
            str(VALIDATOR),
            "--config-dir",
            str(TOOL_ROOT),
            "--require-locked",
            "--target-media-qualification-evidence",
            str(checked_in_evidence),
        )
        self.assertEqual(structural.returncode, 0, structural.stderr)

        formal = run_command(
            sys.executable,
            str(TOOL_ROOT / "lib/release_trust.py"),
            "validate-policy",
            "--trust-policy",
            str(TOOL_ROOT / "formal-release-policy.json"),
        )
        self.assertEqual(formal.returncode, 2)
        self.assertRegex(
            formal.stderr,
            r"formal release policy (?:is not locked|must not be group/other writable)",
        )

    def test_repository_policy_cannot_be_promoted_into_a_trust_root(self) -> None:
        policy = self.read_json("formal-release-policy.json")
        policy["lockState"] = "LOCKED"
        policy["rootfsQualification"] = {
            "state": "QUALIFIED",
            "method": "DETERMINISTIC_EXT4_REBUILD_V1",
            "evidenceSha256": "e" * 64,
        }
        for role in ("buildAttestation", "sealEvidence", "releaseSigning"):
            policy[role] = {
                "keyId": role.lower(),
                "publicKeySha256": "f" * 64,
            }
        self.write_json("formal-release-policy.json", policy)
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 2)
        self.assertIn("only an UNLOCKED template", result.stderr)

    def test_digest_pinned_builder_launcher_uses_only_locked_snapshot(self) -> None:
        launcher = (TOOL_ROOT / "run-builder.sh").read_text(encoding="utf-8")
        bootstrap = (TOOL_ROOT / "bootstrap-builder.sh").read_text(
            encoding="utf-8"
        )
        wrapper = (TOOL_ROOT / "New-EcobinOrangePiImage.ps1").read_text(
            encoding="utf-8"
        )
        builder = json.loads(
            (TOOL_ROOT / "builder.lock").read_text(encoding="utf-8")
        )

        self.assertEqual(builder["container"]["platform"], "linux/arm64")
        self.assertRegex(
            builder["container"]["reference"],
            r"@sha256:[0-9a-f]{64}$",
        )
        self.assertIn('docker pull --platform "${builder_platform}"', launcher)
        self.assertIn("--privileged", launcher)
        self.assertIn("dst=/workspace,readonly", launcher)
        self.assertIn("dst=/input/${source_name},readonly", launcher)
        self.assertIn("dst=/software-payload,readonly", launcher)
        self.assertIn("bootstrap-builder.sh", launcher)
        self.assertIn(
            "snapshot.debian.org/archive/debian/20260803T000000Z",
            bootstrap,
        )
        self.assertIn("Acquire::Check-Valid-Until=false", bootstrap)
        self.assertIn("dpkg-query", bootstrap)
        self.assertNotIn("BuilderDigest", wrapper)
        self.assertIn("run-builder.sh", wrapper)

    def test_ca_less_builder_uses_two_phase_signed_https_snapshot(self) -> None:
        bootstrap = (TOOL_ROOT / "bootstrap-builder.sh").read_text(
            encoding="utf-8"
        )

        # debian:bookworm-slim does not contain a CA bundle.  The first HTTPS
        # transfer therefore disables only TLS peer verification while APT
        # still authenticates the immutable snapshot through signed InRelease
        # metadata.  After the exact CA package is installed, the builder must
        # rerun update with normal HTTPS certificate verification.
        self.assertIn(
            'snapshot_url="https://snapshot.debian.org/archive/'
            'debian/20260803T000000Z/"',
            bootstrap,
        )
        self.assertIn(
            "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg",
            bootstrap,
        )
        self.assertIn("Acquire::Retries=4", bootstrap)
        self.assertIn("Acquire::https::Verify-Peer=false", bootstrap)
        self.assertIn("ca-certificates=20230311+deb12u1", bootstrap)
        self.assertIn('apt-get "${bootstrap_apt_options[@]}" update', bootstrap)
        self.assertIn('apt-get "${apt_options[@]}" update', bootstrap)

    def test_runtime_release_has_a_locked_arm64_build_entry(self) -> None:
        launcher = (TOOL_ROOT / "run-runtime-builder.sh").read_text(
            encoding="utf-8"
        )
        bootstrap = (TOOL_ROOT / "bootstrap-builder.sh").read_text(
            encoding="utf-8"
        )
        builder = json.loads(
            (TOOL_ROOT / "builder.lock").read_text(encoding="utf-8")
        )

        self.assertEqual(builder["tools"]["pythonPip"], "23.0.1+dfsg-1")
        self.assertEqual(builder["tools"]["pythonVenv"], "3.11.2-1+b1")
        self.assertEqual(builder["tools"]["python311Venv"], "3.11.2-6+deb12u8")
        self.assertEqual(
            builder["tools"]["pythonCryptography"],
            "38.0.4-3+deb12u1",
        )
        self.assertIn("builder.lock", launcher)
        self.assertIn("dst=/workspace,readonly", launcher)
        self.assertIn("dst=/runtime-input/signing-private.pem,readonly", launcher)
        self.assertIn("formal runtime releases require a clean repository", launcher)
        self.assertIn("repository status could not be verified", launcher)
        self.assertIn("signing private key permissions are unsafe", launcher)
        self.assertIn("--runtime-release-only", launcher)
        self.assertNotIn("--privileged", launcher)
        self.assertIn("--runtime-release-only", bootstrap)
        self.assertIn("hardware/install/build_runtime_release.py", bootstrap)
        self.assertEqual(bootstrap.count("image_only_tools = {"), 2)
        self.assertIn('"qemuUserStatic",', bootstrap)
        self.assertIn(
            'if build_mode != "image" and key in image_only_tools:',
            bootstrap,
        )

    def test_target_package_installer_is_chrooted_pinned_and_service_safe(self) -> None:
        installer = (TOOL_ROOT / "install-target-packages.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--require-locked", installer)
        self.assertIn("target package installation requires an ARM64 builder", installer)
        self.assertIn("policy-rc.d", installer)
        self.assertIn("exit 101", installer)
        self.assertIn("offline deb set does not match", installer)
        self.assertIn("/usr/bin/dpkg --install", installer)
        self.assertIn("dpkg-query", installer)
        self.assertIn("dpkg --audit", installer)
        self.assertNotIn("apt-get", installer)
        self.assertNotIn("http://", installer)
        self.assertNotIn("https://", installer)
        self.assertNotIn("latest", installer.lower())

    def test_business_release_has_a_locked_arm64_build_entry(self) -> None:
        launcher = (TOOL_ROOT / "run-business-builder.sh").read_text(
            encoding="utf-8"
        )
        bootstrap = (TOOL_ROOT / "bootstrap-builder.sh").read_text(
            encoding="utf-8"
        )
        builder = (
            TOOL_ROOT.parents[1] / "hardware/install/build_business_release.py"
        ).read_text(encoding="utf-8")

        self.assertIn("builder.lock", launcher)
        self.assertIn("dst=/workspace,readonly", launcher)
        self.assertIn(
            "dst=/business-input/signing-private.pem,readonly", launcher
        )
        self.assertIn(
            "formal business releases require a clean repository", launcher
        )
        self.assertIn("repository status could not be verified", launcher)
        self.assertIn("signing private key permissions are unsafe", launcher)
        self.assertIn("--business-release-only", launcher)
        self.assertNotIn("--privileged", launcher)
        self.assertIn("--business-release-only", bootstrap)
        self.assertIn("hardware/install/build_business_release.py", bootstrap)
        self.assertEqual(bootstrap.count('"business-release",'), 2)
        self.assertIn('"business",', builder)
        self.assertIn("_verify_business_environment", builder)

    def test_target_image_contains_dns_and_trusted_time_runtime(self) -> None:
        package_lock = (TOOL_ROOT / "apt-packages.lock").read_text(
            encoding="utf-8"
        )
        verifier = (TOOL_ROOT / "verify-image.sh").read_text(encoding="utf-8")

        self.assertRegex(
            package_lock,
            r"(?m)^systemd-resolved:arm64=[^\s]+ sha256=[0-9a-f]{64}$",
        )
        self.assertRegex(
            package_lock,
            r"(?m)^chrony:arm64=[^\s]+ sha256=[0-9a-f]{64}$",
        )
        self.assertIn("usr/bin/resolvectl", verifier)
        self.assertIn("systemd-resolved.service", verifier)
        self.assertIn("usr/bin/chronyc", verifier)
        self.assertIn("usr/sbin/chronyd", verifier)
        self.assertIn("chrony.service", verifier)

    def test_controlled_payload_has_a_locked_arm64_build_entry(self) -> None:
        launcher = (TOOL_ROOT / "run-payload-builder.sh").read_text(encoding="utf-8")
        builder = (TOOL_ROOT / "build-software-payload.sh").read_text(encoding="utf-8")
        stage = (TOOL_ROOT / "lib/stage_signed_runtime_payload.py").read_text(
            encoding="utf-8"
        )
        lock_generator = (
            TOOL_ROOT / "lib/generate_software_payload_lock.py"
        ).read_text(encoding="utf-8")

        self.assertIn("builder.lock", launcher)
        self.assertIn("dst=/workspace,readonly", launcher)
        self.assertIn("runtime-trust,readonly", launcher)
        self.assertIn("business-trust,readonly", launcher)
        self.assertIn(
            "MCU, runtime and business trust directories must be separate",
            launcher,
        )
        self.assertIn("--software-payload-only", launcher)
        self.assertIn("repository status could not be verified", launcher)
        self.assertIn("uv sync", builder)
        self.assertIn("--frozen", builder)
        self.assertIn("--only-group", builder)
        self.assertIn("lib/harden_venv.py", builder)
        self.assertLess(builder.index("uv sync"), builder.index("lib/harden_venv.py"))
        self.assertIn('"uv ${expected_uv}"|"uv ${expected_uv} "*', builder)
        self.assertNotIn(
            '[[ "$(uv --version)" = "uv ${expected_uv}" ]]', builder
        )
        self.assertIn("stage_signed_runtime_payload.py", builder)
        self.assertIn("verified_archive_stream", stage)
        self.assertIn("safe_extract_archive_stream", stage)
        self.assertIn("load_and_validate_payload", lock_generator)
        self.assertIn("components/communication-agent", builder)
        self.assertIn("components/device-updater", builder)
        self.assertIn("components/device-updater/helpers", builder)
        self.assertIn("components/device-updater/systemd", builder)
        self.assertIn("business_runtime_cutover.py", builder)
        self.assertIn("business_runtime_cutover_state.py", builder)
        self.assertIn("device_management_preflight.py", builder)
        self.assertIn("updater_control_cli.py", builder)
        self.assertIn("business_activation_primitives.py", builder)
        self.assertIn("mcu_flash_primitives.py", builder)
        self.assertIn("mcu_flash_recovery.py", builder)
        self.assertIn("--communication-agent-release-id", launcher)
        self.assertIn("--device-updater-release-id", launcher)

    def test_container_git_allows_only_the_exact_read_only_repository(self) -> None:
        """Container root must read a non-root bind mount without trusting all repos."""

        for script_name, expected_calls in (
            ("build-software-payload.sh", 1),
            ("build-image.sh", 2),
        ):
            script = (TOOL_ROOT / script_name).read_text(encoding="utf-8")
            self.assertEqual(
                script.count('git -c safe.directory="${repository_root}"'),
                expected_calls,
            )
            self.assertNotIn("safe.directory=*", script)
            self.assertNotIn("safe.directory='*'", script)
            self.assertNotIn('safe.directory="*"', script)

    def test_complete_qualified_fixture_is_accepted(self) -> None:
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("source=LOCKED", result.stdout)
        self.assertIn("layout=LOCKED", result.stdout)

    def test_qualified_media_requires_the_actual_external_evidence_file(self) -> None:
        missing = run_command(
            sys.executable,
            str(VALIDATOR),
            "--config-dir",
            str(self.fixture),
            "--require-locked",
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("target media qualification evidence is required", missing.stderr)

        layout = self.read_json("image-layout.json")
        layout["targetMedia"]["evidenceSha256"] = "d" * 64
        self.write_json("image-layout.json", layout)
        mismatched = self.validate_fixture()
        self.assertEqual(mismatched.returncode, 2)
        self.assertIn("evidence digest differs", mismatched.stderr)

    def test_nested_locked_validation_consumes_the_snapshotted_evidence_environment(self) -> None:
        environment = os.environ.copy()
        environment["ECOBIN_TARGET_MEDIA_QUALIFICATION_EVIDENCE"] = str(
            self.target_media_evidence
        )
        nested = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--config-dir",
                str(self.fixture),
                "--require-locked",
            ],
            cwd=TOOL_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(nested.returncode, 0, nested.stderr)
        for script_name in (
            "build-image.sh",
            "attest-candidates.sh",
            "seal-image.sh",
            "release-image.sh",
        ):
            script = (TOOL_ROOT / script_name).read_text(encoding="utf-8")
            self.assertIn("ECOBIN_TARGET_MEDIA_QUALIFICATION_EVIDENCE", script)

    def test_media_evidence_minimum_must_equal_layout_and_measured_samples(self) -> None:
        self.target_media_evidence_value["minimumQualifiedMediaBytes"] = 30_500_000_000
        self.write_target_media_evidence(update_layout_digest=True)
        differs_from_samples = self.validate_fixture()
        self.assertEqual(differs_from_samples.returncode, 2)
        self.assertIn("minimum does not equal the smallest sample", differs_from_samples.stderr)

        self.target_media_evidence_value["minimumQualifiedMediaBytes"] = 31_000_000_000
        self.target_media_evidence_value["measurements"][1]["measuredBytes"] = 31_000_000_000
        self.write_target_media_evidence(update_layout_digest=True)
        differs_from_layout = self.validate_fixture()
        self.assertEqual(differs_from_layout.returncode, 2)
        self.assertIn("minimum differs from image layout", differs_from_layout.stderr)

    def test_media_evidence_accepts_explicit_single_card_owner_approval(self) -> None:
        self.target_media_evidence_value["artifactClass"] = (
            "TARGET_MEDIA_SINGLE_CARD_CAPACITY_QUALIFICATION"
        )
        self.target_media_evidence_value["method"] = (
            "WINDOWS_STORAGE_API_SINGLE_CARD_V1"
        )
        self.target_media_evidence_value["measurementTool"] = (
            "PowerShell Get-Disk.Size"
        )
        self.target_media_evidence_value["sampleSelection"] = (
            "SINGLE_CARD_PROJECT_OWNER_ACCEPTED"
        )
        self.target_media_evidence_value["measurements"] = [
            self.target_media_evidence_value["measurements"][0]
        ]
        self.target_media_evidence_value["minimumQualifiedMediaBytes"] = 31_000_000_000
        layout = self.read_json("image-layout.json")
        layout["targetMedia"]["minimumQualifiedMediaBytes"] = 31_000_000_000
        self.write_json("image-layout.json", layout)
        self.write_target_media_evidence(update_layout_digest=True)

        accepted = self.validate_fixture()

        self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_media_evidence_rejects_one_card_and_implausibly_small_capacity(self) -> None:
        self.target_media_evidence_value["measurements"] = [
            self.target_media_evidence_value["measurements"][0]
        ]
        self.target_media_evidence_value["minimumQualifiedMediaBytes"] = 31_000_000_000
        layout = self.read_json("image-layout.json")
        layout["targetMedia"]["minimumQualifiedMediaBytes"] = 31_000_000_000
        self.write_json("image-layout.json", layout)
        self.write_target_media_evidence(update_layout_digest=True)
        one_card = self.validate_fixture()
        self.assertEqual(one_card.returncode, 2)
        self.assertIn("at least two independently identified cards", one_card.stderr)

        second = copy.deepcopy(self.target_media_evidence_value["measurements"][0])
        second["sampleId"] = "fixture-card-b"
        second["measuredBytes"] = 20_000_000_000
        self.target_media_evidence_value["measurements"].append(second)
        self.target_media_evidence_value["minimumQualifiedMediaBytes"] = 20_000_000_000
        layout = self.read_json("image-layout.json")
        layout["targetMedia"]["minimumQualifiedMediaBytes"] = 20_000_000_000
        self.write_json("image-layout.json", layout)
        self.write_target_media_evidence(update_layout_digest=True)
        too_small = self.validate_fixture()
        self.assertEqual(too_small.returncode, 2)
        self.assertIn("implausibly small for marketed 32 GB media", too_small.stderr)

    def test_unlocked_source_cannot_hide_candidate_identity(self) -> None:
        source = self.read_json("source.lock.json")
        source["lockState"] = "UNLOCKED"
        self.write_json("source.lock.json", source)
        result = run_command(
            sys.executable,
            str(VALIDATOR),
            "--config-dir",
            str(self.fixture),
            "--allow-unlocked",
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("must keep all identity fields null", result.stderr)

    def test_zero_digest_and_latest_url_are_rejected(self) -> None:
        source = self.read_json("source.lock.json")
        source["artifact"]["sha256"] = "0" * 64
        self.write_json("source.lock.json", source)
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 2)
        self.assertIn("non-zero", result.stderr)

        source["artifact"]["sha256"] = "1" * 64
        source["artifact"]["url"] = "https://example.invalid/latest/base.img"
        self.write_json("source.lock.json", source)
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 2)
        self.assertIn("mutable", result.stderr)

    def test_layout_without_card_safety_margin_is_rejected(self) -> None:
        layout = self.read_json("image-layout.json")
        layout["targetMedia"]["minimumQualifiedMediaBytes"] = 1_048_576
        self.write_json("image-layout.json", layout)
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 2)
        self.assertIn("safety margin", result.stderr)

    def test_unqualified_media_cannot_unlock_formal_image_build(self) -> None:
        layout = self.read_json("image-layout.json")
        layout["targetMedia"].update(
            {
                "qualificationState": "UNQUALIFIED",
                "minimumQualifiedMediaBytes": None,
                "evidenceSha256": None,
            }
        )
        self.write_json("image-layout.json", layout)
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 2)
        self.assertIn("top layout UNLOCKED", result.stderr)

    def test_source_raw_digest_must_match_locked_geometry(self) -> None:
        layout = self.read_json("image-layout.json")
        layout["sourceGeometry"]["rawImageSha256"] = "f" * 64
        self.write_json("image-layout.json", layout)
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 2)
        self.assertIn("raw image SHA-256", result.stderr)

    def test_raw_assembler_writes_locked_prefix_and_complete_root(self) -> None:
        source = pathlib.Path(self.temporary_directory.name) / "source.img"
        root = pathlib.Path(self.temporary_directory.name) / "root.img"
        output = pathlib.Path(self.temporary_directory.name) / "output.img"
        prefix = bytes(range(256)) * 16
        root_payload = b"R" * 8192
        source.write_bytes(prefix + b"S" * len(root_payload))
        root.write_bytes(root_payload)
        layout_path = pathlib.Path(self.temporary_directory.name) / "assemble-layout.json"
        layout_path.write_text(
            json.dumps(
                {
                    "sourceGeometry": {
                        "bootPrefixBytes": len(prefix),
                        "bootPrefixSha256": hashlib.sha256(prefix).hexdigest(),
                        "logicalSectorBytes": 512,
                        "rootPartition": {"sectorCount": len(root_payload) // 512},
                    },
                    "compactImage": {"fixedRawImageBytes": len(prefix) + len(root_payload)},
                }
            ),
            encoding="utf-8",
        )
        result = run_command(
            sys.executable, str(RAW_ASSEMBLER), "--source-image", str(source),
            "--root-partition", str(root), "--output", str(output),
            "--layout", str(layout_path),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_bytes(), prefix + root_payload)
        repeated = run_command(
            sys.executable, str(RAW_ASSEMBLER), "--source-image", str(source),
            "--root-partition", str(root), "--output", str(output),
            "--layout", str(layout_path),
        )
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(output.read_bytes(), prefix + root_payload)

    def test_apt_lock_requires_exact_package_versions(self) -> None:
        path = self.fixture / "apt-packages.lock"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "python3:arm64=3.11.2-1+deb12u6 sha256=" + "b" * 64,
                "python3",
            ),
            encoding="utf-8",
            newline="\n",
        )
        result = self.validate_fixture()
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid exact apt package entry", result.stderr)

    def test_candidate_manifest_is_deterministic_and_contains_no_secret(self) -> None:
        image = pathlib.Path(self.temporary_directory.name) / "candidate.img"
        image.write_bytes(b"\0" * 1_048_576)
        outputs = []
        for index in (1, 2):
            output = pathlib.Path(self.temporary_directory.name) / f"manifest-{index}.json"
            result = run_command(
                sys.executable,
                str(MANIFEST_GENERATOR),
                "--config-dir",
                str(self.fixture),
                "--image",
                str(image),
                "--output",
                str(output),
                "--release-id",
                "fixture-001",
                "--version",
                "0.1.0",
                "--git-commit",
                "a" * 40,
                "--source-dirty",
                "false",
                "--software-payload-lock",
                str(self.software_payload_lock),
                "--software-payload-sha256",
                self.software_payload_sha256,
                "--rootfs-deterministic",
                "false",
                "--target-media-qualification-evidence",
                str(self.target_media_evidence),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append(output.read_bytes())
        self.assertEqual(outputs[0], outputs[1])
        manifest = json.loads(outputs[0])
        self.assertEqual(manifest["artifactClass"], "UNSIGNED_NO_SECRET_CANDIDATE")
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["software"]["payloadSchemaVersion"], 2)
        self.assertFalse(manifest["security"]["k1Injected"])
        self.assertFalse(manifest["security"]["setupApKeyInjected"])
        self.assertTrue(manifest["security"]["persistentSwapDisabled"])
        self.assertEqual(
            manifest["software"]["implementedSlices"],
            ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"],
        )
        self.assertTrue(manifest["software"]["runtimeInstalled"])
        self.assertTrue(manifest["software"]["factoryPortalInstalled"])
        self.assertFalse(
            manifest["software"]["buildReproducibility"]["releaseEligible"]
        )
        self.assertEqual(
            set(manifest["software"]["components"]),
            {
                "hardwareRuntime",
                "enrollment",
                "remoteSupport",
                "factoryTest",
                "firstBoot",
                "communicationAgent",
                "deviceUpdater",
            },
        )
        self.assertEqual(manifest["builder"]["platform"], "linux/arm64")
        self.assertEqual(manifest["builder"]["uvArtifact"]["version"], "1.0.0")
        self.assertTrue(manifest["software"]["uart5"]["configured"])
        self.assertEqual(manifest["software"]["uart5"]["device"], "/dev/ttyS5")
        self.assertEqual(
            manifest["software"]["mcuBootControl"]["resetGateWpi"],
            5,
        )
        self.assertRegex(
            manifest["software"]["mcuBootControl"]["safeHelperSha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertTrue(manifest["software"]["rootfsExpansion"]["installed"])
        self.assertEqual(
            manifest["software"]["rootfsExpansion"]["strategy"],
            "growpart-resize2fs",
        )
        self.assertEqual(
            manifest["artifacts"]["rawImageSha256"],
            hashlib.sha256(image.read_bytes()).hexdigest(),
        )
        serialized = outputs[0].lower()
        self.assertNotIn(b"password", serialized)
        self.assertNotIn(b"privatekey", serialized)

    def test_rootfs_sanitizer_removes_identity_and_locks_default_passwords(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "candidate-root"
        for directory in (
            "etc/ssh",
            "etc/default",
            "etc/NetworkManager/system-connections",
            "etc/systemd/system/getty@tty1.service.d",
            "etc/systemd/system/multi-user.target.wants",
            "var/lib/systemd",
            "var/lib/chrony",
            "var/lib/cloud/instance",
            "var/lib/ecobin/hardware/photos",
            "var/lib/ecobin/factory-test",
            "var/lib/ecobin/communication",
            "var/lib/ecobin/business",
            "var/lib/ecobin/updater",
            "root/EcoBin/hardware/data",
            "var/log",
            "var/cache/apt/archives",
            "var/lib/apt/lists",
            "tmp",
            "var/tmp",
        ):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "etc/shadow").write_text(
            "root:$6$known-root:1:2:3:4:5:6:7\n"
            "orangepi:$6$known-orangepi:1:2:3:4:5:6:7\n"
            "daemon:*:1:2:3:4:5:6:7\n",
            encoding="utf-8",
        )
        (root / "etc/passwd").write_text(
            "root:x:0:0:root:/root:/bin/bash\n"
            "orangepi:x:1000:1000:Orange Pi:/home/orangepi:/bin/bash\n",
            encoding="utf-8",
        )
        (root / "etc/machine-id").write_text("a" * 32 + "\n", encoding="ascii")
        (root / "etc/fstab").write_text(
            "UUID=root / ext4 defaults 0 1\n/swapfile none swap sw 0 0\n",
            encoding="utf-8",
        )
        (root / "swapfile").write_bytes(b"swap")
        (root / "etc/dphys-swapfile").write_text("CONF_SWAPSIZE=100\n", encoding="utf-8")
        (root / "etc/default/orangepi-zram-config").write_text("ENABLED=true\nSWAP=true\n", encoding="utf-8")
        (root / "etc/systemd/system/multi-user.target.wants/swapfile.swap").write_text(
            "[Swap]\nWhat=/swapfile\n", encoding="utf-8"
        )
        (root / "etc/systemd/system/multi-user.target.wants/orangepi-zram-config.service").write_text("[Unit]\n", encoding="utf-8")
        for name in (
            "ecobin-business-activation-helper.socket",
            "ecobin-mcu-flash-helper@fixture.service",
        ):
            (root / "etc/systemd/system/multi-user.target.wants" / name).write_text(
                "[Unit]\n",
                encoding="utf-8",
            )
        (root / "etc/ssh/ssh_host_ed25519_key").write_text(
            "fixture-host-key", encoding="ascii"
        )
        (root / "etc/NetworkManager/system-connections/customer.nmconnection").write_text(
            "[connection]\nautoconnect=true\n", encoding="utf-8"
        )
        getty_autologin = (
            root
            / "etc/systemd/system/getty@tty1.service.d/10-orangepi-autologin.conf"
        )
        getty_autologin.write_text(
            "[Service]\n"
            "ExecStart=\n"
            "ExecStart=-/sbin/agetty --autologin orangepi --noclear %I $TERM\n",
            encoding="utf-8",
        )
        (root / "var/lib/systemd/random-seed").write_text("seed", encoding="ascii")
        (root / "var/lib/chrony/chrony.drift").write_text(
            "12.345 0.100\n", encoding="ascii"
        )
        (root / "var/lib/cloud/instance/id").write_text("device", encoding="ascii")
        (root / "var/lib/ecobin/factory-test/result.json").write_text(
            "{}", encoding="ascii"
        )
        for state_directory, database in (
            ("communication", "communication.db"),
            ("business", "edge.db"),
            ("updater", "updater.db"),
        ):
            (root / f"var/lib/ecobin/{state_directory}/{database}").write_text(
                "device-specific state",
                encoding="ascii",
            )
        (root / "var/lib/ecobin/device-capabilities.json").write_text(
            '{"schemaVersion":1,"mcuRemoteUpdateCapable":true}',
            encoding="ascii",
        )
        (root / "var/lib/ecobin/hardware/photos/test.jpg").write_text(
            "photo", encoding="ascii"
        )
        (root / "root/EcoBin/hardware/data/edge.db").write_text(
            "state", encoding="ascii"
        )
        (root / "var/log/boot.log").write_text("history", encoding="ascii")

        result = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / "etc/machine-id").read_bytes(), b"")
        self.assertFalse((root / "etc/ssh/ssh_host_ed25519_key").exists())
        self.assertEqual(
            list((root / "etc/NetworkManager/system-connections").iterdir()), []
        )
        self.assertFalse((root / "var/lib/systemd/random-seed").exists())
        self.assertEqual(list((root / "var/lib/chrony").iterdir()), [])
        self.assertFalse(getty_autologin.exists())
        self.assertFalse((root / "swapfile").exists())
        self.assertFalse((root / "etc/dphys-swapfile").exists())
        self.assertFalse(
            (root / "etc/systemd/system/multi-user.target.wants/swapfile.swap").exists()
        )
        fstab_text = (root / "etc/fstab").read_text(encoding="utf-8")
        self.assertIn("UUID=root / ext4", fstab_text)
        self.assertNotIn(" swap ", fstab_text)
        self.assertEqual((root / "etc/default/orangepi-zram-config").read_text(encoding="utf-8"), "ENABLED=false\nSWAP=false\n")
        self.assertFalse((root / "etc/systemd/system/multi-user.target.wants/orangepi-zram-config.service").exists())
        self.assertFalse(
            (
                root
                / "etc/systemd/system/multi-user.target.wants/"
                "ecobin-business-activation-helper.socket"
            ).exists()
        )
        self.assertFalse(
            (
                root
                / "etc/systemd/system/multi-user.target.wants/"
                "ecobin-mcu-flash-helper@fixture.service"
            ).exists()
        )
        self.assertEqual(list((root / "var/lib/cloud").iterdir()), [])
        self.assertEqual(list((root / "var/lib/ecobin/factory-test").iterdir()), [])
        for state_directory in ("communication", "business", "updater"):
            self.assertEqual(
                list((root / f"var/lib/ecobin/{state_directory}").iterdir()),
                [],
            )
        self.assertFalse(
            (root / "var/lib/ecobin/device-capabilities.json").exists()
        )
        self.assertEqual(list((root / "var/lib/ecobin/hardware").iterdir()), [])
        self.assertEqual(list((root / "root/EcoBin/hardware/data").iterdir()), [])
        self.assertEqual(list((root / "var/log").iterdir()), [])
        shadow_fields = {
            line.split(":", 2)[0]: line.split(":", 2)[1]
            for line in (root / "etc/shadow").read_text(encoding="utf-8").splitlines()
        }
        self.assertEqual(shadow_fields["root"], "!")
        self.assertEqual(shadow_fields["orangepi"], "!")
        self.assertNotIn("known-root", (root / "etc/shadow").read_text(encoding="utf-8"))
        self.assertIn("root:x:0:0", (root / "etc/passwd").read_text(encoding="utf-8"))

    def test_local_login_audit_rejects_getty_short_autologin_option(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "candidate-getty-autologin"
        unit_directory = root / "etc/systemd/system"
        unit_directory.mkdir(parents=True)
        (root / "var").mkdir()
        (root / "etc/shadow").write_text(
            "root:!:1:2:3:4:5:6:7\norangepi:!:1:2:3:4:5:6:7\n",
            encoding="utf-8",
        )
        (unit_directory / "serial-getty@ttyS0.service").write_text(
            "[Service]\nExecStart=-/sbin/agetty -a orangepi 115200 ttyS0\n",
            encoding="utf-8",
        )

        result = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
            "--audit-local-login-only",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("getty or serial-getty", result.stderr)

    def test_local_login_audit_accepts_only_exact_getty_masks(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "candidate-getty-mask"
        unit_directory = root / "etc/systemd/system"
        unit_directory.mkdir(parents=True)
        (root / "var").mkdir()
        (root / "etc/shadow").write_text(
            "root:!:1:2:3:4:5:6:7\norangepi:!:1:2:3:4:5:6:7\n",
            encoding="utf-8",
        )
        mask = unit_directory / "serial-getty@ttyS5.service"
        try:
            mask.symlink_to("/dev/null")
        except OSError as error:
            self.skipTest(f"host does not permit symbolic-link fixtures: {error}")

        accepted = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
            "--audit-local-login-only",
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        mask.unlink()
        mask.symlink_to("/usr/lib/systemd/system/serial-getty@.service")
        rejected = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
            "--audit-local-login-only",
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("not an exact systemd mask", rejected.stderr)

    def test_local_login_audit_rejects_display_manager_automatic_login(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "candidate-gdm-autologin"
        (root / "etc/gdm3").mkdir(parents=True)
        (root / "var").mkdir()
        (root / "etc/shadow").write_text(
            "root:!:1:2:3:4:5:6:7\norangepi:!:1:2:3:4:5:6:7\n",
            encoding="utf-8",
        )
        (root / "etc/gdm3/custom.conf").write_text(
            "[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=orangepi\n",
            encoding="utf-8",
        )

        result = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
            "--audit-local-login-only",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("display-manager", result.stderr)

    def test_rootfs_sanitizer_removes_only_blocked_enable_links(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "candidate-links"
        wants = root / "etc/systemd/system/multi-user.target.wants"
        units = root / "lib/systemd/system"
        (root / "var").mkdir(parents=True)
        wants.mkdir(parents=True)
        units.mkdir(parents=True)
        (root / "etc/shadow").write_text(
            "root:x:1:2:3:4:5:6:7\norangepi:x:1:2:3:4:5:6:7\n",
            encoding="utf-8",
        )
        for name in (
            "orangepi-resize-filesystem.service",
            "ecobin-hardware.service",
            "ecobin-mcu-simulator.service",
            "ecobin-first-boot.service",
        ):
            (units / name).write_text("[Unit]\n", encoding="utf-8")
            try:
                (wants / name).symlink_to(pathlib.Path("/lib/systemd/system") / name)
            except OSError as error:
                self.skipTest(f"host does not permit symbolic-link fixtures: {error}")
        (wants / "vendor-resize-alias.service").symlink_to(
            "/lib/systemd/system/orangepi-resize-filesystem.service"
        )
        (wants / "ecobin-enrollment.service").write_text(
            "[Unit]\n", encoding="utf-8"
        )

        result = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(wants / "orangepi-resize-filesystem.service"))
        self.assertFalse(os.path.lexists(wants / "ecobin-hardware.service"))
        self.assertFalse(os.path.lexists(wants / "ecobin-mcu-simulator.service"))
        self.assertFalse(os.path.lexists(wants / "vendor-resize-alias.service"))
        self.assertFalse(os.path.lexists(wants / "ecobin-enrollment.service"))
        self.assertTrue((wants / "ecobin-first-boot.service").is_symlink())

    def test_rootfs_sanitizer_refuses_filesystem_root(self) -> None:
        result = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            pathlib.Path(pathlib.Path.cwd().anchor).as_posix(),
            "--confirm-root",
            pathlib.Path(pathlib.Path.cwd().anchor).as_posix(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to sanitize a filesystem root", result.stderr)

    def test_rootfs_sanitizer_never_crosses_a_parent_symlink(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "candidate-symlink"
        external = pathlib.Path(self.temporary_directory.name) / "outside"
        (root / "etc").mkdir(parents=True)
        external.mkdir()
        (root / "etc/shadow").write_text(
            "root:x:1:2:3:4:5:6:7\norangepi:x:1:2:3:4:5:6:7\n",
            encoding="utf-8",
        )
        sentinel = external / "sentinel"
        sentinel.write_text("must-survive", encoding="utf-8")
        try:
            (root / "var").symlink_to(external, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"host does not permit symbolic-link fixtures: {error}")

        result = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("crosses a symbolic link", result.stderr)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "must-survive")

    def test_rootfs_sanitizer_removes_all_git_metadata_without_following_links(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "candidate-git-metadata"
        external = pathlib.Path(self.temporary_directory.name) / "outside-git-target"
        (root / "etc").mkdir(parents=True)
        (root / "var").mkdir()
        (root / "usr/src/vendor/.git").mkdir(parents=True)
        (root / "opt/worktree").mkdir(parents=True)
        (root / "srv/symlinked").mkdir(parents=True)
        external.mkdir()
        (root / "etc/shadow").write_text(
            "root:x:1:2:3:4:5:6:7\norangepi:x:1:2:3:4:5:6:7\n",
            encoding="utf-8",
        )
        (root / "usr/src/vendor/.git/config").write_text(
            "[core]\nrepositoryformatversion = 0\n", encoding="utf-8"
        )
        (root / "opt/worktree/.git").write_text(
            "gitdir: /forbidden/external/path\n", encoding="utf-8"
        )
        sentinel = external / "sentinel"
        sentinel.write_text("must-survive", encoding="utf-8")
        try:
            (root / "srv/symlinked/.git").symlink_to(
                external, target_is_directory=True
            )
        except OSError as error:
            self.skipTest(f"host does not permit symbolic-link fixtures: {error}")

        result = run_command(
            sys.executable,
            str(ROOTFS_SANITIZER),
            "--root",
            str(root),
            "--confirm-root",
            str(root),
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((root / "usr/src/vendor/.git").exists())
        self.assertFalse((root / "opt/worktree/.git").exists())
        self.assertFalse(os.path.lexists(root / "srv/symlinked/.git"))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "must-survive")

    @unittest.skipIf(sys.platform == "win32", "ext4 tooling runs on Linux")
    def test_ext4_timestamp_normalizer_uses_unambiguous_epoch_values(self) -> None:
        for command in ("debugfs", "mke2fs"):
            if shutil.which(command) is None:
                self.skipTest(f"{command} is unavailable")
        image = pathlib.Path(self.temporary_directory.name) / "timestamps.img"
        with image.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024)
        formatted = run_command("mke2fs", "-q", "-F", "-t", "ext4", str(image))
        self.assertEqual(formatted.returncode, 0, formatted.stderr)
        inventory = pathlib.Path(self.temporary_directory.name) / "inventory.json"
        inventory.write_text(
            json.dumps({"entries": [{"inode": 2}]}),
            encoding="utf-8",
            newline="\n",
        )
        epoch = 1_783_765_141
        normalized = run_command(
            sys.executable,
            str(EXT4_NORMALIZER),
            "--device",
            str(image),
            "--inventory",
            str(inventory),
            "--epoch",
            str(epoch),
        )
        self.assertEqual(normalized.returncode, 0, normalized.stderr)
        inspected = run_command("debugfs", "-R", "stat <2>", str(image))
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        expected = f"0x{epoch:08x}:00000000"
        for field in ("atime", "mtime", "ctime", "crtime"):
            self.assertIn(f"{field}: {expected}", inspected.stdout)

    def test_sealing_inode_inventory_contains_no_paths_or_file_digests(self) -> None:
        root = pathlib.Path(self.temporary_directory.name) / "mounted-root"
        (root / "etc/ecobin").mkdir(parents=True)
        secret = root / "etc/ecobin/enrollment.key"
        secret.write_text("must-not-enter-inventory\n", encoding="ascii")
        output = pathlib.Path(self.temporary_directory.name) / "inodes.json"

        captured = run_command(
            sys.executable,
            str(EXT4_INODE_INVENTORY),
            "--root",
            str(root),
            "--output",
            str(output),
        )

        self.assertEqual(captured.returncode, 0, captured.stderr)
        payload = output.read_text(encoding="utf-8")
        inventory = json.loads(payload)
        self.assertEqual(set(inventory), {"entries"})
        self.assertEqual(
            [entry["inode"] for entry in inventory["entries"]],
            sorted({entry["inode"] for entry in inventory["entries"]}),
        )
        self.assertGreaterEqual(len(inventory["entries"]), 4)
        self.assertNotIn("enrollment.key", payload)
        self.assertNotIn("must-not-enter-inventory", payload)
        self.assertNotIn("sha256", payload.lower())

    @unittest.skipIf(sys.platform == "win32", "Linux Bash integration runs in WSL/Linux")
    @unittest.skipUnless(shutil.which("bash"), "bash is not installed")
    def test_bash_scripts_parse_and_validation_entry_is_fail_closed(self) -> None:
        scripts = (
            "bootstrap-builder.sh",
            "build-image.sh",
            "build-software-payload.sh",
            "run-business-builder.sh",
            "run-builder.sh",
            "run-payload-builder.sh",
            "sanitize-candidate.sh",
            "seal-image.sh",
            "release-image.sh",
            "attest-candidates.sh",
            "verify-image.sh",
            "expand-rootfs.sh",
            "flash-and-verify.sh",
            "trusted-flash-entry.sh",
        )
        parse = run_command(
            "bash",
            "-n",
            *(str(TOOL_ROOT / name) for name in scripts),
            str(TOOL_ROOT / "lib" / "block_device.sh"),
        )
        self.assertEqual(parse.returncode, 0, parse.stderr)

        blocked = run_command("bash", str(TOOL_ROOT / "build-image.sh"), "--validate-only")
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("target media qualification evidence is required", blocked.stderr)

        accepted = run_command(
            "bash",
            str(TOOL_ROOT / "build-image.sh"),
            "--validate-only",
            "--config-dir",
            str(self.fixture),
            "--target-media-qualification-evidence",
            str(self.target_media_evidence),
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

    def test_destructive_and_read_only_boundaries_are_explicit(self) -> None:
        flash = (TOOL_ROOT / "flash-and-verify.sh").read_text(encoding="utf-8")
        build = (TOOL_ROOT / "build-image.sh").read_text(encoding="utf-8")
        sanitizer = (TOOL_ROOT / "sanitize-candidate.sh").read_text(encoding="utf-8")
        verify = (TOOL_ROOT / "verify-image.sh").read_text(encoding="utf-8")
        expansion = (TOOL_ROOT / "expand-rootfs.sh").read_text(encoding="utf-8")
        seal = (TOOL_ROOT / "seal-image.sh").read_text(encoding="utf-8")
        payload_builder = (TOOL_ROOT / "build-software-payload.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("--confirm-device", flash)
        self.assertIn("target backs the running root filesystem", flash)
        self.assertIn("iflag=count_bytes,fullblock", flash)
        self.assertIn("sanitize-candidate.sh", build)
        self.assertLess(
            build.index("sanitize-candidate.sh"), build.index("verify-image.sh")
        )
        self.assertIn("--partscan", sanitizer)
        self.assertIn("root partition is not the final image partition", sanitizer)
        self.assertIn("candidate image must not be a hard link", sanitizer)
        self.assertIn("--read-only", verify)
        self.assertIn("ro,noload,nodev,nosuid,noexec", verify)
        self.assertIn("root and orangepi password authentication must be locked", verify)
        self.assertIn("--audit-local-login-only", verify)
        self.assertIn("--target-media-qualification-evidence", verify)
        self.assertIn("automatic login is enabled", verify)
        self.assertIn("var/lib/ecobin/factory-test", verify)
        self.assertIn("var/lib/ecobin/communication/communication.db", verify)
        self.assertIn("var/lib/ecobin/business/edge.db", verify)
        self.assertIn("var/lib/ecobin/updater/updater.db", verify)
        self.assertIn("var/lib/ecobin/communication", verify)
        self.assertIn("var/lib/ecobin/business", verify)
        self.assertIn("var/lib/ecobin/updater", verify)
        self.assertIn("ecobin-business-activation-helper.socket", verify)
        self.assertIn("ecobin-mcu-flash-helper@*.service", verify)
        self.assertIn("root/EcoBin/hardware/data", verify)
        self.assertIn("configured OneNet device key", verify)
        self.assertIn("image_software_installer.py", verify)
        self.assertIn("rebuild-rootfs.sh", build)
        self.assertIn("assemble_raw_image.py", build)
        self.assertLess(
            sanitizer.index("sanitize_rootfs.py"),
            sanitizer.index("image_software_installer.py"),
        )
        self.assertIn("growpart", expansion)
        self.assertIn("resize2fs", expansion)
        self.assertIn("root partition is not the final partition", expansion)
        self.assertIn("findmnt -nro MAJ:MIN /", expansion)
        self.assertIn('/sys/dev/block/${root_maj_min}', expansion)
        self.assertIn("verify_node_identity", expansion)
        self.assertIn("flock -n", expansion)
        self.assertIn("target media qualification is absent", expansion)
        self.assertIn("strict sysfs partition growth", expansion)
        self.assertIn("resized ext4 does not cover", expansion)
        self.assertIn("persistent swap unit must not be enabled", verify)
        self.assertIn("Orange Pi zram or zram swap is not explicitly disabled", verify)
        self.assertIn("target device is smaller than the signed qualified-media minimum", flash)
        self.assertIn("--build-attestation", seal)
        self.assertIn("--trust-policy", seal)
        self.assertIn("--evidence-signature", seal)
        self.assertIn("capture_ext4_inode_inventory.py", seal)
        self.assertIn("normalize_ext4_metadata.py", seal)
        self.assertIn("--allow-block-device", seal)
        self.assertIn("verify-image.sh\" --candidate", seal)
        self.assertIn("verify-image.sh\" --sealed", seal)
        self.assertLess(
            seal.index("verify-image.sh\" --candidate"),
            seal.index("inject_factory_secrets.py"),
        )
        self.assertNotIn("enrollment_key_sha", seal.lower())
        self.assertNotIn("setup_ap_key_sha", seal.lower())
        self.assertIn("release_trust.py\" sign", seal)
        self.assertLess(
            payload_builder.index("validate-trust"),
            payload_builder.index('${runtime_trust_directory}/"*.pem'),
        )
        self.assertLess(
            payload_builder.index("validate-trust"),
            payload_builder.index("stage_signed_runtime_payload.py"),
        )

    def test_block_device_queries_are_compatible_with_locked_debian_12(self) -> None:
        helper = (TOOL_ROOT / "lib" / "block_device.sh").read_text(encoding="utf-8")
        self.assertIn("/sys/class/block/", helper)
        self.assertIn('/partition")', helper)
        self.assertIn('/start")', helper)
        self.assertIn('/size")', helper)
        self.assertIn("blkid -s PTUUID", helper)
        self.assertNotIn("lsblk -", helper)

        partition_scripts = (
            "sanitize-candidate.sh",
            "verify-image.sh",
            "seal-image.sh",
            "release-image.sh",
        )
        for name in partition_scripts:
            script = (TOOL_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertNotIn("NAME,TYPE,PARTN", script)
                self.assertIn("ecobin_final_partition_geometry", script)
                self.assertIn("ecobin_verify_dos_partition_identity", script)
                self.assertIn("--offset", script)
                self.assertIn("--sizelimit", script)

        detach_scripts = (*partition_scripts, "rebuild-rootfs.sh")
        for name in detach_scripts:
            script = (TOOL_ROOT / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                self.assertNotIn("losetup -d --", script)

    def test_rootfs_rebuild_pins_directory_hash_signedness(self) -> None:
        rebuild = (TOOL_ROOT / "rebuild-rootfs.sh").read_text(encoding="utf-8")
        flag_write = 'debugfs -w -R "set_super_value flags ${filesystem_flag_value}"'
        self.assertIn('("signed_directory_hash",): "0x1"', rebuild)
        self.assertIn('("unsigned_directory_hash",): "0x2"', rebuild)
        self.assertIn(flag_write, rebuild)
        self.assertLess(rebuild.index("mke2fs -q"), rebuild.index(flag_write))
        self.assertLess(rebuild.index(flag_write), rebuild.index("tune2fs -E hash_alg"))

    @unittest.skipIf(sys.platform == "win32", "release verification runs on Linux")
    def test_signed_release_inventory_rejects_manifest_and_sbom_tampering(self) -> None:
        for command in ("bash", "openssl", "zstd"):
            if shutil.which(command) is None:
                self.skipTest(f"{command} is unavailable")
        release = pathlib.Path(self.temporary_directory.name) / "release"
        (release / "schemas").mkdir(parents=True)
        raw = pathlib.Path(self.temporary_directory.name) / "raw.img"
        raw.write_bytes(b"signed raw fixture" * 128)
        archive = release / "ecobin-orangepi-zero3-1.0.0.img.zst"
        compressed = run_command(
            "zstd", "-q", "-f", "-o", str(archive), str(raw)
        )
        self.assertEqual(compressed.returncode, 0, compressed.stderr)
        private_key = pathlib.Path(self.temporary_directory.name) / "private.pem"
        public_key = pathlib.Path(self.temporary_directory.name) / "public.pem"
        generated = run_command(
            "openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private_key)
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        exported = run_command(
            "openssl", "pkey", "-in", str(private_key), "-pubout", "-out", str(public_key)
        )
        self.assertEqual(exported.returncode, 0, exported.stderr)
        public_der = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", str(public_key), "-outform", "DER"],
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        shutil.copy2(TOOL_ROOT / "flash-and-verify.sh", release / "flash-and-verify.sh")
        for relative, content in (
            ("flash-and-verify.ps1", b"fixture powershell\n"),
            ("factory-checklist.md", b"fixture checklist\n"),
            ("schemas/release-manifest.schema.json", b"{}\n"),
            ("schemas/build-attestation.schema.json", b"{}\n"),
            ("schemas/rootfs-qualification-evidence.schema.json", b"{}\n"),
            ("schemas/target-media-qualification-evidence.schema.json", b"{}\n"),
            ("schemas/seal-evidence.schema.json", b"{}\n"),
            ("apt-packages.txt", b"demo:arm64=1\n"),
            ("python-packages.txt", b"runtime demo==1\n"),
            ("sbom.spdx.json", b'{"spdxVersion":"SPDX-2.3"}\n'),
            ("build-attestation.json", b'{"fixture":"build"}\n'),
            ("build-attestation.sig", b"b" * 64),
            (
                "rootfs-qualification-evidence.json",
                b'{"fixture":"rootfs-qualification"}\n',
            ),
            (
                "target-media-qualification-evidence.json",
                self.target_media_evidence.read_bytes(),
            ),
            ("seal-evidence.json", b'{"fixture":"seal"}\n'),
            ("seal-evidence.sig", b"s" * 64),
        ):
            path = release / relative
            path.write_bytes(content)
        manifest = {
            "artifactClass": "SEALED_SIGNED_RELEASE",
            "sourceDirty": False,
            "security": {
                "signingState": "SIGNED",
                "imageSigningKeyId": "factory_2026",
                "imageSigningPublicKeySha256": hashlib.sha256(public_der).hexdigest(),
            },
            "artifacts": {
                "compressedImageFile": archive.name,
                "compressedImageSha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "compressedImageBytes": archive.stat().st_size,
                "sealedRawImageBytes": raw.stat().st_size,
                "sealedRawImageSha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
                "checksumFile": "release-checksums.txt",
                "signatureFile": "release-checksums.sig",
                "targetMediaQualificationEvidenceFile":
                    "target-media-qualification-evidence.json",
                "targetMediaQualificationEvidenceSha256": hashlib.sha256(
                    self.target_media_evidence.read_bytes()
                ).hexdigest(),
            },
            "layout": {
                "targetMedia": {
                    "qualificationState": "QUALIFIED",
                    "marketedCapacityGB": 32,
                    "marketedCapacityBytes": 32_000_000_000,
                    "minimumQualifiedMediaBytes": 30_000_000_000,
                    "evidenceSha256": hashlib.sha256(
                        self.target_media_evidence.read_bytes()
                    ).hexdigest(),
                }
            },
        }
        manifest_path = release / "image-manifest.json"
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        names = (
            archive.name,
            "image-manifest.json",
            "sbom.spdx.json",
            "apt-packages.txt",
            "python-packages.txt",
            "build-attestation.json",
            "build-attestation.sig",
            "rootfs-qualification-evidence.json",
            "target-media-qualification-evidence.json",
            "seal-evidence.json",
            "seal-evidence.sig",
            "schemas/release-manifest.schema.json",
            "schemas/build-attestation.schema.json",
            "schemas/rootfs-qualification-evidence.schema.json",
            "schemas/target-media-qualification-evidence.schema.json",
            "schemas/seal-evidence.schema.json",
            "flash-and-verify.sh",
            "flash-and-verify.ps1",
            "factory-checklist.md",
        )
        checksums = release / "release-checksums.txt"
        checksums.write_text(
            "".join(
                f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}\n"
                for name in names
            ),
            encoding="ascii",
        )
        signature = release / "release-checksums.sig"
        signed = run_command(
            "openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private_key),
            "-in", str(checksums), "-out", str(signature),
        )
        self.assertEqual(signed.returncode, 0, signed.stderr)

        arguments = (
            "bash", str(TOOL_ROOT / "flash-and-verify.sh"), "--verify-only",
            "--image-zst", str(archive), "--checksums-file", str(checksums),
            "--signature-file", str(signature), "--public-key-file", str(public_key),
            "--manifest", str(manifest_path), "--signing-key-id", "factory_2026",
        )
        accepted = run_command(*arguments)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        original_manifest = manifest_path.read_bytes()
        manifest_path.write_bytes(original_manifest + b" ")
        rejected_manifest = run_command(*arguments)
        self.assertNotEqual(rejected_manifest.returncode, 0)
        self.assertIn("inventory member digest differs", rejected_manifest.stderr)
        manifest_path.write_bytes(original_manifest)
        sbom = release / "sbom.spdx.json"
        original_sbom = sbom.read_bytes()
        sbom.write_bytes(original_sbom + b" ")
        rejected_sbom = run_command(*arguments)
        self.assertNotEqual(rejected_sbom.returncode, 0)
        self.assertIn("inventory member digest differs", rejected_sbom.stderr)

        # Even a newly signed inventory must not turn internally inconsistent
        # card-capacity claims into qualification evidence.
        sbom.write_bytes(original_sbom)
        target_evidence = release / "target-media-qualification-evidence.json"
        invalid_evidence = json.loads(target_evidence.read_text(encoding="utf-8"))
        invalid_evidence["minimumQualifiedMediaBytes"] = 31_000_000_000
        target_evidence.write_text(
            json.dumps(invalid_evidence, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        evidence_sha = hashlib.sha256(target_evidence.read_bytes()).hexdigest()
        manifest["layout"]["targetMedia"]["evidenceSha256"] = evidence_sha
        manifest["artifacts"]["targetMediaQualificationEvidenceSha256"] = evidence_sha
        manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        checksums.write_text(
            "".join(
                f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}\n"
                for name in names
            ),
            encoding="ascii",
        )
        resigned = run_command(
            "openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private_key),
            "-in", str(checksums), "-out", str(signature),
        )
        self.assertEqual(resigned.returncode, 0, resigned.stderr)
        rejected_semantics = run_command(*arguments)
        self.assertNotEqual(rejected_semantics.returncode, 0)
        self.assertIn(
            "signed target-media minimum differs from measured samples",
            rejected_semantics.stderr,
        )

    def test_every_json_document_parses_without_duplicate_keys(self) -> None:
        sys.path.insert(0, str(TOOL_ROOT / "lib"))
        try:
            from validate_inputs import load_json

            for path in (
                TOOL_ROOT / "source.lock.json",
                TOOL_ROOT / "builder.lock",
                TOOL_ROOT / "image-layout.json",
                *(TOOL_ROOT / "schemas").glob("*.json"),
            ):
                with self.subTest(path=path.name):
                    self.assertIsInstance(load_json(path), dict)
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
