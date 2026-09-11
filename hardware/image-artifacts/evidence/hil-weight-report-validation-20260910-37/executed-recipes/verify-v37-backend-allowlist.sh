#!/usr/bin/env bash
set -Eeuo pipefail

runtime_env=/etc/ecobin/runtime.env
backup=${1:?approved backup path required}
key=ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS
base=0.1.0,hardware-runtime-20260827-01,hardware-runtime-20260830-11,hardware-runtime-20260831-12,hardware-runtime-20260831-13,hardware-runtime-20260903-19,hardware-runtime-20260903-21,hardware-runtime-20260904-23,hardware-runtime-20260904-24,hardware-runtime-20260904-26,hardware-runtime-20260905-27
previous=${base},hardware-runtime-20260906-31,hardware-runtime-20260907-32,hardware-runtime-20260907-33,hardware-runtime-20260908-34
expected=${previous},hardware-runtime-20260910-37

run_bounded() {
    local duration=$1
    shift
    timeout --foreground --signal=TERM --kill-after=5s "$duration" "$@"
}

[[ "$(id -u)" = 0 ]]
[[ "$(hostname)" = VM-0-16-ubuntu ]]
[[ -f "$runtime_env" && ! -L "$runtime_env" ]]
[[ "$(stat -c '%U:%G:%a:%F' -- "$runtime_env")" = 'root:root:600:regular file' ]]
[[ "$(grep -c "^${key}=" "$runtime_env" || true)" = 1 ]]
grep -Fqx "${key}=${expected}" "$runtime_env"
grep -Fqx 'businessReleaseRemoteDispatchEnabled=false' "$runtime_env"
! grep -Fq 'hardware-runtime-20260906-30' "$runtime_env"
! grep -Fq 'hardware-runtime-20260906-29' "$runtime_env"
for retired in \
    hardware-runtime-20260902-15 \
    hardware-runtime-20260903-16 \
    hardware-runtime-20260903-17 \
    hardware-runtime-20260903-18 \
    hardware-runtime-20260903-20 \
    hardware-runtime-20260904-25 \
    hardware-runtime-20260906-28; do
    ! grep -Fq "$retired" "$runtime_env"
done
[[ "$backup" = /etc/ecobin/runtime.env.pre-v37-* ]]
! grep -Fq 'hardware-runtime-20260910-35' "$runtime_env"
! grep -Fq 'hardware-runtime-20260910-36' "$runtime_env"
grep -Fqx 'ECOBIN_RELEASE_ID=20260907231251-adb66043a1da' /etc/ecobin/deployment.env
[[ "$(docker inspect --format '{{.Image}}' ecobin-target-backend)" = sha256:7b9bb7f11ad99f09caf389135d56ca79868c85cb7c912217c56ee6d3e756781b ]]
[[ "$(docker inspect --format '{{.Image}}' ecobin-target-web)" = sha256:0d21d75be2dfcfc1437b8e7b148dcb30d23e0ce2d7a81fc53703c35e5438168e ]]
run_bounded 120s /usr/local/sbin/ecobin-production-preflight
run_bounded 15s systemctl is-active --quiet ecobin-stage-runtime-secrets.service
run_bounded 15s systemctl is-active --quiet ecobin-target-app.service
[[ "$(run_bounded 10s docker inspect --format '{{.State.Status}}/{{.State.Health.Status}}' ecobin-target-backend)" = running/healthy ]]
[[ "$(run_bounded 10s docker inspect --format '{{.State.Status}}/{{.State.Health.Status}}' ecobin-target-web)" = running/healthy ]]
run_bounded 30s docker exec ecobin-target-backend sh -eu -c \
    'test "${ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS:-}" = "$1"' \
    sh "$expected"
run_bounded 30s docker exec ecobin-target-backend sh -eu -c \
    'test "${businessReleaseRemoteDispatchEnabled:-}" = false'
run_bounded 60s docker exec ecobin-target-backend /usr/local/bin/ecobin-backend-healthcheck
run_bounded 15s curl --fail --silent --connect-timeout 3 --max-time 10 \
    http://127.0.0.1:18080/ >/dev/null
run_bounded 60s /usr/local/sbin/ecobin-runtime-secret-probe
[[ -f "$backup" && ! -L "$backup" ]]
[[ "$(stat -c '%U:%G:%a:%F' -- "$backup")" = 'root:root:600:regular file' ]]
[[ "$(grep -c "^${key}=" "$backup" || true)" = 1 ]]
grep -Fqx "${key}=${previous}" "$backup"
grep -Fqx 'businessReleaseRemoteDispatchEnabled=false' "$backup"
cmp -s <(grep -v "^$key=" "$backup") <(grep -v "^$key=" "$runtime_env")
printf 'v37-allowlist-independent-verification=PASS services=active containers=healthy v31=true v32=true v33=true v34=true v37=true v35=false v36=false v30=false v29=false retiredCandidates=false backupPreviousValue=true dispatch=false backupMetadata=root:root:0600\n'
