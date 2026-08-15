#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
remote_support_root="$(cd "${script_directory}/.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/ecobin-ca-test.XXXXXX")"

cleanup() {
    case "${test_root}" in
        "${TMPDIR:-/tmp}"/ecobin-ca-test.*) rm -rf -- "${test_root}" ;;
        *) echo "refusing to remove unexpected test directory" >&2 ;;
    esac
}
trap cleanup EXIT

ca_directory="${test_root}/ca"
operator_key="${test_root}/operator"
certificate="${test_root}/operator-cert.pub"

if bash "${remote_support_root}/bin/generate-maintenance-ca.sh" \
    --output-directory "${remote_support_root}/.forbidden-test-ca" \
    --acknowledge-unencrypted-private-key >/dev/null 2>&1; then
    echo "CA generator accepted an output path inside the repository" >&2
    exit 1
fi
[[ ! -e "${remote_support_root}/.forbidden-test-ca" ]]

bash "${remote_support_root}/bin/generate-maintenance-ca.sh" \
    --output-directory "${ca_directory}" \
    --acknowledge-unencrypted-private-key >/dev/null
[[ "$(stat -c '%a' "${ca_directory}/maintenance-user-ca")" = 600 ]]
[[ "$(stat -c '%a' "${ca_directory}/maintenance-user-ca.pub")" = 644 ]]

ssh-keygen -q -t ed25519 -N '' -f "${operator_key}"
now="$(date +%s)"
bash "${remote_support_root}/bin/sign-maintenance-certificate.sh" \
    --ca-private-key "${ca_directory}/maintenance-user-ca" \
    --operator-public-key "${operator_key}.pub" \
    --output-certificate "${certificate}" \
    --serial 42 \
    --key-id operator-7:session-9 \
    --hardware-sn ECM0-TESTDEVICE01 \
    --valid-after-epoch "$((now - 10))" \
    --valid-before-epoch "$((now + 600))" >/dev/null

certificate_details="$(ssh-keygen -L -f "${certificate}")"
grep -Fq 'Key ID: "operator-7:session-9"' <<<"${certificate_details}"
grep -Fq 'ecobin-jump' <<<"${certificate_details}"
grep -Fq 'ecobin-device-ECM0-TESTDEVICE01' <<<"${certificate_details}"
grep -Fq 'permit-port-forwarding' <<<"${certificate_details}"
grep -Fq 'permit-pty' <<<"${certificate_details}"
! grep -Fq 'permit-agent-forwarding' <<<"${certificate_details}"
! grep -Fq 'permit-X11-forwarding' <<<"${certificate_details}"
! grep -Fq 'permit-user-rc' <<<"${certificate_details}"

if bash "${remote_support_root}/bin/sign-maintenance-certificate.sh" \
    --ca-private-key "${ca_directory}/maintenance-user-ca" \
    --operator-public-key "${operator_key}.pub" \
    --output-certificate "${test_root}/too-long-cert.pub" \
    --serial 43 \
    --key-id operator-7:session-10 \
    --hardware-sn ECM0-TESTDEVICE01 \
    --valid-after-epoch "${now}" \
    --valid-before-epoch "$((now + 1801))" >/dev/null 2>&1; then
    echo "signer accepted a certificate lifetime over 30 minutes" >&2
    exit 1
fi

printf 'ecobin-ca-helpers-test=PASS\n'
