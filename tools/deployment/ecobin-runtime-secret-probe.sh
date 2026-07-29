#!/usr/bin/env bash
set -euo pipefail

container_name="${1:-ecobin-backend}"
image_name="$(docker inspect "${container_name}" --format '{{.Config.Image}}')"
image_user="$(docker image inspect "${image_name}" --format '{{.Config.User}}')"

printf 'image=%s configured-user=%s\n' \
    "${image_name}" "${image_user:-<empty>}"

if docker image inspect "${image_name}" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' |
    grep -Eq '^(dbPassword|jwtSecret|appAesKey|DB_RUNTIME_PASSWORD|MYSQL_ROOT_PASSWORD)='
then
    echo "image-static-secret-env=FAIL"
    exit 1
fi
echo "image-static-secret-env=PASS"

if docker inspect "${container_name}" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' |
    grep -Eq '^(dbPassword|jwtSecret|appAesKey|DB_RUNTIME_PASSWORD|MYSQL_ROOT_PASSWORD)='
then
    echo "container-static-secret-env=PRESENT"
else
    echo "container-static-secret-env=ABSENT"
fi

docker run \
    --rm \
    --network none \
    --user 10001:10001 \
    --read-only \
    --mount type=bind,src=/run/ecobin-secrets/backend,dst=/run/secrets,readonly \
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
            appAesKey \
            schema-owner-password
        do
            test ! -e "/run/secrets/${file_name}"
        done
        echo "runtime-secret-access=PASS"
    '

docker ps -a --format '{{.Names}} {{.Status}}' |
    grep '^ecobin-' |
    sort
