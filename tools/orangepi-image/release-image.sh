#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
config_directory="${script_directory}"
trust_policy=""
sealed_image=""
seal_evidence=""
seal_evidence_signature=""
seal_evidence_public_key=""
build_attestation=""
build_attestation_signature=""
build_attestation_public_key=""
rootfs_qualification_evidence=""
target_media_qualification_evidence=""
candidate_manifest=""
output_directory=""
signing_private_key=""
signing_public_key=""
signing_key_id=""
staging_directory=""
loop_device=""
mount_directory=""

fail() { printf 'image-release=FAIL: %s\n' "$1" >&2; exit 1; }

# shellcheck source=lib/block_device.sh
source "${script_directory}/lib/block_device.sh"

usage() {
    cat <<'EOF'
Usage: release-image.sh --sealed-image FILE
       --seal-evidence FILE --seal-evidence-signature FILE
       --seal-evidence-public-key FILE --build-attestation FILE
       --build-attestation-signature FILE --build-attestation-public-key FILE
       --rootfs-qualification-evidence FILE
       --target-media-qualification-evidence FILE
       --candidate-manifest FILE --output-dir DIR --trust-policy FILE
       --signing-private-key FILE --signing-public-key FILE
       --signing-key-id ID [--config-dir DIR]

Formal release trusts two authenticated facts: two independent no-secret
candidates matched byte-for-byte, and one sealed image passed signed file-level
seal verification. Secret-bearing raw images are intentionally not expected to
be byte-for-byte reproducible.
EOF
}

json_value() {
    python3 - "$1" "$2" <<'PY'
import json
import pathlib
import sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for part in sys.argv[2].split("."):
    value = value[part]
print(str(value).lower() if isinstance(value, bool) else value)
PY
}

cleanup() {
    local failed=false
    if [[ -n "${mount_directory}" && -d "${mount_directory}" ]] \
        && mountpoint -q -- "${mount_directory}"; then
        umount -- "${mount_directory}" || failed=true
    fi
    if [[ -n "${loop_device}" ]]; then
        if [[ -n "${mount_directory}" && -d "${mount_directory}" ]] \
            && mountpoint -q -- "${mount_directory}"; then
            failed=true
        else
            losetup -d "${loop_device}" || failed=true
        fi
    fi
    if [[ -n "${mount_directory}" && -d "${mount_directory}" ]]; then
        case "${mount_directory}" in
            /tmp/ecobin-image-release.*) rmdir -- "${mount_directory}" 2>/dev/null || failed=true ;;
            *) failed=true ;;
        esac
    fi
    if [[ -n "${staging_directory}" && -d "${staging_directory}" ]]; then
        case "$(basename -- "${staging_directory}")" in
            .ecobin-orange-release.*) rm -rf -- "${staging_directory}" || failed=true ;;
            *) failed=true ;;
        esac
    fi
    [[ "${failed}" = false ]]
}

on_exit() {
    local original_status=$?
    trap - EXIT INT TERM HUP
    if ! cleanup; then
        printf 'image-release=FAIL: protected cleanup did not complete\n' >&2
        exit 1
    fi
    exit "${original_status}"
}
trap on_exit EXIT
trap 'exit 130' INT TERM HUP

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir) config_directory="$2"; shift 2 ;;
        --trust-policy) trust_policy="$2"; shift 2 ;;
        --sealed-image) sealed_image="$2"; shift 2 ;;
        --seal-evidence) seal_evidence="$2"; shift 2 ;;
        --seal-evidence-signature) seal_evidence_signature="$2"; shift 2 ;;
        --seal-evidence-public-key) seal_evidence_public_key="$2"; shift 2 ;;
        --build-attestation) build_attestation="$2"; shift 2 ;;
        --build-attestation-signature) build_attestation_signature="$2"; shift 2 ;;
        --build-attestation-public-key) build_attestation_public_key="$2"; shift 2 ;;
        --rootfs-qualification-evidence) rootfs_qualification_evidence="$2"; shift 2 ;;
        --target-media-qualification-evidence) target_media_qualification_evidence="$2"; shift 2 ;;
        --candidate-manifest) candidate_manifest="$2"; shift 2 ;;
        --output-dir) output_directory="$2"; shift 2 ;;
        --signing-private-key) signing_private_key="$2"; shift 2 ;;
        --signing-public-key) signing_public_key="$2"; shift 2 ;;
        --signing-key-id) signing_key_id="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

[[ -n "${sealed_image}${seal_evidence}${seal_evidence_signature}" \
    && -n "${seal_evidence_public_key}${build_attestation}" \
    && -n "${build_attestation_signature}${build_attestation_public_key}" \
    && -n "${candidate_manifest}${output_directory}${trust_policy}${rootfs_qualification_evidence}${target_media_qualification_evidence}" \
    && -n "${signing_private_key}${signing_public_key}${signing_key_id}" ]] \
    || fail "all release, external policy and authenticated evidence inputs are required"
[[ "${signing_key_id}" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] \
    || fail "signing key ID has an invalid format"
[[ "$(id -u)" = 0 ]] || fail "release inspection requires root"
ulimit -c 0 || fail "core dumps must be disabled before handling release keys"
for command_name in python3 sha256sum stat readlink cmp zstd losetup blkid \
    mount umount mountpoint udevadm awk cp chmod touch flock find dpkg-query \
    swapon sync; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required command is missing: ${command_name}"
done
[[ -z "$(swapon --noheadings --show=NAME 2>/dev/null)" ]] \
    || fail "active swap is forbidden while handling the sealed image and release key"
python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${config_directory}" --require-locked \
    --target-media-qualification-evidence "${target_media_qualification_evidence}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" validate-policy \
    --trust-policy "${trust_policy}" --repository-root "${repository_root}" >/dev/null
[[ "$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
    = 3.11 ]] || fail "release metadata requires Python 3.11"
[[ "$(dpkg-query -W -f='${Version}' zstd)" = \
    "$(json_value "${config_directory}/builder.lock" tools.zstd)" ]] \
    || fail "release compressor differs from locked zstd version"

for input in "${sealed_image}" "${seal_evidence}" \
    "${seal_evidence_signature}" "${seal_evidence_public_key}" \
    "${build_attestation}" "${build_attestation_signature}" \
    "${build_attestation_public_key}" "${candidate_manifest}" \
    "${rootfs_qualification_evidence}" \
    "${target_media_qualification_evidence}" \
    "${signing_private_key}" "${signing_public_key}" "${trust_policy}"; do
    [[ -f "${input}" && ! -L "${input}" && "$(stat -c '%h' -- "${input}")" = 1 ]] \
        || fail "release input must be a regular non-linked file"
done
config_directory="$(readlink -f -- "${config_directory}")"
trust_policy="$(readlink -f -- "${trust_policy}")"
sealed_image="$(readlink -f -- "${sealed_image}")"
seal_evidence="$(readlink -f -- "${seal_evidence}")"
seal_evidence_signature="$(readlink -f -- "${seal_evidence_signature}")"
seal_evidence_public_key="$(readlink -f -- "${seal_evidence_public_key}")"
build_attestation="$(readlink -f -- "${build_attestation}")"
build_attestation_signature="$(readlink -f -- "${build_attestation_signature}")"
build_attestation_public_key="$(readlink -f -- "${build_attestation_public_key}")"
candidate_manifest="$(readlink -f -- "${candidate_manifest}")"
signing_private_key="$(readlink -f -- "${signing_private_key}")"
signing_public_key="$(readlink -f -- "${signing_public_key}")"
sealed_parent="$(dirname -- "${sealed_image}")"
python3 "${script_directory}/lib/release_trust.py" validate-directory \
    --directory "${sealed_parent}" >/dev/null
[[ "$(stat -c '%u:%g:%a:%h' -- "${sealed_image}")" = 0:0:600:1 ]] \
    || fail "sealed image must be root:root mode 0600 with one link"
exec 8<"${sealed_image}"
flock -n 8 || fail "sealed image is already in use"

[[ "${output_directory}" = /* ]] || output_directory="${PWD}/${output_directory}"
output_parent="$(dirname -- "${output_directory}")"
output_name="$(basename -- "${output_directory}")"
[[ -n "${output_name}" && "${output_name}" != . && "${output_name}" != .. \
    && -d "${output_parent}" ]] || fail "release output parent must already exist"
output_parent="$(readlink -f -- "${output_parent}")"
[[ "$(stat -c '%u:%g:%a' -- "${output_parent}")" = 0:0:700 ]] \
    || fail "release output parent must be root:root mode 0700"
python3 "${script_directory}/lib/release_trust.py" validate-directory \
    --directory "${output_parent}" >/dev/null
output_directory="${output_parent}/${output_name}"
[[ ! -e "${output_directory}" && ! -L "${output_directory}" ]] \
    || fail "release output directory already exists"
staging_directory="$(mktemp -d \
    "${output_parent}/.ecobin-orange-release.XXXXXXXX")"
[[ "$(stat -c '%u:%g:%a' -- "${staging_directory}")" = 0:0:700 ]] \
    || fail "release staging directory permissions are unsafe"

snapshot() {
    local source="$1"
    local name="$2"
    local maximum_bytes="$3"
    python3 "${script_directory}/lib/release_trust.py" snapshot \
        --source "${source}" --destination "${staging_directory}/${name}" \
        --mode 0644 --maximum-bytes "${maximum_bytes}" >/dev/null
}
snapshot "${candidate_manifest}" candidate-manifest.json 1048576
snapshot "${build_attestation}" build-attestation.json 1048576
snapshot "${build_attestation_signature}" build-attestation.sig 4096
snapshot "${rootfs_qualification_evidence}" rootfs-qualification-evidence.json 1048576
snapshot "${target_media_qualification_evidence}" target-media-qualification-evidence.json 1048576
snapshot "${seal_evidence}" seal-evidence.json 1048576
snapshot "${seal_evidence_signature}" seal-evidence.sig 4096
candidate_manifest="${staging_directory}/candidate-manifest.json"
build_attestation="${staging_directory}/build-attestation.json"
build_attestation_signature="${staging_directory}/build-attestation.sig"
rootfs_qualification_evidence="${staging_directory}/rootfs-qualification-evidence.json"
target_media_qualification_evidence="${staging_directory}/target-media-qualification-evidence.json"
export ECOBIN_TARGET_MEDIA_QUALIFICATION_EVIDENCE="${target_media_qualification_evidence}"
expected_rootfs_evidence_sha="$(json_value "${trust_policy}" rootfsQualification.evidenceSha256)"
[[ "$(sha256sum -- "${rootfs_qualification_evidence}" | awk '{print $1}')" = "${expected_rootfs_evidence_sha}" ]] \
    || fail "rootfs qualification evidence digest differs from formal policy"
python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${config_directory}" --require-locked \
    --target-media-qualification-evidence "${target_media_qualification_evidence}" >/dev/null
seal_evidence="${staging_directory}/seal-evidence.json"
seal_evidence_signature="${staging_directory}/seal-evidence.sig"

mapfile -t build_facts < <(
    python3 "${script_directory}/lib/release_trust.py" verify-build \
        --config-dir "${config_directory}" --trust-policy "${trust_policy}" \
        --attestation "${build_attestation}" \
        --signature "${build_attestation_signature}" \
        --public-key "${build_attestation_public_key}" \
        --candidate-manifest "${candidate_manifest}" --emit-identities
)
[[ ${#build_facts[@]} = 6 ]] || fail "authenticated build facts are incomplete"
release_id="${build_facts[0]}"
version="${build_facts[1]}"
git_commit="${build_facts[2]}"
software_payload_sha256="${build_facts[3]}"
source_date_epoch="${build_facts[4]}"
candidate_sha256="${build_facts[5]}"
[[ "${release_id}" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ \
    && "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ \
    && "${git_commit}" =~ ^[0-9a-f]{40}$ \
    && "${software_payload_sha256}${candidate_sha256}" =~ ^[0-9a-f]{128}$ \
    && "${source_date_epoch}" =~ ^[0-9]+$ ]] \
    || fail "authenticated build facts are malformed"
python3 "${script_directory}/lib/release_trust.py" verify-seal \
    --config-dir "${config_directory}" --trust-policy "${trust_policy}" \
    --evidence "${seal_evidence}" --signature "${seal_evidence_signature}" \
    --public-key "${seal_evidence_public_key}" \
    --build-attestation "${build_attestation}" --sealed-image "${sealed_image}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" check-key \
    --trust-policy "${trust_policy}" --role releaseSigning \
    --key "${signing_public_key}" --key-id "${signing_key_id}" >/dev/null
bash "${script_directory}/verify-image.sh" --sealed \
    --config-dir "${config_directory}" --image "${sealed_image}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}"

compressed_name="ecobin-orangepi-zero3-${version}.img.zst"
compressed_image="${staging_directory}/${compressed_name}"
checksum_path="${staging_directory}/release-checksums.txt"
signature_path="${staging_directory}/release-checksums.sig"
zstd --compress --ultra -19 --long=27 --threads=1 --no-progress \
    --output="${compressed_image}" -- "${sealed_image}"

root_partition_number="$(json_value \
    "${config_directory}/image-layout.json" rootFilesystem.partitionNumber)"
expected_filesystem_uuid="$(json_value \
    "${config_directory}/image-layout.json" rootFilesystem.filesystemUuid)"
expected_partition_uuid="$(json_value \
    "${config_directory}/image-layout.json" rootFilesystem.partitionUuid)"
partition_table_type="$(json_value \
    "${config_directory}/image-layout.json" sourceGeometry.partitionTableType)"
expected_disk_identifier="$(json_value \
    "${config_directory}/image-layout.json" sourceGeometry.diskIdentifier)"
logical_sector_bytes="$(json_value \
    "${config_directory}/image-layout.json" sourceGeometry.logicalSectorBytes)"
expected_root_start="$(json_value \
    "${config_directory}/image-layout.json" sourceGeometry.rootPartition.startSector)"
expected_root_sectors="$(json_value \
    "${config_directory}/image-layout.json" sourceGeometry.rootPartition.sectorCount)"
[[ "${logical_sector_bytes}" = 512 ]] \
    || fail "only locked 512-byte image sectors are supported"
loop_device="$(losetup --find --show --partscan --read-only -- "${sealed_image}")"
udevadm settle 2>/dev/null || true
root_geometry="$(ecobin_final_partition_geometry \
    "${loop_device}" "${root_partition_number}")" \
    || fail "locked root partition is absent or not final"
read -r root_start root_sectors unexpected <<< "${root_geometry}"
[[ -z "${unexpected}" && "${root_start}" = "${expected_root_start}" \
    && "${root_sectors}" = "${expected_root_sectors}" ]] \
    || fail "root partition sysfs geometry differs from image-layout.json"
ecobin_verify_dos_partition_identity \
    "${loop_device}" "${partition_table_type}" "${expected_disk_identifier}" \
    "${root_partition_number}" "${expected_partition_uuid}" \
    || fail "release root partition UUID differs from locked layout"
losetup -d "${loop_device}"
loop_device=""
root_offset_bytes="$((root_start * logical_sector_bytes))"
root_size_bytes="$((root_sectors * logical_sector_bytes))"
loop_device="$(losetup --find --show --read-only \
    --offset "${root_offset_bytes}" --sizelimit "${root_size_bytes}" \
    -- "${sealed_image}")"
[[ -b "${loop_device}" ]] || fail "failed to attach release root slice"
root_partition="${loop_device}"
[[ "$(blkid -s TYPE -o value -- "${root_partition}")" = ext4 \
    && "$(blkid -s UUID -o value -- "${root_partition}")" = \
        "${expected_filesystem_uuid}" ]] \
    || fail "release root partition identity differs from locked layout"
mount_directory="$(mktemp -d /tmp/ecobin-image-release.XXXXXXXX)"
[[ "$(stat -c '%u:%g:%a' -- "${mount_directory}")" = 0:0:700 ]] \
    || fail "release mount directory permissions are unsafe"
mount -t ext4 -o ro,noload,nodev,nosuid,noexec -- \
    "${root_partition}" "${mount_directory}"

metadata_arguments=(
    --rootfs "${mount_directory}"
    --config-dir "${config_directory}"
    --trust-policy "${trust_policy}"
    --candidate-manifest "${candidate_manifest}"
    --target-media-qualification-evidence "${target_media_qualification_evidence}"
    --build-attestation "${build_attestation}"
    --build-attestation-signature "${build_attestation_signature}"
    --build-attestation-public-key "${build_attestation_public_key}"
    --seal-evidence "${seal_evidence}"
    --seal-evidence-signature "${seal_evidence_signature}"
    --seal-evidence-public-key "${seal_evidence_public_key}"
    --sealed-image "${sealed_image}"
    --compressed-image "${compressed_image}"
    --signing-key-id "${signing_key_id}"
)
signing_public_key_sha256="$(python3 \
    "${script_directory}/lib/release_trust.py" check-key \
    --trust-policy "${trust_policy}" --role releaseSigning \
    --key "${signing_public_key}" --key-id "${signing_key_id}")"
metadata_arguments+=(--signing-public-key-sha256 "${signing_public_key_sha256}")
python3 "${script_directory}/lib/generate_release_metadata.py" \
    "${metadata_arguments[@]}" --output-directory "${staging_directory}"
metadata_peer="${staging_directory}/.metadata-peer"
mkdir -m 0700 -- "${metadata_peer}"
python3 "${script_directory}/lib/generate_release_metadata.py" \
    "${metadata_arguments[@]}" --output-directory "${metadata_peer}"
for metadata_name in apt-packages.txt python-packages.txt sbom.spdx.json \
    image-manifest.json; do
    cmp -s -- "${staging_directory}/${metadata_name}" \
        "${metadata_peer}/${metadata_name}" \
        || fail "independent release metadata generation differs"
done
rm -rf -- "${metadata_peer}"
umount -- "${mount_directory}"
losetup -d "${loop_device}"
loop_device=""
rmdir -- "${mount_directory}"
mount_directory=""
rm -f -- "${candidate_manifest}"

mkdir -- "${staging_directory}/schemas"
cp -- "${script_directory}/schemas/release-manifest.schema.json" \
    "${script_directory}/schemas/build-attestation.schema.json" \
    "${script_directory}/schemas/rootfs-qualification-evidence.schema.json" \
    "${script_directory}/schemas/target-media-qualification-evidence.schema.json" \
    "${script_directory}/schemas/seal-evidence.schema.json" \
    "${staging_directory}/schemas/"
cp -- "${script_directory}/flash-and-verify.sh" \
    "${script_directory}/flash-and-verify.ps1" \
    "${script_directory}/factory-checklist.md" "${staging_directory}/"
chmod 0755 -- "${staging_directory}/flash-and-verify.sh"
chmod 0644 -- "${staging_directory}"/*.json "${staging_directory}"/*.sig \
    "${staging_directory}/flash-and-verify.ps1" \
    "${staging_directory}/factory-checklist.md" \
    "${staging_directory}/schemas/"*.json \
    "${staging_directory}/sbom.spdx.json" \
    "${staging_directory}/apt-packages.txt" \
    "${staging_directory}/python-packages.txt"

(
    cd "${staging_directory}"
    sha256sum -- \
        "${compressed_name}" image-manifest.json sbom.spdx.json \
        apt-packages.txt python-packages.txt build-attestation.json \
        build-attestation.sig rootfs-qualification-evidence.json target-media-qualification-evidence.json seal-evidence.json seal-evidence.sig \
        schemas/release-manifest.schema.json \
        schemas/build-attestation.schema.json schemas/rootfs-qualification-evidence.schema.json schemas/target-media-qualification-evidence.schema.json schemas/seal-evidence.schema.json \
        flash-and-verify.sh flash-and-verify.ps1 factory-checklist.md \
        > release-checksums.txt
)
chmod 0644 -- "${checksum_path}"
python3 "${script_directory}/lib/release_trust.py" sign \
    --trust-policy "${trust_policy}" --role releaseSigning \
    --private-key "${signing_private_key}" --key-id "${signing_key_id}" \
    --payload "${checksum_path}" --output "${signature_path}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" verify-signature \
    --trust-policy "${trust_policy}" --role releaseSigning \
    --public-key "${signing_public_key}" --key-id "${signing_key_id}" \
    --payload "${checksum_path}" --signature "${signature_path}" >/dev/null
chmod 0644 -- "${signature_path}"
find "${staging_directory}" -mindepth 1 -exec \
    touch -h -d "@${source_date_epoch}" -- {} +
sync -f -- "${staging_directory}"

mv -- "${staging_directory}" "${output_directory}"
staging_directory=""
sync -f -- "${output_parent}"
printf 'image-release=PASS output=%s signing_key_id=%s\n' \
    "${output_directory}" "${signing_key_id}"
