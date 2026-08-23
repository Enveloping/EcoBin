#!/usr/bin/env bash
set -euo pipefail
umask 077

archive_path=""
checksums_file=""
signature_file=""
public_key_file=""
manifest_file=""
signing_key_id=""
device_path=""
confirmed_device_path=""
verify_only=false

fail() {
    printf 'card-flash=FAIL: %s\n' "$1" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: flash-and-verify.sh --image-zst FILE --checksums-file FILE
       --signature-file FILE --public-key-file FILE --manifest FILE
       --signing-key-id ID --device /dev/DEVICE
       --confirm-device /dev/DEVICE
  flash-and-verify.sh --verify-only --image-zst FILE --checksums-file FILE
       --signature-file FILE --public-key-file FILE --manifest FILE
       --signing-key-id ID

WARNING: This destroys data on the confirmed device. The trusted public key is
external to the release directory. Signature verification precedes artifact
hashing; the target is written only after the compressed and raw payloads both
match the release manifest.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --image-zst)
            [[ $# -ge 2 ]] || fail "--image-zst requires a value"
            archive_path="$2"
            shift 2
            ;;
        --checksums-file)
            [[ $# -ge 2 ]] || fail "--checksums-file requires a value"
            checksums_file="$2"
            shift 2
            ;;
        --signature-file)
            [[ $# -ge 2 ]] || fail "--signature-file requires a value"
            signature_file="$2"
            shift 2
            ;;
        --public-key-file)
            [[ $# -ge 2 ]] || fail "--public-key-file requires a value"
            public_key_file="$2"
            shift 2
            ;;
        --manifest)
            [[ $# -ge 2 ]] || fail "--manifest requires a value"
            manifest_file="$2"
            shift 2
            ;;
        --signing-key-id)
            [[ $# -ge 2 ]] || fail "--signing-key-id requires a value"
            signing_key_id="$2"
            shift 2
            ;;
        --device)
            [[ $# -ge 2 ]] || fail "--device requires a value"
            device_path="$2"
            shift 2
            ;;
        --confirm-device)
            [[ $# -ge 2 ]] || fail "--confirm-device requires a value"
            confirmed_device_path="$2"
            shift 2
            ;;
        --verify-only)
            verify_only=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) fail "unknown argument" ;;
    esac
done

[[ -n "${archive_path}" && -n "${checksums_file}" \
    && -n "${signature_file}" && -n "${public_key_file}" \
    && -n "${manifest_file}" && -n "${signing_key_id}" ]] \
    || fail "all signed release inputs are required"
[[ "${signing_key_id}" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] \
    || fail "signing key ID has an invalid format"
if [[ "${verify_only}" = true ]]; then
    [[ -z "${device_path}${confirmed_device_path}" ]] \
        || fail "--verify-only does not accept a target device"
else
    [[ -n "${device_path}" && -n "${confirmed_device_path}" ]] \
        || fail "card writing requires both repeated target inputs"
    [[ "${device_path}" = "${confirmed_device_path}" ]] \
        || fail "--device and --confirm-device must be identical"
    [[ "${device_path}" = /dev/* && "${device_path}" != /dev/ ]] \
        || fail "target must use an explicit /dev path"
    [[ "$(id -u)" = 0 ]] || fail "writing removable media requires root"
fi

for command_name in \
    python3 sha256sum stat readlink lsblk findmnt blockdev dd sync grep \
    openssl zstd awk; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required command is missing: ${command_name}"
done
for input in \
    "${archive_path}" "${checksums_file}" "${signature_file}" \
    "${public_key_file}" "${manifest_file}"; do
    [[ -f "${input}" && ! -L "${input}" ]] \
        || fail "release input must be a regular non-symlink file"
    [[ "$(stat -c '%h' -- "${input}")" = 1 ]] \
        || fail "release input must not be hard-linked"
done
archive_path="$(readlink -f -- "${archive_path}")"
checksums_file="$(readlink -f -- "${checksums_file}")"
signature_file="$(readlink -f -- "${signature_file}")"
public_key_file="$(readlink -f -- "${public_key_file}")"
manifest_file="$(readlink -f -- "${manifest_file}")"
[[ "$(basename -- "${archive_path}")" = *.img.zst ]] \
    || fail "release image must be a .img.zst artifact"
[[ "$(stat -c '%s' -- "${signature_file}")" = 64 ]] \
    || fail "detached signature is not a raw Ed25519 signature"
public_mode="$(stat -c '%a' -- "${public_key_file}")"
[[ $((8#${public_mode} & 8#022)) = 0 \
    && "$(stat -c '%s' -- "${public_key_file}")" -le 16384 ]] \
    || fail "trusted public key permissions or size are unsafe"
trusted_public_key_sha256="$(openssl pkey -pubin -in "${public_key_file}" \
    -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
[[ "${trusted_public_key_sha256}" =~ ^[0-9a-f]{64}$ ]] \
    || fail "trusted image public key is invalid"

# This is deliberately the first content-authenticity operation. The signature
# covers the exact canonical release inventory; only after it verifies do we
# hash the listed artifacts.
openssl pkeyutl -verify -pubin -inkey "${public_key_file}" -rawin \
    -in "${checksums_file}" -sigfile "${signature_file}" </dev/null \
    >/dev/null 2>&1 || fail "image release signature verification failed"

release_directory="$(dirname -- "${checksums_file}")"
[[ "$(basename -- "${checksums_file}")" = release-checksums.txt \
    && "${archive_path}" = "${release_directory}/$(basename -- "${archive_path}")" ]] \
    || fail "release inputs must come from one canonical release directory"
expected_names=(
    "$(basename -- "${archive_path}")"
    image-manifest.json
    sbom.spdx.json
    apt-packages.txt
    python-packages.txt
    build-attestation.json
    build-attestation.sig
    rootfs-qualification-evidence.json
    target-media-qualification-evidence.json
    seal-evidence.json
    seal-evidence.sig
    schemas/release-manifest.schema.json
    schemas/build-attestation.schema.json
    schemas/rootfs-qualification-evidence.schema.json
    schemas/target-media-qualification-evidence.schema.json
    schemas/seal-evidence.schema.json
    flash-and-verify.sh
    flash-and-verify.ps1
    factory-checklist.md
)
[[ "${manifest_file}" = "${release_directory}/image-manifest.json" ]] \
    || fail "manifest must be the signed release-directory manifest"
[[ "${signature_file}" = "${release_directory}/release-checksums.sig" ]] \
    || fail "signature must be the canonical release inventory signature"
mapfile -t checksum_lines < "${checksums_file}"
[[ ${#checksum_lines[@]} = ${#expected_names[@]} ]] \
    || fail "release inventory entry count is invalid"
expected_compressed_sha=""
for index in "${!expected_names[@]}"; do
    checksum_line="${checksum_lines[index]}"
    [[ "${checksum_line}" =~ ^([0-9a-f]{64})\ \ ([A-Za-z0-9._/-]+)$ ]] \
        || fail "release inventory line has an invalid format"
    expected_digest="${BASH_REMATCH[1]}"
    relative_name="${BASH_REMATCH[2]}"
    [[ "${relative_name}" = "${expected_names[index]}" \
        && "${relative_name}" != /* \
        && "${relative_name}" != *../* \
        && "${relative_name}" != ../* ]] \
        || fail "release inventory names or order differ from policy"
    controlled_file="${release_directory}/${relative_name}"
    [[ -f "${controlled_file}" && ! -L "${controlled_file}" \
        && "$(stat -c '%h' -- "${controlled_file}")" = 1 ]] \
        || fail "signed release inventory member is unsafe"
    actual_digest="$(sha256sum -- "${controlled_file}" | awk '{print $1}')"
    [[ "${actual_digest}" = "${expected_digest}" ]] \
        || fail "signed release inventory member digest differs"
    if [[ "${index}" = 0 ]]; then expected_compressed_sha="${expected_digest}"; fi
done
actual_compressed_sha="$(sha256sum -- "${archive_path}" | awk '{print $1}')"
[[ "${actual_compressed_sha}" = "${expected_compressed_sha}" ]] \
    || fail "compressed image SHA-256 verification failed"

readarray -t manifest_values < <(python3 - \
    "${manifest_file}" "$(basename -- "${archive_path}")" \
    "${actual_compressed_sha}" "$(stat -c '%s' -- "${archive_path}")" \
    "${signing_key_id}" "${trusted_public_key_sha256}" \
    "${release_directory}/target-media-qualification-evidence.json" <<'PY'
import datetime
import hashlib
import json
import pathlib
import re
import sys

pairs_seen = []
def no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value

manifest = json.loads(
    pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"),
    object_pairs_hook=no_duplicates,
)
artifacts = manifest.get("artifacts", {})
security = manifest.get("security", {})
if (
    manifest.get("artifactClass") != "SEALED_SIGNED_RELEASE"
    or manifest.get("sourceDirty") is not False
    or security.get("signingState") != "SIGNED"
    or security.get("imageSigningKeyId") != sys.argv[5]
    or security.get("imageSigningPublicKeySha256") != sys.argv[6]
    or artifacts.get("compressedImageFile") != sys.argv[2]
    or artifacts.get("compressedImageSha256") != sys.argv[3]
    or artifacts.get("compressedImageBytes") != int(sys.argv[4])
    or artifacts.get("checksumFile") != "release-checksums.txt"
    or artifacts.get("signatureFile") != "release-checksums.sig"
    or any(
        key.lower() in {"manifestsha256", "imagemanifestsha256"}
        for key in artifacts
    )
    or not isinstance(artifacts.get("sealedRawImageBytes"), int)
    or artifacts["sealedRawImageBytes"] <= 0
    or not re.fullmatch(r"[0-9a-f]{64}", artifacts.get("sealedRawImageSha256", ""))
):
    raise SystemExit("release manifest is inconsistent with signed artifact")
print(artifacts["sealedRawImageBytes"])
print(artifacts["sealedRawImageSha256"])
layout = manifest.get("layout", {})
target = layout.get("targetMedia", {}) if isinstance(layout, dict) else {}
evidence_path = pathlib.Path(sys.argv[7])
evidence_payload = evidence_path.read_bytes()
evidence_sha256 = hashlib.sha256(evidence_payload).hexdigest()
evidence = json.loads(evidence_payload.decode("utf-8"), object_pairs_hook=no_duplicates)
required_evidence_keys = {
    "$schema", "schemaVersion", "artifactClass", "qualificationState",
    "method", "deviceClass", "marketedCapacityGB", "marketedCapacityBytes",
    "batchId", "measuredAt", "measurementTool", "sampleSelection",
    "minimumQualifiedMediaBytes", "measurements",
}
if (
    target.get("qualificationState") != "QUALIFIED"
    or isinstance(target.get("minimumQualifiedMediaBytes"), bool)
    or not isinstance(target.get("minimumQualifiedMediaBytes"), int)
    or target["minimumQualifiedMediaBytes"] <= 0
    or target.get("marketedCapacityGB") != 32
    or target.get("marketedCapacityBytes") != 32000000000
    or target.get("evidenceSha256") != evidence_sha256
    or artifacts.get("targetMediaQualificationEvidenceFile")
       != "target-media-qualification-evidence.json"
    or artifacts.get("targetMediaQualificationEvidenceSha256") != evidence_sha256
    or not isinstance(evidence, dict)
    or set(evidence) != required_evidence_keys
    or evidence.get("$schema")
       != "./schemas/target-media-qualification-evidence.schema.json"
    or evidence.get("schemaVersion") != 1
    or evidence.get("artifactClass")
       != "TARGET_MEDIA_BATCH_CAPACITY_QUALIFICATION"
    or evidence.get("qualificationState") != "QUALIFIED"
    or evidence.get("method") != "BLOCK_DEVICE_CAPACITY_SAMPLE_MINIMUM_V1"
    or evidence.get("deviceClass") != "TF_CARD"
    or evidence.get("marketedCapacityGB") != 32
    or evidence.get("marketedCapacityBytes") != 32000000000
    or evidence.get("measurementTool") != "blockdev --getsize64"
    or evidence.get("sampleSelection")
       != "SAME_PROCUREMENT_BATCH_MULTIPLE_CARDS"
    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", evidence.get("batchId", ""))
    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", evidence.get("measuredAt", ""))
):
    raise SystemExit("signed release manifest lacks qualified target-media capacity")
try:
    datetime.datetime.strptime(evidence["measuredAt"], "%Y-%m-%dT%H:%M:%SZ")
except ValueError:
    raise SystemExit("signed target-media evidence has invalid measurement time") from None
measurements = evidence.get("measurements")
if not isinstance(measurements, list) or not 2 <= len(measurements) <= 256:
    raise SystemExit("signed target-media evidence lacks multiple card samples")
sample_ids = set()
measured_sizes = []
for measurement in measurements:
    if not isinstance(measurement, dict) or set(measurement) != {
        "sampleId", "measuredBytes", "logicalSectorBytes", "wholeDevice"
    }:
        raise SystemExit("signed target-media evidence has malformed samples")
    sample_id = measurement.get("sampleId")
    measured_bytes = measurement.get("measuredBytes")
    if (
        not isinstance(sample_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", sample_id)
        or sample_id in sample_ids
        or isinstance(measured_bytes, bool)
        or not isinstance(measured_bytes, int)
        or measured_bytes < 30000000000
        or measured_bytes % 512 != 0
        or measurement.get("logicalSectorBytes") != 512
        or measurement.get("wholeDevice") is not True
    ):
        raise SystemExit("signed target-media evidence has invalid capacity samples")
    sample_ids.add(sample_id)
    measured_sizes.append(measured_bytes)
if (
    evidence.get("minimumQualifiedMediaBytes") != min(measured_sizes)
    or evidence["minimumQualifiedMediaBytes"] != target["minimumQualifiedMediaBytes"]
):
    raise SystemExit("signed target-media minimum differs from measured samples")
print(target["minimumQualifiedMediaBytes"])
PY
)
[[ ${#manifest_values[@]} = 3 ]] \
    || fail "release manifest did not provide raw image identity"
expected_raw_size="${manifest_values[0]}"
expected_raw_sha="${manifest_values[1]}"
minimum_qualified_media_bytes="${manifest_values[2]}"

# Fully decode and hash before any destructive write. A valid compressed digest
# alone is insufficient if the manifest names a different raw payload.
read -r decoded_size decoded_sha < <(
    zstd --decompress --stdout --no-progress -- "${archive_path}" \
        | python3 -c 'import hashlib,sys
h=hashlib.sha256(); n=0
while True:
    chunk=sys.stdin.buffer.read(1024*1024)
    if not chunk: break
    n += len(chunk); h.update(chunk)
print(n, h.hexdigest())'
)
[[ "${decoded_size}" = "${expected_raw_size}" \
    && "${decoded_sha}" = "${expected_raw_sha}" ]] \
    || fail "decoded raw image identity differs from release manifest"

if [[ "${verify_only}" = true ]]; then
    printf 'card-flash-verification=PASS compressed_sha256=%s signing_key_id=%s\n' \
        "${actual_compressed_sha}" "${signing_key_id}"
    exit 0
fi

device_path="$(readlink -f -- "${device_path}")"
[[ -b "${device_path}" ]] || fail "target is not a block device"
[[ "$(lsblk -dnro TYPE -- "${device_path}")" = disk ]] \
    || fail "target must be a whole disk, not a partition or mapper"
[[ "$(lsblk -dnro RM -- "${device_path}")" = 1 ]] \
    || fail "target is not reported as removable; refusing to write"
[[ "$(blockdev --getro "${device_path}")" = 0 ]] \
    || fail "target block device is read-only"
if lsblk -nrpo MOUNTPOINTS -- "${device_path}" | grep -q '[^[:space:]]'; then
    fail "target or one of its partitions is mounted; unmount it manually"
fi
root_source="$(readlink -f -- "$(findmnt -nro SOURCE /)")"
while read -r ancestor_name ancestor_type; do
    if [[ "${ancestor_type}" = disk \
        && "$(readlink -f -- "/dev/${ancestor_name}")" = "${device_path}" ]]; then
        fail "target backs the running root filesystem"
    fi
done < <(lsblk -snro NAME,TYPE -- "${root_source}")
device_size="$(blockdev --getsize64 "${device_path}")"
[[ "${device_size}" -ge "${minimum_qualified_media_bytes}" ]] \
    || fail "target device is smaller than the signed qualified-media minimum"
[[ "${expected_raw_size}" -le "${device_size}" ]] \
    || fail "decoded image is larger than target device"

printf 'card-flash=START device=%s bytes=%s\n' \
    "${device_path}" "${expected_raw_size}"
zstd --decompress --stdout --no-progress -- "${archive_path}" \
    | dd of="${device_path}" bs=4M iflag=fullblock conv=fsync status=progress
sync
blockdev --flushbufs "${device_path}"
reread_sha="$(dd if="${device_path}" bs=4M count="${expected_raw_size}" \
    iflag=count_bytes,fullblock status=none | sha256sum | awk '{print $1}')"
[[ "${reread_sha}" = "${expected_raw_sha}" ]] \
    || fail "written-range reread SHA-256 does not match raw image"
printf 'card-flash=PASS device=%s reread_sha256=%s signing_key_id=%s\n' \
    "${device_path}" "${reread_sha}" "${signing_key_id}"
