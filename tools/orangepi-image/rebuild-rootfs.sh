#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_directory="${script_directory}"
source_image=""
output_partition=""
working_directory=""
source_loop=""
target_loop=""
source_mount=""
target_mount=""

fail() { printf 'rootfs-rebuild=FAIL: %s\n' "$1" >&2; exit 1; }
json_value() {
    python3 - "$1" "$2" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for part in sys.argv[2].split("."): value = value[part]
print(str(value).lower() if isinstance(value, bool) else value)
PY
}
cleanup() {
    for directory in "${target_mount}" "${source_mount}"; do
        [[ -n "${directory}" ]] || continue
        mountpoint -q -- "${directory}" && umount -- "${directory}" || true
        rmdir -- "${directory}" 2>/dev/null || true
    done
    [[ -z "${target_loop}" ]] || losetup -d "${target_loop}" 2>/dev/null || true
    [[ -z "${source_loop}" ]] || losetup -d "${source_loop}" 2>/dev/null || true
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir) config_directory="$2"; shift 2 ;;
        --source-image) source_image="$2"; shift 2 ;;
        --output-partition) output_partition="$2"; shift 2 ;;
        --working-directory) working_directory="$2"; shift 2 ;;
        *) fail "unknown argument: $1" ;;
    esac
done
[[ "$(id -u)" = 0 ]] || fail "root is required"
[[ -n "${source_image}" && -n "${output_partition}" && -n "${working_directory}" ]] || fail "source, output, and working directory are required"
for command_name in python3 losetup mount umount mountpoint mke2fs tune2fs e2fsck debugfs dumpe2fs; do
    command -v "${command_name}" >/dev/null || fail "required command is missing: ${command_name}"
done
source_image="$(readlink -f -- "${source_image}")"
config_directory="$(readlink -f -- "${config_directory}")"
working_directory="$(readlink -f -- "${working_directory}")"
[[ -f "${source_image}" && ! -L "${source_image}" && "$(stat -c %h -- "${source_image}")" = 1 ]] || fail "source image must be a regular single-link file"
[[ -d "${working_directory}" && ! -L "${working_directory}" ]] || fail "working directory is unsafe"
[[ ! -e "${output_partition}" ]] || fail "output partition already exists"

layout="${config_directory}/image-layout.json"
python3 "${script_directory}/lib/verify_raw_layout.py" --image "${source_image}" --layout "${layout}"
sector_bytes="$(json_value "${layout}" sourceGeometry.logicalSectorBytes)"
start_sector="$(json_value "${layout}" sourceGeometry.rootPartition.startSector)"
sector_count="$(json_value "${layout}" sourceGeometry.rootPartition.sectorCount)"
partition_bytes="$((sector_bytes * sector_count))"
epoch="$(json_value "${layout}" rootFilesystem.buildProfile.sourceDateEpoch)"
uuid="$(json_value "${layout}" rootFilesystem.filesystemUuid)"
label="$(json_value "${layout}" rootFilesystem.filesystemLabel)"
hash_seed="$(json_value "${layout}" rootFilesystem.buildProfile.directoryHashSeed)"
block_count="$(json_value "${layout}" rootFilesystem.buildProfile.blockCount)"
inode_count="$(json_value "${layout}" rootFilesystem.buildProfile.inodeCount)"
if ! filesystem_flag_value="$(python3 - "${layout}" <<'PY'
import json
import pathlib
import sys

flags = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["rootFilesystem"]["buildProfile"]["filesystemFlags"]
values = {
    ("signed_directory_hash",): "0x1",
    ("unsigned_directory_hash",): "0x2",
}
try:
    print(values[tuple(flags)])
except (KeyError, TypeError):
    raise SystemExit(2) from None
PY
)"; then
    fail "locked filesystem flags are unsupported"
fi

source_loop="$(losetup --find --show --read-only --offset "$((sector_bytes * start_sector))" --sizelimit "${partition_bytes}" -- "${source_image}")"
source_mount="$(mktemp -d "${working_directory}/source-root.XXXXXXXX")"
mount -t ext4 -o ro,noload,noatime,nodev,nosuid,noexec -- "${source_loop}" "${source_mount}"
source_inventory="${working_directory}/source-rootfs.inventory.json"
target_inventory="${working_directory}/target-rootfs.inventory.json"
python3 "${script_directory}/lib/rootfs_tree_inventory.py" capture --root "${source_mount}" --output "${source_inventory}"

# O_EXCL is intentional: no stale or attacker-selected partition image is reused.
( set -o noclobber; : > "${output_partition}" ) || fail "cannot exclusively create output partition"
truncate -s "${partition_bytes}" -- "${output_partition}"
env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LC_ALL=C TZ=UTC E2FSPROGS_FAKE_TIME="${epoch}" \
    mke2fs -q -F -t ext4 -b 4096 -I 256 -N "${inode_count}" -g 32768 -G 16 -m 2 \
    -L "${label}" -U "${uuid}" -J size=64 \
    -O has_journal,ext_attr,resize_inode,dir_index,filetype,extent,flex_bg,sparse_super,large_file,huge_file,dir_nlink,extra_isize,^64bit,^metadata_csum,^metadata_csum_seed,^orphan_file \
    -E "hash_seed=${hash_seed},lazy_itable_init=0,lazy_journal_init=0,nodiscard,root_owner=0:0" \
    -d "${source_mount}" "${output_partition}" "${block_count}"
# libext2fs chooses this hint from the builder architecture's default char
# signedness.  Set the locked, architecture-independent value immediately
# after population; mke2fs -d creates linear directories and no htree whose
# hashes would need conversion.
debugfs -w -R "set_super_value flags ${filesystem_flag_value}" \
    "${output_partition}" >/dev/null
env E2FSPROGS_FAKE_TIME="${epoch}" tune2fs -E hash_alg=half_md4 \
    -o journal_data_writeback,user_xattr,acl -e continue -c -1 -i 0 -- \
    "${output_partition}" >/dev/null

target_loop="$(losetup --find --show --read-only -- "${output_partition}")"
target_mount="$(mktemp -d "${working_directory}/target-root.XXXXXXXX")"
mount -t ext4 -o ro,noload,noatime,nodev,nosuid,noexec -- "${target_loop}" "${target_mount}"
python3 "${script_directory}/lib/rootfs_tree_inventory.py" capture --root "${target_mount}" --output "${target_inventory}"
python3 "${script_directory}/lib/rootfs_tree_inventory.py" compare --expected "${source_inventory}" --actual "${target_inventory}"
umount -- "${target_mount}"
losetup -d "${target_loop}"
target_loop=""
python3 "${script_directory}/lib/normalize_ext4_metadata.py" --device "${output_partition}" --inventory "${target_inventory}" --epoch "${epoch}"
set +e
env E2FSPROGS_FAKE_TIME="${epoch}" e2fsck -f -n -- "${output_partition}" >/dev/null
check_status=$?
set -e
[[ "${check_status}" = 0 ]] || fail "read-only e2fsck returned ${check_status}"
python3 "${script_directory}/lib/verify_ext4_profile.py" --device "${output_partition}" --layout "${layout}"
printf 'rootfs-rebuild=PASS partition=%s\n' "${output_partition}"
