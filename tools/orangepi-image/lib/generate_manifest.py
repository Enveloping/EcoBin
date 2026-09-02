#!/usr/bin/env python3
"""Generate a deterministic unsigned manifest for the complete image."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

from validate_inputs import RELEASE_VERSION, ValidationError, load_json, validate_inputs


RELEASE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True, type=pathlib.Path)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--source-dirty", choices=("true", "false"), required=True)
    parser.add_argument("--software-payload-lock", required=True, type=pathlib.Path)
    parser.add_argument("--software-payload-sha256", required=True)
    parser.add_argument("--rootfs-deterministic", choices=("true", "false"), required=True)
    parser.add_argument(
        "--target-media-qualification-evidence",
        required=True,
        type=pathlib.Path,
    )
    parser.add_argument(
        "--repository-root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[3],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        validated = validate_inputs(
            args.config_dir,
            require_locked=True,
            target_media_evidence_path=args.target_media_qualification_evidence,
        )
        if not RELEASE_ID.fullmatch(args.release_id):
            raise ValidationError("invalid release ID")
        if not RELEASE_VERSION.fullmatch(args.version):
            raise ValidationError("version must use semantic form x.y.z")
        if not GIT_COMMIT.fullmatch(args.git_commit):
            raise ValidationError("git commit must be 40 lowercase hexadecimal characters")
        if not args.image.is_file() or args.image.is_symlink():
            raise ValidationError("candidate image must be a regular non-symlink file")
        if args.output.exists():
            raise ValidationError("manifest output already exists")
        image_size = args.image.stat().st_size
        expected_size = validated.layout["compactImage"]["fixedRawImageBytes"]
        if image_size != expected_size:
            raise ValidationError("candidate image size does not match locked layout")
        if not re.fullmatch(r"[0-9a-f]{64}", args.software_payload_sha256):
            raise ValidationError("software payload SHA-256 is malformed")
        if (
            not args.software_payload_lock.is_file()
            or args.software_payload_lock.is_symlink()
            or sha256(args.software_payload_lock) != args.software_payload_sha256
        ):
            raise ValidationError("software payload lock identity differs")
        software_payload = load_json(args.software_payload_lock)
        components = software_payload.get("components")
        payload_schema_version = software_payload.get("schemaVersion")
        legacy_component_names = (
            "hardwareRuntime",
            "enrollment",
            "remoteSupport",
            "factoryTest",
            "firstBoot",
        )
        component_names = legacy_component_names + (
            "communicationAgent",
            "deviceUpdater",
        ) if payload_schema_version == 2 else legacy_component_names
        if (
            isinstance(payload_schema_version, bool)
            or payload_schema_version not in (1, 2)
            or software_payload.get("lockState") != "LOCKED"
            or software_payload.get("sourceGitCommit") != args.git_commit
            or not isinstance(components, dict)
            or set(components) != set(component_names)
        ):
            raise ValidationError("software payload lock is not bound to this build")
        software_components = {}
        for name in component_names:
            release = components[name]
            release_id = release.get("releaseId") if isinstance(release, dict) else None
            if not isinstance(release_id, str) or not RELEASE_ID.fullmatch(release_id):
                raise ValidationError(f"software component has no real release ID: {name}")
            if (
                payload_schema_version == 2
                and name in {"communicationAgent", "deviceUpdater"}
                and len(release_id) > 32
            ):
                raise ValidationError(
                    f"software component release ID exceeds device fact limit: {name}"
                )
            software_components[name] = release_id

        repository_root = args.repository_root.resolve(strict=True)
        seal_example = repository_root / "contracts/examples/onenet/authorize-factory-seal.command.json"
        try:
            seal_contract = load_json(seal_example)
        except ValidationError as exc:
            raise ValidationError("factory-seal contract identity is unavailable") from exc
        seal_payload = seal_contract.get("payload")
        migration = (
            repository_root
            / "ecobin-bootstrap/src/main/resources/db/p0-migration/"
            "V56__factory_seal_authorization.sql"
        )
        if (
            seal_contract.get("schemaVersion") != 2
            or seal_contract.get("payloadSchemaVersion") != 2
            or not isinstance(seal_payload, dict)
            or seal_payload.get("sealAuthorizationSchemaVersion") != 1
            or migration.is_symlink()
            or not migration.is_file()
            or "CREATE TABLE dev_factory_seal_authorization"
            not in migration.read_text(encoding="utf-8")
        ):
            raise ValidationError("factory-seal contract/migration revisions are incomplete")

        source = validated.source
        builder = validated.builder
        layout = validated.layout
        config_dir = validated.config_dir
        safe_gpio_source = (
            repository_root / "hardware/system/mcu_safe_gpio.py"
        )
        safe_gpio_unit = (
            repository_root / "hardware/ecobin-mcu-safe-gpio.service"
        )
        expansion_source = config_dir / "expand-rootfs.sh"
        expansion_unit = (
            repository_root / "hardware/ecobin-expand-rootfs.service"
        )
        for component in (
            safe_gpio_source,
            safe_gpio_unit,
            expansion_source,
            expansion_unit,
        ):
            if not component.is_file() or component.is_symlink():
                raise ValidationError(
                    "immutable board component source is missing or unsafe"
                )
        manifest = {
            "$schema": "./schemas/image-manifest.schema.json",
            "schemaVersion": payload_schema_version,
            "artifactClass": "UNSIGNED_NO_SECRET_CANDIDATE",
            "releaseId": args.release_id,
            "version": args.version,
            "gitCommit": args.git_commit,
            "sourceDirty": args.source_dirty == "true",
            "sourceDateEpoch": builder["sourceDateEpoch"],
            "baseImage": {
                "url": source["artifact"]["url"],
                "fileName": source["artifact"]["fileName"],
                "sha256": source["artifact"]["sha256"],
                "downloadBytes": source["artifact"]["downloadBytes"],
                "extractedImageBytes": source["artifact"]["extractedImageBytes"],
                "extractedImageSha256": source["artifact"]["extractedImageSha256"],
            },
            "builder": {
                "reference": builder["container"]["reference"],
                "digest": builder["container"]["digest"],
                "platform": builder["container"]["platform"],
                "uvArtifact": builder["uvArtifact"],
                "tools": builder["tools"],
            },
            "locks": {
                "sourceLockSha256": sha256(config_dir / "source.lock.json"),
                "builderLockSha256": sha256(config_dir / "builder.lock"),
                "aptPackagesLockSha256": sha256(config_dir / "apt-packages.lock"),
                "imageLayoutSha256": sha256(config_dir / "image-layout.json"),
                "softwarePayloadLockSha256": args.software_payload_sha256,
            },
            "platform": {
                "board": source["board"],
                "operatingSystem": source["operatingSystem"],
                "pythonTarget": "3.11",
            },
            "layout": layout,
            "software": {
                "implementedSlices": [
                    "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8"
                ],
                "runtimeInstalled": True,
                **(
                    {"payloadSchemaVersion": payload_schema_version}
                    if payload_schema_version == 2
                    else {}
                ),
                "components": software_components,
                "uart5": {
                    "configured": True,
                    "overlay": "ph-uart5",
                    "conflictingOverlay": "ph-pwm12",
                    "device": "/dev/ttyS5",
                    "applicationSerial": "115200/8N1",
                    "romSerial": "115200/8E1",
                    "linuxConsoleDisabled": True,
                    "gettyMasked": True,
                },
                "mcuBootControl": {
                    "boot0Wpi": 2,
                    "boot0ActiveLevel": 1,
                    "resetGateWpi": 5,
                    "resetGateActiveLevel": 1,
                    "gpioExecutable": "/usr/bin/gpio",
                    "safeHelperSha256": sha256(safe_gpio_source),
                    "safeUnitSha256": sha256(safe_gpio_unit),
                },
                "rootfsExpansion": {
                    "installed": True,
                    "helperSha256": sha256(expansion_source),
                    "unitSha256": sha256(expansion_unit),
                    "enabledIndirectlyBy": "ecobin-first-boot.service",
                    "strategy": layout["firstBootExpansion"]["strategy"],
                },
                "factoryPortalInstalled": True,
                "buildReproducibility": {
                    "rootfsDeterministic": args.rootfs_deterministic == "true",
                    "releaseEligible": (
                        args.rootfs_deterministic == "true"
                        and args.source_dirty == "false"
                    ),
                },
            },
            "factory": {
                "targetMedia": "32 GB TF",
                "firstBootExpansionSchemaVersion": layout["firstBootExpansion"]["schemaVersion"],
                "stateSchemaVersion": 1,
                "sealContractVersion": seal_payload[
                    "sealAuthorizationSchemaVersion"
                ],
                "commandEnvelopeSchemaVersion": seal_contract["schemaVersion"],
                "databaseMigration": "V56",
            },
            "security": {
                "enrollmentKeyId": "K1",
                "k1Injected": False,
                "setupApKeyInjected": False,
                "deviceCredentialsPresent": False,
                "persistentSwapDisabled": True,
                "signingState": "UNSIGNED",
            },
            "artifacts": {
                "rawImageFile": args.image.name,
                "rawImageBytes": image_size,
                "rawImageSha256": sha256(args.image),
                "targetMediaQualificationEvidenceFile":
                    "target-media-qualification-evidence.json",
                "targetMediaQualificationEvidenceSha256": sha256(
                    args.target_media_qualification_evidence
                ),
            },
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except (OSError, ValidationError) as exc:
        print(f"candidate-manifest=FAIL: {exc}", file=sys.stderr)
        return 2
    print(f"candidate-manifest=PASS output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
