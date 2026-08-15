#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_root="$(cd "${script_directory}/.." && pwd)"

usage() {
    cat >&2 <<'EOF'
Usage: install-server.sh --maintenance-ca-public-key PATH \
    [--backend-uid 10001] [--backend-gid 10001]

Installs the ecobin-tunnel, ecobin-jump and ecobin-lease-reader boundary on a
systemd Linux bastion. It does not generate or install a CA private key and it
does not change the firewall.
EOF
}

maintenance_ca_public_key=
backend_uid=10001
backend_gid=10001
while (($# > 0)); do
    case "$1" in
        --maintenance-ca-public-key)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            maintenance_ca_public_key="$2"
            shift 2
            ;;
        --backend-uid)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            backend_uid="$2"
            shift 2
            ;;
        --backend-gid)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            backend_gid="$2"
            shift 2
            ;;
        *) usage; exit 2 ;;
    esac
done

[[ "$(id -u)" = 0 ]] || {
    echo "install-server.sh must run as root" >&2
    exit 1
}
[[ -n "${maintenance_ca_public_key}" ]] || { usage; exit 2; }
[[ "${backend_uid}" =~ ^[1-9][0-9]{0,9}$ && "${backend_gid}" =~ ^[1-9][0-9]{0,9}$ ]] || {
    echo "backend UID and GID must be positive decimal values" >&2
    exit 2
}
[[ -f "${maintenance_ca_public_key}" && ! -L "${maintenance_ca_public_key}" ]] || {
    echo "maintenance CA public key must be a regular non-symlink file" >&2
    exit 1
}

for required_command in getent groupadd id install passwd python3 ssh-keygen sshd stat systemctl systemd-tmpfiles useradd usermod; do
    command -v "${required_command}" >/dev/null || {
        echo "required command is missing: ${required_command}" >&2
        exit 1
    }
done
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo "Python 3.11 or newer is required" >&2
    exit 1
}

ca_key_type="$(awk 'NF && $1 !~ /^#/ {print $1; exit}' "${maintenance_ca_public_key}")"
ca_line_count="$(awk 'NF && $1 !~ /^#/ {count++} END {print count+0}' "${maintenance_ca_public_key}")"
[[ "${ca_key_type}" = ssh-ed25519 && "${ca_line_count}" = 1 ]] || {
    echo "maintenance CA file must contain exactly one ssh-ed25519 public key" >&2
    exit 1
}
ssh-keygen -l -E sha256 -f "${maintenance_ca_public_key}" >/dev/null

ensure_group() {
    local group_name="$1"
    if ! getent group "${group_name}" >/dev/null; then
        groupadd --system "${group_name}"
    fi
}

ensure_user() {
    local user_name="$1"
    local primary_group="$2"
    local home_directory="$3"
    local login_shell="$4"
    if getent passwd "${user_name}" >/dev/null; then
        local existing_home existing_shell
        existing_home="$(getent passwd "${user_name}" | cut -d: -f6)"
        existing_shell="$(getent passwd "${user_name}" | cut -d: -f7)"
        [[ "${existing_home}" = "${home_directory}" && "${existing_shell}" = "${login_shell}" ]] || {
            echo "existing account ${user_name} has unexpected home or shell" >&2
            exit 1
        }
    else
        useradd --system --gid "${primary_group}" --home-dir "${home_directory}" \
            --no-create-home --shell "${login_shell}" "${user_name}"
    fi
    passwd --lock "${user_name}" >/dev/null 2>&1 || true
}

ensure_group ecobin-lease-readers
ensure_group ecobin-tunnel
ensure_group ecobin-jump
ensure_user ecobin-lease-reader ecobin-lease-readers /nonexistent /usr/sbin/nologin
ensure_user ecobin-tunnel ecobin-tunnel /var/lib/ecobin/remote-support/tunnel-home /bin/sh
ensure_user ecobin-jump ecobin-jump /var/empty/ecobin-jump /usr/sbin/nologin
usermod --groups '' ecobin-lease-reader
usermod --groups ecobin-lease-readers ecobin-tunnel
usermod --groups '' ecobin-jump

lease_reader_uid="$(id -u ecobin-lease-reader)"
lease_reader_gid="$(getent group ecobin-lease-readers | cut -d: -f3)"
tunnel_uid="$(id -u ecobin-tunnel)"
jump_uid="$(id -u ecobin-jump)"
[[ "${lease_reader_uid}" != "${tunnel_uid}" && \
   "${lease_reader_uid}" != "${jump_uid}" && \
   "${tunnel_uid}" != "${jump_uid}" && \
   "${lease_reader_uid}" != "${backend_uid}" && \
   "${tunnel_uid}" != "${backend_uid}" && \
   "${jump_uid}" != "${backend_uid}" ]] || {
    echo "backend and SSH boundary accounts must use distinct non-root UIDs" >&2
    exit 1
}

install -d -o root -g root -m 0751 /var/lib/ecobin/remote-support
install -d -o "${backend_uid}" -g ecobin-lease-readers -m 2750 \
    /var/lib/ecobin/remote-support/desired
install -d -o root -g root -m 0755 \
    /var/lib/ecobin/remote-support/tunnel-home /var/empty/ecobin-jump
install -d -o root -g root -m 0755 /etc/ecobin
install -d -o root -g root -m 0755 /etc/ecobin/remote-support
install -d -o root -g root -m 0755 /usr/local/libexec/ecobin-remote-support

install -o root -g root -m 0644 \
    "${source_root}/lib/ecobin_remote_support.py" \
    /usr/local/libexec/ecobin-remote-support/ecobin_remote_support.py
install -o root -g root -m 0755 \
    "${source_root}/bin/ecobin-authorized-keys" \
    /usr/local/libexec/ecobin-remote-support/authorized-keys
install -o root -g root -m 0755 \
    "${source_root}/bin/ecobin-lease-guard" \
    /usr/local/libexec/ecobin-remote-support/lease-guard
install -o root -g root -m 0755 \
    "${source_root}/bin/ecobin-leasectl" \
    /usr/local/sbin/ecobin-remote-support-leasectl
install -o root -g root -m 0755 \
    "${source_root}/bin/verify-server.sh" \
    /usr/local/sbin/ecobin-remote-support-verify
install -o root -g root -m 0644 \
    "${maintenance_ca_public_key}" \
    /etc/ecobin/remote-support/maintenance-user-ca.pub
install -o root -g root -m 0644 \
    "${source_root}/config/jump-authorized-principals" \
    /etc/ecobin/remote-support/jump-authorized-principals

policy_temporary="$(mktemp /etc/ecobin/remote-support/.server-policy.XXXXXX)"
cleanup_policy_temporary() {
    [[ ! -e "${policy_temporary}" ]] || rm -f -- "${policy_temporary}"
}
trap cleanup_policy_temporary EXIT
cat > "${policy_temporary}" <<EOF
{
  "schemaVersion": 1,
  "backendUid": ${backend_uid},
  "backendGid": ${backend_gid},
  "leaseReaderUid": ${lease_reader_uid},
  "leaseReaderGid": ${lease_reader_gid},
  "tunnelUid": ${tunnel_uid},
  "desiredLeaseDirectory": "/var/lib/ecobin/remote-support/desired",
  "actualLeaseDirectory": "/run/ecobin/remote-support/actual",
  "listenHost": "127.0.0.1",
  "ports": [22011, 22012, 22013, 22014],
  "maxLeaseSeconds": 1800,
  "clockSkewSeconds": 60,
  "guardPollSeconds": 1,
  "listenerStartupSeconds": 5
}
EOF
chmod 0644 "${policy_temporary}"
chown root:root "${policy_temporary}"
mv -f -- "${policy_temporary}" /etc/ecobin/remote-support/server-policy.json

tmpfiles_temporary="$(mktemp /etc/tmpfiles.d/.ecobin-remote-support.XXXXXX)"
cat > "${tmpfiles_temporary}" <<EOF
d /run/ecobin 0755 root root -
d /run/ecobin/remote-support 0755 root root -
d /run/ecobin/remote-support/actual 2750 ecobin-tunnel ${backend_gid} -
EOF
chmod 0644 "${tmpfiles_temporary}"
chown root:root "${tmpfiles_temporary}"
mv -f -- "${tmpfiles_temporary}" /etc/tmpfiles.d/ecobin-remote-support.conf
systemd-tmpfiles --create /etc/tmpfiles.d/ecobin-remote-support.conf

sshd_directory=/etc/ssh/sshd_config.d
[[ -d "${sshd_directory}" ]] || {
    echo "${sshd_directory} is absent; refusing to edit the main sshd_config" >&2
    exit 1
}
sshd_target="${sshd_directory}/60-ecobin-remote-support.conf"
sshd_backup="${sshd_target}.previous"
had_existing_sshd_target=false
if [[ -e "${sshd_target}" ]]; then
    had_existing_sshd_target=true
    cp -a -- "${sshd_target}" "${sshd_backup}"
fi
restore_sshd_target() {
    if [[ "${had_existing_sshd_target}" = true && -e "${sshd_backup}" ]]; then
        mv -f -- "${sshd_backup}" "${sshd_target}"
    else
        rm -f -- "${sshd_target}"
    fi
}
install -o root -g root -m 0644 \
    "${source_root}/config/sshd_config.ecobin-remote-support" "${sshd_target}"
if ! sshd -t; then
    restore_sshd_target
    echo "sshd rejected the EcoBin configuration; previous state restored" >&2
    exit 1
fi

if ! /usr/local/sbin/ecobin-remote-support-verify --skip-listener-check; then
    restore_sshd_target
    echo "effective sshd verification failed; previous state restored" >&2
    exit 1
fi
if systemctl list-unit-files ssh.service --no-legend 2>/dev/null | grep -q '^ssh\.service'; then
    ssh_service=ssh.service
else
    ssh_service=sshd.service
fi
if ! systemctl reload "${ssh_service}"; then
    restore_sshd_target
    sshd -t || true
    systemctl reload "${ssh_service}" || true
    echo "sshd reload failed; previous configuration restored" >&2
    exit 1
fi

rm -f -- "${sshd_backup}"
printf 'ecobin-remote-support-install=PASS backend-uid=%s backend-gid=%s\n' \
    "${backend_uid}" "${backend_gid}"
