#!/usr/bin/env bash
set -euo pipefail

run_id="${1:?usage: h02-stage3-backup.sh <UTC-run-id>}"
container_name="${H02_CONTAINER_NAME:-ecobin-target-mysql84}"
network_name="${H02_NETWORK_NAME:-ecobin-target-db}"
mysql_image="${H02_MYSQL_IMAGE:-mysql@sha256:8dbcf531a03aade657e181b9cf2f1d1803ce621a1d55610cb44cb531ab7d7db6}"
backup_password_path="${H02_BACKUP_PASSWORD_PATH:-/etc/ecobin/secrets/db-backup-password}"
recipient_cert="${H02_BACKUP_RECIPIENT_CERT:-/etc/ecobin/backup-public/ecobin-backup-recipient.crt}"
backup_directory="/var/backups/ecobin/h02/${run_id}"
backup_path="${backup_directory}/ecobin-h02-permission-catalog-${run_id}.sql.gz.cms"

if [[ "$(id -u)" != "0" ]]; then
    echo "must run as root" >&2
    exit 1
fi
if [[ ! "${run_id}" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
    echo "invalid UTC run id" >&2
    exit 1
fi
if [[ "$(stat -c '%u:%g:%a' "${backup_password_path}")" != "0:0:600" ]]; then
    echo "invalid backup password file metadata" >&2
    exit 1
fi
if [[ ! -r "${recipient_cert}" ]]; then
    echo "backup recipient certificate is unavailable" >&2
    exit 1
fi

install -d -o root -g root -m 0700 "${backup_directory}"

docker run --rm \
    --network "${network_name}" \
    --mount \
    "type=bind,source=${backup_password_path},target=/run/secrets/db_password,readonly" \
    --env "H02_DB_HOST=${container_name}" \
    --env "H02_DB_NAME=ecobin" \
    "${mysql_image}" \
    sh -ec '
        MYSQL_PWD="$(cat /run/secrets/db_password)"
        export MYSQL_PWD
        exec mysqldump \
            -h "${H02_DB_HOST}" \
            -u ecobin_backup \
            --single-transaction \
            --skip-triggers \
            --no-tablespaces \
            --no-create-info \
            --compact \
            --skip-comments \
            --set-gtid-purged=OFF \
            "${H02_DB_NAME}"
    ' |
    gzip -9 |
    openssl cms -encrypt \
        -binary \
        -aes-256-cbc \
        -outform DER \
        -out "${backup_path}" \
        "${recipient_cert}"

chown root:root "${backup_path}"
chmod 0600 "${backup_path}"

printf 'path=%s\n' "${backup_path}"
printf 'bytes=%s\n' "$(stat -c '%s' "${backup_path}")"
printf 'sha256=%s\n' "$(sha256sum "${backup_path}" | awk '{print $1}')"
