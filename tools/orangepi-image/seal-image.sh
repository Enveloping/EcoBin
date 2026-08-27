#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_directory="${script_directory}"
trust_policy=""
target_media_qualification_evidence=""
candidate_image=""
output_image=""
build_attestation=""
build_attestation_signature=""
build_attestation_public_key=""
software_payload_sha256=""
release_id=""
version=""
git_commit=""
enrollment_key_file=""
setup_ap_key_file=""
sealing_private_key=""
sealing_public_key=""
sealing_key_id=""
evidence_path=""
evidence_signature_path=""
loop_device=""
mount_directory=""
temporary_evidence_directory=""
sealed_inode_inventory=""
output_created=false
evidence_created=false
evidence_signature_created=false
seal_complete=false

fail() { printf 'image-seal=FAIL: %s\n' "$1" >&2; exit 1; }

# shellcheck source=lib/block_device.sh
source "${script_directory}/lib/block_device.sh"

usage() {
    cat <<'EOF'
Usage: seal-image.sh --candidate FILE --output FILE
       --build-attestation FILE --build-attestation-signature FILE
       --build-attestation-public-key FILE --software-payload-sha256 HEX
       --release-id ID --version X.Y.Z --git-commit HEX --trust-policy FILE
       --target-media-qualification-evidence FILE
       --enrollment-key-file FILE --setup-ap-key-file FILE
       --sealing-private-key FILE --sealing-public-key FILE --sealing-key-id ID
       --evidence FILE --evidence-signature FILE [--config-dir DIR]

The candidate must be covered by the locked external build attestation. This
operation emits signed file-level evidence; secret values and their individual
digests are never printed or included in evidence.
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
print(value)
PY
}

cleanup() {
    local cleanup_failed=false
    if [[ -n "${mount_directory}" && -d "${mount_directory}" ]] \
        && mountpoint -q -- "${mount_directory}"; then
        sync -f -- "${mount_directory}" 2>/dev/null || true
        umount -- "${mount_directory}" || cleanup_failed=true
    fi
    if [[ -n "${loop_device}" ]]; then
        if [[ -n "${mount_directory}" && -d "${mount_directory}" ]] \
            && mountpoint -q -- "${mount_directory}"; then
            cleanup_failed=true
        else
            losetup -d "${loop_device}" || cleanup_failed=true
        fi
    fi
    if [[ -n "${mount_directory}" && -d "${mount_directory}" ]]; then
        case "${mount_directory}" in
            /tmp/ecobin-image-seal.*) rmdir -- "${mount_directory}" 2>/dev/null || true ;;
            *) cleanup_failed=true ;;
        esac
    fi
    if [[ -n "${temporary_evidence_directory}" \
        && -d "${temporary_evidence_directory}" ]]; then
        case "$(basename -- "${temporary_evidence_directory}")" in
            .ecobin-seal-evidence.*) rm -rf -- "${temporary_evidence_directory}" || cleanup_failed=true ;;
            *) cleanup_failed=true ;;
        esac
    fi
    if [[ "${output_created}" = true && "${seal_complete}" != true \
        && -f "${output_image}" && ! -L "${output_image}" ]]; then
        rm -f -- "${output_image}" || cleanup_failed=true
    fi
    if [[ "${evidence_created}" = true && "${seal_complete}" != true \
        && -f "${evidence_path}" && ! -L "${evidence_path}" ]]; then
        rm -f -- "${evidence_path}" || cleanup_failed=true
    fi
    if [[ "${evidence_signature_created}" = true && "${seal_complete}" != true \
        && -f "${evidence_signature_path}" \
        && ! -L "${evidence_signature_path}" ]]; then
        rm -f -- "${evidence_signature_path}" || cleanup_failed=true
    fi
    [[ "${cleanup_failed}" = false ]]
}

on_exit() {
    local original_status=$?
    trap - EXIT INT TERM HUP
    if ! cleanup; then
        printf 'image-seal=FAIL: protected cleanup did not complete\n' >&2
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
        --target-media-qualification-evidence) target_media_qualification_evidence="$2"; shift 2 ;;
        --candidate) candidate_image="$2"; shift 2 ;;
        --output) output_image="$2"; shift 2 ;;
        --build-attestation) build_attestation="$2"; shift 2 ;;
        --build-attestation-signature) build_attestation_signature="$2"; shift 2 ;;
        --build-attestation-public-key) build_attestation_public_key="$2"; shift 2 ;;
        --software-payload-sha256) software_payload_sha256="$2"; shift 2 ;;
        --release-id) release_id="$2"; shift 2 ;;
        --version) version="$2"; shift 2 ;;
        --git-commit) git_commit="$2"; shift 2 ;;
        --enrollment-key-file) enrollment_key_file="$2"; shift 2 ;;
        --setup-ap-key-file) setup_ap_key_file="$2"; shift 2 ;;
        --sealing-private-key) sealing_private_key="$2"; shift 2 ;;
        --sealing-public-key) sealing_public_key="$2"; shift 2 ;;
        --sealing-key-id) sealing_key_id="$2"; shift 2 ;;
        --evidence) evidence_path="$2"; shift 2 ;;
        --evidence-signature) evidence_signature_path="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done

[[ "$(id -u)" = 0 ]] || fail "image sealing requires root"
ulimit -c 0 || fail "core dumps must be disabled before handling factory secrets"
[[ -n "${candidate_image}${output_image}${build_attestation}" \
    && -n "${build_attestation_signature}${build_attestation_public_key}" \
    && -n "${software_payload_sha256}${release_id}${version}${git_commit}${trust_policy}${target_media_qualification_evidence}" \
    && -n "${enrollment_key_file}${setup_ap_key_file}" \
    && -n "${sealing_private_key}${sealing_public_key}${sealing_key_id}" \
    && -n "${evidence_path}${evidence_signature_path}" ]] \
    || fail "all sealing and authenticated evidence inputs are required"
for command_name in python3 chmod stat readlink losetup \
    blkid mount umount mountpoint sync awk openssl udevadm swapon; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required command is missing: ${command_name}"
done
[[ -z "$(swapon --noheadings --show=NAME 2>/dev/null)" ]] \
    || fail "active swap is forbidden while handling factory secrets"
python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${config_directory}" --require-locked \
    --target-media-qualification-evidence "${target_media_qualification_evidence}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" validate-policy \
    --trust-policy "${trust_policy}" --repository-root \
    "$(cd "${script_directory}/../.." && pwd)" >/dev/null

for input in "${candidate_image}" "${build_attestation}" \
    "${build_attestation_signature}" "${build_attestation_public_key}" \
    "${enrollment_key_file}" "${setup_ap_key_file}" "${sealing_private_key}" \
    "${sealing_public_key}" "${trust_policy}"; do
    [[ -f "${input}" && ! -L "${input}" && "$(stat -c '%h' -- "${input}")" = 1 ]] \
        || fail "seal input must be a regular non-linked file"
done
[[ -f "${target_media_qualification_evidence}" \
    && ! -L "${target_media_qualification_evidence}" \
    && "$(stat -c '%h' -- "${target_media_qualification_evidence}")" = 1 ]] \
    || fail "target-media qualification evidence must be a regular non-linked file"
config_directory="$(readlink -f -- "${config_directory}")"
trust_policy="$(readlink -f -- "${trust_policy}")"
target_media_qualification_evidence="$(readlink -f -- "${target_media_qualification_evidence}")"
export ECOBIN_TARGET_MEDIA_QUALIFICATION_EVIDENCE="${target_media_qualification_evidence}"
candidate_image="$(readlink -f -- "${candidate_image}")"
build_attestation="$(readlink -f -- "${build_attestation}")"
build_attestation_signature="$(readlink -f -- "${build_attestation_signature}")"
build_attestation_public_key="$(readlink -f -- "${build_attestation_public_key}")"
enrollment_key_file="$(readlink -f -- "${enrollment_key_file}")"
setup_ap_key_file="$(readlink -f -- "${setup_ap_key_file}")"
sealing_private_key="$(readlink -f -- "${sealing_private_key}")"
sealing_public_key="$(readlink -f -- "${sealing_public_key}")"
python3 "${script_directory}/lib/release_trust.py" check-key \
    --trust-policy "${trust_policy}" --role sealEvidence \
    --key "${sealing_public_key}" --key-id "${sealing_key_id}" >/dev/null

prepare_output() {
    local raw_path="$1"
    local label="$2"
    [[ "${raw_path}" = /* ]] || raw_path="${PWD}/${raw_path}"
    local parent name
    parent="$(dirname -- "${raw_path}")"
    name="$(basename -- "${raw_path}")"
    [[ -n "${name}" && "${name}" != . && "${name}" != .. ]] \
        || fail "${label} name is unsafe"
    [[ -d "${parent}" ]] || fail "${label} parent must already exist"
    parent="$(readlink -f -- "${parent}")"
    [[ "$(stat -c '%u:%g:%a' -- "${parent}")" = 0:0:700 ]] \
        || fail "${label} parent must be root:root mode 0700"
    python3 "${script_directory}/lib/release_trust.py" validate-directory \
        --directory "${parent}" >/dev/null
    raw_path="${parent}/${name}"
    [[ ! -e "${raw_path}" && ! -L "${raw_path}" ]] \
        || fail "${label} already exists"
    printf '%s\n' "${raw_path}"
}
output_image="$(prepare_output "${output_image}" 'sealed output')"
evidence_path="$(prepare_output "${evidence_path}" 'seal evidence')"
evidence_signature_path="$(prepare_output \
    "${evidence_signature_path}" 'seal evidence signature')"
[[ "${output_image}" != "${candidate_image}" \
    && "${evidence_path}" != "${evidence_signature_path}" \
    && "${evidence_path}" != "${output_image}" \
    && "${evidence_signature_path}" != "${output_image}" ]] \
    || fail "seal output paths overlap"

evidence_parent="$(dirname -- "${evidence_path}")"
[[ "${evidence_parent}" = "$(dirname -- "${evidence_signature_path}")" ]] \
    || fail "seal evidence and signature must share one controlled directory"
temporary_evidence_directory="$(mktemp -d \
    "${evidence_parent}/.ecobin-seal-evidence.XXXXXXXX")"
[[ "$(stat -c '%u:%g:%a' -- "${temporary_evidence_directory}")" = 0:0:700 ]] \
    || fail "seal staging directory permissions are unsafe"
snapshot_attestation="${temporary_evidence_directory}/build-attestation.json"
snapshot_attestation_signature="${temporary_evidence_directory}/build-attestation.sig"
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${build_attestation}" --destination "${snapshot_attestation}" \
    --mode 0600 --maximum-bytes 1048576 >/dev/null
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${build_attestation_signature}" \
    --destination "${snapshot_attestation_signature}" --mode 0600 \
    --maximum-bytes 4096 >/dev/null
build_attestation="${snapshot_attestation}"
build_attestation_signature="${snapshot_attestation_signature}"

# The controlled output is the one and only snapshot of the external candidate.
# All no-secret verification below consumes these exact bytes before injection.
python3 "${script_directory}/lib/release_trust.py" snapshot \
    --source "${candidate_image}" --destination "${output_image}" \
    --mode 0600 >/dev/null
output_created=true
[[ "$(stat -c '%h' -- "${output_image}")" = 1 ]] \
    || fail "sealed output must not be hard-linked"
python3 "${script_directory}/lib/release_trust.py" verify-build \
    --config-dir "${config_directory}" --trust-policy "${trust_policy}" \
    --attestation "${build_attestation}" \
    --signature "${build_attestation_signature}" \
    --public-key "${build_attestation_public_key}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --candidate "${output_image}" >/dev/null
bash "${script_directory}/verify-image.sh" --candidate \
    --config-dir "${config_directory}" --image "${output_image}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}"
expected_size="$(json_value "${config_directory}/image-layout.json" \
    compactImage.fixedRawImageBytes)"
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
[[ "$(stat -c '%s' -- "${output_image}")" = "${expected_size}" ]] \
    || fail "sealed output size differs from locked layout"

loop_device="$(losetup --find --show --partscan -- "${output_image}")"
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
    || fail "sealed root partition UUID differs from locked layout"
losetup -d "${loop_device}"
loop_device=""
root_offset_bytes="$((root_start * logical_sector_bytes))"
root_size_bytes="$((root_sectors * logical_sector_bytes))"
loop_device="$(losetup --find --show \
    --offset "${root_offset_bytes}" --sizelimit "${root_size_bytes}" \
    -- "${output_image}")"
[[ -b "${loop_device}" ]] || fail "failed to attach sealed root slice"
root_partition="${loop_device}"
[[ "$(blkid -s TYPE -o value -- "${root_partition}")" = ext4 \
    && "$(blkid -s UUID -o value -- "${root_partition}")" = \
        "${expected_filesystem_uuid}" ]] \
    || fail "sealed root partition identity differs from locked layout"
mount_directory="$(mktemp -d /tmp/ecobin-image-seal.XXXXXXXX)"
[[ "$(stat -c '%u:%g:%a' -- "${mount_directory}")" = 0:0:700 ]] \
    || fail "temporary mount permissions are unsafe"
mount -t ext4 -o rw,nodev,nosuid,noexec -- "${root_partition}" "${mount_directory}"
python3 "${script_directory}/lib/inject_factory_secrets.py" \
    --rootfs "${mount_directory}" \
    --enrollment-key-file "${enrollment_key_file}" \
    --setup-ap-key-file "${setup_ap_key_file}"
sealed_inode_inventory="${temporary_evidence_directory}/sealed-inodes.json"
python3 "${script_directory}/lib/capture_ext4_inode_inventory.py" \
    --root "${mount_directory}" --output "${sealed_inode_inventory}"
sync -f -- "${mount_directory}"
umount -- "${mount_directory}"
source_date_epoch="$(json_value \
    "${config_directory}/image-layout.json" \
    rootFilesystem.buildProfile.sourceDateEpoch)"
python3 "${script_directory}/lib/normalize_ext4_metadata.py" \
    --device "${root_partition}" --inventory "${sealed_inode_inventory}" \
    --epoch "${source_date_epoch}" --allow-block-device
rm -f -- "${sealed_inode_inventory}"
sealed_inode_inventory=""
losetup -d "${loop_device}"
loop_device=""
rmdir -- "${mount_directory}"
mount_directory=""

bash "${script_directory}/verify-image.sh" --sealed \
    --config-dir "${config_directory}" --image "${output_image}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}"
temporary_evidence="${temporary_evidence_directory}/seal-evidence.json"
temporary_signature="${temporary_evidence_directory}/seal-evidence.sig"
python3 "${script_directory}/lib/generate_seal_evidence.py" \
    --config-dir "${config_directory}" --trust-policy "${trust_policy}" \
    --build-attestation "${build_attestation}" \
    --sealed-image "${output_image}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}" --output "${temporary_evidence}"
python3 "${script_directory}/lib/release_trust.py" sign \
    --trust-policy "${trust_policy}" --role sealEvidence \
    --private-key "${sealing_private_key}" --key-id "${sealing_key_id}" \
    --payload "${temporary_evidence}" --output "${temporary_signature}" >/dev/null
python3 "${script_directory}/lib/release_trust.py" verify-seal \
    --config-dir "${config_directory}" --trust-policy "${trust_policy}" \
    --evidence "${temporary_evidence}" \
    --signature "${temporary_signature}" --public-key "${sealing_public_key}" \
    --build-attestation "${build_attestation}" --sealed-image "${output_image}" \
    --release-id "${release_id}" --version "${version}" \
    --git-commit "${git_commit}" >/dev/null
chmod 0644 -- "${temporary_evidence}" "${temporary_signature}"
python3 "${script_directory}/lib/release_trust.py" publish \
    --source "${temporary_evidence}" --destination "${evidence_path}" \
    --mode 0644 >/dev/null
evidence_created=true
python3 "${script_directory}/lib/release_trust.py" publish \
    --source "${temporary_signature}" \
    --destination "${evidence_signature_path}" --mode 0644 >/dev/null
evidence_signature_created=true
rm -f -- "${temporary_evidence}" "${temporary_signature}"
rm -f -- "${snapshot_attestation}" "${snapshot_attestation_signature}"
rmdir -- "${temporary_evidence_directory}"
temporary_evidence_directory=""
seal_complete=true
printf 'image-seal=PASS output=%s evidence=%s evidence_signature=%s\n' \
    "${output_image}" "${evidence_path}" "${evidence_signature_path}"
