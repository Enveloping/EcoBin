#!/usr/bin/env bash
set -euo pipefail

compose_file="${ECOBIN_APP_COMPOSE_FILE:-/etc/ecobin/compose/docker-compose.target-app.yml}"
deployment_env="${ECOBIN_DEPLOYMENT_ENV_FILE:-/etc/ecobin/deployment.env}"
runtime_env="${ECOBIN_RUNTIME_ENV_FILE:-/etc/ecobin/runtime.env}"

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

if grep -Eq '^(dbPassword|jwtSecret|wechatSecret|iotAccessId|iotSecretKey|onenetAccessKey|cosSecretId|cosSecretKey|wechatPayApiV3Key|MYSQL_ROOT_PASSWORD|DB_RUNTIME_PASSWORD)=' "${runtime_env}"; then
    fail "runtime.env contains a secret value; use /run/secrets instead"
fi
if grep -Eq '^(wechatAppid|miniappSecretStoreDirectory)=' "${runtime_env}"; then
    fail "runtime.env contains a removed global mini-program setting"
fi

backend_image="$(env_value "${deployment_env}" ECOBIN_BACKEND_IMAGE)"
web_image="$(env_value "${deployment_env}" ECOBIN_WEB_IMAGE)"
for image in "${backend_image}" "${web_image}"; do
    [[ "${image}" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]] \
        || fail "production images must be pinned by sha256 digest"
    [[ "${image##*@sha256:}" != \
        0000000000000000000000000000000000000000000000000000000000000000 ]] \
        || fail "placeholder image digest is not deployable"
    docker image inspect "${image}" >/dev/null 2>&1 \
        || fail "required production image is not present: ${image%%@*}"
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
[[ "$(env_value "${runtime_env}" defaultPlatformAdminEnabled)" = false ]] \
    || fail "development administrator initializer must be disabled"
[[ "$(env_value "${runtime_env}" TZ)" = UTC ]] \
    || fail "production runtime timezone must be UTC"

external_mode="$(env_value "${runtime_env}" externalMode \
    | tr '[:upper:]' '[:lower:]')"
[[ "${external_mode}" = fake || "${external_mode}" = real ]] \
    || fail "externalMode must be fake or real"

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

[[ "$(stat -c '%u:%g:%a' /run/ecobin-secrets/backend)" = 0:10001:750 ]] \
    || fail "backend runtime secret directory metadata is invalid"
[[ "$(docker network inspect ecobin-target-db \
    --format '{{.Internal}}')" = true ]] \
    || fail "ecobin-target-db is missing or is not internal"

docker compose \
    --env-file "${deployment_env}" \
    --file "${compose_file}" \
    config --quiet

printf 'production-preflight=PASS mode=%s\n' "${external_mode}"
