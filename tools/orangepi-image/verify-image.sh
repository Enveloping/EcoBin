#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
config_directory="${script_directory}"
image_path=""
audit_mode="candidate"
software_payload=""
software_payload_sha256=""
release_id=""
version=""
git_commit=""
loop_device=""
mount_directory=""

fail() {
    printf 'image-verification=FAIL: %s\n' "$1" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: verify-image.sh --image FILE [--config-dir DIR] [--candidate|--sealed]
       [--software-payload DIR] --software-payload-sha256 HEX
       --release-id ID --version X.Y.Z --git-commit HEX

The image is attached read-only and its root filesystem is mounted with
ro,noload,nodev,nosuid,noexec. Candidate mode requires K1 and setup AP secrets
to be absent. Sealed mode only checks their expected paths and permissions; it
does not print or hash their values.
EOF
}

cleanup() {
    if [[ -n "${mount_directory}" && -d "${mount_directory}" ]]; then
        if mountpoint -q -- "${mount_directory}"; then
            umount -- "${mount_directory}" || true
        fi
        case "${mount_directory}" in
            /tmp/ecobin-image-verify.*)
                rmdir -- "${mount_directory}" 2>/dev/null || true
                ;;
        esac
    fi
    if [[ -n "${loop_device}" ]]; then
        losetup -d -- "${loop_device}" 2>/dev/null || true
    fi
}
trap cleanup EXIT

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
        --candidate)
            audit_mode="candidate"
            shift
            ;;
        --sealed)
            audit_mode="sealed"
            shift
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
if [[ -n "${software_payload}${software_payload_sha256}${release_id}${version}${git_commit}" ]]; then
    [[ -n "${software_payload_sha256}" && -n "${release_id}" \
        && -n "${version}" && -n "${git_commit}" ]] \
        || fail "external software audit identities must be supplied together"
    [[ "${software_payload_sha256}" =~ ^[0-9a-f]{64}$ ]] \
        || fail "external software payload SHA-256 is malformed"
    if [[ -n "${software_payload}" ]]; then
        [[ -d "${software_payload}" && ! -L "${software_payload}" ]] \
            || fail "software payload must be a regular directory"
        software_payload="$(readlink -f -- "${software_payload}")"
    fi
fi
[[ "$(id -u)" = 0 ]] || fail "read-only loop inspection requires root"
for command_name in \
    python3 losetup lsblk blkid mount umount mountpoint stat find grep awk \
    readlink basename sfdisk dumpe2fs; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required command is missing: ${command_name}"
done

python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${config_directory}" --require-locked >/dev/null
[[ -f "${image_path}" && ! -L "${image_path}" ]] \
    || fail "image must be a regular non-symlink file"
config_directory="$(readlink -f -- "${config_directory}")"
image_path="$(readlink -f -- "${image_path}")"
python3 "${script_directory}/lib/verify_raw_layout.py" \
    --image "${image_path}" --layout "${config_directory}/image-layout.json" \
    || fail "raw image partition geometry or boot prefix differs from the lock"

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

loop_device="$(losetup --find --show --partscan --read-only -- "${image_path}")"
[[ -b "${loop_device}" ]] || fail "failed to attach a read-only loop device"
udevadm settle 2>/dev/null || true

mapfile -t partition_rows < <(lsblk -nrpo NAME,TYPE,PARTN -- "${loop_device}" \
    | awk '$2 == "part" {print $1 " " $3}')
[[ ${#partition_rows[@]} -gt 0 ]] || fail "image has no partitions"
root_partition=""
highest_partition=0
for row in "${partition_rows[@]}"; do
    partition_name="${row% *}"
    partition_number="${row##* }"
    [[ "${partition_number}" =~ ^[0-9]+$ ]] \
        || fail "image contains a partition without a numeric PARTN"
    (( partition_number > highest_partition )) && highest_partition="${partition_number}"
    if [[ "${partition_number}" = "${root_partition_number}" ]]; then
        root_partition="${partition_name}"
    fi
done
[[ -n "${root_partition}" && -b "${root_partition}" ]] \
    || fail "locked root partition is absent"
[[ "${root_partition_number}" = "${highest_partition}" ]] \
    || fail "root partition is not the final image partition"

actual_type="$(blkid -s TYPE -o value -- "${root_partition}")"
actual_uuid="$(blkid -s UUID -o value -- "${root_partition}")"
actual_partuuid="$(blkid -s PARTUUID -o value -- "${root_partition}")"
[[ "${actual_type}" = ext4 ]] || fail "locked root partition is not ext4"
[[ "${actual_uuid,,}" = "${expected_filesystem_uuid,,}" ]] \
    || fail "root filesystem UUID does not match image-layout.json"
[[ "${actual_partuuid,,}" = "${expected_partition_uuid,,}" ]] \
    || fail "root partition UUID does not match image-layout.json"
python3 "${script_directory}/lib/verify_ext4_profile.py" \
    --device "${root_partition}" --layout "${config_directory}/image-layout.json" \
    || fail "root ext4 construction profile differs from image-layout.json"

mount_directory="$(mktemp -d /tmp/ecobin-image-verify.XXXXXXXX)"
mount -t ext4 -o ro,noload,nodev,nosuid,noexec -- \
    "${root_partition}" "${mount_directory}"

assert_rooted_path_has_no_symlink_component() {
    local relative_path="$1"
    local current_path="${mount_directory}"
    local component
    local -a components
    IFS='/' read -r -a components <<< "${relative_path}"
    for component in "${components[@]}"; do
        [[ -n "${component}" && "${component}" != . && "${component}" != .. ]] \
            || fail "audit policy contains an unsafe path component"
        current_path="${current_path}/${component}"
        [[ ! -L "${current_path}" ]] \
            || fail "candidate audit path crosses a symbolic link: /${relative_path}"
        [[ -e "${current_path}" ]] || break
    done
}

for forbidden_path in \
    etc/ecobin/device-credentials.json \
    etc/ecobin/remote-support-credentials.json \
    var/lib/ecobin/enrollment-state.json \
    var/lib/ecobin/first-boot/state.json \
    var/lib/ecobin/first-boot/sealed.json \
    var/lib/ecobin/remote-support/state.db \
    var/lib/ecobin/hardware/edge.db \
    var/lib/dbus/machine-id \
    var/lib/systemd/random-seed \
    var/lib/systemd/timesync/clock \
    var/lib/private/systemd-timesync/clock \
    var/lib/urandom/random-seed \
    var/lib/NetworkManager/secret_key \
    var/lib/NetworkManager/seen-bssids \
    var/lib/NetworkManager/timestamps \
    etc/udev/rules.d/70-persistent-net.rules \
    root/.ssh \
    home/orangepi/.ssh \
    root/.bash_history \
    root/.lesshst \
    root/.python_history \
    root/.wget-hsts \
    home/orangepi/.bash_history \
    home/orangepi/.lesshst \
    home/orangepi/.python_history \
    home/orangepi/.wget-hsts \
    root/EcoBin/hardware/.env \
    home/orangepi/EcoBin/hardware/.env \
    etc/ecobin/onenet-device.key \
    etc/ecobin/onenet.key \
    swapfile \
    etc/dphys-swapfile \
    etc/systemd/swap.conf \
    etc/systemd/swap.conf.d \
    var/lib/dphys-swapfile; do
    assert_rooted_path_has_no_symlink_component "${forbidden_path}"
    [[ ! -e "${mount_directory}/${forbidden_path}" \
        && ! -L "${mount_directory}/${forbidden_path}" ]] \
        || fail "candidate contains device-unique state: /${forbidden_path}"
done
zram_config="${mount_directory}/etc/default/orangepi-zram-config"
[[ -f "${zram_config}" && ! -L "${zram_config}" ]] \
    || fail "Orange Pi zram must have an explicit regular disabled configuration"
grep -qx 'ENABLED=false' "${zram_config}" \
    && grep -qx 'SWAP=false' "${zram_config}" \
    || fail "Orange Pi zram or zram swap is not explicitly disabled"
for zram_path in etc/systemd/zram-generator.conf etc/systemd/zram-generator.conf.d usr/lib/systemd/zram-generator.conf usr/lib/systemd/zram-generator.conf.d etc/default/zramswap; do
    [[ ! -e "${mount_directory}/${zram_path}" && ! -L "${mount_directory}/${zram_path}" ]] \
        || fail "automatic zram configuration is forbidden: /${zram_path}"
done

assert_rooted_path_has_no_symlink_component etc/ssh
[[ ! -L "${mount_directory}/etc/ssh" ]] \
    || fail "candidate /etc/ssh must not be a symbolic link"
if find "${mount_directory}/etc/ssh" -maxdepth 1 -type f \
    -name 'ssh_host_*' -print -quit 2>/dev/null | grep -q .; then
    fail "image contains generated SSH host keys"
fi
if find "${mount_directory}" -xdev -type d -name .git -print -quit \
    2>/dev/null | grep -q .; then
    fail "image contains Git metadata"
fi
assert_rooted_path_has_no_symlink_component etc/machine-id
[[ ! -L "${mount_directory}/etc/machine-id" ]] \
    || fail "candidate /etc/machine-id must not be a symbolic link"
if [[ -s "${mount_directory}/etc/machine-id" ]]; then
    fail "image machine-id must be empty or absent before first boot"
fi

for clean_directory in \
    var/lib/ecobin/hardware \
    var/lib/ecobin/remote-support \
    var/lib/ecobin/first-boot \
    var/lib/ecobin/factory-test \
    root/EcoBin/hardware/data \
    var/lib/cloud \
    var/lib/dhcp \
    var/lib/dhcpcd5 \
    var/lib/wpa_supplicant \
    var/log \
    var/cache/apt/archives \
    var/lib/apt/lists \
    tmp \
    var/tmp; do
    assert_rooted_path_has_no_symlink_component "${clean_directory}"
    full_directory="${mount_directory}/${clean_directory}"
    [[ ! -L "${full_directory}" ]] \
        || fail "candidate cleanup directory is a symbolic link: /${clean_directory}"
    if [[ -d "${full_directory}" ]] \
        && find "${full_directory}" -mindepth 1 -print -quit 2>/dev/null \
            | grep -q .; then
        fail "candidate cleanup directory is not empty: /${clean_directory}"
    fi
done

shadow_file="${mount_directory}/etc/shadow"
assert_rooted_path_has_no_symlink_component etc/shadow
[[ -f "${shadow_file}" && ! -L "${shadow_file}" ]] \
    || fail "candidate is missing a regular /etc/shadow"
awk -F: '
    $1 == "root" || $1 == "orangepi" {
        seen[$1]++
        if ($2 != "!") bad = 1
    }
    END {
        if (seen["root"] != 1 || seen["orangepi"] != 1 || bad) exit 1
    }
' "${shadow_file}" \
    || fail "root and orangepi password authentication must be locked"

python3 "${script_directory}/lib/sanitize_rootfs.py" \
    --root "${mount_directory}" \
    --confirm-root "${mount_directory}" \
    --audit-local-login-only \
    || fail "getty, serial-getty, or display-manager automatic login is enabled"

if ! python3 - "${mount_directory}" "${config_directory}/apt-packages.lock" "${expected_filesystem_uuid}" "${expected_partition_uuid}" <<'PY'
import os
import pathlib
import re
import stat
import sys

root = pathlib.Path(sys.argv[1])
apt_lock = pathlib.Path(sys.argv[2])
expected_uuid = sys.argv[3].lower()
expected_partuuid = sys.argv[4].lower()
boot = root / "boot"
env_path = boot / "orangepiEnv.txt"
if env_path.is_symlink() or not env_path.is_file():
    raise SystemExit(1)
content = env_path.read_text(encoding="utf-8")
rootdev_lines = [line.split("#", 1)[0].strip() for line in content.splitlines() if line.split("#", 1)[0].strip().startswith("rootdev=")]
if len(rootdev_lines) != 1 or rootdev_lines[0].split("=", 1)[1].lower() not in {"uuid=" + expected_uuid, "partuuid=" + expected_partuuid}:
    raise SystemExit(1)
fstab = root / "etc/fstab"
if fstab.is_symlink() or not fstab.is_file():
    raise SystemExit(1)
root_rows = []
for raw_line in fstab.read_text(encoding="utf-8").splitlines():
    fields = raw_line.split("#", 1)[0].split()
    if len(fields) >= 3:
        if fields[2].lower() == "swap":
            raise SystemExit(1)
        if fields[1] == "/":
            root_rows.append(fields)
if len(root_rows) != 1 or root_rows[0][2] != "ext4" or root_rows[0][0].lower() not in {"uuid=" + expected_uuid, "partuuid=" + expected_partuuid}:
    raise SystemExit(1)
overlay_lines = [
    line for line in content.splitlines()
    if re.match(r"^\s*overlays\s*=", line)
]
if len(overlay_lines) != 1:
    raise SystemExit(1)
overlay_value = overlay_lines[0].split("=", 1)[1].split("#", 1)[0]
overlays = overlay_value.split()
if overlays.count("ph-uart5") != 1 or "ph-pwm12" in overlays:
    raise SystemExit(1)
if not any(
    candidate.is_file()
    for candidate in boot.rglob("*ph-uart5*.dtbo")
):
    raise SystemExit(1)

console_pattern = re.compile(
    rb"(?<![A-Za-z0-9_])console=(?:/dev/)?ttyS5(?:[,\s\x00]|$)"
)
for relative in (
    pathlib.Path("orangepiEnv.txt"),
    pathlib.Path("boot.cmd"),
    pathlib.Path("boot.scr"),
    pathlib.Path("extlinux/extlinux.conf"),
):
    candidate = boot / relative
    if os.path.lexists(candidate):
        if candidate.is_symlink() or not candidate.is_file():
            raise SystemExit(1)
        if console_pattern.search(candidate.read_bytes()):
            raise SystemExit(1)

systemd = root / "etc/systemd/system"
for unit_name in (
    "serial-getty@ttyS5.service",
    "getty@ttyS5.service",
):
    mask = systemd / unit_name
    if not mask.is_symlink() or os.readlink(mask) != "/dev/null":
        raise SystemExit(1)

helper = root / "usr/lib/ecobin/mcu_safe_gpio.py"
unit = systemd / "ecobin-mcu-safe-gpio.service"
enabled = systemd / "multi-user.target.wants/ecobin-mcu-safe-gpio.service"
expansion = root / "usr/lib/ecobin/expand-rootfs.sh"
expansion_unit = systemd / "ecobin-expand-rootfs.service"
installed_layout = root / "usr/share/ecobin/image-layout.json"
for trusted_tool in (
    root / "usr/bin/gpio",
    root / "usr/bin/growpart",
    root / "usr/bin/stm32flash",
    root / "usr/sbin/nft",
):
    metadata = trusted_tool.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or metadata.st_mode & 0o022
        or not metadata.st_mode & 0o111
    ):
        raise SystemExit(1)
for candidate in (helper, unit):
    metadata = candidate.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o644
    ):
        raise SystemExit(1)
for candidate, expected_mode in (
    (expansion, 0o755),
    (expansion_unit, 0o644),
    (installed_layout, 0o644),
):
    metadata = candidate.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        raise SystemExit(1)
if not enabled.is_symlink() or os.readlink(enabled) != "../ecobin-mcu-safe-gpio.service":
    raise SystemExit(1)
unit_content = unit.read_text(encoding="utf-8")
if (
    "ExecStart=/usr/bin/python3 /usr/lib/ecobin/mcu_safe_gpio.py "
    "--gpio-path /usr/bin/gpio"
) not in unit_content:
    raise SystemExit(1)
if (
    "ExecStart=/usr/lib/ecobin/expand-rootfs.sh "
    "--layout /usr/share/ecobin/image-layout.json"
) not in expansion_unit.read_text(encoding="utf-8"):
    raise SystemExit(1)
if installed_layout.read_bytes() != apt_lock.parent.joinpath(
    "image-layout.json"
).read_bytes():
    raise SystemExit(1)
helper_content = helper.read_text(encoding="utf-8")
if "MCU_BOOT0_WPI = 2" not in helper_content or "MCU_RESET_GATE_WPI = 5" not in helper_content:
    raise SystemExit(1)

expected_packages = {}
for line in apt_lock.read_text(encoding="utf-8").splitlines():
    if not line or line.startswith("#"):
        continue
    specification = line.split(" sha256=", 1)[0]
    package_with_arch, version = specification.split("=", 1)
    package = package_with_arch.split(":", 1)[0]
    expected_packages[package] = version
status_text = (root / "var/lib/dpkg/status").read_text(encoding="utf-8")
installed = {}
for block in status_text.split("\n\n"):
    fields = {}
    for line in block.splitlines():
        if ": " in line and not line.startswith((" ", "\t")):
            key, value = line.split(": ", 1)
            fields[key] = value
    if fields.get("Status") == "install ok installed":
        installed[fields.get("Package")] = fields.get("Version")
if any(installed.get(package) != version for package, version in expected_packages.items()):
    raise SystemExit(1)
PY
then
    fail "target packages, UART5, or immutable MCU safe-GPIO configuration is incomplete"
fi

systemd_directory="${mount_directory}/etc/systemd/system"
assert_rooted_path_has_no_symlink_component etc/systemd/system
[[ ! -L "${systemd_directory}" ]] \
    || fail "candidate /etc/systemd/system must not be a symbolic link"
if [[ -d "${systemd_directory}" ]]; then
    while IFS= read -r -d '' enabled_link; do
        enabled_name="$(basename -- "${enabled_link}")"
        enabled_target=""
        if [[ -L "${enabled_link}" ]]; then
            enabled_target="$(readlink -- "${enabled_link}")"
        fi
        target_name="${enabled_target##*/}"
        for candidate_name in "${enabled_name}" "${target_name}"; do
            lowercase_name="${candidate_name,,}"
            case "${lowercase_name}" in
                orangepi-resize-filesystem.service|\
                ecobin-cellular-uplink.service|\
                ecobin-enrollment.service|\
                ecobin-factory-ap.service|\
                ecobin-factory-portal.service|\
                ecobin-factory-test.service|\
                ecobin-factory-handoff.service|\
                ecobin-hardware.service|\
                ecobin-remote-support.service|\
                ecobin-runtime.target)
                    fail "stage service must not be independently enabled: ${candidate_name}"
                    ;;
                *.swap|dphys-swapfile*|systemd-swap*|orangepi-zram*|zramswap*|systemd-zram-setup@*)
                    fail "persistent swap unit must not be enabled: ${candidate_name}"
                    ;;
                *simulat*|*mock*|*fake*)
                    fail "image contains a pre-enabled simulator, mock, or fake unit"
                    ;;
            esac
        done
    done < <(find "${systemd_directory}" \( -type l -o -type f \) \
        \( -path '*.wants/*' -o -path '*.requires/*' -o -path '*.upholds/*' \) \
        -print0 2>/dev/null)
fi

connection_directory="${mount_directory}/etc/NetworkManager/system-connections"
assert_rooted_path_has_no_symlink_component etc/NetworkManager/system-connections
[[ ! -L "${connection_directory}" ]] \
    || fail "NetworkManager connection directory must not be a symbolic link"
if [[ -d "${connection_directory}" ]] \
    && find "${connection_directory}" -type l -print -quit 2>/dev/null \
        | grep -q .; then
    fail "NetworkManager connection directory contains a symbolic link"
fi
if [[ -d "${connection_directory}" ]]; then
    while IFS= read -r -d '' connection_profile; do
        if grep -Eiq \
            '^[[:space:]]*autoconnect[[:space:]]*=[[:space:]]*(true|yes|1)[[:space:]]*$' \
            "${connection_profile}"; then
            fail "image contains a pre-enabled NetworkManager connection"
        fi
        if ! grep -Eiq \
            '^[[:space:]]*autoconnect[[:space:]]*=[[:space:]]*(false|no|0)[[:space:]]*$' \
            "${connection_profile}"; then
            fail "NetworkManager profile does not explicitly disable autoconnect"
        fi
    done < <(find "${connection_directory}" -maxdepth 1 -type f -print0 2>/dev/null)
fi


ecobin_config_directory="${mount_directory}/etc/ecobin"
assert_rooted_path_has_no_symlink_component etc/ecobin
[[ ! -L "${ecobin_config_directory}" ]] \
    || fail "candidate /etc/ecobin must not be a symbolic link"
if [[ -d "${ecobin_config_directory}" ]] \
    && grep -rqsE \
        '^[[:space:]]*((ECOBIN_)?ONENET_([A-Z0-9_]*_)?(DEVICE_)?KEY|ECOBIN_DEVICE_KEY)[[:space:]]*=[[:space:]]*[^#[:space:]]' \
        "${ecobin_config_directory}"; then
    fail "image contains a configured OneNet device key"
fi
if [[ -d "${ecobin_config_directory}" ]] \
    && grep -rqsE \
        '"deviceKey"[[:space:]]*:[[:space:]]*"[^"[:space:]]+' \
        "${ecobin_config_directory}"; then
    fail "image contains a configured OneNet device key"
fi

software_audit=(
    python3 "${repository_root}/hardware/system/image_software_installer.py" audit
    --rootfs "${mount_directory}"
    --repository-root "${repository_root}"
)
if [[ -n "${software_payload_sha256}" ]]; then
    software_audit+=(
        --payload-sha256 "${software_payload_sha256}"
        --release-id "${release_id}"
        --version "${version}"
        --git-commit "${git_commit}"
    )
    if [[ -n "${software_payload}" ]]; then
        software_audit+=(--payload "${software_payload}")
    fi
fi
"${software_audit[@]}" \
    || fail "installed EcoBin software, units, or enable state differs from the controlled sources"

if [[ "${audit_mode}" = candidate ]]; then
    [[ ! -e "${mount_directory}/etc/ecobin/enrollment.key" \
        && ! -L "${mount_directory}/etc/ecobin/enrollment.key" ]] \
        || fail "no-secret candidate unexpectedly contains K1"
    [[ ! -e "${mount_directory}/etc/ecobin/setup-ap.key" \
        && ! -L "${mount_directory}/etc/ecobin/setup-ap.key" ]] \
        || fail "no-secret candidate unexpectedly contains setup AP password"
else
    for secret_path in etc/ecobin/enrollment.key etc/ecobin/setup-ap.key; do
        full_path="${mount_directory}/${secret_path}"
        [[ -f "${full_path}" && ! -L "${full_path}" ]] \
            || fail "sealed image is missing protected input: /${secret_path}"
        [[ "$(stat -c '%u:%g:%a' -- "${full_path}")" = 0:0:600 ]] \
            || fail "sealed protected input has invalid ownership or mode: /${secret_path}"
    done
fi

printf 'image-verification=PASS mode=%s image=%s root_partition=%s\n' \
    "${audit_mode}" "${image_path}" "${root_partition_number}"
