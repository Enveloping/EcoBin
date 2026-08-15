#!/usr/bin/env bash
set -euo pipefail

skip_listener_check=false
if [[ "${1:-}" = --skip-listener-check && $# = 1 ]]; then
    skip_listener_check=true
elif (($# != 0)); then
    echo "Usage: verify-server.sh [--skip-listener-check]" >&2
    exit 2
fi

for required_command in getent id python3 ssh-keygen sshd stat; do
    command -v "${required_command}" >/dev/null || {
        echo "required command is missing: ${required_command}" >&2
        exit 1
    }
done
for account in ecobin-tunnel ecobin-jump ecobin-lease-reader; do
    getent passwd "${account}" >/dev/null || {
        echo "required account is absent: ${account}" >&2
        exit 1
    }
done
assert_account_groups() {
    local account="$1"
    shift
    local actual_group expected_group matched
    for actual_group in $(id -nG "${account}"); do
        matched=false
        for expected_group in "$@"; do
            [[ "${actual_group}" != "${expected_group}" ]] || matched=true
        done
        [[ "${matched}" = true ]] || {
            echo "${account} has unexpected supplementary group ${actual_group}" >&2
            exit 1
        }
    done
    for expected_group in "$@"; do
        id -nG "${account}" | tr ' ' '\n' | grep -Fqx "${expected_group}" || {
            echo "${account} is missing required group ${expected_group}" >&2
            exit 1
        }
    done
}
assert_account_groups ecobin-lease-reader ecobin-lease-readers
assert_account_groups ecobin-tunnel ecobin-tunnel ecobin-lease-readers
assert_account_groups ecobin-jump ecobin-jump

backend_uid="$(python3 -c 'import json; print(json.load(open("/etc/ecobin/remote-support/server-policy.json"))["backendUid"])')"
[[ "$(stat -c '%u:%G:%a' /var/lib/ecobin/remote-support/desired)" = \
    "${backend_uid}:ecobin-lease-readers:2750" ]] || {
    echo "desired lease directory ownership or mode is invalid" >&2
    exit 1
}
backend_gid="$(python3 -c 'import json; print(json.load(open("/etc/ecobin/remote-support/server-policy.json"))["backendGid"])')"
tunnel_uid="$(id -u ecobin-tunnel)"
[[ "$(stat -c '%u:%g:%a' /run/ecobin/remote-support/actual)" = \
    "${tunnel_uid}:${backend_gid}:2750" ]] || {
    echo "actual lease directory ownership or mode is invalid" >&2
    exit 1
}
[[ "$(stat -c '%u:%g:%a' /etc/ecobin/remote-support/server-policy.json)" = "0:0:644" ]] || {
    echo "server policy ownership or mode is invalid" >&2
    exit 1
}
[[ "$(stat -c '%u:%g:%a' /usr/local/libexec/ecobin-remote-support/authorized-keys)" = "0:0:755" ]] || {
    echo "AuthorizedKeysCommand ownership or mode is invalid" >&2
    exit 1
}
for trusted_file in \
    /etc/ecobin/remote-support/maintenance-user-ca.pub \
    /etc/ecobin/remote-support/jump-authorized-principals; do
    [[ "$(stat -c '%u:%g:%a' "${trusted_file}")" = "0:0:644" ]] || {
        echo "trusted SSH file ownership or mode is invalid: ${trusted_file}" >&2
        exit 1
    }
done

sshd -t
tunnel_config="$(sshd -T -C user=ecobin-tunnel,host=bastion.invalid,addr=127.0.0.1,laddr=127.0.0.1,lport=22)"
jump_config="$(sshd -T -C user=ecobin-jump,host=bastion.invalid,addr=127.0.0.1,laddr=127.0.0.1,lport=22)"
require_effective_line() {
    local config="$1"
    local expected="$2"
    grep -Fqx "${expected}" <<<"${config}" || {
        echo "effective sshd config is missing: ${expected}" >&2
        exit 1
    }
}
require_effective_line "${tunnel_config}" "authorizedkeysfile none"
require_effective_line "${tunnel_config}" "authorizedkeyscommand /usr/local/libexec/ecobin-remote-support/authorized-keys %u %t %k %f"
require_effective_line "${tunnel_config}" "authorizedkeyscommanduser ecobin-lease-reader"
require_effective_line "${tunnel_config}" "allowtcpforwarding remote"
require_effective_line "${tunnel_config}" "gatewayports no"
require_effective_line "${tunnel_config}" "permitopen none"
require_effective_line "${tunnel_config}" "permitlisten 127.0.0.1:22011 127.0.0.1:22012 127.0.0.1:22013 127.0.0.1:22014"
require_effective_line "${tunnel_config}" "permittty no"
require_effective_line "${jump_config}" "authorizedkeysfile none"
require_effective_line "${jump_config}" "allowtcpforwarding local"
require_effective_line "${jump_config}" "gatewayports no"
require_effective_line "${jump_config}" "maxsessions 0"
require_effective_line "${jump_config}" "trustedusercakeys /etc/ecobin/remote-support/maintenance-user-ca.pub"
require_effective_line "${jump_config}" "authorizedprincipalsfile /etc/ecobin/remote-support/jump-authorized-principals"
require_effective_line "${jump_config}" "permitopen 127.0.0.1:22011 127.0.0.1:22012 127.0.0.1:22013 127.0.0.1:22014"

if [[ "${skip_listener_check}" = false ]]; then
    command -v ss >/dev/null || {
        echo "ss is required for listener verification" >&2
        exit 1
    }
    while read -r local_address; do
        [[ -z "${local_address}" || "${local_address}" =~ ^127\.0\.0\.1:2201[1-4]$ ]] || {
            echo "remote-support port is not loopback-only: ${local_address}" >&2
            exit 1
        }
    done < <(ss -H -ltn | awk '$4 ~ /:2201[1-4]$/ {print $4}')
fi

ca_fingerprint="$(ssh-keygen -l -E sha256 -f /etc/ecobin/remote-support/maintenance-user-ca.pub | awk '{print $2}')"
printf 'ecobin-remote-support-verify=PASS ca-fingerprint=%s\n' "${ca_fingerprint}"
