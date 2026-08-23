#!/usr/bin/env python3
"""Generate the canonical statement signed by a controlled build-attestation key."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

from release_trust import (
    GIT_COMMIT,
    RELEASE_ID,
    VERSION,
    ReleaseTrustError,
    input_lock_hashes,
    load_policy,
    sha256_file,
)
from validate_inputs import ValidationError, load_json, validate_inputs
from build_provenance import ProvenanceError, digest, validate_evidence, validate_receipts


def _same_file_contents(first: Path, second: Path) -> bool:
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as left, second.open("rb") as right:
        while True:
            left_chunk = left.read(1024 * 1024)
            right_chunk = right.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _require_distinct_regular(first: Path, second: Path, label: str) -> None:
    for path in (first, second):
        details = path.lstat()
        if path.is_symlink() or not path.is_file() or details.st_nlink != 1:
            raise ReleaseTrustError(f"{label} must be regular independent files")
    first_stat = first.stat()
    second_stat = second.stat()
    if (first_stat.st_dev, first_stat.st_ino) == (second_stat.st_dev, second_stat.st_ino):
        raise ReleaseTrustError(f"{label} must be physically distinct files")


def _git_output(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={repository}",
            "-C",
            str(repository),
            *arguments,
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ReleaseTrustError("cannot inspect the attested source repository")
    return result.stdout.strip()


def _validate_manifest(
    manifest_path: Path,
    *,
    image_path: Path,
    release_id: str,
    version: str,
    git_commit: str,
    locks: dict[str, str],
    layout: dict[str, Any],
    source_date_epoch: int,
) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    artifacts = manifest.get("artifacts")
    software = manifest.get("software")
    security = manifest.get("security")
    if (
        manifest.get("artifactClass") != "UNSIGNED_NO_SECRET_CANDIDATE"
        or manifest.get("releaseId") != release_id
        or manifest.get("version") != version
        or manifest.get("gitCommit") != git_commit
        or manifest.get("sourceDirty") is not False
        or manifest.get("sourceDateEpoch") != source_date_epoch
        or manifest.get("layout") != layout
        or manifest.get("locks") != {
            key: value for key, value in locks.items()
            if key != "formalReleasePolicySha256"
        }
        or not isinstance(artifacts, dict)
        or artifacts.get("rawImageBytes") != image_path.stat().st_size
        or artifacts.get("rawImageSha256") != sha256_file(image_path)
        or not isinstance(software, dict)
        or software.get("buildReproducibility") != {
            "rootfsDeterministic": True,
            "releaseEligible": True,
        }
        or not isinstance(security, dict)
        or security.get("k1Injected") is not False
        or security.get("setupApKeyInjected") is not False
        or security.get("deviceCredentialsPresent") is not False
        or security.get("signingState") != "UNSIGNED"
    ):
        raise ReleaseTrustError(
            "candidate manifest identity differs from independently measured build facts"
        )
    return manifest


def generate(args: argparse.Namespace) -> None:
    validated = validate_inputs(
        args.config_dir,
        require_locked=True,
        target_media_evidence_path=args.target_media_qualification_evidence,
    )
    policy = load_policy(args.trust_policy)
    if not RELEASE_ID.fullmatch(args.release_id):
        raise ReleaseTrustError("release ID is malformed")
    if not VERSION.fullmatch(args.version):
        raise ReleaseTrustError("version is malformed")
    if not GIT_COMMIT.fullmatch(args.git_commit):
        raise ReleaseTrustError("Git commit is malformed")
    if args.output.exists() or args.output.is_symlink():
        raise ReleaseTrustError("build attestation output already exists")
    _require_distinct_regular(args.candidate_a, args.candidate_b, "candidate images")
    _require_distinct_regular(args.manifest_a, args.manifest_b, "candidate manifests")
    if not _same_file_contents(args.candidate_a, args.candidate_b):
        raise ReleaseTrustError("independent candidate raw images differ byte-for-byte")
    if not _same_file_contents(args.manifest_a, args.manifest_b):
        raise ReleaseTrustError("independent candidate manifests differ byte-for-byte")
    expected_size = validated.layout["compactImage"]["fixedRawImageBytes"]
    if args.candidate_a.stat().st_size != expected_size:
        raise ReleaseTrustError("candidate image size differs from the locked layout")
    if (
        not args.software_payload_lock.is_file()
        or args.software_payload_lock.is_symlink()
        or sha256_file(args.software_payload_lock) != args.software_payload_sha256
    ):
        raise ReleaseTrustError("software payload lock differs from the expected digest")
    repository = args.repository_root.resolve(strict=True)
    if _git_output(repository, "rev-parse", "HEAD") != args.git_commit:
        raise ReleaseTrustError("source repository HEAD differs from the attested commit")
    if _git_output(repository, "status", "--porcelain", "--untracked-files=normal"):
        raise ReleaseTrustError("source worktree is dirty and cannot be attested")
    locks = input_lock_hashes(
        args.config_dir,
        args.software_payload_sha256,
        args.trust_policy,
    )
    source_date_epoch = validated.builder["sourceDateEpoch"]
    evidence = validate_evidence(args.rootfs_qualification_evidence, policy, validated.layout, args.config_dir / "image-layout.json", validated.builder)
    build_locks = {key: value for key, value in locks.items() if key != "formalReleasePolicySha256"}
    receipts = validate_receipts(
        [(args.receipt_a, args.receipt_signature_a, args.receipt_public_key_a, args.candidate_a, args.manifest_a),
         (args.receipt_b, args.receipt_signature_b, args.receipt_public_key_b, args.candidate_b, args.manifest_b)],
        policy, validated.builder, args.release_id, args.version, args.git_commit, build_locks,
    )
    _validate_manifest(
        args.manifest_a,
        image_path=args.candidate_a,
        release_id=args.release_id,
        version=args.version,
        git_commit=args.git_commit,
        locks=locks,
        layout=validated.layout,
        source_date_epoch=source_date_epoch,
    )
    _validate_manifest(
        args.manifest_b,
        image_path=args.candidate_b,
        release_id=args.release_id,
        version=args.version,
        git_commit=args.git_commit,
        locks=locks,
        layout=validated.layout,
        source_date_epoch=source_date_epoch,
    )
    if (
        _git_output(repository, "rev-parse", "HEAD") != args.git_commit
        or _git_output(repository, "status", "--porcelain", "--untracked-files=normal")
    ):
        raise ReleaseTrustError(
            "source repository changed during candidate attestation"
        )
    raw_sha = sha256_file(args.candidate_a)
    manifest_sha = sha256_file(args.manifest_a)
    attestation = {
        "$schema": "./schemas/build-attestation.schema.json",
        "schemaVersion": 2,
        "artifactClass": "AUTHENTICATED_REPRODUCIBLE_CANDIDATE_BUILD",
        "releaseId": args.release_id,
        "version": args.version,
        "gitCommit": args.git_commit,
        "sourceDateEpoch": source_date_epoch,
        "locks": locks,
        "rootfsQualification": {"evidenceSha256": digest(args.rootfs_qualification_evidence), "method": evidence["method"]},
        "buildReceipts": [
            {"invocationUid": item["invocationUid"], "builderIdentity": item["builderIdentity"], "builderDomain": item["builderDomain"], "receiptSha256": digest(path)}
            for item, path in zip(receipts, (args.receipt_a, args.receipt_b))
        ],
        "candidates": [
            {
                "role": "independent-a",
                "rawImageBytes": args.candidate_a.stat().st_size,
                "rawImageSha256": raw_sha,
                "manifestSha256": manifest_sha,
            },
            {
                "role": "independent-b",
                "rawImageBytes": args.candidate_b.stat().st_size,
                "rawImageSha256": raw_sha,
                "manifestSha256": manifest_sha,
            },
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
            "keyId": policy["buildAttestation"]["keyId"],
            "publicKeySha256": policy["buildAttestation"]["publicKeySha256"],
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(attestation, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--trust-policy", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--candidate-a", required=True, type=Path)
    parser.add_argument("--manifest-a", required=True, type=Path)
    parser.add_argument("--candidate-b", required=True, type=Path)
    parser.add_argument("--manifest-b", required=True, type=Path)
    parser.add_argument("--software-payload-lock", required=True, type=Path)
    parser.add_argument("--software-payload-sha256", required=True)
    parser.add_argument("--rootfs-qualification-evidence", required=True, type=Path)
    parser.add_argument(
        "--target-media-qualification-evidence", required=True, type=Path
    )
    for suffix in ("a", "b"):
        parser.add_argument(f"--receipt-{suffix}", required=True, type=Path)
        parser.add_argument(f"--receipt-signature-{suffix}", required=True, type=Path)
        parser.add_argument(f"--receipt-public-key-{suffix}", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        generate(args)
    except (OSError, ValidationError, ReleaseTrustError, ProvenanceError, json.JSONDecodeError) as error:
        print(f"build-attestation=FAIL: {error}", file=sys.stderr)
        return 2
    print(f"build-attestation=PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
