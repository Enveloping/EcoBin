#!/usr/bin/env python3
"""Generate deterministic public metadata for one sealed Orange Pi release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Iterable

from release_trust import (
    ReleaseTrustError,
    load_policy,
    sha256_file,
    verify_build_attestation,
    verify_seal_evidence,
)
from validate_inputs import (
    ValidationError,
    load_json,
    validate_target_media_qualification_evidence,
)


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
RELEASE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
LEGACY_COMPONENT_NAMES = (
    "hardwareRuntime",
    "enrollment",
    "remoteSupport",
    "factoryTest",
    "firstBoot",
)

COMPONENT_NAMES = LEGACY_COMPONENT_NAMES + (
    "communicationAgent",
    "deviceUpdater",
)
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_DPKG_STATUS_BYTES = 32 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024


class ReleaseMetadataError(RuntimeError):
    """A release input is incomplete, mutable, or inconsistent."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseMetadataError(f"JSON contains duplicate key: {key}")
        value[key] = item
    return value


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise ReleaseMetadataError("required metadata input is missing") from error
    if (
        stat.S_ISLNK(before.st_mode)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > maximum_bytes
    ):
        raise ReleaseMetadataError("metadata input is not a safe regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if (
            details.st_dev != before.st_dev
            or details.st_ino != before.st_ino
            or details.st_size != before.st_size
            or not stat.S_ISREG(details.st_mode)
        ):
            raise ReleaseMetadataError("metadata input changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise ReleaseMetadataError("metadata input exceeds size limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if after.st_size != details.st_size:
            raise ReleaseMetadataError("metadata input changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_json(path: Path) -> dict[str, Any]:
    raw = _read_regular(path, maximum_bytes=MAX_JSON_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseMetadataError("metadata JSON is malformed") from error
    if not isinstance(value, dict):
        raise ReleaseMetadataError("metadata JSON root must be an object")
    return value


def _sha256(path: Path) -> str:
    return sha256_file(path)


def _validate_rootfs(path: Path) -> Path:
    if not path.is_absolute():
        raise ReleaseMetadataError("rootfs path must be absolute")
    try:
        root = path.resolve(strict=True)
    except OSError as error:
        raise ReleaseMetadataError("rootfs is missing") from error
    if root == Path(root.anchor).resolve(strict=True):
        raise ReleaseMetadataError("refusing to inspect the host root")
    marker = root / "etc/os-release"
    if not marker.is_file() or marker.is_symlink():
        raise ReleaseMetadataError("rootfs has no regular os-release")
    return root


def _parse_dpkg_status(root: Path) -> list[dict[str, str]]:
    status_path = root / "var/lib/dpkg/status"
    raw = _read_regular(status_path, maximum_bytes=MAX_DPKG_STATUS_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as error:
        raise ReleaseMetadataError("dpkg status is not UTF-8") from error
    packages: list[dict[str, str]] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        fields: dict[str, str] = {}
        current: str | None = None
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")):
                if current is not None:
                    fields[current] += "\n" + line[1:]
                continue
            if ":" not in line:
                raise ReleaseMetadataError("dpkg status contains malformed field")
            current, value = line.split(":", 1)
            fields[current] = value.strip()
        if fields.get("Status") != "install ok installed":
            continue
        name = fields.get("Package", "")
        version = fields.get("Version", "")
        architecture = fields.get("Architecture", "")
        if not name or not version or not architecture:
            raise ReleaseMetadataError("installed dpkg package lacks identity")
        packages.append(
            {"name": name, "version": version, "architecture": architecture}
        )
    packages.sort(key=lambda item: (item["name"], item["architecture"], item["version"]))
    if not packages:
        raise ReleaseMetadataError("rootfs has no installed dpkg inventory")
    identities = [(item["name"], item["architecture"]) for item in packages]
    if len(identities) != len(set(identities)):
        raise ReleaseMetadataError("dpkg inventory contains duplicate package identity")
    return packages


def _metadata_paths(root: Path) -> Iterable[Path]:
    opt = root / "opt/ecobin"
    if not opt.is_dir() or opt.is_symlink():
        raise ReleaseMetadataError("installed EcoBin payload root is missing")
    for current_text, directory_names, file_names in os.walk(opt, followlinks=False):
        current = Path(current_text)
        safe_directories: list[str] = []
        for name in directory_names:
            child = current / name
            if child.is_symlink():
                continue
            safe_directories.append(name)
        directory_names[:] = safe_directories
        if current.name.endswith(".dist-info") and "METADATA" in file_names:
            candidate = current / "METADATA"
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ReleaseMetadataError("Python metadata escapes rootfs")
            yield candidate
            directory_names[:] = []


def _parse_python_packages(root: Path) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for metadata_path in _metadata_paths(root):
        raw = _read_regular(metadata_path, maximum_bytes=MAX_METADATA_BYTES)
        parsed = BytesParser().parsebytes(raw, headersonly=True)
        name = parsed.get("Name", "").strip()
        version = parsed.get("Version", "").strip()
        if not name or not version or "\n" in name or "\n" in version:
            raise ReleaseMetadataError("Python distribution lacks safe identity")
        location = metadata_path.parent.parent.relative_to(root).as_posix()
        packages.append({"name": name, "version": version, "location": "/" + location})
    packages.sort(key=lambda item: (item["location"], item["name"].lower(), item["version"]))
    if not packages:
        raise ReleaseMetadataError("EcoBin payload has no installed Python inventory")
    identities = [
        (item["location"], item["name"].lower())
        for item in packages
    ]
    if len(identities) != len(set(identities)):
        raise ReleaseMetadataError("Python inventory contains duplicate distribution")
    return packages


def _spdx_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:20]
    safe = re.sub(r"[^A-Za-z0-9.-]+", "-", parts[0]).strip("-.")[:40] or "item"
    return f"SPDXRef-{prefix}-{safe}-{digest}"


def _build_sbom(
    *,
    manifest: dict[str, Any],
    sealed_sha256: str,
    apt_packages: list[dict[str, str]],
    python_packages: list[dict[str, str]],
) -> dict[str, Any]:
    epoch = manifest.get("sourceDateEpoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise ReleaseMetadataError("candidate manifest has invalid release epoch")
    created = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    release_id = manifest.get("releaseId")
    version = manifest.get("version")
    if not isinstance(release_id, str) or not isinstance(version, str):
        raise ReleaseMetadataError("candidate manifest lacks release identity")
    namespace_seed = json.dumps(
        {
            "releaseId": release_id,
            "sealedSha256": sealed_sha256,
            "apt": apt_packages,
            "python": python_packages,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    namespace_digest = hashlib.sha256(namespace_seed).hexdigest()
    image_spdx = _spdx_id("Image", release_id, sealed_sha256)
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": image_spdx,
            "name": "ecobin-orangepi-zero3-image",
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "supplier": "Organization: EcoBin",
            "checksums": [{"algorithm": "SHA256", "checksumValue": sealed_sha256}],
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": image_spdx,
        }
    ]
    for item in apt_packages:
        package_id = _spdx_id(
            "Debian",
            item["name"],
            item["architecture"],
            item["version"],
        )
        packages.append(
            {
                "SPDXID": package_id,
                "name": item["name"],
                "versionInfo": item["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "supplier": "Organization: Debian",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            "pkg:deb/debian/"
                            f"{item['name']}@{item['version']}?arch={item['architecture']}"
                        ),
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": image_spdx,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": package_id,
            }
        )
    for item in python_packages:
        package_id = _spdx_id(
            "Python",
            item["name"],
            item["version"],
            item["location"],
        )
        packages.append(
            {
                "SPDXID": package_id,
                "name": item["name"],
                "versionInfo": item["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "supplier": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pypi/{item['name']}@{item['version']}",
                        "comment": f"Installed at {item['location']}",
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": image_spdx,
                "relationshipType": "CONTAINS",
                "relatedSpdxElement": package_id,
            }
        )
    packages.sort(key=lambda item: item["SPDXID"])
    relationships.sort(
        key=lambda item: (
            item["spdxElementId"],
            item["relationshipType"],
            item["relatedSpdxElement"],
        )
    )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"EcoBin Orange Pi image {release_id}",
        "documentNamespace": (
            "https://www.jinshoubao.com/spdx/orangepi/" + namespace_digest
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Organization: EcoBin", "Tool: ecobin-image-release-v1"],
        },
        "documentDescribes": [image_spdx],
        "packages": packages,
        "relationships": relationships,
    }


def _write_new(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    if path.exists() or path.is_symlink():
        raise ReleaseMetadataError("release metadata output already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def generate(args: argparse.Namespace) -> None:
    root = _validate_rootfs(args.rootfs)
    candidate = _read_json(args.candidate_manifest)
    controlled_layout = load_json(args.config_dir / "image-layout.json")
    if candidate.get("layout") != controlled_layout:
        raise ReleaseMetadataError(
            "candidate target-media layout differs from controlled image layout"
        )
    validate_target_media_qualification_evidence(
        args.target_media_qualification_evidence,
        controlled_layout,
    )
    attestation = verify_build_attestation(
        config_dir=args.config_dir,
        trust_policy_path=args.trust_policy,
        attestation_path=args.build_attestation,
        signature_path=args.build_attestation_signature,
        public_key_path=args.build_attestation_public_key,
        expected_manifest_path=args.candidate_manifest,
    )
    evidence = verify_seal_evidence(
        config_dir=args.config_dir,
        trust_policy_path=args.trust_policy,
        evidence_path=args.seal_evidence,
        signature_path=args.seal_evidence_signature,
        public_key_path=args.seal_evidence_public_key,
        build_attestation_path=args.build_attestation,
        sealed_image_path=args.sealed_image,
        expected_release_id=attestation["releaseId"],
        expected_version=attestation["version"],
        expected_git_commit=attestation["gitCommit"],
    )
    if candidate.get("artifactClass") != "UNSIGNED_NO_SECRET_CANDIDATE":
        raise ReleaseMetadataError("candidate manifest has wrong artifact class")
    if (
        candidate.get("sourceDirty") is not False
        or candidate.get("releaseId") != attestation["releaseId"]
        or candidate.get("version") != attestation["version"]
        or candidate.get("gitCommit") != attestation["gitCommit"]
        or candidate.get("sourceDateEpoch") != attestation["sourceDateEpoch"]
    ):
        raise ReleaseMetadataError("candidate identity differs from authenticated build facts")
    software = candidate.get("software")
    if not isinstance(software, dict):
        raise ReleaseMetadataError("candidate manifest lacks software inventory")
    if software.get("runtimeInstalled") is not True or software.get("factoryPortalInstalled") is not True:
        raise ReleaseMetadataError("candidate image payload is incomplete")
    candidate_schema_version = candidate.get("schemaVersion")
    if isinstance(candidate_schema_version, bool):
        raise ReleaseMetadataError("candidate manifest schema version is unsupported")
    if candidate_schema_version == 1:
        component_names = LEGACY_COMPONENT_NAMES
        if "payloadSchemaVersion" in software:
            raise ReleaseMetadataError("schema-v1 candidate carries schema-v2 software facts")
    elif candidate_schema_version == 2:
        component_names = COMPONENT_NAMES
        if software.get("payloadSchemaVersion") != 2:
            raise ReleaseMetadataError("candidate software payload schema is not v2")
    else:
        raise ReleaseMetadataError("candidate manifest schema version is unsupported")
    # Unsigned candidate booleans are not authorization. verify_build_attestation
    # has already authenticated their exact true values together with two
    # independently audited byte-identical no-secret raws.
    software = dict(software)
    software["buildReproducibility"] = {
        "rootfsDeterministic": True,
        "releaseEligible": True,
        "proof": "AUTHENTICATED_TWO_CANDIDATE_BYTE_MATCH",
        "candidateCount": 2,
    }
    components = software.get("components")
    if not isinstance(components, dict) or set(components) != set(component_names):
        raise ReleaseMetadataError("candidate component release identities are incomplete")
    for name in component_names:
        release_id = components[name]
        if not isinstance(release_id, str) or not RELEASE_ID_PATTERN.fullmatch(release_id):
            raise ReleaseMetadataError(
                f"candidate component release identity is invalid: {name}"
            )
        if (
            candidate_schema_version == 2
            and name in {"communicationAgent", "deviceUpdater"}
            and len(release_id) > 32
        ):
            raise ReleaseMetadataError(
                f"candidate component release identity exceeds device fact limit: {name}"
            )
    implemented = software.get("implementedSlices")
    if implemented != [f"P{number}" for number in range(1, 9)]:
        raise ReleaseMetadataError("candidate has not completed P1 through P8")
    candidate_artifacts = candidate.get("artifacts")
    if not isinstance(candidate_artifacts, dict):
        raise ReleaseMetadataError("candidate manifest lacks artifact identity")
    target_media_evidence_sha256 = _sha256(
        args.target_media_qualification_evidence
    )
    if (
        candidate_artifacts.get("targetMediaQualificationEvidenceFile")
        != "target-media-qualification-evidence.json"
        or candidate_artifacts.get("targetMediaQualificationEvidenceSha256")
        != target_media_evidence_sha256
    ):
        raise ReleaseMetadataError(
            "candidate manifest target-media evidence differs from supplied evidence"
        )
    if (
        evidence.get("candidateRawImageSha256")
        != candidate_artifacts.get("rawImageSha256")
    ):
        raise ReleaseMetadataError("seal evidence is not bound to candidate manifest")
    if not KEY_ID_PATTERN.fullmatch(args.signing_key_id):
        raise ReleaseMetadataError("image signing key ID is invalid")
    if not SHA256_PATTERN.fullmatch(args.signing_public_key_sha256):
        raise ReleaseMetadataError("image signing public key fingerprint is invalid")
    policy = load_policy(args.trust_policy)
    if (
        args.signing_key_id != policy["releaseSigning"]["keyId"]
        or args.signing_public_key_sha256
        != policy["releaseSigning"]["publicKeySha256"]
    ):
        raise ReleaseMetadataError("release signing identity differs from external policy")

    sealed_sha256 = _sha256(args.sealed_image)
    compressed_sha256 = _sha256(args.compressed_image)
    if evidence.get("sealedRawImageSha256") != sealed_sha256:
        raise ReleaseMetadataError("sealed image does not match authenticated evidence")
    if evidence.get("rawImageBytes") != args.sealed_image.stat().st_size:
        raise ReleaseMetadataError("sealed image byte length does not match evidence")
    if not SHA256_PATTERN.fullmatch(sealed_sha256) or not SHA256_PATTERN.fullmatch(compressed_sha256):
        raise ReleaseMetadataError("release artifact digest is invalid")

    output = args.output_directory.resolve(strict=True)
    if not output.is_dir() or output.is_symlink():
        raise ReleaseMetadataError("release metadata output is unsafe")
    apt_packages = _parse_dpkg_status(root)
    python_packages = _parse_python_packages(root)
    apt_text = "".join(
        f"{item['name']}:{item['architecture']}={item['version']}\n"
        for item in apt_packages
    ).encode("utf-8")
    python_text = "".join(
        f"{item['location']} {item['name']}=={item['version']}\n"
        for item in python_packages
    ).encode("utf-8")
    apt_path = output / "apt-packages.txt"
    python_path = output / "python-packages.txt"
    sbom_path = output / "sbom.spdx.json"
    manifest_path = output / "image-manifest.json"
    _write_new(apt_path, apt_text)
    _write_new(python_path, python_text)
    sbom = _build_sbom(
        manifest=candidate,
        sealed_sha256=sealed_sha256,
        apt_packages=apt_packages,
        python_packages=python_packages,
    )
    _write_new(sbom_path, _json_bytes(sbom))

    security = dict(candidate.get("security") or {})
    security.update(
        {
            "enrollmentKeyId": "K1",
            "k1Injected": True,
            "setupApKeyInjected": True,
            "deviceCredentialsPresent": False,
            "signingState": "SIGNED",
            "imageSigningKeyId": args.signing_key_id,
            "imageSigningPublicKeySha256": args.signing_public_key_sha256,
        }
    )
    compressed_name = args.compressed_image.name
    checksum_name = "release-checksums.txt"
    signature_name = "release-checksums.sig"
    release_manifest = {
        "$schema": "./schemas/release-manifest.schema.json",
        "schemaVersion": candidate_schema_version,
        "artifactClass": "SEALED_SIGNED_RELEASE",
        "releaseId": candidate["releaseId"],
        "version": candidate["version"],
        "gitCommit": candidate["gitCommit"],
        "sourceDirty": False,
        "sourceDateEpoch": candidate["sourceDateEpoch"],
        "baseImage": candidate["baseImage"],
        "builder": candidate["builder"],
        "locks": attestation["locks"],
        "platform": candidate["platform"],
        "layout": candidate["layout"],
        "software": software,
        "factory": candidate["factory"],
        "security": security,
        "audit": {
            "candidateAuditPassed": True,
            "sealedAuditPassed": True,
            "candidateReproducibilityAttested": True,
            "authenticatedSealEvidenceVerified": True,
            "deviceUniqueStateAbsent": True,
        },
        "artifacts": {
            "candidateRawImageSha256": evidence["candidateRawImageSha256"],
            "sealedRawImageBytes": args.sealed_image.stat().st_size,
            "sealedRawImageSha256": sealed_sha256,
            "compressedImageFile": compressed_name,
            "compressedImageBytes": args.compressed_image.stat().st_size,
            "compressedImageSha256": compressed_sha256,
            "checksumFile": checksum_name,
            "signatureFile": signature_name,
            "sbomFile": sbom_path.name,
            "sbomSha256": _sha256(sbom_path),
            "aptPackagesFile": apt_path.name,
            "aptPackagesSha256": _sha256(apt_path),
            "pythonPackagesFile": python_path.name,
            "pythonPackagesSha256": _sha256(python_path),
            "buildAttestationFile": "build-attestation.json",
            "buildAttestationSha256": sha256_file(args.build_attestation),
            "buildAttestationSignatureFile": "build-attestation.sig",
            "buildAttestationSignatureSha256": sha256_file(
                args.build_attestation_signature
            ),
            "targetMediaQualificationEvidenceFile":
                "target-media-qualification-evidence.json",
            "targetMediaQualificationEvidenceSha256":
                target_media_evidence_sha256,
            "sealEvidenceFile": "seal-evidence.json",
            "sealEvidenceSha256": sha256_file(args.seal_evidence),
            "sealEvidenceSignatureFile": "seal-evidence.sig",
            "sealEvidenceSignatureSha256": sha256_file(
                args.seal_evidence_signature
            ),
        },
    }
    serialized = _json_bytes(release_manifest)
    lowered = serialized.lower()
    if b"privatekey" in lowered or b"setup-ap-key=" in lowered:
        raise ReleaseMetadataError("release manifest contains forbidden secret material")
    _write_new(manifest_path, serialized)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--trust-policy", required=True, type=Path)
    parser.add_argument("--candidate-manifest", required=True, type=Path)
    parser.add_argument(
        "--target-media-qualification-evidence", required=True, type=Path
    )
    parser.add_argument("--build-attestation", required=True, type=Path)
    parser.add_argument("--build-attestation-signature", required=True, type=Path)
    parser.add_argument("--build-attestation-public-key", required=True, type=Path)
    parser.add_argument("--seal-evidence", required=True, type=Path)
    parser.add_argument("--seal-evidence-signature", required=True, type=Path)
    parser.add_argument("--seal-evidence-public-key", required=True, type=Path)
    parser.add_argument("--sealed-image", required=True, type=Path)
    parser.add_argument("--compressed-image", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--signing-key-id", required=True)
    parser.add_argument("--signing-public-key-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    try:
        generate(args)
    except (OSError, ValidationError, ReleaseMetadataError, ReleaseTrustError) as error:
        print(f"release-metadata=FAIL: {error}", file=sys.stderr)
        return 2
    print(f"release-metadata=PASS output={args.output_directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
