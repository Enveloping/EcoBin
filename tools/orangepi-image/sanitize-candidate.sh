#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
config_directory="${script_directory}"
image_path=""
target_deb_directory=""
software_payload=""
software_payload_sha256=""
release_id=""
version=""
git_commit=""
loop_device=""
mount_directory=""

fail() {
    printf 'candidate-sanitization=FAIL: %s\n' "$1" >&2
    exit 1
}

# shellcheck source=lib/block_device.sh
source "${script_directory}/lib/block_device.sh"

usage() {
    cat <<'EOF'
Usage: sanitize-candidate.sh --image FILE --target-deb-dir DIR
       --software-payload DIR --software-payload-sha256 HEX
       --release-id ID --version X.Y.Z --git-commit HEX [--config-dir DIR]

The input must be a copied regular image, not a block device, symlink, or hard
link. The exact locked final ext4 partition is mounted read-write below a fresh
temporary directory. No host path is used as a cleanup root.
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
    local cleanup_status=0
    if [[ -n "${mount_directory}" && -d "${mount_directory}" ]] \
        && mountpoint -q -- "${mount_directory}"; then
        sync -f -- "${mount_directory}" 2>/dev/null || true
        if ! umount -- "${mount_directory}"; then
            printf 'candidate-sanitization=FAIL: unable to unmount temporary root; loop retained\n' >&2
            cleanup_status=1
        fi
    fi
    if [[ -n "${loop_device}" ]]; then
        if [[ -n "${mount_directory}" && -d "${mount_directory}" ]] \
            && mountpoint -q -- "${mount_directory}"; then
            printf 'candidate-sanitization=FAIL: refusing to detach a still-mounted loop device\n' >&2
            cleanup_status=1
        elif ! losetup -d "${loop_device}"; then
            printf 'candidate-sanitization=FAIL: unable to detach candidate loop device\n' >&2
            cleanup_status=1
        fi
    fi
    if [[ -n "${mount_directory}" && -d "${mount_directory}" ]]; then
        case "${mount_directory}" in
            /tmp/ecobin-image-sanitize.*)
                rmdir -- "${mount_directory}" 2>/dev/null || true
                ;;
            *)
                printf 'candidate-sanitization=FAIL: refusing to remove unexpected mount path\n' >&2
                cleanup_status=1
                ;;
        esac
    fi
    return "${cleanup_status}"
}

on_exit() {
    local original_status=$?
    trap - EXIT INT TERM HUP
    if ! cleanup; then
        exit 1
    fi
    exit "${original_status}"
}
trap on_exit EXIT
trap 'exit 130' INT TERM HUP

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir)
            [[ $# -ge 2 ]] || fail "--config-dir requires a value"
            config_directory="$2"
            shift 2
            ;;
        --image)
            [[ $# -ge 2 ]] || fail "--image requires a value"
            image_path="$2"
            shift 2
            ;;
        --target-deb-dir)
            [[ $# -ge 2 ]] || fail "--target-deb-dir requires a value"
            target_deb_directory="$2"
            shift 2
            ;;
        --software-payload)
            [[ $# -ge 2 ]] || fail "--software-payload requires a value"
            software_payload="$2"
            shift 2
            ;;
        --software-payload-sha256)
            [[ $# -ge 2 ]] || fail "--software-payload-sha256 requires a value"
            software_payload_sha256="$2"
            shift 2
            ;;
        --release-id)
            [[ $# -ge 2 ]] || fail "--release-id requires a value"
            release_id="$2"
            shift 2
            ;;
        --version)
            [[ $# -ge 2 ]] || fail "--version requires a value"
            version="$2"
            shift 2
            ;;
        --git-commit)
            [[ $# -ge 2 ]] || fail "--git-commit requires a value"
            git_commit="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument: $1"
            ;;
    esac
done

[[ -n "${image_path}" ]] || fail "--image is required"
[[ -n "${target_deb_directory}" ]] || fail "--target-deb-dir is required"
[[ -n "${software_payload}" && -n "${software_payload_sha256}" \
    && -n "${release_id}" && -n "${version}" && -n "${git_commit}" ]] \
    || fail "controlled software payload and build identities are required"
[[ "${software_payload_sha256}" =~ ^[0-9a-f]{64}$ ]] \
    || fail "software payload SHA-256 is malformed"
[[ "$(id -u)" = 0 ]] || fail "candidate sanitization requires root"
for command_name in \
    python3 losetup lsblk blkid mount umount mountpoint readlink stat flock sync \
    grep sed awk; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required command is missing: ${command_name}"
done

python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${config_directory}" --require-locked >/dev/null
[[ -f "${image_path}" && ! -L "${image_path}" ]] \
    || fail "candidate image must be a regular non-symlink file"
[[ "$(stat -c '%h' -- "${image_path}")" = 1 ]] \
    || fail "candidate image must not be a hard link"
config_directory="$(readlink -f -- "${config_directory}")"
image_path="$(readlink -f -- "${image_path}")"
[[ -d "${target_deb_directory}" && ! -L "${target_deb_directory}" ]] \
    || fail "target deb directory must be a regular directory"
target_deb_directory="$(readlink -f -- "${target_deb_directory}")"
[[ -d "${software_payload}" && ! -L "${software_payload}" ]] \
    || fail "software payload must be a regular directory"
software_payload="$(readlink -f -- "${software_payload}")"

exec 9<>"${image_path}"
flock -n 9 || fail "candidate image is already in use"
if losetup -j -- "${image_path}" | grep -q .; then
    fail "candidate image is already attached to a loop device"
fi

expected_size="$(json_value "${config_directory}/image-layout.json" \
    compactImage.fixedRawImageBytes)"
root_partition_number="$(json_value \
    "${config_directory}/image-layout.json" rootFilesystem.partitionNumber)"
expected_filesystem_uuid="$(json_value \
    "${config_directory}/image-layout.json" rootFilesystem.filesystemUuid)"
expected_partition_uuid="$(json_value \
    "${config_directory}/image-layout.json" rootFilesystem.partitionUuid)"
[[ "$(stat -c '%s' -- "${image_path}")" = "${expected_size}" ]] \
    || fail "raw image size does not match image-layout.json"

loop_device="$(losetup --find --show --partscan -- "${image_path}")"
[[ -b "${loop_device}" ]] || fail "failed to attach candidate loop device"
backing_file="$(losetup -nO BACK-FILE -- "${loop_device}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
[[ -n "${backing_file}" ]] || fail "loop device did not report a backing file"
[[ "$(readlink -f -- "${backing_file}")" = "${image_path}" ]] \
    || fail "loop device is not backed by the candidate image"
udevadm settle 2>/dev/null || true

partition_rows_output="$(ecobin_list_direct_partitions "${loop_device}")" \
    || fail "cannot resolve candidate partitions through sysfs"
[[ -n "${partition_rows_output}" ]] || fail "candidate image has no partitions"
mapfile -t partition_rows <<< "${partition_rows_output}"
root_partition=""
highest_partition=0
for row in "${partition_rows[@]}"; do
    partition_name="${row% *}"
    partition_number="${row##* }"
    [[ "${partition_number}" =~ ^[0-9]+$ ]] \
        || fail "candidate contains a non-numeric sysfs partition number"
    (( partition_number > highest_partition )) && highest_partition="${partition_number}"
    if [[ "${partition_number}" = "${root_partition_number}" ]]; then
        root_partition="${partition_name}"
    fi
done
[[ -n "${root_partition}" && -b "${root_partition}" ]] \
    || fail "locked root partition is absent"
[[ "${root_partition_number}" = "${highest_partition}" ]] \
    || fail "locked root partition is not the final image partition"

actual_type="$(blkid -s TYPE -o value -- "${root_partition}")"
actual_uuid="$(blkid -s UUID -o value -- "${root_partition}")"
actual_partuuid="$(blkid -s PARTUUID -o value -- "${root_partition}")"
[[ "${actual_type}" = ext4 ]] || fail "locked root partition is not ext4"
[[ "${actual_uuid,,}" = "${expected_filesystem_uuid,,}" ]] \
    || fail "root filesystem UUID does not match image-layout.json"
[[ "${actual_partuuid,,}" = "${expected_partition_uuid,,}" ]] \
    || fail "root partition UUID does not match image-layout.json"

mount_directory="$(mktemp -d /tmp/ecobin-image-sanitize.XXXXXXXX)"
[[ "$(stat -c '%u:%g:%a' -- "${mount_directory}")" = 0:0:700 ]] \
    || fail "temporary mount directory does not have root-only permissions"
mount -t ext4 -o rw,nosuid -- \
    "${root_partition}" "${mount_directory}"

bash "${script_directory}/install-target-packages.sh" \
    --config-dir "${config_directory}" \
    --rootfs "${mount_directory}" \
    --deb-dir "${target_deb_directory}"
python3 "${script_directory}/lib/sanitize_rootfs.py" \
    --root "${mount_directory}" --confirm-root "${mount_directory}"
python3 "${repository_root}/hardware/system/orangepi_image_config.py" \
    --rootfs "${mount_directory}" \
    --safe-gpio-source \
    "${repository_root}/hardware/system/mcu_safe_gpio.py" \
    --safe-gpio-unit-source \
    "${repository_root}/hardware/ecobin-mcu-safe-gpio.service" \
    --expand-rootfs-source \
    "${repository_root}/tools/orangepi-image/expand-rootfs.sh" \
    --expand-rootfs-unit-source \
    "${repository_root}/hardware/ecobin-expand-rootfs.service" \
    --image-layout-source \
    "${config_directory}/image-layout.json"
python3 "${repository_root}/hardware/system/image_software_installer.py" install \
    --rootfs "${mount_directory}" \
    --repository-root "${repository_root}" \
    --payload "${software_payload}" \
    --payload-sha256 "${software_payload_sha256}" \
    --release-id "${release_id}" \
    --version "${version}" \
    --git-commit "${git_commit}"
sync -f -- "${mount_directory}"
umount -- "${mount_directory}"
losetup -d "${loop_device}"
loop_device=""
rmdir -- "${mount_directory}"
mount_directory=""
printf 'candidate-sanitization=PASS image=%s root_partition=%s\n' \
    "${image_path}" "${root_partition_number}"
