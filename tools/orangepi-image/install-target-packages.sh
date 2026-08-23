#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
config_directory="${script_directory}"
rootfs=""
deb_directory=""
staging_directory=""
created_policy=false
mounted_devices=()
created_device_placeholders=()

fail() {
    printf 'target-packages=FAIL: %s\n' "$1" >&2
    exit 1
}

cleanup() {
    local cleanup_failed=false
    local index
    for ((index=${#mounted_devices[@]}-1; index>=0; index--)); do
        if mountpoint -q -- "${mounted_devices[index]}"; then
            umount -- "${mounted_devices[index]}" || cleanup_failed=true
        fi
    done
    for ((index=${#created_device_placeholders[@]}-1; index>=0; index--)); do
        rm -f -- "${created_device_placeholders[index]}" || cleanup_failed=true
    done
    if [[ -n "${staging_directory}" && -d "${staging_directory}" ]]; then
        case "${staging_directory}" in
            "${rootfs}"/tmp/ecobin-target-debs.*)
                rm -rf -- "${staging_directory}" || cleanup_failed=true
                ;;
            *)
                cleanup_failed=true
                ;;
        esac
    fi
    if [[ "${created_policy}" = true && -n "${rootfs}" ]]; then
        rm -f -- "${rootfs}/usr/sbin/policy-rc.d" || cleanup_failed=true
    fi
    [[ "${cleanup_failed}" = false ]]
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
        --rootfs)
            [[ $# -ge 2 ]] || fail "--rootfs requires a value"
            rootfs="$2"
            shift 2
            ;;
        --deb-dir)
            [[ $# -ge 2 ]] || fail "--deb-dir requires a value"
            deb_directory="$2"
            shift 2
            ;;
        --config-dir)
            [[ $# -ge 2 ]] || fail "--config-dir requires a value"
            config_directory="$2"
            shift 2
            ;;
        *)
            fail "unknown argument"
            ;;
    esac
done

[[ "$(id -u)" = 0 ]] || fail "target package installation requires root"
[[ "$(uname -m)" = aarch64 || "$(uname -m)" = arm64 ]] \
    || fail "target package installation requires an ARM64 builder"
for command_name in \
    python3 chroot dpkg-deb readlink stat cp sync find sort basename chmod \
    rm mktemp mount umount mountpoint touch sha256sum awk; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required target installer command is missing: ${command_name}"
done
python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${config_directory}" --require-locked >/dev/null

[[ -n "${rootfs}" && "${rootfs}" = /* ]] \
    || fail "rootfs must be an explicit absolute path"
[[ -d "${rootfs}" && ! -L "${rootfs}" ]] || fail "rootfs is unsafe"
rootfs="$(readlink -f -- "${rootfs}")"
[[ "${rootfs}" != / && -f "${rootfs}/etc/os-release" \
    && -x "${rootfs}/usr/bin/dpkg" ]] \
    || fail "rootfs is not a supported Debian image"
[[ -d "${rootfs}/tmp" && ! -L "${rootfs}/tmp" ]] \
    || fail "rootfs temporary directory is unsafe"
[[ -d "${rootfs}/dev" && ! -L "${rootfs}/dev" ]] \
    || fail "rootfs device directory is unsafe"
[[ "$(stat -c '%u:%g' -- "${rootfs}")" = 0:0 ]] \
    || fail "rootfs must be owned by root"

[[ -n "${deb_directory}" && -d "${deb_directory}" \
    && ! -L "${deb_directory}" ]] || fail "offline deb directory is unsafe"
deb_directory="$(readlink -f -- "${deb_directory}")"
[[ "$(stat -c '%u:%g' -- "${deb_directory}")" = 0:0 \
    && "$(stat -c '%a' -- "${deb_directory}")" =~ ^[0-7]*[05][0-5]$ ]] \
    || fail "offline deb directory ownership or mode is unsafe"

mapfile -t package_lock_rows < <(
    python3 - "${config_directory}/apt-packages.lock" <<'PY'
import pathlib
import sys
for line in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    if line and not line.startswith("#"):
        print(line)
PY
)
[[ "${#package_lock_rows[@]}" -gt 0 ]] || fail "target package lock is empty"

package_specs=()
declare -A locked_archive_sha256=()
for row in "${package_lock_rows[@]}"; do
    specification="${row%% sha256=*}"
    digest="${row##* sha256=}"
    package_specs+=("${specification}")
    locked_archive_sha256["${specification}"]="${digest}"
done

mapfile -t deb_files < <(find "${deb_directory}" -mindepth 1 -maxdepth 1 \
    -type f -links 1 -name '*.deb' -printf '%p\n' | sort)
[[ "${#deb_files[@]}" = "${#package_specs[@]}" ]] \
    || fail "offline deb set does not match the target package lock"

declare -A locked_versions=()
for specification in "${package_specs[@]}"; do
    package_with_arch="${specification%%=*}"
    package="${package_with_arch%%:*}"
    arch="${package_with_arch#*:}"
    version="${specification#*=}"
    locked_versions["${package}:${arch}"]="${version}"
done
declare -A seen_packages=()
for deb in "${deb_files[@]}"; do
    [[ ! -L "${deb}" && "$(stat -c '%u:%g' -- "${deb}")" = 0:0 \
        && "$(stat -c '%a' -- "${deb}")" =~ ^[0-7]*[04][04]$ ]] \
        || fail "offline deb file ownership or mode is unsafe"
    package="$(dpkg-deb -f "${deb}" Package)"
    arch="$(dpkg-deb -f "${deb}" Architecture)"
    version="$(dpkg-deb -f "${deb}" Version)"
    key="${package}:${arch}"
    specification="${key}=${version}"
    actual_archive_sha256="$(sha256sum -- "${deb}" | awk '{print $1}')"
    [[ -n "${locked_versions[$key]:-}" \
        && "${locked_versions[$key]}" = "${version}" \
        && "${locked_archive_sha256[$specification]:-}" \
            = "${actual_archive_sha256}" \
        && -z "${seen_packages[$key]:-}" ]] \
        || fail "offline deb identity differs from the lock"
    seen_packages["${key}"]=1
done
[[ "${#seen_packages[@]}" = "${#locked_versions[@]}" ]] \
    || fail "offline deb identities are incomplete"

policy_path="${rootfs}/usr/sbin/policy-rc.d"
if [[ -e "${policy_path}" || -L "${policy_path}" ]]; then
    fail "candidate already contains policy-rc.d; refusing ambiguous service policy"
fi
printf '#!/bin/sh\nexit 101\n' > "${policy_path}"
chmod 0755 "${policy_path}"
created_policy=true

for device_name in null zero random urandom; do
    source_device="/dev/${device_name}"
    target_device="${rootfs}/dev/${device_name}"
    [[ -c "${source_device}" ]] || fail "builder device boundary is unavailable"
    [[ ! -e "${target_device}" && ! -L "${target_device}" ]] \
        || fail "candidate device placeholder already exists"
    touch -- "${target_device}"
    chmod 0600 "${target_device}"
    created_device_placeholders+=("${target_device}")
    mount --bind "${source_device}" "${target_device}"
    mounted_devices+=("${target_device}")
done

staging_directory="$(mktemp -d "${rootfs}/tmp/ecobin-target-debs.XXXXXXXX")"
[[ "$(stat -c '%u:%g:%a' -- "${staging_directory}")" = 0:0:700 ]] \
    || fail "target package staging directory is unsafe"
for deb in "${deb_files[@]}"; do
    cp --reflink=never --preserve=mode,timestamps -- "${deb}" "${staging_directory}/"
done
# Rewrite the paths relative to the chroot while retaining the random private
# directory component.
chroot_debs=()
for deb in "${deb_files[@]}"; do
    chroot_debs+=("/tmp/$(basename -- "${staging_directory}")/$(basename -- "${deb}")")
done

chroot "${rootfs}" /usr/bin/env -i \
    HOME=/root PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    LC_ALL=C.UTF-8 TZ=UTC DEBIAN_FRONTEND=noninteractive \
    /usr/bin/dpkg --install "${chroot_debs[@]}"

for specification in "${package_specs[@]}"; do
    package="${specification%%=*}"
    expected="${specification#*=}"
    actual="$(chroot "${rootfs}" /usr/bin/dpkg-query -W -f='${Version}' "${package}")"
    [[ "${actual}" = "${expected}" ]] \
        || fail "installed target package version differs: ${package%%:*}"
done
audit_output="$(chroot "${rootfs}" /usr/bin/dpkg --audit)"
[[ -z "${audit_output}" ]] || fail "target package dependency audit failed"
for ((index=${#mounted_devices[@]}-1; index>=0; index--)); do
    umount -- "${mounted_devices[index]}"
done
mounted_devices=()
for ((index=${#created_device_placeholders[@]}-1; index>=0; index--)); do
    rm -f -- "${created_device_placeholders[index]}"
done
created_device_placeholders=()
rm -rf -- "${staging_directory}"
staging_directory=""
rm -f -- "${policy_path}"
created_policy=false
sync -f -- "${rootfs}"
printf 'target-packages=PASS count=%s\n' "${#package_specs[@]}"
