#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

mode=${1:-apply}
runtime_env=/etc/ecobin/runtime.env
key=ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS
old_value=0.1.0,hardware-runtime-20260827-01,hardware-runtime-20260830-11,hardware-runtime-20260831-12,hardware-runtime-20260831-13,hardware-runtime-20260903-19,hardware-runtime-20260903-21,hardware-runtime-20260904-23,hardware-runtime-20260904-24,hardware-runtime-20260904-26,hardware-runtime-20260905-27,hardware-runtime-20260906-31,hardware-runtime-20260907-32,hardware-runtime-20260907-33,hardware-runtime-20260908-34
new_value=0.1.0,hardware-runtime-20260827-01,hardware-runtime-20260830-11,hardware-runtime-20260831-12,hardware-runtime-20260831-13,hardware-runtime-20260903-19,hardware-runtime-20260903-21,hardware-runtime-20260904-23,hardware-runtime-20260904-24,hardware-runtime-20260904-26,hardware-runtime-20260905-27,hardware-runtime-20260906-31,hardware-runtime-20260907-32,hardware-runtime-20260907-33,hardware-runtime-20260908-34,hardware-runtime-20260910-37
backup=
backup_ready=false
temporary=
original_sha256=
applied_sha256=

fail() {
    printf 'v37-allowlist-deployment=FAIL: %s\n' "$1" >&2
    return 1
}

wait_for_healthy_containers() {
    local backend deadline web
    deadline=$((SECONDS + 120))
    while (( SECONDS < deadline )); do
        backend="$(run_bounded 5s docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ecobin-target-backend 2>/dev/null || true)"
        web="$(run_bounded 5s docker inspect --format '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' ecobin-target-web 2>/dev/null || true)"
        if [[ "$backend" = running/healthy && "$web" = running/healthy ]]; then
            return 0
        fi
        if (( SECONDS < deadline )); then
            sleep 2
        fi
    done
    return 1
}

run_bounded() {
    local duration=$1
    shift
    timeout --foreground --signal=TERM --kill-after=5s "$duration" "$@"
}

validate_file_metadata() {
    [[ -f "$runtime_env" && ! -L "$runtime_env" ]] \
        || fail 'runtime.env is missing, not regular, or a symlink'
    [[ "$(stat -c '%U:%G:%a:%F' -- "$runtime_env")" = 'root:root:600:regular file' ]] \
        || fail 'runtime.env metadata differs'
    [[ "$(grep -c "^${key}=" "$runtime_env" || true)" = 1 ]] \
        || fail 'allowlist key is not unique'
}

validate_exact_value() {
    local expected=$1
    grep -Fqx "${key}=${expected}" "$runtime_env" \
        || fail 'allowlist value differs from the required exact value'
}

restore_previous_configuration() {
    local current_sha256 restore_tmp rollback_failed
    rollback_failed=false
    if [[ ! -f "$runtime_env" || -L "$runtime_env" ]]; then
        printf 'v37-allowlist-rollback=REFUSED reason=runtime-env-unsafe backup=%s\n' \
            "$backup" >&2
        return 1
    fi
    current_sha256="$(sha256sum -- "$runtime_env" | awk '{print $1}')"
    if [[ -n "$original_sha256" && "$current_sha256" = "$original_sha256" ]]; then
        printf 'v37-allowlist-rollback=SKIPPED reason=original-still-active backup=%s\n' \
            "$backup" >&2
        return 0
    fi
    if [[ -z "$applied_sha256" || "$current_sha256" != "$applied_sha256" ]]; then
        printf 'v37-allowlist-rollback=REFUSED reason=concurrent-change backup=%s\n' \
            "$backup" >&2
        return 1
    fi
    restore_tmp="$(mktemp /etc/ecobin/.runtime.env.rollback-v37.XXXXXXXX)" || return 1
    install -o root -g root -m 0600 -- "$backup" "$restore_tmp" \
        || rollback_failed=true
    if [[ "$rollback_failed" = false \
        && "$(sha256sum -- "$restore_tmp" | awk '{print $1}')" != "$original_sha256" ]]; then
        rollback_failed=true
    fi
    if [[ "$rollback_failed" = false \
        && "$(sha256sum -- "$runtime_env" | awk '{print $1}')" != "$applied_sha256" ]]; then
        printf 'v37-allowlist-rollback=REFUSED reason=concurrent-change backup=%s\n' \
            "$backup" >&2
        rm -f -- "$restore_tmp"
        return 1
    fi
    if [[ "$rollback_failed" = false ]]; then
        mv -f -- "$restore_tmp" "$runtime_env" || rollback_failed=true
    fi
    if [[ -e "$restore_tmp" ]]; then
        rm -f -- "$restore_tmp"
    fi
    if [[ "$rollback_failed" = false ]]; then
        run_bounded 90s systemctl restart ecobin-stage-runtime-secrets.service \
            || rollback_failed=true
        run_bounded 120s /usr/local/sbin/ecobin-production-preflight \
            || rollback_failed=true
        run_bounded 300s systemctl reload ecobin-target-app.service \
            || rollback_failed=true
        wait_for_healthy_containers || rollback_failed=true
    fi
    if [[ "$rollback_failed" = true ]]; then
        printf 'v37-allowlist-rollback=FAIL backup=%s\n' "$backup" >&2
        return 1
    fi
    printf 'v37-allowlist-rollback=PASS backup=%s\n' "$backup" >&2
}

finish_failure() {
    local status=$1 reason=$2
    trap - ERR HUP INT TERM
    set +e
    printf 'v37-allowlist-deployment=ABORT reason=%s status=%s\n' "$reason" "$status" >&2
    if [[ -n "$temporary" && -e "$temporary" ]]; then
        rm -f -- "$temporary"
    fi
    if [[ "$backup_ready" = true ]]; then
        if [[ -n "$original_sha256" && -n "$backup" \
            && -f "$backup" && ! -L "$backup" ]]; then
            restore_previous_configuration
        else
            printf 'v37-allowlist-rollback=REFUSED reason=backup-missing-or-unsafe\n' >&2
        fi
    elif [[ -n "$backup" && "$backup" = /etc/ecobin/runtime.env.pre-v37-* ]]; then
        if [[ -f "$backup" && ! -L "$backup" ]]; then
            if rm -f -- "$backup"; then
                printf 'v37-allowlist-unverified-backup=REMOVED\n' >&2
            else
                printf 'v37-allowlist-unverified-backup=FAIL reason=remove-failed\n' >&2
            fi
        elif [[ -e "$backup" || -L "$backup" ]]; then
            printf 'v37-allowlist-unverified-backup=REFUSED reason=unsafe-path\n' >&2
        fi
    fi
    exit "$status"
}

on_error() {
    local status=$?
    finish_failure "$status" ERR
}

on_signal() {
    local status=$1 signal_name=$2
    finish_failure "$status" "signal-${signal_name}"
}

trap on_error ERR
trap 'on_signal 129 HUP' HUP
trap 'on_signal 130 INT' INT
trap 'on_signal 143 TERM' TERM

[[ "$(id -u)" = 0 ]] || fail 'root is required'
[[ "$mode" = preflight || "$mode" = apply ]] || fail 'mode must be preflight or apply'
[[ "$(hostname)" = VM-0-16-ubuntu ]] || fail "unexpected server"
grep -Fqx "ECOBIN_RELEASE_ID=20260907231251-adb66043a1da" /etc/ecobin/deployment.env || fail "application release changed"
grep -Fqx "businessReleaseRemoteDispatchEnabled=false" "$runtime_env" || fail "remote dispatch is not disabled"
install -d -o root -g root -m 0700 /run/ecobin-deploy
[[ -d /run/ecobin-deploy && ! -L /run/ecobin-deploy ]] \
    || fail 'deployment lock directory is unsafe'
exec 9>/run/ecobin-deploy/runtime-env.lock
chmod 0600 /run/ecobin-deploy/runtime-env.lock
flock -n 9 || fail 'another runtime.env deployment holds the lock'
validate_file_metadata

if [[ "$mode" = preflight ]]; then
    validate_exact_value "$old_value"
    grep -Fq 'hardware-runtime-20260831-13' "$runtime_env" \
        || fail 'v13 is absent before deployment'
    ! grep -Fq 'hardware-runtime-20260910-37' "$runtime_env" \
        || fail 'v37 is unexpectedly present before deployment'
    printf 'v37-allowlist-preflight=PASS v13=true v19=true v21=true v23=true v24=true v26=true v27=true v28=false v29=false v30=false v31=true v32=true v33=true v34=true v37=false unique=true\n'
    exit 0
fi

validate_exact_value "$old_value"
original_sha256="$(sha256sum -- "$runtime_env" | awk '{print $1}')"
backup="$(mktemp "/etc/ecobin/runtime.env.pre-v37-$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX")"
install -o root -g root -m 0600 -- "$runtime_env" "$backup"
[[ "$(stat -c '%U:%G:%a:%F' -- "$backup")" = 'root:root:600:regular file' ]] \
    || fail 'backup metadata differs'
[[ "$(sha256sum -- "$backup" | awk '{print $1}')" = "$original_sha256" ]] \
    || fail 'backup digest differs from the original'
backup_ready=true

temporary="$(mktemp /etc/ecobin/.runtime.env.v37.XXXXXXXX)"
awk -v replacement="${key}=${new_value}" -v key_prefix="${key}=" '
    index($0, key_prefix) == 1 { print replacement; next }
    { print }
' "$runtime_env" > "$temporary"
chown root:root "$temporary"
chmod 0600 "$temporary"
[[ "$(grep -c "^${key}=" "$temporary" || true)" = 1 ]] \
    || fail 'candidate allowlist key is not unique'
grep -Fqx "${key}=${new_value}" "$temporary" \
    || fail 'candidate allowlist value differs'
grep -Fq 'hardware-runtime-20260831-13' "$temporary" \
    || fail 'candidate removes v13'
grep -Fq 'hardware-runtime-20260903-19' "$temporary" \
    || fail 'candidate omits v19'
for retired in \
    hardware-runtime-20260902-15 \
    hardware-runtime-20260903-16 \
    hardware-runtime-20260903-17 \
    hardware-runtime-20260903-18; do
    ! grep -Fq "$retired" "$temporary" \
        || fail "candidate still contains retired runtime: $retired"
done
cmp -s <(grep -v "^${key}=" "$runtime_env") <(grep -v "^${key}=" "$temporary") || fail "unrelated configuration changed"
applied_sha256="$(sha256sum -- "$temporary" | awk '{print $1}')"

validate_file_metadata
validate_exact_value "$old_value"
[[ "$(sha256sum -- "$runtime_env" | awk '{print $1}')" = "$original_sha256" ]] \
    || fail 'runtime.env changed concurrently before replacement'

mv -f -- "$temporary" "$runtime_env"
temporary=
validate_file_metadata
validate_exact_value "$new_value"
[[ "$(sha256sum -- "$runtime_env" | awk '{print $1}')" = "$applied_sha256" ]] \
    || fail 'runtime.env digest differs after replacement'

run_bounded 90s systemctl restart ecobin-stage-runtime-secrets.service
run_bounded 120s /usr/local/sbin/ecobin-production-preflight
run_bounded 300s systemctl reload ecobin-target-app.service
wait_for_healthy_containers || fail 'target containers did not become healthy'
run_bounded 15s systemctl is-active --quiet ecobin-stage-runtime-secrets.service
run_bounded 15s systemctl is-active --quiet ecobin-target-app.service
run_bounded 30s docker exec ecobin-target-backend sh -eu -c \
    'test "${ECOBIN_DEVICE_ACCEPTANCE_SUPPORTED_EDGE_SOFTWARE_VERSIONS:-}" = "$1"' \
    sh "$new_value"
run_bounded 60s docker exec ecobin-target-backend /usr/local/bin/ecobin-backend-healthcheck
run_bounded 15s curl --fail --silent --connect-timeout 3 --max-time 10 \
    http://127.0.0.1:18080/ >/dev/null
run_bounded 60s /usr/local/sbin/ecobin-runtime-secret-probe
[[ -f "$backup" && ! -L "$backup" ]] || fail 'backup disappeared'
[[ "$(stat -c '%U:%G:%a:%F' -- "$backup")" = 'root:root:600:regular file' ]] \
    || fail 'backup metadata changed'
[[ "$(sha256sum -- "$backup" | awk '{print $1}')" = "$original_sha256" ]] \
    || fail 'backup digest changed'
validate_file_metadata
validate_exact_value "$new_value"
[[ "$(sha256sum -- "$runtime_env" | awk '{print $1}')" = "$applied_sha256" ]] \
    || fail 'runtime.env changed after activation'

trap - ERR HUP INT TERM
printf 'v37-allowlist-deployment=PASS backup=%s v13=true v19=true v21=true v23=true v24=true v26=true v27=true v28=false v29=false v30=false v31=true v32=true v33=true v34=true v37=true v15=false v16=false v17=false v18=false\n' "$backup"
