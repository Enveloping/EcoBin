#!/usr/bin/env bash
set -euo pipefail

source_dir="${ECOBIN_SECRET_SOURCE_DIR:-/etc/ecobin/secrets}"
certificate_dir="${ECOBIN_CERTIFICATE_SOURCE_DIR:-/etc/ecobin/wechatpay}"
runtime_root="${ECOBIN_RUNTIME_SECRET_DIR:-/run/ecobin-secrets}"
backend_uid="${ECOBIN_BACKEND_UID:-10001}"
backend_gid="${ECOBIN_BACKEND_GID:-10001}"
backend_dir="${runtime_root}/backend"
external_mode="$(printf '%s' "${externalMode:-fake}" | tr '[:upper:]' '[:lower:]')"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[[ "$(id -u)" = 0 ]] || fail "runtime secrets must be staged by root"
[[ "${external_mode}" = fake || "${external_mode}" = real ]] \
    || fail "externalMode must be fake or real"

require_directory() {
    local path="$1"
    local expected="$2"
    local metadata

    [[ -d "${path}" ]] || fail "missing protected directory: ${path}"
    metadata="$(stat -c '%u:%g:%a' "${path}")"
    [[ "${metadata}" = "${expected}" ]] \
        || fail "invalid protected directory metadata for ${path}: ${metadata}"
}

require_root_file() {
    local path="$1"
    local expected_mode="$2"
    local label="$3"
    local metadata

    [[ -f "${path}" && ! -L "${path}" && -s "${path}" ]] \
        || fail "missing or empty ${label}"
    metadata="$(stat -c '%u:%g:%a' "${path}")"
    [[ "${metadata}" = "0:0:${expected_mode}" ]] \
        || fail "invalid owner/mode for ${label}: ${metadata}"
}

require_environment_value() {
    local name="$1"
    [[ -n "${!name:-}" ]] || fail "missing REAL runtime setting: ${name}"
}

require_directory "${source_dir}" "0:0:700"

for secret_name in \
    mysql-root-password \
    db-app-password \
    db-backup-password \
    jwt-secret
do
    require_root_file \
        "${source_dir}/${secret_name}" 600 \
        "persistent secret ${secret_name}"
done

if [[ "${external_mode}" = real ]]; then
    for secret_name in \
        onenet-subscription-access-id \
        onenet-subscription-secret-key \
        onenet-access-key \
        cos-secret-id \
        cos-secret-key \
        wechatpay-api-v3-key \
        wechatpay-merchant-private-key.pem
    do
        require_root_file \
            "${source_dir}/${secret_name}" 600 \
            "persistent REAL secret ${secret_name}"
    done

    require_directory "${certificate_dir}" "0:0:755"
    require_root_file \
        "${certificate_dir}/apiclient_cert.pem" 644 \
        "merchant API certificate"
    require_root_file \
        "${certificate_dir}/pub_key.pem" 644 \
        "WeChat Pay public key"

    for setting_name in \
        iotSubscriptionName \
        onenetProductId \
        cosRegion \
        cosBucketName \
        cosBaseUrl \
        wechatPayMchid \
        wechatPayMerchantSerialNumber \
        wechatPayPublicKeyId \
        wechatPayNotifyBaseUrl
    do
        require_environment_value "${setting_name}"
    done
    [[ "${onenetSubscriptionEnabled:-}" = true ]] \
        || fail "REAL mode requires onenetSubscriptionEnabled=true"
    [[ "${wechatPayMerchantPrivateKeyPath:-}" \
        = /run/secrets/wechatpay/apiclient_key.pem ]] \
        || fail "unexpected merchant private key runtime path"
    [[ "${wechatPayPublicKeyPath:-}" \
        = /run/secrets/wechatpay/pub_key.pem ]] \
        || fail "unexpected WeChat Pay public key runtime path"
    [[ "${wechatPayPublicKeyId}" =~ ^PUB_KEY_ID_[0-9A-Za-z]+$ ]] \
        || fail "invalid WeChat Pay public key ID"

    api_v3_bytes="$(wc -c < "${source_dir}/wechatpay-api-v3-key")"
    [[ "${api_v3_bytes}" = 32 ]] \
        || fail "wechatpay-api-v3-key must contain exactly 32 UTF-8 bytes"

    openssl pkey \
        -in "${source_dir}/wechatpay-merchant-private-key.pem" \
        -passin pass: -check -noout >/dev/null 2>&1 \
        || fail "merchant private key is invalid or encrypted"
    openssl x509 \
        -in "${certificate_dir}/apiclient_cert.pem" \
        -checkend 604800 -noout >/dev/null 2>&1 \
        || fail "merchant API certificate is invalid or expires within 7 days"
    openssl pkey -pubin \
        -in "${certificate_dir}/pub_key.pem" \
        -noout >/dev/null 2>&1 \
        || fail "WeChat Pay public key is invalid"

    private_key_fingerprint="$({
        openssl pkey \
            -in "${source_dir}/wechatpay-merchant-private-key.pem" \
            -passin pass: -pubout -outform DER 2>/dev/null
    } | sha256sum | awk '{print $1}')"
    merchant_cert_fingerprint="$({
        openssl x509 \
            -in "${certificate_dir}/apiclient_cert.pem" \
            -pubkey -noout 2>/dev/null \
            | openssl pkey -pubin -outform DER 2>/dev/null
    } | sha256sum | awk '{print $1}')"
    [[ "${private_key_fingerprint}" = "${merchant_cert_fingerprint}" ]] \
        || fail "merchant private key does not match apiclient certificate"

    actual_serial="$(
        openssl x509 -in "${certificate_dir}/apiclient_cert.pem" \
            -serial -noout \
            | cut -d= -f2 \
            | tr -d '[:space:]:' \
            | tr '[:lower:]' '[:upper:]'
    )"
    configured_serial="$(
        printf '%s' "${wechatPayMerchantSerialNumber}" \
            | tr -d '[:space:]:' \
            | tr '[:lower:]' '[:upper:]'
    )"
    [[ "${actual_serial}" = "${configured_serial}" ]] \
        || fail "merchant certificate serial does not match runtime setting"
fi

install -d -o root -g root -m 0700 "${runtime_root}"
install -d -o root -g "${backend_gid}" -m 0750 "${backend_dir}"

install -o root -g "${backend_gid}" -m 0440 \
    "${source_dir}/db-app-password" "${backend_dir}/dbPassword"
install -o root -g "${backend_gid}" -m 0440 \
    "${source_dir}/jwt-secret" "${backend_dir}/jwtSecret"

real_runtime_files=(
    iotAccessId
    iotSecretKey
    onenetAccessKey
    cosSecretId
    cosSecretKey
    wechatPayApiV3Key
)

if [[ "${external_mode}" = real ]]; then
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/onenet-subscription-access-id" \
        "${backend_dir}/iotAccessId"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/onenet-subscription-secret-key" \
        "${backend_dir}/iotSecretKey"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/onenet-access-key" \
        "${backend_dir}/onenetAccessKey"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/cos-secret-id" \
        "${backend_dir}/cosSecretId"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/cos-secret-key" \
        "${backend_dir}/cosSecretKey"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/wechatpay-api-v3-key" \
        "${backend_dir}/wechatPayApiV3Key"
    install -d -o root -g "${backend_gid}" -m 0750 \
        "${backend_dir}/wechatpay"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/wechatpay-merchant-private-key.pem" \
        "${backend_dir}/wechatpay/apiclient_key.pem"
    install -o root -g "${backend_gid}" -m 0440 \
        "${certificate_dir}/pub_key.pem" \
        "${backend_dir}/wechatpay/pub_key.pem"
else
    for runtime_file in "${real_runtime_files[@]}"; do
        rm -f -- "${backend_dir}/${runtime_file}"
    done
    rm -rf -- "${backend_dir}/wechatpay"
fi

# These identities and legacy names are never available to the backend.
rm -f -- \
    "${backend_dir}/wechatSecret" \
    "${backend_dir}/appAesKey" \
    "${backend_dir}/mysql-root-password" \
    "${backend_dir}/db-backup-password" \
    "${backend_dir}/schema-owner-password"

expected_runtime_files=(dbPassword jwtSecret)
if [[ "${external_mode}" = real ]]; then
    expected_runtime_files+=("${real_runtime_files[@]}")
fi
for runtime_secret in "${expected_runtime_files[@]}"; do
    metadata="$(stat -c '%u:%g:%a' "${backend_dir}/${runtime_secret}")"
    [[ "${metadata}" = "0:${backend_gid}:440" ]] \
        || fail "invalid runtime secret owner/mode for ${runtime_secret}: ${metadata}"
done

[[ "$(stat -c '%u:%g:%a' "${backend_dir}")" \
    = "0:${backend_gid}:750" ]] \
    || fail "invalid backend runtime secret directory metadata"
printf 'runtime secrets staged mode=%s backend_uid=%s backend_gid=%s\n' \
    "${external_mode}" "${backend_uid}" "${backend_gid}"
