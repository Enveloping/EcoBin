#!/usr/bin/env bash
set -euo pipefail

# Single entry point for the production Compose stack. The remote-support bind
# mounts are present only while that feature is enabled, so a server that has
# not installed the SSH boundary can still run the ordinary application stack.
compose_file="${ECOBIN_APP_COMPOSE_FILE:-/etc/ecobin/compose/docker-compose.target-app.yml}"
remote_compose_file="${ECOBIN_REMOTE_SUPPORT_COMPOSE_FILE:-/etc/ecobin/compose/docker-compose.remote-support.yml}"
deployment_env="${ECOBIN_DEPLOYMENT_ENV_FILE:-/etc/ecobin/deployment.env}"
runtime_env="${ECOBIN_RUNTIME_ENV_FILE:-/etc/ecobin/runtime.env}"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

env_value() {
    local file="$1"
    local key="$2"
    local count value
    count="$(grep -Ec "^${key}=" "${file}" || true)"
    [[ "${count}" = 1 ]] \
        || fail "${file} must contain exactly one ${key} assignment"
    value="$(sed -n "s/^${key}=//p" "${file}")"
    [[ -n "${value}" ]] || fail "${key} must not be empty"
    printf '%s' "${value}"
}

[[ $# = 1 ]] || fail "usage: ecobin-target-app-compose <up|reload|stop|config>"
action="$1"
[[ "${action}" = up || "${action}" = reload \
    || "${action}" = stop || "${action}" = config ]] \
    || fail "unsupported Compose action: ${action}"
[[ -f "${compose_file}" && -f "${deployment_env}" \
    && -f "${runtime_env}" ]] || fail "production Compose inputs are incomplete"

remote_enabled="$(env_value "${runtime_env}" remoteSupportEnabled \
    | tr '[:upper:]' '[:lower:]')"
[[ "${remote_enabled}" = true || "${remote_enabled}" = false ]] \
    || fail "remoteSupportEnabled must be true or false"

compose_args=(
    compose
    --env-file "${deployment_env}"
    --file "${compose_file}"
)
if [[ "${remote_enabled}" = true ]]; then
    [[ -f "${remote_compose_file}" ]] \
        || fail "enabled remote support is missing ${remote_compose_file}"
    compose_args+=(--file "${remote_compose_file}")
fi

case "${action}" in
    up|reload)
        exec docker "${compose_args[@]}" up --detach --no-build \
            --wait --wait-timeout 180
        ;;
    stop)
        exec docker "${compose_args[@]}" stop --timeout 60
        ;;
    config)
        exec docker "${compose_args[@]}" config --quiet
        ;;
esac
