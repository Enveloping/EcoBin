#!/usr/bin/env bash
set -euo pipefail

compose_file="${ECOBIN_APP_COMPOSE_FILE:-/etc/ecobin/compose/docker-compose.target-app.yml}"
remote_compose_file="${ECOBIN_REMOTE_SUPPORT_COMPOSE_FILE:-/etc/ecobin/compose/docker-compose.remote-support.yml}"
deployment_env="${ECOBIN_DEPLOYMENT_ENV_FILE:-/etc/ecobin/deployment.env}"
runtime_env="${ECOBIN_RUNTIME_ENV_FILE:-/etc/ecobin/runtime.env}"
release_store="${ECOBIN_RELEASE_STORE:-/var/lib/ecobin/releases}"
runtime_secret_root="${ECOBIN_RUNTIME_SECRET_DIR:-/run/ecobin-secrets}"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

env_value() {
    local file="$1"
    local key="$2"
    local count
    local value

    count="$(grep -Ec "^${key}=" "${file}" || true)"
    [[ "${count}" = 1 ]] \
        || fail "${file} must contain exactly one ${key} assignment"
    value="$(sed -n "s/^${key}=//p" "${file}")"
    [[ -n "${value}" ]] || fail "${key} must not be empty"
    printf '%s' "${value}"
}

optional_env_value() {
    local file="$1"
    local key="$2"
    sed -n "s/^${key}=//p" "${file}" | tail -n 1
}

require_root_controlled_file() {
    local file="$1"
    local uid
    local mode

    [[ -f "${file}" && ! -L "${file}" ]] \
        || fail "missing protected file: ${file}"
    uid="$(stat -c '%u' "${file}")"
    mode="$(stat -c '%a' "${file}")"
    [[ "${uid}" = 0 ]] || fail "${file} must be owned by root"
    (( (0${mode} & 0022) == 0 )) \
        || fail "${file} must not be group/world writable"
    if grep -q $'\r' "${file}"; then
        fail "${file} contains CRLF line endings"
    fi
}

[[ "$(id -u)" = 0 ]] || fail "production preflight must run as root"
require_root_controlled_file "${compose_file}"
require_root_controlled_file "${deployment_env}"
require_root_controlled_file "${runtime_env}"

if grep -Eq '^(dbPassword|jwtSecret|bagCodeKeyK1|deviceEnrollmentKeyK1|defaultPlatformAdminPassword|wechatSecret|iotAccessId|iotSecretKey|onenetAccessKey|cosSecretId|cosSecretKey|wechatPayApiV3Key|MYSQL_ROOT_PASSWORD|DB_RUNTIME_PASSWORD)=' "${runtime_env}"; then
    fail "runtime.env contains a secret value; use /run/secrets instead"
fi
if grep -Eq '^(wechatAppid|miniappSecretStoreDirectory)=' "${runtime_env}"; then
    fail "runtime.env contains a removed global mini-program setting"
fi

image_mode="$(env_value "${deployment_env}" ECOBIN_IMAGE_MODE)"
release_id="$(env_value "${deployment_env}" ECOBIN_RELEASE_ID)"
backend_image="$(env_value "${deployment_env}" ECOBIN_BACKEND_IMAGE)"
backend_image_id="$(env_value "${deployment_env}" ECOBIN_BACKEND_IMAGE_ID)"
web_image="$(env_value "${deployment_env}" ECOBIN_WEB_IMAGE)"
web_image_id="$(env_value "${deployment_env}" ECOBIN_WEB_IMAGE_ID)"
backend_log_directory="$(env_value \
    "${deployment_env}" ECOBIN_BACKEND_LOG_DIRECTORY)"

[[ "${backend_log_directory}" =~ ^/[A-Za-z0-9._/-]+$ \
    && "${backend_log_directory}" != / \
    && "/${backend_log_directory#/}/" != *"/../"* ]] \
    || fail "ECOBIN_BACKEND_LOG_DIRECTORY must be a safe absolute path"
[[ -d "${backend_log_directory}" && ! -L "${backend_log_directory}" ]] \
    || fail "backend persistent log directory is missing or is a link"
[[ "$(readlink -f -- "${backend_log_directory}")" = \
    "${backend_log_directory}" ]] \
    || fail "backend persistent log directory traverses a symbolic link"
[[ "$(stat -c '%u:%g:%a' "${backend_log_directory}")" = \
    10001:10001:750 ]] \
    || fail "backend persistent log directory metadata must be 10001:10001:750"

[[ "${image_mode}" = local ]] \
    || fail "ECOBIN_IMAGE_MODE must be local"
[[ "${release_id}" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] \
    || fail "invalid local release ID"
[[ "${backend_image}" = "ecobin-local/backend:${release_id}" ]] \
    || fail "backend image tag does not match the local release ID"
[[ "${web_image}" = "ecobin-local/web:${release_id}" ]] \
    || fail "Web image tag does not match the local release ID"

zero_image_id="sha256:0000000000000000000000000000000000000000000000000000000000000000"
for expected_image_id in "${backend_image_id}" "${web_image_id}"; do
    [[ "${expected_image_id}" =~ ^sha256:[0-9a-f]{64}$ \
        && "${expected_image_id}" != "${zero_image_id}" ]] \
        || fail "invalid or placeholder local image ID"
done

release_record="${release_store}/${release_id}/images.env"
require_root_controlled_file "${release_record}"
[[ "$(env_value "${release_record}" ECOBIN_IMAGE_MODE)" = local ]] \
    || fail "stored release image mode mismatch"
[[ "$(env_value "${release_record}" ECOBIN_RELEASE_ID)" = \
    "${release_id}" ]] || fail "stored release ID mismatch"
release_git_commit="$(env_value "${release_record}" \
    ECOBIN_RELEASE_GIT_COMMIT)"
[[ "${release_git_commit}" =~ ^[0-9a-f]{40}$ ]] \
    || fail "stored release Git commit is invalid"

for component in backend web; do
    if [[ "${component}" = backend ]]; then
        image="${backend_image}"
        expected_image_id="${backend_image_id}"
        record_image_key=ECOBIN_BACKEND_IMAGE
        record_id_key=ECOBIN_BACKEND_IMAGE_ID
    else
        image="${web_image}"
        expected_image_id="${web_image_id}"
        record_image_key=ECOBIN_WEB_IMAGE
        record_id_key=ECOBIN_WEB_IMAGE_ID
    fi

    [[ "$(env_value "${release_record}" "${record_image_key}")" = \
        "${image}" ]] || fail "stored ${component} image tag mismatch"
    [[ "$(env_value "${release_record}" "${record_id_key}")" = \
        "${expected_image_id}" ]] \
        || fail "stored ${component} image ID mismatch"
    actual_image_id="$(docker image inspect --format '{{.Id}}' \
        "${image}" 2>/dev/null)" \
        || fail "required local ${component} image is not present"
    [[ "${actual_image_id}" = "${expected_image_id}" ]] \
        || fail "local ${component} image tag no longer points to the approved ID"
    [[ "$(docker image inspect --format \
        '{{ index .Config.Labels "org.opencontainers.image.version" }}' \
        "${image}")" = "${release_id}" ]] \
        || fail "local ${component} image release label mismatch"
    [[ "$(docker image inspect --format \
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
        "${image}")" = "${release_git_commit}" ]] \
        || fail "local ${component} image revision label mismatch"
    artifact_sha="$(docker image inspect --format \
        '{{ index .Config.Labels "org.ecobin.artifact.sha256" }}' \
        "${image}")"
    [[ "${artifact_sha}" =~ ^[0-9a-f]{64}$ ]] \
        || fail "local ${component} image artifact label is invalid"
done

public_origin="$(env_value "${deployment_env}" ECOBIN_PUBLIC_ORIGIN)"
[[ "${public_origin}" =~ ^https://[A-Za-z0-9.-]+$ ]] \
    || fail "ECOBIN_PUBLIC_ORIGIN must be an HTTPS origin without path/query"
[[ "$(env_value "${deployment_env}" ECOBIN_WEB_LOOPBACK_PORT)" \
    =~ ^[0-9]{4,5}$ ]] || fail "invalid Web loopback port"

db_url="$(env_value "${runtime_env}" dbUrl)"
[[ "${db_url}" = jdbc:mysql://ecobin-target-mysql84:3306/ecobin\?* ]] \
    || fail "dbUrl must target the isolated target MySQL service"
[[ "${db_url}" != *createDatabaseIfNotExist* ]] \
    || fail "runtime database URL must not auto-create a database"
[[ "$(env_value "${runtime_env}" dbUsername)" = ecobin_app ]] \
    || fail "runtime database identity must be ecobin_app"
[[ "$(env_value "${runtime_env}" defaultPlatformAdminEnabled)" = true ]] \
    || fail "default platform administrator bootstrap must be enabled"
configured_log_path="$(optional_env_value "${runtime_env}" ecobinLogPath)"
[[ -z "${configured_log_path}" \
    || "${configured_log_path}" = /var/log/ecobin/backend ]] \
    || fail "ecobinLogPath must match the backend container log mount"
[[ "$(env_value "${runtime_env}" TZ)" = UTC ]] \
    || fail "production runtime timezone must be UTC"

external_mode="$(env_value "${runtime_env}" externalMode \
    | tr '[:upper:]' '[:lower:]')"
[[ "${external_mode}" = fake || "${external_mode}" = real ]] \
    || fail "externalMode must be fake or real"
device_enrollment_enabled="$(env_value \
    "${runtime_env}" deviceEnrollmentEnabled | tr '[:upper:]' '[:lower:]')"
remote_support_enabled="$(env_value \
    "${runtime_env}" remoteSupportEnabled | tr '[:upper:]' '[:lower:]')"
[[ "${device_enrollment_enabled}" = true \
    || "${device_enrollment_enabled}" = false ]] \
    || fail "deviceEnrollmentEnabled must be true or false"
[[ "${remote_support_enabled}" = true \
    || "${remote_support_enabled}" = false ]] \
    || fail "remoteSupportEnabled must be true or false"

if [[ "${device_enrollment_enabled}" = true ]]; then
    [[ -r "${runtime_secret_root}/backend/deviceEnrollmentKeyK1" ]] \
        || fail "enabled device enrollment is missing its runtime K1"
else
    [[ ! -e "${runtime_secret_root}/backend/deviceEnrollmentKeyK1" ]] \
        || fail "disabled device enrollment must not retain its runtime K1"
fi

if [[ "${remote_support_enabled}" = true ]]; then
    require_root_controlled_file "${remote_compose_file}"
    for key in \
        remoteSupportTunnelHost \
        remoteSupportTunnelServerHostPublicKey \
        remoteSupportMaintenanceCaPublicKey \
        remoteSupportSignerCaPrivateKeyPath \
        remoteSupportLeaseDesiredDirectory \
        remoteSupportLeaseActualDirectory
    do
        env_value "${runtime_env}" "${key}" >/dev/null
    done
    [[ "$(env_value "${runtime_env}" remoteSupportSignerCaPrivateKeyPath)" \
        = /run/secrets/remote-support/maintenance-user-ca ]] \
        || fail "unexpected remote support CA private key path"
    [[ -r "${runtime_secret_root}/backend/remote-support/maintenance-user-ca" ]] \
        || fail "enabled remote support is missing its runtime CA private key"
    desired_directory="$(env_value \
        "${runtime_env}" remoteSupportLeaseDesiredDirectory)"
    actual_directory="$(env_value \
        "${runtime_env}" remoteSupportLeaseActualDirectory)"
    [[ "${desired_directory}" = /var/lib/ecobin/remote-support/desired \
        && "${actual_directory}" = /run/ecobin/remote-support/actual ]] \
        || fail "remote support lease directories must use the installed SSH boundary paths"
    [[ -d "${desired_directory}" && ! -L "${desired_directory}" ]] \
        || fail "remote support desired lease directory is missing or is a link"
    [[ -d "${actual_directory}" && ! -L "${actual_directory}" ]] \
        || fail "remote support actual lease directory is missing or is a link"
else
    [[ ! -e "${runtime_secret_root}/backend/remote-support" ]] \
        || fail "disabled remote support must not retain its runtime CA private key"
fi

external_non_secret_keys=(
    iotSubscriptionName
    onenetProductId
    cosRegion
    cosBucketName
    cosBaseUrl
    wechatPayMchid
    wechatPayMerchantSerialNumber
    wechatPayMerchantPrivateKeyPath
    wechatPayPublicKeyId
    wechatPayPublicKeyPath
    wechatPayNotifyBaseUrl
)
if [[ "${external_mode}" = fake ]]; then
    [[ "$(env_value "${runtime_env}" onenetSubscriptionEnabled)" = false ]] \
        || fail "Fake mode must disable the OneNet subscription"
    for key in "${external_non_secret_keys[@]}"; do
        [[ -z "$(optional_env_value "${runtime_env}" "${key}")" ]] \
            || fail "Fake mode must not carry REAL setting ${key}"
    done
else
    [[ "$(env_value "${runtime_env}" onenetSubscriptionEnabled)" = true ]] \
        || fail "REAL mode must enable the OneNet subscription"
    for key in "${external_non_secret_keys[@]}"; do
        env_value "${runtime_env}" "${key}" >/dev/null
    done
    [[ "$(env_value "${runtime_env}" wechatPayMerchantPrivateKeyPath)" \
        = /run/secrets/wechatpay/apiclient_key.pem ]] \
        || fail "unexpected merchant private key path"
    [[ "$(env_value "${runtime_env}" wechatPayPublicKeyPath)" \
        = /run/secrets/wechatpay/pub_key.pem ]] \
        || fail "unexpected WeChat Pay public key path"
    [[ "$(env_value "${runtime_env}" wechatPayPublicKeyId)" \
        =~ ^PUB_KEY_ID_[0-9A-Za-z]+$ ]] \
        || fail "invalid WeChat Pay public key ID"
    [[ "$(env_value "${runtime_env}" wechatPayNotifyBaseUrl)" \
        = "${public_origin}" ]] \
        || fail "WeChat Pay notify origin must match the public origin"
fi

[[ "$(stat -c '%u:%g:%a' "${runtime_secret_root}/backend")" = \
    0:10001:750 ]] \
    || fail "backend runtime secret directory metadata is invalid"
db_network_name="$(env_value "${deployment_env}" ECOBIN_DB_NETWORK_NAME)"
[[ "$(docker network inspect "${db_network_name}" \
    --format '{{.Internal}}')" = true ]] \
    || fail "${db_network_name} is missing or is not internal"

compose_args=(
    compose
    --env-file "${deployment_env}"
    --file "${compose_file}"
)
if [[ "${remote_support_enabled}" = true ]]; then
    compose_args+=(--file "${remote_compose_file}")
fi
docker "${compose_args[@]}" config --quiet

printf 'production-preflight=PASS mode=%s\n' "${external_mode}"
