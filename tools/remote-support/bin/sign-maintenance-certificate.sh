#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage: sign-maintenance-certificate.sh \
  --ca-private-key PATH --operator-public-key PATH --output-certificate PATH \
  --serial DECIMAL --key-id SAFE_ID --hardware-sn HARDWARE_SN \
  --valid-after-epoch EPOCH --valid-before-epoch EPOCH

The certificate contains principals ecobin-jump and
ecobin-device-<HARDWARE_SN>, and only permit-port-forwarding/permit-pty
extensions. Its validity must not exceed 30 minutes.
EOF
}

ca_private_key=
operator_public_key=
output_certificate=
serial=
key_id=
hardware_sn=
valid_after_epoch=
valid_before_epoch=
while (($# > 0)); do
    case "$1" in
        --ca-private-key|--operator-public-key|--output-certificate|--serial|--key-id|--hardware-sn|--valid-after-epoch|--valid-before-epoch)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            option="$1"
            value="$2"
            shift 2
            case "${option}" in
                --ca-private-key) ca_private_key="${value}" ;;
                --operator-public-key) operator_public_key="${value}" ;;
                --output-certificate) output_certificate="${value}" ;;
                --serial) serial="${value}" ;;
                --key-id) key_id="${value}" ;;
                --hardware-sn) hardware_sn="${value}" ;;
                --valid-after-epoch) valid_after_epoch="${value}" ;;
                --valid-before-epoch) valid_before_epoch="${value}" ;;
            esac
            ;;
        *) usage; exit 2 ;;
    esac
done

for required_value in ca_private_key operator_public_key output_certificate serial key_id hardware_sn valid_after_epoch valid_before_epoch; do
    [[ -n "${!required_value}" ]] || { usage; exit 2; }
done
[[ "${serial}" =~ ^[1-9][0-9]{0,19}$ ]] || {
    echo "serial must be a positive decimal integer" >&2
    exit 2
}
[[ "${key_id}" =~ ^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$ ]] || {
    echo "key-id has an invalid format" >&2
    exit 2
}
[[ "${hardware_sn}" =~ ^[A-Za-z0-9_-]{8,64}$ ]] || {
    echo "hardware-sn has an invalid format" >&2
    exit 2
}
[[ "${valid_after_epoch}" =~ ^[0-9]{1,18}$ && "${valid_before_epoch}" =~ ^[0-9]{1,18}$ ]] || {
    echo "certificate validity must use epoch seconds" >&2
    exit 2
}
((valid_before_epoch > valid_after_epoch)) || {
    echo "valid-before must follow valid-after" >&2
    exit 2
}
((valid_before_epoch - valid_after_epoch <= 1800)) || {
    echo "certificate validity cannot exceed 1800 seconds" >&2
    exit 2
}
current_epoch="$(date +%s)"
((valid_after_epoch >= current_epoch - 120 && valid_after_epoch <= current_epoch + 60)) || {
    echo "valid-after must be within 120 seconds before or 60 seconds after now" >&2
    exit 2
}
((valid_before_epoch > current_epoch && valid_before_epoch <= current_epoch + 1800)) || {
    echo "valid-before must be in the future and no more than 1800 seconds from now" >&2
    exit 2
}
[[ -f "${ca_private_key}" && ! -L "${ca_private_key}" ]] || {
    echo "CA private key must be a regular non-symlink file" >&2
    exit 1
}
ca_mode="$(stat -c '%a' "${ca_private_key}")"
[[ "${ca_mode}" = 400 || "${ca_mode}" = 600 ]] || {
    echo "CA private key must have mode 0400 or 0600" >&2
    exit 1
}
[[ -f "${operator_public_key}" && ! -L "${operator_public_key}" ]] || {
    echo "operator public key must be a regular non-symlink file" >&2
    exit 1
}
[[ ! -e "${output_certificate}" && ! -L "${output_certificate}" ]] || {
    echo "refusing to overwrite an existing certificate" >&2
    exit 1
}

key_type="$(awk 'NF && $1 !~ /^#/ {print $1; exit}' "${operator_public_key}")"
line_count="$(awk 'NF && $1 !~ /^#/ {count++} END {print count+0}' "${operator_public_key}")"
[[ "${key_type}" = ssh-ed25519 && "${line_count}" = 1 ]] || {
    echo "operator public key file must contain exactly one ssh-ed25519 key" >&2
    exit 1
}
ssh-keygen -l -E sha256 -f "${operator_public_key}" >/dev/null

format_epoch_utc() {
    date --utc --date="@${1}" '+%Y%m%d%H%M%SZ'
}

validity="$(format_epoch_utc "${valid_after_epoch}"):$(format_epoch_utc "${valid_before_epoch}")"
output_directory="$(dirname "${output_certificate}")"
mkdir -p -- "${output_directory}"
[[ -d "${output_directory}" && ! -L "${output_directory}" ]] || {
    echo "certificate output directory must be a real directory" >&2
    exit 1
}
temporary_directory="$(mktemp -d "${output_directory}/.ecobin-cert.XXXXXX")"
cleanup() {
    case "${temporary_directory}" in
        "${output_directory}"/.ecobin-cert.*) rm -rf -- "${temporary_directory}" ;;
        *) echo "refusing to remove unexpected temporary directory" >&2 ;;
    esac
}
trap cleanup EXIT
cp -- "${operator_public_key}" "${temporary_directory}/operator.pub"

ssh-keygen -q -s "${ca_private_key}" -I "${key_id}" -z "${serial}" \
    -n "ecobin-jump,ecobin-device-${hardware_sn}" -V "${validity}" \
    -O clear -O permit-port-forwarding -O permit-pty \
    "${temporary_directory}/operator.pub"
install -m 0644 "${temporary_directory}/operator-cert.pub" "${output_certificate}"

printf 'maintenance-certificate-created=YES\ncertificate=%s\n' "${output_certificate}"
