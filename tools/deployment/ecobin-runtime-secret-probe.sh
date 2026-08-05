#!/usr/bin/env bash
set -euo pipefail

backend_container="${1:-ecobin-target-backend}"
web_container="${2:-ecobin-target-web}"

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

image_name="$(docker inspect "${backend_container}" --format '{{.Config.Image}}')"
image_user="$(docker image inspect "${image_name}" --format '{{.Config.User}}')"
container_user="$(docker inspect "${backend_container}" --format '{{.Config.User}}')"
container_environment="$(
    docker inspect "${backend_container}" \
        --format '{{range .Config.Env}}{{println .}}{{end}}'
)"
external_mode="$(
    printf '%s\n' "${container_environment}" \
        | sed -n 's/^externalMode=//p' \
        | tail -n 1 \
        | tr '[:upper:]' '[:lower:]'
)"

[[ "${image_user}" = 10001:10001 ]] \
    || fail "backend image user is not 10001:10001"
[[ "${container_user}" = 10001:10001 ]] \
    || fail "backend container user is not 10001:10001"
[[ "${external_mode}" = fake || "${external_mode}" = real ]] \
    || fail "backend externalMode is missing or invalid"

secret_environment_pattern='^(dbPassword|jwtSecret|wechatSecret|iotAccessId|iotSecretKey|onenetAccessKey|cosSecretId|cosSecretKey|wechatPayApiV3Key|MYSQL_ROOT_PASSWORD|DB_RUNTIME_PASSWORD)='
if docker image inspect "${image_name}" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep -Eq "${secret_environment_pattern}"
then
    fail "backend image contains a static secret environment variable"
fi
if printf '%s\n' "${container_environment}" \
    | grep -Eq "${secret_environment_pattern}"
then
    fail "backend container contains a static secret environment variable"
fi

[[ "$(docker inspect "${backend_container}" \
    --format '{{.HostConfig.ReadonlyRootfs}}')" = true ]] \
    || fail "backend root filesystem is writable"
[[ "$(docker inspect "${backend_container}" \
    --format '{{json .HostConfig.PortBindings}}')" = '{}' ]] \
    || fail "backend publishes a host port"
[[ "$(docker inspect "${backend_container}" \
    --format '{{range .Mounts}}{{if eq .Destination "/run/secrets"}}{{.RW}}{{end}}{{end}}')" \
    = false ]] || fail "/run/secrets is not a read-only bind mount"
[[ "$(docker inspect "${backend_container}" \
    --format '{{.HostConfig.RestartPolicy.Name}}')" = unless-stopped ]] \
    || fail "backend restart policy is not unless-stopped"
[[ "$(docker inspect "${backend_container}" \
    --format '{{.HostConfig.PidsLimit}}')" -gt 0 ]] \
    || fail "backend PID limit is missing"

backend_networks="$(
    docker inspect "${backend_container}" \
        --format '{{range $name, $network := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
        | tr -d '\r' \
        | sed '/^$/d' \
        | sort
)"
[[ "${backend_networks}" = $'ecobin-target-app\necobin-target-db' ]] \
    || fail "backend network membership is unexpected"

web_networks="$(
    docker inspect "${web_container}" \
        --format '{{range $name, $network := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
        | tr -d '\r' \
        | sed '/^$/d' \
        | sort
)"
[[ "${web_networks}" = ecobin-target-app ]] \
    || fail "web must join only ecobin-target-app"
web_binding="$(
    docker inspect "${web_container}" \
        --format '{{with index .HostConfig.PortBindings "80/tcp"}}{{(index . 0).HostIp}}:{{(index . 0).HostPort}}{{end}}'
)"
[[ "${web_binding}" =~ ^127\.0\.0\.1:[0-9]+$ ]] \
    || fail "web port is not bound to IPv4 loopback only"
[[ "$(docker inspect "${web_container}" \
    --format '{{.HostConfig.ReadonlyRootfs}}')" = true ]] \
    || fail "web root filesystem is writable"

docker run \
    --rm \
    --network none \
    --user 10001:10001 \
    --read-only \
    --env "ECOBIN_PROBE_MODE=${external_mode}" \
    --mount \
        type=bind,src=/run/ecobin-secrets/backend,dst=/run/secrets,readonly \
    --entrypoint /bin/sh \
    "${image_name}" \
    -eu -c '
        test "$(id -u):$(id -g)" = "10001:10001"
        for file_name in dbPassword jwtSecret; do
            test -r "/run/secrets/${file_name}"
            test ! -w "/run/secrets/${file_name}"
        done
        for file_name in \
            mysql-root-password \
            db-backup-password \
            wechatSecret \
            appAesKey \
            schema-owner-password
        do
            test ! -e "/run/secrets/${file_name}"
        done

        if [ "${ECOBIN_PROBE_MODE}" = real ]; then
            for file_name in \
                iotAccessId \
                iotSecretKey \
                onenetAccessKey \
                cosSecretId \
                cosSecretKey \
                wechatPayApiV3Key
            do
                test -r "/run/secrets/${file_name}"
                test ! -w "/run/secrets/${file_name}"
            done
            for file_name in apiclient_key.pem pub_key.pem; do
                test -r "/run/secrets/wechatpay/${file_name}"
                test ! -w "/run/secrets/wechatpay/${file_name}"
            done
        else
            for file_name in \
                iotAccessId \
                iotSecretKey \
                onenetAccessKey \
                cosSecretId \
                cosSecretKey \
                wechatPayApiV3Key \
                wechatpay
            do
                test ! -e "/run/secrets/${file_name}"
            done
        fi

    '

printf 'runtime-security-probe=PASS mode=%s backend=%s web=%s\n' \
    "${external_mode}" "${backend_container}" "${web_container}"
