from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_ROOT / "lib"))

from release_trust import (  # noqa: E402
    ReleaseTrustError,
    load_policy,
    main as release_trust_main,
    public_key_fingerprint,
    snapshot_new,
    validate_controlled_directory,
    validate_external_policy_path,
    enforce_secret_memory_policy,
)


class ReleaseTrustSecurityTest(unittest.TestCase):
    @unittest.skipUnless(
        os.name != "posix" or os.geteuid() == 0,
        "private-key signing requires root on Linux/WSL",
    )
    def test_builder_receipt_uses_fail_closed_signing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private_key = root / "receipt-private.pem"
            public_key = root / "receipt-public.pem"
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "ED25519", "-out", private_key],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    "openssl", "pkey", "-in", private_key, "-pubout",
                    "-out", public_key,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            public_der = subprocess.run(
                [
                    "openssl", "pkey", "-pubin", "-in", public_key,
                    "-outform", "DER",
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ).stdout
            fingerprint = hashlib.sha256(public_der).hexdigest()
            policy = root / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "$schema": "./schemas/formal-release-policy.schema.json",
                        "schemaVersion": 1,
                        "lockState": "LOCKED",
                        "reason": "Builder receipt signing test fixture.",
                        "rootfsQualification": {
                            "state": "QUALIFIED",
                            "method": "DETERMINISTIC_EXT4_REBUILD_V1",
                            "evidenceSha256": "1" * 64,
                        },
                        "builderReceipts": [
                            {
                                "builderIdentity": "builder-a",
                                "builderDomain": "domain-a",
                                "keyId": "receipt-a",
                                "publicKeySha256": fingerprint,
                            },
                            {
                                "builderIdentity": "builder-b",
                                "builderDomain": "domain-b",
                                "keyId": "receipt-b",
                                "publicKeySha256": "2" * 64,
                            },
                        ],
                        "buildAttestation": {
                            "keyId": "attestation",
                            "publicKeySha256": "3" * 64,
                        },
                        "sealEvidence": {
                            "keyId": "seal",
                            "publicKeySha256": "4" * 64,
                        },
                        "releaseSigning": {
                            "keyId": "release",
                            "publicKeySha256": "5" * 64,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            receipt = root / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "artifactClass": "SIGNED_INDEPENDENT_IMAGE_BUILD_RECEIPT",
                        "invocationUid": "11111111-1111-4111-8111-111111111111",
                        "builderIdentity": "builder-a",
                        "builderDomain": "domain-a",
                        "builder": {},
                        "releaseId": "r1",
                        "version": "1.0.0",
                        "gitCommit": "a" * 40,
                        "inputLocks": {},
                        "candidate": {},
                        "completedAt": "2026-08-23T12:00:00Z",
                        "signingKeyId": "receipt-a",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            signature = root / "receipt.sig"
            with patch("release_trust.enforce_secret_memory_policy") as memory_gate, patch(
                "release_trust.validate_controlled_directory",
                return_value=root,
            ):
                result = release_trust_main(
                    [
                        "sign", "--trust-policy", str(policy),
                        "--role", "builderReceipt",
                        "--private-key", str(private_key),
                        "--key-id", "receipt-a",
                        "--payload", str(receipt),
                        "--output", str(signature),
                    ]
                )
            self.assertEqual(result, 0)
            memory_gate.assert_called_once_with("builder receipt signing")
            verified = subprocess.run(
                [
                    "openssl", "pkeyutl", "-verify", "-pubin",
                    "-inkey", public_key, "-rawin", "-in", receipt,
                    "-sigfile", signature,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(verified.returncode, 0)
            original_signature = signature.read_bytes()
            stderr = io.StringIO()
            with patch("release_trust.enforce_secret_memory_policy"), patch(
                "release_trust.validate_controlled_directory",
                return_value=root,
            ), patch("sys.stderr", stderr):
                overwrite = release_trust_main(
                    [
                        "sign", "--trust-policy", str(policy),
                        "--role", "builderReceipt",
                        "--private-key", str(private_key),
                        "--key-id", "receipt-a",
                        "--payload", str(receipt),
                        "--output", str(signature),
                    ]
                )
            self.assertEqual(overwrite, 2)
            self.assertEqual(signature.read_bytes(), original_signature)

            mismatched_receipt = root / "mismatched-receipt.json"
            mismatched_value = json.loads(receipt.read_text(encoding="utf-8"))
            mismatched_value["builderDomain"] = "domain-b"
            mismatched_receipt.write_text(
                json.dumps(
                    mismatched_value,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            rejected_signature = root / "mismatched-receipt.sig"
            stderr = io.StringIO()
            with patch("release_trust.enforce_secret_memory_policy"), patch(
                "release_trust.validate_controlled_directory",
                return_value=root,
            ), patch("sys.stderr", stderr):
                rejected = release_trust_main(
                    [
                        "sign", "--trust-policy", str(policy),
                        "--role", "builderReceipt",
                        "--private-key", str(private_key),
                        "--key-id", "receipt-a",
                        "--payload", str(mismatched_receipt),
                        "--output", str(rejected_signature),
                    ]
                )
            self.assertEqual(rejected, 2)
            self.assertIn("payload identity differs", stderr.getvalue())
            self.assertFalse(rejected_signature.exists())

    def test_repository_policy_path_is_never_an_external_trust_anchor(self) -> None:
        with self.assertRaisesRegex(ReleaseTrustError, "outside the source repository"):
            validate_external_policy_path(
                TOOL_ROOT / "formal-release-policy.json",
                TOOL_ROOT.parents[1],
            )

    def test_locked_policy_requires_independent_rootfs_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy = Path(temporary) / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "$schema": "./schemas/formal-release-policy.schema.json",
                        "schemaVersion": 1,
                        "lockState": "LOCKED",
                        "reason": "Fixture deliberately lacks qualification.",
                        "rootfsQualification": {
                            "state": "UNQUALIFIED",
                            "method": None,
                            "evidenceSha256": None,
                        },
                        "builderReceipts": [
                            {"builderIdentity": "builder-a", "builderDomain": "domain-a", "keyId": "receipt-a", "publicKeySha256": "4" * 64},
                            {"builderIdentity": "builder-b", "builderDomain": "domain-b", "keyId": "receipt-b", "publicKeySha256": "5" * 64},
                        ],
                        **{
                            role: {
                                "keyId": role.lower(),
                                "publicKeySha256": "a" * 64,
                            }
                            for role in (
                                "buildAttestation",
                                "sealEvidence",
                                "releaseSigning",
                            )
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ReleaseTrustError,
                "deterministic rootfs qualification",
            ):
                load_policy(policy)

    def test_locked_policy_requires_three_distinct_key_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy = Path(temporary) / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "$schema": "./schemas/formal-release-policy.schema.json",
                        "schemaVersion": 1,
                        "lockState": "LOCKED",
                        "reason": "Fixture deliberately reuses one trust key.",
                        "rootfsQualification": {
                            "state": "QUALIFIED",
                            "method": "DETERMINISTIC_EXT4_REBUILD_V1",
                            "evidenceSha256": "e" * 64,
                        },
                        "builderReceipts": [
                            {"builderIdentity": "builder-a", "builderDomain": "domain-a", "keyId": "receipt-a", "publicKeySha256": "4" * 64},
                            {"builderIdentity": "builder-b", "builderDomain": "domain-b", "keyId": "receipt-b", "publicKeySha256": "5" * 64},
                        ],
                        **{
                            role: {
                                "keyId": f"{role.lower()}_key",
                                "publicKeySha256": "a" * 64,
                            }
                            for role in (
                                "buildAttestation",
                                "sealEvidence",
                                "releaseSigning",
                            )
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ReleaseTrustError, "distinct key IDs"):
                load_policy(policy)

    @unittest.skipUnless(
        os.name == "posix" and os.geteuid() == 0,
        "root-owned directory and key policy is verified under root on Linux/WSL",
    )
    def test_controlled_snapshot_rejects_writable_ancestor_and_symlink_target(self) -> None:
        root = Path("/root") / f"ecobin-release-trust-test-{os.getpid()}"
        root.mkdir(mode=0o700)
        self.addCleanup(lambda: shutil.rmtree(root) if root.exists() else None)
        source = root / "source"
        source.write_bytes(b"authenticated payload")
        os.chmod(source, 0o600)

        unsafe = root / "unsafe"
        unsafe.mkdir(mode=0o777)
        os.chmod(unsafe, 0o777)
        unsafe_child = unsafe / "child"
        unsafe_child.mkdir(mode=0o700)
        with self.assertRaisesRegex(ReleaseTrustError, "parent chain"):
            validate_controlled_directory(unsafe_child)

        controlled = root / "controlled"
        controlled.mkdir(mode=0o700)
        oversized_destination = controlled / "oversized"
        with self.assertRaisesRegex(ReleaseTrustError, "maximum byte count"):
            snapshot_new(source, oversized_destination, 0o600, None, 4)
        self.assertFalse(oversized_destination.exists())

        destination = controlled / "snapshot"
        outside = root / "outside"
        outside.write_bytes(b"must survive")
        destination.symlink_to(outside)
        with self.assertRaises((FileExistsError, ReleaseTrustError)):
            snapshot_new(source, destination, 0o600, None)
        self.assertEqual(outside.read_bytes(), b"must survive")
        self.assertTrue(destination.is_symlink())

        destination.unlink()
        digest = snapshot_new(
            source,
            destination,
            0o600,
            "d2f75693596e4fea04f110826bed8e76b2514463ca783f9a5150f7901c2bf92a",
        )
        self.assertEqual(
            digest,
            "d2f75693596e4fea04f110826bed8e76b2514463ca783f9a5150f7901c2bf92a",
        )

    @unittest.skipUnless(
        os.name == "posix" and os.geteuid() == 0,
        "private-key ownership policy is verified under root on Linux/WSL",
    )
    def test_private_key_reader_rejects_non_root_owner(self) -> None:
        root = Path("/root") / f"ecobin-private-key-test-{os.getpid()}"
        root.mkdir(mode=0o700)
        self.addCleanup(lambda: shutil.rmtree(root) if root.exists() else None)
        key = root / "key.pem"
        key.write_text("not-even-a-key\n", encoding="ascii")
        os.chmod(key, 0o600)
        os.chown(key, 65534, 65534)
        with patch("release_trust.enforce_secret_memory_policy"):
            with self.assertRaisesRegex(ReleaseTrustError, "root:root"):
                public_key_fingerprint(key, private=True)

    @unittest.skipUnless(
        os.name == "posix" and os.geteuid() == 0,
        "swap policy is verified under root on Linux/WSL",
    )
    def test_private_key_entry_rejects_active_swap(self) -> None:
        for device in ("/swapfile", "/dev/zram0"):
            with self.subTest(device=device):
                swaps = (
                    "Filename Type Size Used Priority\n"
                    f"{device} partition 1024 0 -2\n"
                )
                with patch("resource.setrlimit"), patch(
                    "pathlib.Path.read_text",
                    return_value=swaps,
                ):
                    with self.assertRaisesRegex(
                        ReleaseTrustError,
                        "active swap is forbidden",
                    ):
                        enforce_secret_memory_policy("test signing")


if __name__ == "__main__":
    unittest.main()
