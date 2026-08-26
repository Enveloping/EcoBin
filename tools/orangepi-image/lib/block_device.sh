# Shared block-device discovery for the locked Debian 12 image builder.
#
# util-linux 2.38.1 does not expose a PARTN lsblk column. Keep lsblk limited
# to stable device/type discovery and read the kernel-owned partition number
# from sysfs after proving that every partition is a direct child of the
# requested disk.
ecobin_list_direct_partitions() {
    local disk_device="$1"
    local disk_name="${disk_device##*/}"
    local disk_sys_link="/sys/class/block/${disk_name}"
    local disk_sys=""
    local block_rows=""
    local partition_name=""
    local partition_type=""
    local unexpected=""
    local partition_sys_link=""
    local partition_sys=""
    local partition_number=""

    [[ -b "${disk_device}" && ! -L "${disk_device}" ]] || return 1
    [[ -L "${disk_sys_link}" ]] || return 1
    disk_sys="$(readlink -f -- "${disk_sys_link}")" || return 1
    [[ "${disk_sys}" == /sys/devices/* && -r "${disk_sys}/dev" \
        && ! -f "${disk_sys}/partition" ]] || return 1

    block_rows="$(lsblk -nrpo NAME,TYPE -- "${disk_device}")" || return 1
    while read -r partition_name partition_type unexpected; do
        [[ -n "${partition_name}" ]] || continue
        [[ -z "${unexpected}" ]] || return 1
        [[ "${partition_type}" = part ]] || continue
        partition_sys_link="/sys/class/block/${partition_name##*/}"
        [[ -L "${partition_sys_link}" ]] || return 1
        partition_sys="$(readlink -f -- "${partition_sys_link}")" || return 1
        [[ "${partition_sys}" == /sys/devices/* \
            && "${partition_sys%/*}" = "${disk_sys}" \
            && -r "${partition_sys}/partition" ]] || return 1
        partition_number="$(<"${partition_sys}/partition")"
        [[ "${partition_number}" =~ ^[0-9]+$ ]] || return 1
        printf '%s %s\n' "${partition_name}" "${partition_number}"
    done <<< "${block_rows}"
}
