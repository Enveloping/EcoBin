# Shared block-device discovery for the locked Debian 12 image builder.
#
# util-linux 2.38.1 does not expose a PARTN lsblk column, and a privileged
# container is not guaranteed to receive udev-created /dev/loopXpY nodes.
# Resolve the locked final partition entirely through kernel-owned sysfs and
# return its 512-byte-sector start and size. Callers can then map that range to
# a separate loop device without creating nodes in the host /dev tree.
ecobin_final_partition_geometry() {
    local disk_device="$1"
    local expected_partition_number="$2"
    local disk_name="${disk_device##*/}"
    local disk_sys_link="/sys/class/block/${disk_name}"
    local disk_sys=""
    local partition_sys=""
    local partition_number=""
    local partition_start=""
    local partition_size=""
    local selected_start=""
    local selected_size=""
    local highest_partition=0

    [[ -b "${disk_device}" && ! -L "${disk_device}" ]] || return 1
    [[ "${expected_partition_number}" =~ ^[0-9]+$ ]] || return 1
    [[ -L "${disk_sys_link}" ]] || return 1
    disk_sys="$(readlink -f -- "${disk_sys_link}")" || return 1
    [[ "${disk_sys}" == /sys/devices/* && -r "${disk_sys}/dev" \
        && ! -f "${disk_sys}/partition" ]] || return 1

    for partition_sys in "${disk_sys}"/*; do
        [[ -f "${partition_sys}/partition" ]] || continue
        [[ -r "${partition_sys}/start" && -r "${partition_sys}/size" \
            && -r "${partition_sys}/dev" ]] || return 1
        partition_number="$(<"${partition_sys}/partition")"
        partition_start="$(<"${partition_sys}/start")"
        partition_size="$(<"${partition_sys}/size")"
        [[ "${partition_number}" =~ ^[0-9]+$ \
            && "${partition_start}" =~ ^[0-9]+$ \
            && "${partition_size}" =~ ^[1-9][0-9]*$ ]] || return 1
        (( partition_number > highest_partition )) \
            && highest_partition="${partition_number}"
        if [[ "${partition_number}" = "${expected_partition_number}" ]]; then
            selected_start="${partition_start}"
            selected_size="${partition_size}"
        fi
    done

    [[ -n "${selected_start}" && -n "${selected_size}" \
        && "${expected_partition_number}" = "${highest_partition}" ]] \
        || return 1
    printf '%s %s\n' "${selected_start}" "${selected_size}"
}

ecobin_verify_dos_partition_identity() {
    local disk_device="$1"
    local partition_table_type="$2"
    local expected_disk_identifier="$3"
    local partition_number="$4"
    local expected_partition_uuid="$5"
    local derived_partition_uuid=""
    local actual_disk_identifier=""

    [[ -b "${disk_device}" && ! -L "${disk_device}" \
        && "${partition_table_type}" = dos \
        && "${expected_disk_identifier}" =~ ^[0-9a-f]{8}$ \
        && "${partition_number}" =~ ^[1-9][0-9]*$ ]] || return 1
    derived_partition_uuid="$(printf '%s-%02d' \
        "${expected_disk_identifier}" "${partition_number}")"
    [[ "${expected_partition_uuid,,}" = "${derived_partition_uuid}" ]] \
        || return 1
    actual_disk_identifier="$(blkid -s PTUUID -o value -- "${disk_device}")" \
        || return 1
    [[ "${actual_disk_identifier,,}" = "${expected_disk_identifier}" ]]
}
