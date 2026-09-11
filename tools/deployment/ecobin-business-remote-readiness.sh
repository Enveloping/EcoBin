#!/usr/bin/env bash
set -Eeuo pipefail

expected_release_id=${1:-}
expected_commit=${2:-}

fail() {
    printf 'production-business-remote-readiness=FAIL: %s\n' "$1" >&2
    exit 2
}

run_bounded() {
    local duration=$1
    shift
    timeout --foreground --signal=TERM --kill-after=5s "$duration" "$@"
}

[[ "$(id -u)" = 0 ]] || fail 'root is required'
[[ "$(hostname)" = VM-0-16-ubuntu ]] || fail 'unexpected server hostname'
[[ -n "$expected_release_id" && -n "$expected_commit" ]] \
    || fail 'expected application release ID and full Git commit are required'
grep -Fqx 'businessReleaseRemoteDispatchEnabled=false' /etc/ecobin/runtime.env \
    || fail 'remote business dispatch is not disabled'
grep -Eq '^ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS=.*hardware-runtime-20260906-31(,|$)' \
    /etc/ecobin/runtime.env \
    || fail 'v31 is absent from the backend acceptance list'
grep -Eq '^ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS=.*hardware-runtime-20260907-32(,|$)' \
    /etc/ecobin/runtime.env \
    || fail 'v32 is absent from the backend acceptance list'
! grep -Fq 'hardware-runtime-20260906-30' /etc/ecobin/runtime.env \
    || fail 'retired v30 remains in the backend acceptance list'
! grep -Fq 'hardware-runtime-20260906-29' /etc/ecobin/runtime.env \
    || fail 'retired v29 remains in the backend acceptance list'
grep -Fqx "ECOBIN_RELEASE_ID=${expected_release_id}" /etc/ecobin/deployment.env \
    || fail 'the active application release ID differs'
grep -Fqx "ECOBIN_GIT_COMMIT=${expected_commit}" \
    "/var/lib/ecobin/releases/${expected_release_id}/manifest.env" \
    || fail 'the installed application commit differs'

run_bounded 120s /usr/local/sbin/ecobin-production-preflight
run_bounded 15s systemctl is-active --quiet ecobin-stage-runtime-secrets.service
run_bounded 15s systemctl is-active --quiet ecobin-target-app.service
[[ "$(run_bounded 10s docker inspect --format \
    '{{.State.Status}}/{{.State.Health.Status}}' ecobin-target-backend)" = running/healthy ]] \
    || fail 'the backend container is not healthy'
[[ "$(run_bounded 10s docker inspect --format \
    '{{.State.Status}}/{{.State.Health.Status}}' ecobin-target-web)" = running/healthy ]] \
    || fail 'the Web container is not healthy'
run_bounded 20s docker exec ecobin-target-backend sh -eu -c \
    'test "${businessReleaseRemoteDispatchEnabled:-}" = false'
run_bounded 60s docker exec ecobin-target-backend \
    /usr/local/bin/ecobin-backend-healthcheck
run_bounded 15s curl --fail --silent --connect-timeout 3 --max-time 10 \
    http://127.0.0.1:18080/ >/dev/null
run_bounded 60s /usr/local/sbin/ecobin-runtime-secret-probe

query=$'SELECT CONCAT("flyway=", COUNT(*), "/", MAX(CAST(version AS UNSIGNED))) FROM flyway_schema_history WHERE success=1;\nSELECT CONCAT("tables=", COUNT(*)) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type="BASE TABLE" AND table_name<>"flyway_schema_history";\nSELECT CONCAT("readyRelease=", COUNT(*)) FROM dev_edge_software_release_control WHERE release_uid="9d5cf9e6-7b09-4409-9dc4-54a291984e22" AND version_name="0.3.0-rc.3" AND release_sequence=3 AND release_status="READY" AND LOWER(HEX(package_sha256))="6c5276bfa83bfba00636322b028219ef5087ec24d8a58b33925a06ba3f592610" AND package_size=53755880 AND signing_key_id="business_2026";\nSELECT CONCAT("nonterminalRollouts=", COUNT(*)) FROM dev_edge_software_rollout WHERE rollout_status NOT IN ("COMPLETED","STOPPED","VALIDATION_FAILED");\nSELECT CONCAT("activeDeployments=", COUNT(*)) FROM dev_edge_software_deployment WHERE deployment_status NOT IN ("PLANNED","SUCCEEDED","ROLLED_BACK","DEFERRED","REJECTED","FAILED_LOCKED","CANCELLED");'
database_result="$(run_bounded 30s docker exec ecobin-target-mysql84 sh -ec '
    export MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"
    exec mysql --batch --skip-column-names -uroot ecobin -e "$1"
' sh "$query")"
grep -Fqx 'flyway=69/69' <<<"$database_result" \
    || fail 'the database migration epoch differs'
grep -Fqx 'tables=132' <<<"$database_result" \
    || fail 'the database domain-table count differs'
grep -Fqx 'readyRelease=1' <<<"$database_result" \
    || fail 'the approved healthy test release differs'
grep -Fqx 'nonterminalRollouts=0' <<<"$database_result" \
    || fail 'a nonterminal business update plan already exists'
grep -Fqx 'activeDeployments=0' <<<"$database_result" \
    || fail 'a business update deployment is already active'

printf '%s\n' "$database_result"
printf 'production-business-remote-readiness=PASS release=%s commit=%s dispatch=false v31Accepted=true v32Accepted=true v30Accepted=false v29Accepted=false testRelease=0.3.0-rc.3 plansIdle=true\n' \
    "$expected_release_id" "$expected_commit"
