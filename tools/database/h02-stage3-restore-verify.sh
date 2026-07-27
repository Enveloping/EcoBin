#!/usr/bin/env bash
set -euo pipefail

action="${1:?usage: h02-stage3-restore-verify.sh <prepare|import|verify>}"
container_name="${H02_RESTORE_CONTAINER_NAME:-ecobin-h02-restore-mysql84}"
compose_project="${H02_RESTORE_COMPOSE_PROJECT:-ecobin-h02-restore}"
database_name="${H02_RESTORE_DATABASE:-ecobin}"

if [[ "$(id -u)" != "0" ]]; then
    echo "must run as root" >&2
    exit 1
fi
if [[ "${container_name}" != "ecobin-h02-restore-mysql84" ]]; then
    echo "refusing unexpected restore container: ${container_name}" >&2
    exit 1
fi
if [[ "${compose_project}" != "ecobin-h02-restore" ]]; then
    echo "refusing unexpected Compose project: ${compose_project}" >&2
    exit 1
fi
if [[ "${database_name}" != "ecobin" ]]; then
    echo "refusing unexpected database: ${database_name}" >&2
    exit 1
fi

container_id="$(
    docker container ls --all --quiet \
        --filter "name=^/${container_name}$"
)"
if [[ -z "${container_id}" ]] || [[ "$(wc -w <<<"${container_id}")" != "1" ]]; then
    echo "restore container is missing or ambiguous" >&2
    exit 1
fi
if [[ "$(
    docker container inspect \
        --format '{{ index .Config.Labels "com.docker.compose.project" }}' \
        "${container_name}"
)" != "${compose_project}" ]]; then
    echo "restore container does not belong to the expected Compose project" >&2
    exit 1
fi
if [[ "$(
    docker container inspect --format '{{.State.Running}}' "${container_name}"
)" != "true" ]]; then
    echo "restore container is not running" >&2
    exit 1
fi
if [[ "$(
    docker container inspect --format '{{.State.Health.Status}}' "${container_name}"
)" != "healthy" ]]; then
    echo "restore container is not healthy" >&2
    exit 1
fi

mysql_root() {
    docker exec "${container_name}" sh -ec '
        MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"
        export MYSQL_PWD
        exec mysql --batch --skip-column-names -uroot "$@"
    ' sh "$@"
}

case "${action}" in
    prepare)
        permission_before="$(
            mysql_root "${database_name}" \
                -e 'SELECT COUNT(*) FROM iam_permission_definition;'
        )"
        migrations_before="$(
            mysql_root "${database_name}" \
                -e 'SELECT COUNT(*) FROM flyway_schema_history WHERE success = 1;'
        )"
        if [[ "${permission_before}" != "71" && "${permission_before}" != "0" ]]; then
            echo "unexpected permission catalog before restore: ${permission_before}" >&2
            exit 1
        fi
        if [[ "${migrations_before}" != "10" ]]; then
            echo "expected 10 migration records before restore, got ${migrations_before}" >&2
            exit 1
        fi
        mysql_root "${database_name}" \
            -e 'DELETE FROM iam_permission_definition; DELETE FROM flyway_schema_history;'
        permission_after="$(
            mysql_root "${database_name}" \
                -e 'SELECT COUNT(*) FROM iam_permission_definition;'
        )"
        migrations_after="$(
            mysql_root "${database_name}" \
                -e 'SELECT COUNT(*) FROM flyway_schema_history;'
        )"
        if [[ "${permission_after}" != "0" ]]; then
            echo "failed to empty the restore-only permission catalog" >&2
            exit 1
        fi
        if [[ "${migrations_after}" != "0" ]]; then
            echo "failed to empty the restore-only Flyway history" >&2
            exit 1
        fi
        printf 'permission_definitions_before=%s\n' "${permission_before}"
        printf 'permission_definitions_after=%s\n' "${permission_after}"
        printf 'successful_migrations_before=%s\n' "${migrations_before}"
        printf 'successful_migrations_after=%s\n' "${migrations_after}"
        ;;
    import)
        docker exec -i "${container_name}" sh -ec '
            MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"
            export MYSQL_PWD
            exec mysql -uroot "$1"
        ' sh "${database_name}"
        ;;
    verify)
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
                      'iam_permission_definition'
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
        catalog_sha256="$(
            docker exec "${container_name}" sh -ec '
                MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"
                export MYSQL_PWD
                exec mysqldump \
                    -uroot \
                    --single-transaction \
                    --skip-triggers \
                    --no-tablespaces \
                    --no-create-info \
                    --compact \
                    --skip-comments \
                    --set-gtid-purged=OFF \
                    "$1" iam_permission_definition
            ' sh "${database_name}" |
                sha256sum |
                awk '{print $1}'
        )"

        if [[ "${mysql_version}" != "8.4.10" ]]; then
            echo "unexpected MySQL version: ${mysql_version}" >&2
            exit 1
        fi
        if [[ "${migration_count}" != "10" ]]; then
            echo "unexpected successful migration count: ${migration_count}" >&2
            exit 1
        fi
        if [[ "${table_count}" != "83" ]]; then
            echo "unexpected domain table count: ${table_count}" >&2
            exit 1
        fi
        if [[ "${permission_count}" != "71" ]]; then
            echo "unexpected permission definition count: ${permission_count}" >&2
            exit 1
        fi
        if [[ "${business_rows}" != "0" ]]; then
            echo "restore database contains business rows: ${business_rows}" >&2
            exit 1
        fi

        printf 'mysql_version=%s\n' "${mysql_version}"
        printf 'successful_migrations=%s\n' "${migration_count}"
        printf 'domain_tables=%s\n' "${table_count}"
        printf 'permission_definitions=%s\n' "${permission_count}"
        printf 'business_rows=%s\n' "${business_rows}"
        printf 'permission_catalog_sha256=%s\n' "${catalog_sha256}"
        ;;
    *)
        echo "unsupported action: ${action}" >&2
        exit 1
        ;;
esac
