#!/usr/bin/env bash
set -euo pipefail
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin

layout_file="${ECOBIN_IMAGE_LAYOUT_FILE:-/usr/share/ecobin/image-layout.json}"
dry_run=false
lock_file=/run/lock/ecobin-expand-rootfs.lock

fail() { printf 'rootfs-expansion=FAIL: %s\n' "$1" >&2; exit 1; }
json_value() {
    python3 - "$1" "$2" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for part in sys.argv[2].split("."): value = value[part]
print(str(value).lower() if isinstance(value, bool) else value)
PY
}
usage() {
    printf '%s\n' 'Usage: expand-rootfs.sh [--layout FILE] [--dry-run]'
}
while [[ $# -gt 0 ]]; do
    case "$1" in
        --layout) [[ $# -ge 2 ]] || fail "--layout requires a value"; layout_file="$2"; shift 2 ;;
        --dry-run) dry_run=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown argument: $1" ;;
    esac
done
[[ "$(id -u)" = 0 ]] || fail "root filesystem expansion requires root"
for command_name in python3 findmnt readlink stat blockdev growpart resize2fs blkid flock dumpe2fs partx udevadm; do
    command -v "${command_name}" >/dev/null || fail "required command is missing: ${command_name}"
done
[[ -f "${layout_file}" && ! -L "${layout_file}" ]] || fail "layout must be a regular non-symlink file"
layout_file="$(readlink -f -- "${layout_file}")"
[[ "$(json_value "${layout_file}" lockState)" = LOCKED ]] || fail "image layout is not locked"
[[ "$(json_value "${layout_file}" targetMedia.qualificationState)" = QUALIFIED ]] || fail "target media qualification is absent"
[[ "$(json_value "${layout_file}" rootFilesystem.filesystemType)" = ext4 ]] || fail "only ext4 root expansion is supported"
[[ "$(json_value "${layout_file}" rootFilesystem.mustBeFinalPartition)" = true ]] || fail "layout must require the root partition to be final"

exec 9>"${lock_file}"
flock -n 9 || fail "another rootfs expansion is already running"

minimum_media_bytes="$(json_value "${layout_file}" targetMedia.minimumQualifiedMediaBytes)"
minimum_growth_bytes="$(json_value "${layout_file}" firstBootExpansion.minimumGrowthBytes)"
locked_partition_number="$(json_value "${layout_file}" rootFilesystem.partitionNumber)"
locked_start_sector="$(json_value "${layout_file}" sourceGeometry.rootPartition.startSector)"
locked_initial_sectors="$(json_value "${layout_file}" sourceGeometry.rootPartition.sectorCount)"
locked_disk_id="$(json_value "${layout_file}" sourceGeometry.diskIdentifier)"
locked_uuid="$(json_value "${layout_file}" rootFilesystem.filesystemUuid)"
locked_partuuid="$(json_value "${layout_file}" rootFilesystem.partitionUuid)"

root_maj_min="$(findmnt -nro MAJ:MIN /)"
[[ "${root_maj_min}" =~ ^[0-9]+:[0-9]+$ ]] || fail "mounted root has no unambiguous MAJ:MIN identity"
root_sys_link="/sys/dev/block/${root_maj_min}"
[[ -L "${root_sys_link}" ]] || fail "mounted root MAJ:MIN has no sysfs block identity"
root_sys="$(readlink -f -- "${root_sys_link}")"
[[ "${root_sys}" == /sys/devices/* && -f "${root_sys}/partition" && -r "${root_sys}/dev" ]] || fail "mounted root is not a direct sysfs partition"
[[ "$(<"${root_sys}/dev")" = "${root_maj_min}" ]] || fail "sysfs root identity changed"
partition_number="$(<"${root_sys}/partition")"
[[ "${partition_number}" = "${locked_partition_number}" ]] || fail "mounted root partition number differs from the lock"
disk_sys="$(dirname -- "${root_sys}")"
[[ -r "${disk_sys}/dev" && ! -f "${disk_sys}/partition" ]] || fail "cannot identify a direct parent disk in sysfs"
disk_maj_min="$(<"${disk_sys}/dev")"
[[ "${disk_maj_min}" =~ ^[0-9]+:[0-9]+$ ]] || fail "parent disk has invalid sysfs identity"
root_name="$(basename -- "${root_sys}")"
disk_name="$(basename -- "${disk_sys}")"
root_device="/dev/${root_name}"
disk_device="/dev/${disk_name}"

verify_node_identity() {
    local node="$1" expected="$2" kind="$3" actual_hex major_hex minor_hex actual
    [[ -b "${node}" && ! -L "${node}" ]] || fail "${kind} node is not a direct block device: ${node}"
    actual_hex="$(stat -Lc '%t:%T' -- "${node}")"
    major_hex="${actual_hex%%:*}"; minor_hex="${actual_hex##*:}"
    actual="$((16#${major_hex})):$((16#${minor_hex}))"
    [[ "${actual}" = "${expected}" ]] || fail "${kind} node MAJ:MIN differs from sysfs"
}
verify_node_identity "${root_device}" "${root_maj_min}" root
verify_node_identity "${disk_device}" "${disk_maj_min}" disk
[[ "$(findmnt -nro FSTYPE /)" = ext4 ]] || fail "mounted root filesystem is not ext4"

verify_locked_identity() {
    [[ "$(findmnt -nro MAJ:MIN /)" = "${root_maj_min}" ]] || fail "mounted root MAJ:MIN changed during expansion"
    [[ "$(readlink -f -- "/sys/dev/block/${root_maj_min}")" = "${root_sys}" ]] || fail "mounted root sysfs identity changed during expansion"
    [[ "$(blkid -s UUID -o value -- "${root_device}")" = "${locked_uuid}" ]] || fail "root filesystem UUID differs from the lock"
    [[ "$(blkid -s PARTUUID -o value -- "${root_device}")" = "${locked_partuuid}" ]] || fail "root PARTUUID differs from the lock"
    [[ "$(blkid -s TYPE -o value -- "${root_device}")" = ext4 ]] || fail "root filesystem type differs from the lock"
    local pt
    pt="$(blkid -s PTUUID -o value -- "${disk_device}")"
    [[ "${pt,,}" = "${locked_disk_id}" ]] || fail "parent disk PTUUID differs from the lock"
}
verify_locked_identity

highest_partition=0
for sibling in "${disk_sys}"/*; do
    [[ -f "${sibling}/partition" ]] || continue
    sibling_number="$(<"${sibling}/partition")"
    [[ "${sibling_number}" =~ ^[0-9]+$ ]] || fail "parent disk has a non-numeric partition identity"
    (( sibling_number > highest_partition )) && highest_partition="${sibling_number}"
done
[[ "${partition_number}" = "${highest_partition}" ]] || fail "root partition is not the final partition"
start_sectors="$(<"${root_sys}/start")"
size_sectors="$(<"${root_sys}/size")"
[[ "${start_sectors}" = "${locked_start_sector}" ]] || fail "root partition start sector differs from the lock"
[[ "${size_sectors}" =~ ^[0-9]+$ && "${size_sectors}" -ge "${locked_initial_sectors}" ]] || fail "root partition is smaller than the locked initial image"
disk_bytes="$(blockdev --getsize64 "${disk_device}")"
[[ "${disk_bytes}" -ge "${minimum_media_bytes}" ]] || fail "target media is smaller than the qualified minimum"
partition_end_bytes="$(( (start_sectors + size_sectors) * 512 ))"
remaining_bytes="$(( disk_bytes - partition_end_bytes ))"
(( remaining_bytes >= 0 )) || fail "root partition extends beyond its parent disk"
if [[ "${dry_run}" = true ]]; then
    printf 'rootfs-expansion=DRY_RUN disk=%s root=%s maj_min=%s remaining_bytes=%s\n' "${disk_device}" "${root_device}" "${root_maj_min}" "${remaining_bytes}"
    exit 0
fi
if (( remaining_bytes >= minimum_growth_bytes )); then
    growpart "${disk_device}" "${partition_number}"
    partx --update --nr "${partition_number}" "${disk_device}" \
        || fail "kernel partition-table refresh failed"
    udevadm settle || fail "udev did not settle after growpart"
    grown_size="$(<"${root_sys}/size")"
    [[ "${grown_size}" =~ ^[0-9]+$ && "${grown_size}" -gt "${size_sectors}" ]] \
        || fail "growpart completed without strict sysfs partition growth"
fi
verify_locked_identity
[[ "$(<"${root_sys}/start")" = "${locked_start_sector}" ]] || fail "root partition start moved after growpart"
[[ "$(<"${root_sys}/size")" -ge "${size_sectors}" ]] || fail "root partition shrank after growpart"
resize2fs "${root_device}"
verify_locked_identity
read -r filesystem_blocks filesystem_block_size < <(dumpe2fs -h "${root_device}" 2>/dev/null | awk -F: '/^Block count:/{gsub(/ /,"",$2); b=$2} /^Block size:/{gsub(/ /,"",$2); s=$2} END{print b,s}')
current_partition_sectors="$(<"${root_sys}/size")"
[[ "${filesystem_blocks}" =~ ^[0-9]+$ && "${filesystem_block_size}" =~ ^[0-9]+$ ]] \
    || fail "cannot verify resized ext4 geometry"
filesystem_bytes="$((filesystem_blocks * filesystem_block_size))"
partition_bytes="$((current_partition_sectors * 512))"
[[ "${filesystem_bytes}" -le "${partition_bytes}" && "$((partition_bytes - filesystem_bytes))" -lt "${filesystem_block_size}" ]] \
    || fail "resized ext4 does not cover the available root partition"
printf 'rootfs-expansion=PASS disk=%s root=%s maj_min=%s\n' "${disk_device}" "${root_device}" "${root_maj_min}"
