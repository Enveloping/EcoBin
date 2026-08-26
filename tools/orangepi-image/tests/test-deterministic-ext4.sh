#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tool_root="$(cd "${script_directory}/.." && pwd)"
for command_name in python3 mke2fs debugfs sha256sum mount umount losetup; do
    command -v "${command_name}" >/dev/null || { printf 'deterministic-ext4-test=SKIP missing=%s\n' "${command_name}"; exit 0; }
done
if [[ "$(id -u)" = 0 ]]; then
    privilege=()
elif command -v sudo >/dev/null && sudo -n true 2>/dev/null; then
    privilege=(sudo -n)
else
    printf 'deterministic-ext4-test=SKIP reason=no-root-mount-capability\n'
    exit 0
fi
temporary="$(mktemp -d /tmp/ecobin-ext4-test.XXXXXXXX)"
mountpoint_path="${temporary}/mount"
loop_device=""
cleanup() {
    mountpoint -q "${mountpoint_path}" 2>/dev/null && "${privilege[@]}" umount "${mountpoint_path}" || true
    [[ -z "${loop_device}" ]] || "${privilege[@]}" losetup -d "${loop_device}" 2>/dev/null || true
    if [[ "${ECOBIN_KEEP_TEST_TMP:-0}" = 1 ]]; then
        printf 'deterministic-ext4-test=KEEP path=%s\n' "${temporary}" >&2
    else
        rm -rf -- "${temporary}"
    fi
}
trap cleanup EXIT
mkdir "${temporary}/one" "${temporary}/two" "${mountpoint_path}"
python3 - "${temporary}/one" <<'PY'
import os, pathlib, sys
root = pathlib.Path(sys.argv[1])
(root / "etc").mkdir()
(root / "lost+found").mkdir(mode=0o700)
(root / "etc/fstab").write_text("UUID=test / ext4 defaults 0 1\n", encoding="utf-8")
(root / "data").mkdir()
(root / "data/file").write_bytes(b"semantic-content")
os.link(root / "data/file", root / "data/hardlink")
os.symlink("file", root / "data/symlink")
os.mkfifo(root / "data/fifo")
with (root / "data/sparse").open("wb") as stream:
    stream.write(b"A")
    stream.seek(8 * 1024 * 1024 - 1)
    stream.write(b"Z")
os.setxattr(root / "data/file", "user.ecobin", b"inventory")
for path in [root, *root.rglob("*")]:
    if not path.is_symlink():
        os.utime(path, (1700000000, 1700000000), follow_symlinks=False)
PY
sleep 1
cp -a --reflink=never "${temporary}/one/." "${temporary}/two/"
# Force distinct source ctimes while preserving semantic metadata.
touch "${temporary}/two/data/file"
touch -d @1700000000 "${temporary}/two/data/file"
python3 "${tool_root}/lib/rootfs_tree_inventory.py" capture --root "${temporary}/one" --output "${temporary}/source.json"

build_one() {
    local source="$1" image="$2" inventory="$3" epoch=1700000000
    truncate -s 96M "${image}"
    env LC_ALL=C TZ=UTC E2FSPROGS_FAKE_TIME="${epoch}" mke2fs -q -F -t ext4 \
        -b 4096 -I 256 -N 8192 -m 0 -L test -U 11111111-2222-3333-4444-555555555555 \
        -O has_journal,ext_attr,dir_index,filetype,extent,flex_bg,sparse_super,large_file,huge_file,dir_nlink,extra_isize,^64bit,^metadata_csum,^metadata_csum_seed,^orphan_file \
        -E hash_seed=95e5d17c-7d9b-48c9-88a5-616dab33efd4,lazy_itable_init=0,lazy_journal_init=0,nodiscard,root_owner=0:0 -d "${source}" "${image}"
    loop_device="$("${privilege[@]}" losetup --find --show --read-only "${image}")"
    "${privilege[@]}" mount -t ext4 -o ro,noload,noatime,nodev,nosuid,noexec "${loop_device}" "${mountpoint_path}"
    "${privilege[@]}" python3 "${tool_root}/lib/rootfs_tree_inventory.py" capture --root "${mountpoint_path}" --output "${inventory}"
    "${privilege[@]}" umount "${mountpoint_path}"
    "${privilege[@]}" losetup -d "${loop_device}"
    loop_device=""
    "${privilege[@]}" python3 "${tool_root}/lib/normalize_ext4_metadata.py" --device "${image}" --inventory "${inventory}" --epoch "${epoch}"
}
build_one "${temporary}/one" "${temporary}/one.img" "${temporary}/one.json"
build_one "${temporary}/two" "${temporary}/two.img" "${temporary}/two.json"
# Host staging trees and ext4 necessarily have different device/inode IDs,
# ctimes, and directory allocation sizes. Compare the two independently
# materialized ext4 inventories; the byte-for-byte image check below remains
# the stronger deterministic-build assertion.
python3 "${tool_root}/lib/rootfs_tree_inventory.py" compare --expected "${temporary}/one.json" --actual "${temporary}/two.json"
[[ "$(sha256sum "${temporary}/one.img" | awk '{print $1}')" = "$(sha256sum "${temporary}/two.img" | awk '{print $1}')" ]] \
    || { printf 'deterministic-ext4-test=FAIL: normalized images differ\n' >&2; exit 1; }
printf 'deterministic-ext4-test=PASS\n'
