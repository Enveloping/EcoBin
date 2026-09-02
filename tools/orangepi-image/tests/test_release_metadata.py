from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOL_ROOT = Path(__file__).resolve().parents[1]
LIB = TOOL_ROOT / "lib"
sys.path.insert(0, str(LIB))

from release_trust import input_lock_hashes  # noqa: E402


GENERATOR = LIB / "generate_release_metadata.py"


class ReleaseMetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("openssl") is None:
            self.skipTest("OpenSSL is required")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "rootfs"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc/os-release").write_text(
            "ID=debian\nVERSION_ID=12\n", encoding="utf-8", newline="\n"
        )
        status = self.root / "var/lib/dpkg/status"
        status.parent.mkdir(parents=True)
        status.write_text(
            "Package: python3\nStatus: install ok installed\n"
            "Architecture: arm64\nVersion: 3.11.2-1+b1\n\n"
            "Package: zlib1g\nStatus: install ok installed\n"
            "Architecture: arm64\nVersion: 1:1.2.13.dfsg-1\n",
            encoding="utf-8",
            newline="\n",
        )
        metadata = (
            self.root
            / "opt/ecobin/hardware/releases/release-1/.venv/lib/python3.11"
            / "site-packages/cryptography-46.0.7.dist-info/METADATA"
        )
        metadata.parent.mkdir(parents=True)
        metadata.write_text(
            "Metadata-Version: 2.4\nName: cryptography\nVersion: 46.0.7\n",
            encoding="utf-8",
            newline="\n",
        )
        self.sealed = self.base / "sealed.img"
        self.sealed.write_bytes(b"sealed-image-fixture")
        self.compressed = self.base / "image.img.zst"
        self.compressed.write_bytes(b"compressed-image-fixture")

        self.keys: dict[str, tuple[Path, Path, str, str]] = {}
        for role, key_id in (
            ("buildAttestation", "build-attestation-2026"),
            ("sealEvidence", "seal-evidence-2026"),
            ("releaseSigning", "release-signing-2026"),
        ):
            private = self.base / f"{role}.private.pem"
            public = self.base / f"{role}.public.pem"
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    "openssl", "pkey", "-in", str(private), "-pubout", "-out",
                    str(public),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            public_der = subprocess.run(
                ["openssl", "pkey", "-pubin", "-in", str(public), "-outform", "DER"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout
            self.keys[role] = (
                private,
                public,
                key_id,
                hashlib.sha256(public_der).hexdigest(),
            )

        self.policy = self.base / "formal-release-policy.json"
        self._write_json(
            self.policy,
            {
                "$schema": "./schemas/formal-release-policy.schema.json",
                "schemaVersion": 1,
                "lockState": "LOCKED",
                "reason": "Test-only external trust anchors.",
                "rootfsQualification": {
                    "state": "QUALIFIED",
                    "method": "DETERMINISTIC_EXT4_REBUILD_V1",
                    "evidenceSha256": "e" * 64,
                },
                "builderReceipts": [
                    {"builderIdentity": "builder-a", "builderDomain": "domain-a", "keyId": "receipt-a", "publicKeySha256": "d" * 64},
                    {"builderIdentity": "builder-b", "builderDomain": "domain-b", "keyId": "receipt-b", "publicKeySha256": "e" * 64},
                ],
                **{
                    role: {"keyId": values[2], "publicKeySha256": values[3]}
                    for role, values in self.keys.items()
                },
            },
        )
        self.config = self.base / "config"
        self.config.mkdir()
        for name in ("source.lock.json", "builder.lock", "apt-packages.lock"):
            shutil.copy2(TOOL_ROOT / name, self.config / name)
        self.target_media_evidence = self.base / "target-media-qualification-evidence.json"
        self._write_json(
            self.target_media_evidence,
            {
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
                    {"sampleId": "card-a", "measuredBytes": 31_000_000_000, "logicalSectorBytes": 512, "wholeDevice": True},
                    {"sampleId": "card-b", "measuredBytes": 30_000_000_000, "logicalSectorBytes": 512, "wholeDevice": True},
                ],
            },
        )
        layout = json.loads((TOOL_ROOT / "image-layout.json").read_text(encoding="utf-8"))
        layout["lockState"] = "LOCKED"
        layout["targetMedia"].update({
            "qualificationState": "QUALIFIED",
            "minimumQualifiedMediaBytes": 30_000_000_000,
            "evidenceSha256": hashlib.sha256(self.target_media_evidence.read_bytes()).hexdigest(),
        })
        self._write_json(self.config / "image-layout.json", layout)
        payload_sha = "9" * 64
        locks = input_lock_hashes(self.config, payload_sha, self.policy)
        raw_sha = "1" * 64
        self.candidate = self.base / "candidate.json"
        self.candidate_value = {
            "schemaVersion": 2,
            "artifactClass": "UNSIGNED_NO_SECRET_CANDIDATE",
            "releaseId": "fixture-001",
            "version": "1.2.3",
            "gitCommit": "a" * 40,
            "sourceDirty": False,
            "sourceDateEpoch": 1_700_000_000,
            "baseImage": {},
            "builder": {},
            "locks": {
                key: value
                for key, value in locks.items()
                if key != "formalReleasePolicySha256"
            },
            "platform": {},
            "layout": layout,
            "software": {
                "implementedSlices": [f"P{number}" for number in range(1, 9)],
                "runtimeInstalled": True,
                "payloadSchemaVersion": 2,
                "factoryPortalInstalled": True,
                # These are preconditions authenticated by the signed
                # two-candidate proof; release never consumes them unsigned.
                "buildReproducibility": {
                    "rootfsDeterministic": True,
                    "releaseEligible": True,
                },
                "components": {
                    "hardwareRuntime": "runtime-001",
                    "enrollment": "control-001",
                    "remoteSupport": "control-001",
                    "factoryTest": "control-001",
                    "firstBoot": "control-001",
                    "communicationAgent": "communication-001",
                    "deviceUpdater": "updater-001",
                },
            },
            "factory": {},
            "security": {
                "enrollmentKeyId": "K1",
                "k1Injected": False,
                "setupApKeyInjected": False,
                "deviceCredentialsPresent": False,
                "signingState": "UNSIGNED",
            },
            "artifacts": {
                "rawImageBytes": 1_048_576,
                "rawImageSha256": raw_sha,
                "targetMediaQualificationEvidenceFile": "target-media-qualification-evidence.json",
                "targetMediaQualificationEvidenceSha256": hashlib.sha256(self.target_media_evidence.read_bytes()).hexdigest(),
            },
        }
        self._write_json(self.candidate, self.candidate_value)

        build_key = self.keys["buildAttestation"]
        self.attestation = self.base / "build-attestation.json"
        self.attestation_signature = self.base / "build-attestation.sig"
        self.attestation_value = {
            "$schema": "./schemas/build-attestation.schema.json",
            "schemaVersion": 2,
            "artifactClass": "AUTHENTICATED_REPRODUCIBLE_CANDIDATE_BUILD",
            "releaseId": "fixture-001",
            "version": "1.2.3",
            "gitCommit": "a" * 40,
            "sourceDateEpoch": 1_700_000_000,
            "locks": locks,
            "rootfsQualification": {"method": "DETERMINISTIC_EXT4_REBUILD_V1", "evidenceSha256": "e" * 64},
            "buildReceipts": [
                {"invocationUid": "11111111-1111-4111-8111-111111111111", "builderIdentity": "builder-a", "builderDomain": "domain-a", "receiptSha256": "1" * 64},
                {"invocationUid": "22222222-2222-4222-8222-222222222222", "builderIdentity": "builder-b", "builderDomain": "domain-b", "receiptSha256": "2" * 64},
            ],
            "candidates": [
                {
                    "role": role,
                    "rawImageBytes": 1_048_576,
                    "rawImageSha256": raw_sha,
                    "manifestSha256": hashlib.sha256(
                        self.candidate.read_bytes()
                    ).hexdigest(),
                }
                for role in ("independent-a", "independent-b")
            ],
            "verification": {
                "independentCandidateFiles": True,
                "candidateRawByteForByteMatch": True,
                "candidateManifestByteForByteMatch": True,
                "externalImageAuditPassed": True,
                "sourceWorktreeClean": True,
            },
            "signing": {
                "algorithm": "Ed25519",
                "keyId": build_key[2],
                "publicKeySha256": build_key[3],
            },
        }
        self._write_json(self.attestation, self.attestation_value)
        self._sign(build_key[0], self.attestation, self.attestation_signature)

        seal_key = self.keys["sealEvidence"]
        self.evidence = self.base / "seal-evidence.json"
        self.evidence_signature = self.base / "seal-evidence.sig"
        self.evidence_value = {
            "$schema": "./schemas/seal-evidence.schema.json",
            "schemaVersion": 1,
            "artifactClass": "AUTHENTICATED_FILE_LEVEL_SEAL_EVIDENCE",
            "releaseId": "fixture-001",
            "version": "1.2.3",
            "gitCommit": "a" * 40,
            "buildAttestationSha256": hashlib.sha256(
                self.attestation.read_bytes()
            ).hexdigest(),
            "candidateRawImageSha256": raw_sha,
            "sealedRawImageSha256": hashlib.sha256(
                self.sealed.read_bytes()
            ).hexdigest(),
            "rawImageBytes": self.sealed.stat().st_size,
            "protectedFiles": [
                {
                    "path": path,
                    "type": "regular-file",
                    "uid": 0,
                    "gid": 0,
                    "mode": "0600",
                    "present": True,
                    "contentDigestDisclosed": False,
                }
                for path in (
                    "/etc/ecobin/enrollment.key",
                    "/etc/ecobin/setup-ap.key",
                )
            ],
            "verification": {
                "candidateAuditPassed": True,
                "sealedAuditPassed": True,
                "onlyDeclaredSecretsInjected": True,
            },
            "signing": {
                "algorithm": "Ed25519",
                "keyId": seal_key[2],
                "publicKeySha256": seal_key[3],
            },
        }
        self._write_json(self.evidence, self.evidence_value)
        self._sign(seal_key[0], self.evidence, self.evidence_signature)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    @staticmethod
    def _sign(private: Path, payload: Path, output: Path) -> None:
        subprocess.run(
            [
                "openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(private),
                "-in", str(payload), "-out", str(output),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _generate(self, output: Path) -> subprocess.CompletedProcess[str]:
        output.mkdir()
        release_key = self.keys["releaseSigning"]
        return subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--rootfs", str(self.root),
                "--config-dir", str(self.config),
                "--trust-policy", str(self.policy),
                "--candidate-manifest", str(self.candidate),
                "--target-media-qualification-evidence", str(self.target_media_evidence),
                "--build-attestation", str(self.attestation),
                "--build-attestation-signature", str(self.attestation_signature),
                "--build-attestation-public-key", str(self.keys["buildAttestation"][1]),
                "--seal-evidence", str(self.evidence),
                "--seal-evidence-signature", str(self.evidence_signature),
                "--seal-evidence-public-key", str(self.keys["sealEvidence"][1]),
                "--sealed-image", str(self.sealed),
                "--compressed-image", str(self.compressed),
                "--output-directory", str(output),
                "--signing-key-id", release_key[2],
                "--signing-public-key-sha256", release_key[3],
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def test_authenticated_metadata_is_deterministic_and_uses_signed_gate(self) -> None:
        first = self.base / "out-1"
        second = self.base / "out-2"
        first_result = self._generate(first)
        second_result = self._generate(second)
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        self.assertEqual(second_result.returncode, 0, second_result.stderr)
        for name in (
            "apt-packages.txt", "python-packages.txt", "sbom.spdx.json",
            "image-manifest.json",
        ):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        manifest = json.loads((first / "image-manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["software"]["payloadSchemaVersion"], 2)
        reproducibility = manifest["software"]["buildReproducibility"]
        self.assertTrue(reproducibility["releaseEligible"])
        self.assertEqual(
            reproducibility["proof"],
            "AUTHENTICATED_TWO_CANDIDATE_BYTE_MATCH",
        )
        self.assertTrue(manifest["audit"]["candidateReproducibilityAttested"])
        self.assertTrue(manifest["audit"]["authenticatedSealEvidenceVerified"])
        self.assertEqual(
            manifest["locks"]["formalReleasePolicySha256"],
            hashlib.sha256(self.policy.read_bytes()).hexdigest(),
        )

    def test_attested_legacy_candidate_keeps_the_v1_release_contract(self) -> None:
        self.candidate_value["schemaVersion"] = 1
        self.candidate_value["software"].pop("payloadSchemaVersion")
        self.candidate_value["software"]["components"].pop("communicationAgent")
        self.candidate_value["software"]["components"].pop("deviceUpdater")
        self._write_json(self.candidate, self.candidate_value)
        candidate_sha256 = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        for candidate in self.attestation_value["candidates"]:
            candidate["manifestSha256"] = candidate_sha256
        self._write_json(self.attestation, self.attestation_value)
        self._sign(
            self.keys["buildAttestation"][0],
            self.attestation,
            self.attestation_signature,
        )
        self.evidence_value["buildAttestationSha256"] = hashlib.sha256(
            self.attestation.read_bytes()
        ).hexdigest()
        self._write_json(self.evidence, self.evidence_value)
        self._sign(
            self.keys["sealEvidence"][0],
            self.evidence,
            self.evidence_signature,
        )

        output = self.base / "legacy-v1"
        result = self._generate(output)

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(
            (output / "image-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertNotIn("payloadSchemaVersion", manifest["software"])
        self.assertEqual(
            set(manifest["software"]["components"]),
            {
                "hardwareRuntime",
                "enrollment",
                "remoteSupport",
                "factoryTest",
                "firstBoot",
            },
        )

    def test_manifest_tamper_after_attestation_is_rejected(self) -> None:
        self.candidate_value["software"]["buildReproducibility"][
            "releaseEligible"
        ] = False
        self._write_json(self.candidate, self.candidate_value)
        result = self._generate(self.base / "tampered-manifest")
        self.assertEqual(result.returncode, 2)
        self.assertIn("candidate manifest differs", result.stderr)

    def test_even_signed_attestation_cannot_promote_trial_manifest(self) -> None:
        self.candidate_value["software"]["buildReproducibility"] = {
            "rootfsDeterministic": False,
            "releaseEligible": False,
        }
        self._write_json(self.candidate, self.candidate_value)
        trial_manifest_sha = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        for candidate in self.attestation_value["candidates"]:
            candidate["manifestSha256"] = trial_manifest_sha
        self._write_json(self.attestation, self.attestation_value)
        self._sign(
            self.keys["buildAttestation"][0],
            self.attestation,
            self.attestation_signature,
        )
        self.evidence_value["buildAttestationSha256"] = hashlib.sha256(
            self.attestation.read_bytes()
        ).hexdigest()
        self._write_json(self.evidence, self.evidence_value)
        self._sign(
            self.keys["sealEvidence"][0],
            self.evidence,
            self.evidence_signature,
        )
        result = self._generate(self.base / "signed-trial")
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact formal no-secret build facts", result.stderr)

    def test_even_signed_attestation_cannot_promote_secret_bearing_candidate(self) -> None:
        self.candidate_value["security"].update(
            {
                "k1Injected": True,
                "setupApKeyInjected": True,
                "deviceCredentialsPresent": True,
                "signingState": "SIGNED",
            }
        )
        self._write_json(self.candidate, self.candidate_value)
        secret_manifest_sha = hashlib.sha256(self.candidate.read_bytes()).hexdigest()
        for candidate in self.attestation_value["candidates"]:
            candidate["manifestSha256"] = secret_manifest_sha
        self._write_json(self.attestation, self.attestation_value)
        self._sign(
            self.keys["buildAttestation"][0],
            self.attestation,
            self.attestation_signature,
        )
        self.evidence_value["buildAttestationSha256"] = hashlib.sha256(
            self.attestation.read_bytes()
        ).hexdigest()
        self._write_json(self.evidence, self.evidence_value)
        self._sign(
            self.keys["sealEvidence"][0],
            self.evidence,
            self.evidence_signature,
        )
        result = self._generate(self.base / "signed-secret-candidate")
        self.assertEqual(result.returncode, 2)
        self.assertIn("exact formal no-secret build facts", result.stderr)

    def test_unsigned_attestation_or_seal_evidence_tamper_is_rejected(self) -> None:
        self.attestation_value["verification"]["sourceWorktreeClean"] = False
        self._write_json(self.attestation, self.attestation_value)
        result = self._generate(self.base / "tampered-attestation")
        self.assertEqual(result.returncode, 2)
        self.assertIn("signature verification failed", result.stderr)

        self._write_json(self.attestation, self.attestation_value | {
            "verification": {
                **self.attestation_value["verification"],
                "sourceWorktreeClean": True,
            }
        })
        self._sign(
            self.keys["buildAttestation"][0],
            self.attestation,
            self.attestation_signature,
        )
        self.evidence_value["sealedRawImageSha256"] = "f" * 64
        self._write_json(self.evidence, self.evidence_value)
        result = self._generate(self.base / "tampered-evidence")
        self.assertEqual(result.returncode, 2)
        self.assertIn("signature verification failed", result.stderr)

    def test_release_scripts_use_external_proofs_and_one_sealed_raw(self) -> None:
        release = (TOOL_ROOT / "release-image.sh").read_text(encoding="utf-8")
        attest = (TOOL_ROOT / "attest-candidates.sh").read_text(encoding="utf-8")
        seal = (TOOL_ROOT / "seal-image.sh").read_text(encoding="utf-8")
        trusted = (TOOL_ROOT / "trusted-flash-entry.sh").read_text(encoding="utf-8")
        trusted_ps = (TOOL_ROOT / "trusted-flash-entry.ps1").read_text(encoding="utf-8")
        self.assertIn("--build-attestation", release)
        self.assertIn("--rootfs-qualification-evidence", release)
        self.assertIn("--target-media-qualification-evidence", release)
        self.assertIn("rootfs-qualification-evidence.json", release)
        self.assertIn("target-media-qualification-evidence.json", release)
        self.assertIn("--receipt-a", attest)
        self.assertIn("--receipt-b", attest)
        self.assertIn("--seal-evidence-signature", release)
        self.assertNotIn("--reproducibility-peer", release)
        self.assertNotIn("independent sealed images", release)
        self.assertIn("Secret-bearing raw images are intentionally not expected", release)
        self.assertIn("--software-payload-sha256", seal)
        self.assertIn("verify-build", seal)
        self.assertIn("active swap is forbidden", seal)
        self.assertIn("core dumps must be disabled", seal)
        self.assertLess(
            seal.index('release_trust.py" snapshot'),
            seal.index('verify-image.sh" --candidate'),
        )
        self.assertLess(
            trusted.index("verify-signature"),
            trusted.index('bash "${signed_flash}"'),
        )
        self.assertIn("active swap is forbidden", release)
        self.assertLess(
            release.index('sync -f -- "${staging_directory}"'),
            release.index('mv -- "${staging_directory}" "${output_directory}"'),
        )
        self.assertLess(
            release.index('mv -- "${staging_directory}" "${output_directory}"'),
            release.index('sync -f -- "${output_parent}"'),
        )
        self.assertIn("active swap is forbidden", trusted)
        self.assertIn('--source "${source_member}"', trusted)
        self.assertIn('bash "${signed_flash}"', trusted)
        self.assertNotIn('bash "${release_directory}', trusted)
        self.assertIn("/etc/ecobin-image-factory/release-signing-public.pem", trusted)
        self.assertIn("/usr/local/lib/ecobin-image-factory/trusted-flash-entry.sh", trusted_ps)
        self.assertNotIn("flash-and-verify.ps1", trusted_ps)


if __name__ == "__main__":
    unittest.main()
