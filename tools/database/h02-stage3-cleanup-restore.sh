#!/usr/bin/env bash
set -euo pipefail

restore_project="ecobin-h02-restore"
restore_container="ecobin-h02-restore-mysql84"
restore_volume="ecobin-h02-restore-data"
restore_network="ecobin-h02-restore-net"
restore_directory="/etc/ecobin/h02-restore"

production_container="ecobin-target-mysql84"
production_volume="ecobin-target-mysql84-data"
production_network="ecobin-target-db"

if [[ "$(id -u)" != "0" ]]; then
    echo "must run as root" >&2
    exit 1
fi
if [[ "$(realpath -e "${restore_directory}")" != "${restore_directory}" ]]; then
    echo "unexpected restore Compose directory" >&2
    exit 1
fi

if [[ "$(
    docker container inspect \
        --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
        "${restore_container}"
)" != "${restore_project}" ]]; then
    echo "unexpected restore container project label" >&2
    exit 1
fi
if [[ "$(
    docker volume inspect \
        --format '{{ index .Labels "com.docker.compose.project" }}' \
        "${restore_volume}"
)" != "${restore_project}" ]]; then
    echo "unexpected restore volume project label" >&2
    exit 1
fi
if [[ "$(
    docker network inspect \
        --format '{{ index .Labels "com.docker.compose.project" }}' \
        "${restore_network}"
)" != "${restore_project}" ]]; then
    echo "unexpected restore network project label" >&2
    exit 1
fi

docker container inspect "${production_container}" >/dev/null
docker volume inspect "${production_volume}" >/dev/null
docker network inspect "${production_network}" >/dev/null

docker compose \
    --project-name "${restore_project}" \
    --env-file "${restore_directory}/compose.env" \
    --file "${restore_directory}/docker-compose.h02-server.yml" \
    down --volumes --remove-orphans

if docker container inspect "${restore_container}" >/dev/null 2>&1; then
    echo "restore container still exists" >&2
    exit 1
fi
if docker volume inspect "${restore_volume}" >/dev/null 2>&1; then
    echo "restore volume still exists" >&2
    exit 1
fi
if docker network inspect "${restore_network}" >/dev/null 2>&1; then
    echo "restore network still exists" >&2
    exit 1
fi

docker container inspect "${production_container}" >/dev/null
docker volume inspect "${production_volume}" >/dev/null
docker network inspect "${production_network}" >/dev/null

rm -rf -- "${restore_directory}"
rm -f -- \
    /tmp/h02-stage3-backup.sh \
    /tmp/h02-stage3-restore-verify.sh \
    /tmp/ecobin-h02-permission-catalog-20260727T075001Z.sql.gz.cms \
    /usr/local/sbin/h02-stage3-restore-verify

printf 'removed_container=%s\n' "${restore_container}"
printf 'removed_volume=%s\n' "${restore_volume}"
printf 'removed_network=%s\n' "${restore_network}"
printf 'production_container_preserved=%s\n' "${production_container}"
printf 'production_volume_preserved=%s\n' "${production_volume}"
printf 'production_network_preserved=%s\n' "${production_network}"
