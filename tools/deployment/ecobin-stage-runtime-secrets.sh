#!/usr/bin/env bash
set -euo pipefail

source_dir="${ECOBIN_SECRET_SOURCE_DIR:-/etc/ecobin/secrets}"
certificate_dir="${ECOBIN_CERTIFICATE_SOURCE_DIR:-/etc/ecobin/wechatpay}"
business_release_key_source_dir="${ECOBIN_BUSINESS_RELEASE_PUBLIC_KEY_SOURCE_DIR:-/etc/ecobin/business-release-keys}"
runtime_root="${ECOBIN_RUNTIME_SECRET_DIR:-/run/ecobin-secrets}"
backend_uid="${ECOBIN_BACKEND_UID:-10001}"
backend_gid="${ECOBIN_BACKEND_GID:-10001}"
backend_dir="${runtime_root}/backend"
external_mode="$(printf '%s' "${externalMode:-fake}" | tr '[:upper:]' '[:lower:]')"
device_enrollment_enabled="$(printf '%s' "${deviceEnrollmentEnabled:-false}" \
    | tr '[:upper:]' '[:lower:]')"
remote_support_enabled="$(printf '%s' "${remoteSupportEnabled:-false}" \
    | tr '[:upper:]' '[:lower:]')"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[[ "$(id -u)" = 0 ]] || fail "runtime secrets must be staged by root"
[[ "${external_mode}" = fake || "${external_mode}" = real ]] \
    || fail "externalMode must be fake or real"
[[ "${device_enrollment_enabled}" = true \
    || "${device_enrollment_enabled}" = false ]] \
    || fail "deviceEnrollmentEnabled must be true or false"
[[ "${remote_support_enabled}" = true \
    || "${remote_support_enabled}" = false ]] \
    || fail "remoteSupportEnabled must be true or false"

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

validate_business_release_public_keys() {
    local entries=()
    local public_key
    local key_name
    local key_der_hex

    require_directory "${business_release_key_source_dir}" "0:0:755"
    [[ ! -L "${business_release_key_source_dir}" ]] \
        || fail "business release public-key directory must not be a link"
    shopt -s nullglob dotglob
    entries=("${business_release_key_source_dir}"/*)
    shopt -u nullglob dotglob
    [[ "${#entries[@]}" -gt 0 ]] \
        || fail "business release public-key directory is empty"
    for public_key in "${entries[@]}"; do
        key_name="$(basename -- "${public_key}")"
        [[ "${key_name}" =~ ^[0-9A-Za-z][0-9A-Za-z._-]{0,63}\.pem$ ]] \
            || fail "invalid business release public-key filename: ${key_name}"
        require_root_file \
            "${public_key}" 644 \
            "business release public key ${key_name}"
        [[ "$(stat -c '%h' -- "${public_key}")" = 1 ]] \
            || fail "business release public key must have one hard link: ${key_name}"
        if ! key_der_hex="$({
            openssl pkey -pubin -in "${public_key}" -outform DER 2>/dev/null
        } | od -An -tx1 | tr -d '[:space:]')"; then
            fail "business release public key is not valid PEM: ${key_name}"
        fi
        [[ "${key_der_hex}" =~ ^302a300506032b6570032100[0-9a-f]{64}$ ]] \
            || fail "business release public key is not Ed25519: ${key_name}"
    done
}

require_directory "${source_dir}" "0:0:700"

for secret_name in \
    mysql-root-password \
    db-app-password \
    db-backup-password \
    jwt-secret \
    bag-code-key-k1 \
    default-platform-admin-password
do
    require_root_file \
        "${source_dir}/${secret_name}" 600 \
        "persistent secret ${secret_name}"
done

if ! bag_code_key_bytes="$(
    base64 --decode < "${source_dir}/bag-code-key-k1" 2>/dev/null \
        | wc -c
)"; then
    fail "bag-code-key-k1 must be valid Base64"
fi
[[ "${bag_code_key_bytes}" -ge 32 ]] \
    || fail "bag-code-key-k1 must decode to at least 32 bytes"

if [[ "${device_enrollment_enabled}" = true ]]; then
    require_root_file \
        "${source_dir}/device-enrollment-key-k1" 600 \
        "device enrollment global K1"
    if ! enrollment_key_bytes="$(
        base64 --decode < "${source_dir}/device-enrollment-key-k1" \
            2>/dev/null | wc -c
    )"; then
        fail "device-enrollment-key-k1 must be valid Base64"
    fi
    [[ "${enrollment_key_bytes}" -ge 32 ]] \
        || fail "device-enrollment-key-k1 must decode to at least 32 bytes"
fi

if [[ "${remote_support_enabled}" = true ]]; then
    require_root_file \
        "${source_dir}/remote-support-maintenance-user-ca" 600 \
        "remote support maintenance user CA private key"
    ssh-keygen -y -f \
        "${source_dir}/remote-support-maintenance-user-ca" >/dev/null 2>&1 \
        || fail "remote support maintenance user CA private key is invalid or encrypted"
fi

bootstrap_password="$(<"${source_dir}/default-platform-admin-password")"
[[ "${#bootstrap_password}" -ge 8 \
    && "${#bootstrap_password}" -le 256 ]] \
    || fail "default platform administrator password must contain 8 to 256 characters"
unset bootstrap_password

if [[ "${external_mode}" = real ]]; then
    for secret_name in \
        onenet-subscription-access-id \
        onenet-subscription-secret-key \
        onenet-access-key \
        cos-secret-id \
        cos-secret-key \
        business-release-cos-secret-id \
        business-release-cos-secret-key \
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
        businessReleaseCosRegion \
        businessReleaseCosBucketName \
        businessReleaseCosBasePrefix \
        businessReleaseDownloadBaseUrl \
        businessReleaseSigningPublicKeysDirectory \
        businessReleaseRemoteDispatchEnabled \
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
    [[ "${businessReleaseSigningPublicKeysDirectory:-}" \
        = /run/secrets/business-release-keys ]] \
        || fail "unexpected business release public-key runtime path"
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
    validate_business_release_public_keys

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
install -o root -g "${backend_gid}" -m 0440 \
    "${source_dir}/bag-code-key-k1" "${backend_dir}/bagCodeKeyK1"
install -o root -g "${backend_gid}" -m 0440 \
    "${source_dir}/default-platform-admin-password" \
    "${backend_dir}/defaultPlatformAdminPassword"

if [[ "${device_enrollment_enabled}" = true ]]; then
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/device-enrollment-key-k1" \
        "${backend_dir}/deviceEnrollmentKeyK1"
else
    rm -f -- "${backend_dir}/deviceEnrollmentKeyK1"
fi

if [[ "${remote_support_enabled}" = true ]]; then
    install -d -o root -g "${backend_gid}" -m 0750 \
        "${backend_dir}/remote-support"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/remote-support-maintenance-user-ca" \
        "${backend_dir}/remote-support/maintenance-user-ca"
else
    rm -rf -- "${backend_dir}/remote-support"
fi

real_runtime_files=(
    iotAccessId
    iotSecretKey
    onenetAccessKey
    cosSecretId
    cosSecretKey
    businessReleaseCosSecretId
    businessReleaseCosSecretKey
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
        "${source_dir}/business-release-cos-secret-id" \
        "${backend_dir}/businessReleaseCosSecretId"
    install -o root -g "${backend_gid}" -m 0440 \
        "${source_dir}/business-release-cos-secret-key" \
        "${backend_dir}/businessReleaseCosSecretKey"
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
    rm -rf -- "${backend_dir}/business-release-keys"
    install -d -o root -g "${backend_gid}" -m 0750 \
        "${backend_dir}/business-release-keys"
    for public_key in "${business_release_key_source_dir}"/*; do
        install -o root -g "${backend_gid}" -m 0440 \
            "${public_key}" \
            "${backend_dir}/business-release-keys/$(basename -- "${public_key}")"
    done
else
    for runtime_file in "${real_runtime_files[@]}"; do
        rm -f -- "${backend_dir}/${runtime_file}"
    done
    rm -rf -- "${backend_dir}/wechatpay"
    rm -rf -- "${backend_dir}/business-release-keys"
fi

# These identities and legacy names are never available to the backend.
rm -f -- \
    "${backend_dir}/wechatSecret" \
    "${backend_dir}/appAesKey" \
    "${backend_dir}/mysql-root-password" \
    "${backend_dir}/db-backup-password" \
    "${backend_dir}/schema-owner-password"

expected_runtime_files=(
    dbPassword
    jwtSecret
    bagCodeKeyK1
    defaultPlatformAdminPassword
)
if [[ "${device_enrollment_enabled}" = true ]]; then
    expected_runtime_files+=(deviceEnrollmentKeyK1)
fi
if [[ "${external_mode}" = real ]]; then
    expected_runtime_files+=("${real_runtime_files[@]}")
fi
for runtime_secret in "${expected_runtime_files[@]}"; do
    metadata="$(stat -c '%u:%g:%a' "${backend_dir}/${runtime_secret}")"
    [[ "${metadata}" = "0:${backend_gid}:440" ]] \
        || fail "invalid runtime secret owner/mode for ${runtime_secret}: ${metadata}"
done

if [[ "${remote_support_enabled}" = true ]]; then
    metadata="$(stat -c '%u:%g:%a' \
        "${backend_dir}/remote-support/maintenance-user-ca")"
    [[ "${metadata}" = "0:${backend_gid}:440" ]] \
        || fail "invalid runtime secret owner/mode for remote support CA: ${metadata}"
fi

if [[ "${external_mode}" = real ]]; then
    [[ "$(stat -c '%u:%g:%a' \
        "${backend_dir}/business-release-keys")" \
        = "0:${backend_gid}:750" ]] \
        || fail "invalid runtime business release public-key directory metadata"
    for public_key in "${backend_dir}/business-release-keys"/*.pem; do
        [[ "$(stat -c '%u:%g:%a' "${public_key}")" \
            = "0:${backend_gid}:440" ]] \
            || fail "invalid runtime business release public-key metadata"
    done
fi

[[ "$(stat -c '%u:%g:%a' "${backend_dir}")" \
    = "0:${backend_gid}:750" ]] \
    || fail "invalid backend runtime secret directory metadata"
printf 'runtime secrets staged mode=%s enrollment=%s remote_support=%s backend_uid=%s backend_gid=%s\n' \
    "${external_mode}" "${device_enrollment_enabled}" \
    "${remote_support_enabled}" "${backend_uid}" "${backend_gid}"
