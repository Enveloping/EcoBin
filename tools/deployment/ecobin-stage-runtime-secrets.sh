#!/usr/bin/env bash
set -euo pipefail

source_dir="${ECOBIN_SECRET_SOURCE_DIR:-/etc/ecobin/secrets}"
runtime_root="${ECOBIN_RUNTIME_SECRET_DIR:-/run/ecobin-secrets}"
backend_uid="${ECOBIN_BACKEND_UID:-10001}"
backend_gid="${ECOBIN_BACKEND_GID:-10001}"
backend_dir="${runtime_root}/backend"

require_root_secret() {
    local name="$1"
    local path="${source_dir}/${name}"
    local metadata

    if [[ ! -f "${path}" ]]; then
        echo "missing persistent secret: ${name}" >&2
        exit 1
    fi

    metadata="$(stat -c '%u:%g:%a' "${path}")"
    if [[ "${metadata}" != "0:0:600" ]]; then
        echo "invalid owner/mode for persistent secret ${name}: ${metadata}" >&2
        exit 1
    fi
}

for secret_name in \
    mysql-root-password \
    db-app-password \
    db-backup-password \
    jwt-secret
do
    require_root_secret "${secret_name}"
done

install -d -o root -g root -m 0700 "${runtime_root}"
install -d -o root -g "${backend_gid}" -m 0750 "${backend_dir}"

install -o root -g "${backend_gid}" -m 0440 \
    "${source_dir}/db-app-password" "${backend_dir}/dbPassword"
install -o root -g "${backend_gid}" -m 0440 \
    "${source_dir}/jwt-secret" "${backend_dir}/jwtSecret"
# These names are explicitly forbidden in the backend runtime directory.
rm -f \
    "${backend_dir}/appAesKey" \
    "${backend_dir}/mysql-root-password" \
    "${backend_dir}/db-backup-password" \
    "${backend_dir}/schema-owner-password"

for runtime_secret in dbPassword jwtSecret; do
    metadata="$(stat -c '%u:%g:%a' "${backend_dir}/${runtime_secret}")"
    if [[ "${metadata}" != "0:${backend_gid}:440" ]]; then
        echo "invalid runtime secret owner/mode for ${runtime_secret}: ${metadata}" >&2
        exit 1
    fi
done

echo "runtime secrets staged for backend uid=${backend_uid} gid=${backend_gid}"
