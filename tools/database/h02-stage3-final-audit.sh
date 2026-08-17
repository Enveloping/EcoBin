#!/usr/bin/env bash
set -euo pipefail

container_name="ecobin-target-mysql84"
volume_name="ecobin-target-mysql84-data"
network_name="ecobin-target-db"
database_name="ecobin"
expected_migrations="${H02_EXPECTED_MIGRATIONS:-53}"
expected_tables="${H02_EXPECTED_TABLES:-113}"
expected_permissions="${H02_EXPECTED_PERMISSIONS:-76}"
backup_path="${H02_BACKUP_PATH:-/var/backups/ecobin/h02/20260727T075001Z/ecobin-h02-permission-catalog-20260727T075001Z.sql.gz.cms}"

if [[ "$(id -u)" != "0" ]]; then
    echo "must run as root" >&2
    exit 1
fi

mysql_root() {
    docker exec "${container_name}" sh -ec '
        MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"
        export MYSQL_PWD
        exec mysql --batch --skip-column-names -uroot "$@"
    ' sh "$@"
}

mysql_version="$(mysql_root -e 'SELECT VERSION();')"
migration_count="$(
    mysql_root "${database_name}" \
        -e 'SELECT COUNT(*) FROM flyway_schema_history WHERE success = 1;'
)"
table_count="$(
    mysql_root -e "
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = '${database_name}'
          AND table_type = 'BASE TABLE'
          AND table_name <> 'flyway_schema_history';
    "
)"
permission_count="$(
    mysql_root "${database_name}" \
        -e 'SELECT COUNT(*) FROM iam_permission_definition;'
)"
business_rows="$(
    mysql_root "${database_name}" -e "
        SET SESSION group_concat_max_len = 1048576;
        SELECT GROUP_CONCAT(
            CONCAT(
                'SELECT COUNT(*) AS row_count FROM \`',
                table_name,
                '\`'
            )
            SEPARATOR ' UNION ALL '
        )
        INTO @count_queries
        FROM information_schema.tables
        WHERE table_schema = '${database_name}'
          AND table_type = 'BASE TABLE'
          AND table_name NOT IN (
              'flyway_schema_history',
              'iam_permission_definition',
              'dev_remote_support_port_slot'
          );
        SET @count_sql = CONCAT(
            'SELECT COALESCE(SUM(row_count), 0) FROM (',
            @count_queries,
            ') AS business_rows'
        );
        PREPARE count_statement FROM @count_sql;
        EXECUTE count_statement;
        DEALLOCATE PREPARE count_statement;
    " | tail -n 1
)"
locked_accounts="$(
    mysql_root -e "
        SELECT COUNT(*)
        FROM mysql.user
        WHERE user IN ('ecobin_schema_owner', 'ecobin_trigger_definer')
          AND host = '%'
          AND account_locked = 'Y';
    "
)"

image_id="$(
    docker container inspect --format '{{.Image}}' "${container_name}"
)"
health="$(
    docker container inspect --format '{{.State.Health.Status}}' "${container_name}"
)"
restart_policy="$(
    docker container inspect --format '{{.HostConfig.RestartPolicy.Name}}' "${container_name}"
)"
memory_bytes="$(
    docker container inspect --format '{{.HostConfig.Memory}}' "${container_name}"
)"
nano_cpus="$(
    docker container inspect --format '{{.HostConfig.NanoCpus}}' "${container_name}"
)"
pids_limit="$(
    docker container inspect --format '{{.HostConfig.PidsLimit}}' "${container_name}"
)"
published_ports="$(
    docker container inspect --format '{{json .HostConfig.PortBindings}}' "${container_name}"
)"
mounted_volume="$(
    docker container inspect \
        --format '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Name}}{{end}}{{end}}' \
        "${container_name}"
)"
network_internal="$(
    docker network inspect --format '{{.Internal}}' "${network_name}"
)"
host_mysql_listeners="$(
    ss -H -lntp |
        awk '$4 ~ /:(3306|13306)$/ {count++} END {print count+0}'
)"
backup_sha256="$(sha256sum "${backup_path}" | awk '{print $1}')"
backup_mode="$(stat -c '%U:%G:%a' "${backup_path}")"

if [[ "${mysql_version}" != "8.4.10" ]]; then
    echo "unexpected MySQL version" >&2
    exit 1
fi
if [[ "${migration_count}" != "${expected_migrations}" ]]; then
    echo "unexpected migration count" >&2
    exit 1
fi
if [[ "${table_count}" != "${expected_tables}" ]]; then
    echo "unexpected table count" >&2
    exit 1
fi
if [[ "${permission_count}" != "${expected_permissions}" ]]; then
    echo "unexpected permission count" >&2
    exit 1
fi
if [[ "${business_rows}" != "0" ]]; then
    echo "production target contains business rows" >&2
    exit 1
fi
if [[ "${locked_accounts}" != "2" ]]; then
    echo "schema owner or trigger definer is not locked" >&2
    exit 1
fi
if [[ "${health}" != "healthy" ]]; then
    echo "production target is not healthy" >&2
    exit 1
fi
if [[ "${published_ports}" != "{}" ]]; then
    echo "production target publishes a host port" >&2
    exit 1
fi
if [[ "${mounted_volume}" != "${volume_name}" ]]; then
    echo "production target uses an unexpected data volume" >&2
    exit 1
fi
if [[ "${network_internal}" != "true" ]]; then
    echo "production target network is not internal" >&2
    exit 1
fi
if [[ "${host_mysql_listeners}" != "0" ]]; then
    echo "host has a 3306 or 13306 listener" >&2
    exit 1
fi

printf 'mysql_version=%s\n' "${mysql_version}"
printf 'successful_migrations=%s\n' "${migration_count}"
printf 'domain_tables=%s\n' "${table_count}"
printf 'permission_definitions=%s\n' "${permission_count}"
printf 'business_rows=%s\n' "${business_rows}"
printf 'locked_schema_accounts=%s\n' "${locked_accounts}"
printf 'image_id=%s\n' "${image_id}"
printf 'health=%s\n' "${health}"
printf 'restart_policy=%s\n' "${restart_policy}"
printf 'memory_bytes=%s\n' "${memory_bytes}"
printf 'nano_cpus=%s\n' "${nano_cpus}"
printf 'pids_limit=%s\n' "${pids_limit}"
printf 'published_ports=%s\n' "${published_ports}"
printf 'mounted_volume=%s\n' "${mounted_volume}"
printf 'network_internal=%s\n' "${network_internal}"
printf 'host_mysql_listeners=%s\n' "${host_mysql_listeners}"
printf 'backup_sha256=%s\n' "${backup_sha256}"
printf 'backup_mode=%s\n' "${backup_mode}"

for old_container in ecobin-web ecobin-backend ecobin-mysql; do
    printf 'old_container_%s=%s\n' \
        "${old_container}" \
        "$(docker container inspect --format '{{.State.Status}}' "${old_container}")"
done
