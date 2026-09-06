#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

action=${1:-}
confirmation=${2:-}
runtime_env=/etc/ecobin/runtime.env
key=businessReleaseRemoteDispatchEnabled
backup=
temporary=
original_sha256=
applied_sha256=
changed=false

fail() {
    printf 'business-release-dispatch-toggle=FAIL: %s\n' "$1" >&2
    return 1
}

run_bounded() {
    local duration=$1
    shift
    timeout --foreground --signal=TERM --kill-after=5s "$duration" "$@"
}

wait_for_healthy_containers() {
    local backend deadline web
    deadline=$((SECONDS + 150))
    while (( SECONDS < deadline )); do
        backend="$(run_bounded 5s docker inspect --format \
            '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
            ecobin-target-backend 2>/dev/null || true)"
        web="$(run_bounded 5s docker inspect --format \
            '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
            ecobin-target-web 2>/dev/null || true)"
        if [[ "$backend" = running/healthy && "$web" = running/healthy ]]; then
            return 0
        fi
        sleep 2
    done
    return 1
}

runtime_value() {
    sed -n "s/^${key}=//p" "$runtime_env"
}

nonterminal_rollouts() {
    run_bounded 30s docker exec ecobin-target-mysql84 sh -ec '
        export MYSQL_PWD="$(cat /run/secrets/mysql_root_password)"
        exec mysql --batch --skip-column-names -uroot ecobin -e \
          "SELECT COUNT(*) FROM dev_edge_software_rollout
             WHERE rollout_status NOT IN
               (CHAR(67,79,77,80,76,69,84,69,68),
                CHAR(83,84,79,80,80,69,68),
                CHAR(86,65,76,73,68,65,84,73,79,78,95,70,65,73,76,69,68));"
    '
}

verify_runtime() {
    local expected=$1
    [[ "$(runtime_value)" = "$expected" ]] \
        || fail 'runtime.env dispatch value differs'
    run_bounded 20s docker exec ecobin-target-backend sh -eu -c \
        'test "${businessReleaseRemoteDispatchEnabled:-}" = "$1"' sh "$expected"
    run_bounded 15s systemctl is-active --quiet ecobin-target-app.service
    wait_for_healthy_containers
    run_bounded 60s docker exec ecobin-target-backend \
        /usr/local/bin/ecobin-backend-healthcheck
    run_bounded 15s curl --fail --silent --connect-timeout 3 --max-time 10 \
        http://127.0.0.1:18080/ >/dev/null
    run_bounded 60s /usr/local/sbin/ecobin-runtime-secret-probe
}

restore_previous_configuration() {
    local rollback_tmp
    trap - ERR HUP INT TERM
    set +e
    if [[ "$changed" != true || ! -f "$backup" || -L "$backup" ]]; then
        return 0
    fi
    if [[ ! -f "$runtime_env" || -L "$runtime_env" \
        || "$(sha256sum -- "$runtime_env" | awk '{print $1}')" != "$applied_sha256" ]]; then
        printf 'business-release-dispatch-rollback=REFUSED reason=concurrent-change backup=%s\n' \
            "$backup" >&2
        return 1
    fi
    rollback_tmp="$(mktemp /etc/ecobin/.runtime.env.dispatch-rollback.XXXXXXXX)" \
        || return 1
    install -o root -g root -m 0600 -- "$backup" "$rollback_tmp" \
        || return 1
    [[ "$(sha256sum -- "$rollback_tmp" | awk '{print $1}')" = "$original_sha256" ]] \
        || return 1
    mv -f -- "$rollback_tmp" "$runtime_env" || return 1
    run_bounded 90s systemctl restart ecobin-stage-runtime-secrets.service \
        || return 1
    run_bounded 120s /usr/local/sbin/ecobin-production-preflight \
        || return 1
    run_bounded 90s systemctl reload ecobin-target-app.service || return 1
    wait_for_healthy_containers || return 1
    printf 'business-release-dispatch-rollback=PASS backup=%s\n' "$backup" >&2
}

on_error() {
    local status=$?
    restore_previous_configuration || true
    exit "$status"
}

on_signal() {
    local status=$1
    restore_previous_configuration || true
    exit "$status"
}

trap on_error ERR
trap 'on_signal 129' HUP
trap 'on_signal 130' INT
trap 'on_signal 143' TERM

[[ "$(id -u)" = 0 ]] || fail 'root is required'
[[ "$(hostname)" = VM-0-16-ubuntu ]] || fail 'unexpected server hostname'
[[ "$action" = enable || "$action" = disable || "$action" = verify-disabled ]] \
    || fail 'first argument must be enable, disable, or verify-disabled'
if [[ "$action" != verify-disabled ]]; then
    [[ "$confirmation" = --controlled-single-device-test ]] \
        || fail 'second argument must be --controlled-single-device-test'
fi
[[ -f "$runtime_env" && ! -L "$runtime_env" ]] \
    || fail 'runtime.env is absent or unsafe'
[[ "$(stat -c '%U:%G:%a:%F' -- "$runtime_env")" = \
    'root:root:600:regular file' ]] \
    || fail 'runtime.env metadata differs'
[[ "$(grep -c "^${key}=" "$runtime_env" || true)" = 1 ]] \
    || fail 'dispatch key is not unique'

install -d -o root -g root -m 0700 /run/ecobin-deploy
[[ -d /run/ecobin-deploy && ! -L /run/ecobin-deploy ]] \
    || fail 'deployment lock directory is unsafe'
exec 9>/run/ecobin-deploy/runtime-env.lock
chmod 0600 /run/ecobin-deploy/runtime-env.lock
flock -n 9 || fail 'another runtime.env deployment holds the lock'

if [[ "$action" = verify-disabled ]]; then
    verify_runtime false
    printf 'business-release-dispatch-verification=PASS dispatch=false\n'
    exit 0
fi

desired=false
[[ "$action" = enable ]] && desired=true
current="$(runtime_value)"
[[ "$current" = true || "$current" = false ]] \
    || fail 'current dispatch value is invalid'

active_count="$(nonterminal_rollouts)"
[[ "$active_count" = 0 ]] \
    || fail 'a nonterminal update plan exists; settle or stop it before toggling'

if [[ "$current" = "$desired" ]]; then
    verify_runtime "$desired"
    printf 'business-release-dispatch-toggle=PASS action=%s changed=false dispatch=%s\n' \
        "$action" "$desired"
    exit 0
fi

original_sha256="$(sha256sum -- "$runtime_env" | awk '{print $1}')"
backup="$(mktemp "/etc/ecobin/runtime.env.pre-dispatch-${action}-$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX")"
install -o root -g root -m 0600 -- "$runtime_env" "$backup"
[[ "$(sha256sum -- "$backup" | awk '{print $1}')" = "$original_sha256" ]] \
    || fail 'runtime.env backup digest differs'

temporary="$(mktemp /etc/ecobin/.runtime.env.dispatch.XXXXXXXX)"
awk -v replacement="${key}=${desired}" -v key_prefix="${key}=" '
    index($0, key_prefix) == 1 { print replacement; next }
    { print }
' "$runtime_env" > "$temporary"
chown root:root "$temporary"
chmod 0600 "$temporary"
[[ "$(grep -c "^${key}=" "$temporary" || true)" = 1 ]] \
    || fail 'candidate dispatch key is not unique'
grep -Fqx "${key}=${desired}" "$temporary" \
    || fail 'candidate dispatch value differs'
applied_sha256="$(sha256sum -- "$temporary" | awk '{print $1}')"
[[ "$(sha256sum -- "$runtime_env" | awk '{print $1}')" = "$original_sha256" ]] \
    || fail 'runtime.env changed concurrently'
mv -f -- "$temporary" "$runtime_env"
temporary=
changed=true

run_bounded 90s systemctl restart ecobin-stage-runtime-secrets.service
run_bounded 120s /usr/local/sbin/ecobin-production-preflight
run_bounded 90s systemctl reload ecobin-target-app.service
wait_for_healthy_containers || fail 'target containers did not become healthy'
verify_runtime "$desired"
[[ "$(nonterminal_rollouts)" = 0 ]] \
    || fail 'a nonterminal update plan appeared during the toggle'

trap - ERR HUP INT TERM
printf 'business-release-dispatch-toggle=PASS action=%s changed=true dispatch=%s backup=%s\n' \
    "$action" "$desired" "$backup"
