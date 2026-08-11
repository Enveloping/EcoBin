#!/usr/bin/env bash

set -Eeuo pipefail

SERVER_HOST="${ECOBIN_REVERSE_SSH_SERVER_HOST:-115.159.67.35}"
SERVER_PORT="${ECOBIN_REVERSE_SSH_SERVER_PORT:-22}"
SERVER_USER="${ECOBIN_REVERSE_SSH_SERVER_USER:-ecobin-tunnel}"
REMOTE_PORT="${ECOBIN_REVERSE_SSH_REMOTE_PORT:-22023}"
IDENTITY_FILE="${ECOBIN_REVERSE_SSH_IDENTITY_FILE:-/etc/ecobin/reverse-ssh/id_ed25519}"
KNOWN_HOSTS_FILE="${ECOBIN_REVERSE_SSH_KNOWN_HOSTS_FILE:-/etc/ecobin/reverse-ssh/known_hosts}"
RETRY_SECONDS="${ECOBIN_REVERSE_SSH_RETRY_SECONDS:-10}"
CONNECT_TIMEOUT_SECONDS="${ECOBIN_REVERSE_SSH_CONNECT_TIMEOUT_SECONDS:-10}"
SERVER_ALIVE_INTERVAL_SECONDS="${ECOBIN_REVERSE_SSH_SERVER_ALIVE_INTERVAL_SECONDS:-30}"
SERVER_ALIVE_COUNT_MAX="${ECOBIN_REVERSE_SSH_SERVER_ALIVE_COUNT_MAX:-3}"
LOCK_FILE="${ECOBIN_REVERSE_SSH_LOCK_FILE:-/run/lock/ecobin-reverse-ssh-${REMOTE_PORT}.lock}"

MODE="run"
STOP_REQUESTED=0
SSH_CHILD_PID=""
SSH_COMMAND=()

usage() {
    cat <<'EOF'
EcoBin 香橙派反向 SSH 隧道

用法：
  reverse_ssh_tunnel.sh              持续运行，断线后自动重连
  reverse_ssh_tunnel.sh --run        同上
  reverse_ssh_tunnel.sh --once       只运行一段隧道连接，断开后退出
  reverse_ssh_tunnel.sh --check      校验参数、密钥、服务器指纹和 SSH 配置
  reverse_ssh_tunnel.sh --dry-run    只打印将执行的 SSH 命令，不连接网络
  reverse_ssh_tunnel.sh --help       显示帮助

环境变量：
  ECOBIN_REVERSE_SSH_SERVER_HOST                 跳板服务器地址，默认 115.159.67.35
  ECOBIN_REVERSE_SSH_SERVER_PORT                 跳板服务器 SSH 入口端口，默认 22
  ECOBIN_REVERSE_SSH_SERVER_USER                 受限隧道账号，默认 ecobin-tunnel
  ECOBIN_REVERSE_SSH_REMOTE_PORT                 服务器回环反向端口，默认 22023
  ECOBIN_REVERSE_SSH_IDENTITY_FILE               隧道私钥路径
  ECOBIN_REVERSE_SSH_KNOWN_HOSTS_FILE            固定服务器指纹文件路径
  ECOBIN_REVERSE_SSH_RETRY_SECONDS               断线重试间隔，默认 10 秒
  ECOBIN_REVERSE_SSH_CONNECT_TIMEOUT_SECONDS     单次连接超时，默认 10 秒
  ECOBIN_REVERSE_SSH_SERVER_ALIVE_INTERVAL_SECONDS  保活间隔，默认 30 秒
  ECOBIN_REVERSE_SSH_SERVER_ALIVE_COUNT_MAX      连续保活失败次数，默认 3
  ECOBIN_REVERSE_SSH_LOCK_FILE                   单实例锁文件路径

端口含义：
  SERVER_PORT 是香橙派主动连接服务器时使用的公网 SSH 端口。
  REMOTE_PORT 是只监听在服务器 127.0.0.1 上、供跳板访问香橙派的端口。
  香橙派本地目标固定为 127.0.0.1:22，不会暴露到公网。
EOF
}

log() {
    local level="$1"
    shift
    printf '%s [%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$level" "$*" >&2
}

die() {
    log "ERROR" "$*"
    exit 2
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "缺少必需命令：$1"
}

require_integer_between() {
    local name="$1"
    local value="$2"
    local minimum="$3"
    local maximum="$4"

    [[ "$value" =~ ^[0-9]+$ ]] || die "${name} 必须是整数，当前值：${value}"
    (( value >= minimum && value <= maximum )) ||
        die "${name} 必须在 ${minimum}..${maximum} 范围内，当前值：${value}"
}

validate_scalar_configuration() {
    [[ -n "$SERVER_HOST" ]] || die "跳板服务器地址不能为空"
    [[ "$SERVER_HOST" != -* ]] || die "跳板服务器地址不能以 '-' 开头"
    [[ "$SERVER_HOST" =~ ^[A-Za-z0-9._-]+$ ]] ||
        die "跳板服务器地址只允许 IPv4 地址或普通 DNS 名称，当前值：${SERVER_HOST}"
    [[ "$SERVER_USER" =~ ^[A-Za-z_][A-Za-z0-9._-]*$ ]] ||
        die "受限隧道账号格式无效，当前值：${SERVER_USER}"

    require_integer_between "SERVER_PORT" "$SERVER_PORT" 1 65535
    require_integer_between "REMOTE_PORT" "$REMOTE_PORT" 1024 65535
    require_integer_between "RETRY_SECONDS" "$RETRY_SECONDS" 1 300
    require_integer_between "CONNECT_TIMEOUT_SECONDS" "$CONNECT_TIMEOUT_SECONDS" 1 120
    require_integer_between "SERVER_ALIVE_INTERVAL_SECONDS" "$SERVER_ALIVE_INTERVAL_SECONDS" 5 300
    require_integer_between "SERVER_ALIVE_COUNT_MAX" "$SERVER_ALIVE_COUNT_MAX" 1 10

    [[ "$IDENTITY_FILE" == /* ]] || die "隧道私钥必须使用绝对路径：${IDENTITY_FILE}"
    [[ "$KNOWN_HOSTS_FILE" == /* ]] || die "服务器指纹文件必须使用绝对路径：${KNOWN_HOSTS_FILE}"
    [[ "$LOCK_FILE" == /* ]] || die "单实例锁文件必须使用绝对路径：${LOCK_FILE}"
}

known_hosts_lookup_name() {
    if [[ "$SERVER_PORT" == "22" ]]; then
        printf '%s' "$SERVER_HOST"
    else
        printf '[%s]:%s' "$SERVER_HOST" "$SERVER_PORT"
    fi
}

validate_files_and_host_key() {
    local identity_mode
    local host_lookup

    require_command ssh
    require_command ssh-keygen
    require_command stat

    [[ -f "$IDENTITY_FILE" && -r "$IDENTITY_FILE" ]] ||
        die "隧道私钥不存在或不可读：${IDENTITY_FILE}"
    identity_mode="$(stat -Lc '%a' "$IDENTITY_FILE")"
    [[ "$identity_mode" =~ ^[0-7]{3,4}$ ]] ||
        die "无法判断隧道私钥权限：${IDENTITY_FILE}"
    (( (8#$identity_mode & 077) == 0 )) ||
        die "隧道私钥不能向组或其他用户开放，请执行 chmod 600 '${IDENTITY_FILE}'"
    ssh-keygen -y -P '' -f "$IDENTITY_FILE" >/dev/null 2>&1 ||
        die "隧道私钥无效或带有口令；无人值守隧道必须使用受限、无口令的专用密钥"

    [[ -f "$KNOWN_HOSTS_FILE" && -r "$KNOWN_HOSTS_FILE" ]] ||
        die "服务器指纹文件不存在或不可读：${KNOWN_HOSTS_FILE}"
    host_lookup="$(known_hosts_lookup_name)"
    ssh-keygen -F "$host_lookup" -f "$KNOWN_HOSTS_FILE" >/dev/null 2>&1 ||
        die "服务器指纹文件中没有 ${host_lookup}；请先通过可信渠道核对并写入服务器指纹"
}

build_ssh_command() {
    SSH_COMMAND=(
        ssh
        -4
        -N
        -T
        -n
        -p "$SERVER_PORT"
        -i "$IDENTITY_FILE"
        -o "UserKnownHostsFile=${KNOWN_HOSTS_FILE}"
        -o "GlobalKnownHostsFile=/dev/null"
        -o "StrictHostKeyChecking=yes"
        -o "UpdateHostKeys=no"
        -o "VerifyHostKeyDNS=no"
        -o "BatchMode=yes"
        -o "IdentitiesOnly=yes"
        -o "PubkeyAuthentication=yes"
        -o "PasswordAuthentication=no"
        -o "KbdInteractiveAuthentication=no"
        -o "PreferredAuthentications=publickey"
        -o "ForwardAgent=no"
        -o "RequestTTY=no"
        -o "PermitLocalCommand=no"
        -o "ControlMaster=no"
        -o "ExitOnForwardFailure=yes"
        -o "ConnectionAttempts=1"
        -o "ConnectTimeout=${CONNECT_TIMEOUT_SECONDS}"
        -o "ServerAliveInterval=${SERVER_ALIVE_INTERVAL_SECONDS}"
        -o "ServerAliveCountMax=${SERVER_ALIVE_COUNT_MAX}"
        -o "TCPKeepAlive=yes"
        -o "LogLevel=ERROR"
        -R "127.0.0.1:${REMOTE_PORT}:127.0.0.1:22"
        "${SERVER_USER}@${SERVER_HOST}"
    )
}

print_command() {
    local argument

    printf 'command='
    for argument in "${SSH_COMMAND[@]}"; do
        printf '%q ' "$argument"
    done
    printf '\n'
}

check_ssh_configuration() {
    "${SSH_COMMAND[0]}" -G "${SSH_COMMAND[@]:1}" >/dev/null 2>&1 ||
        die "本机 OpenSSH 不接受当前隧道参数"
}

acquire_single_instance_lock() {
    local lock_directory

    require_command flock
    lock_directory="${LOCK_FILE%/*}"
    [[ -d "$lock_directory" && -w "$lock_directory" ]] ||
        die "单实例锁目录不存在或不可写：${lock_directory}"
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "已有反向 SSH 隧道进程正在使用端口 ${REMOTE_PORT}"
}

handle_stop_signal() {
    STOP_REQUESTED=1
    if [[ -n "$SSH_CHILD_PID" ]]; then
        kill -TERM "$SSH_CHILD_PID" >/dev/null 2>&1 || true
    fi
}

run_ssh_session() {
    local status

    log "INFO" \
        "连接 ${SERVER_USER}@${SERVER_HOST}:${SERVER_PORT}；服务器 127.0.0.1:${REMOTE_PORT} -> 香橙派 127.0.0.1:22"
    "${SSH_COMMAND[@]}" &
    SSH_CHILD_PID=$!
    if wait "$SSH_CHILD_PID"; then
        status=0
    else
        status=$?
    fi
    SSH_CHILD_PID=""
    return "$status"
}

parse_arguments() {
    if (( $# > 1 )); then
        usage >&2
        exit 2
    fi

    case "${1:---run}" in
        --run)
            MODE="run"
            ;;
        --once)
            MODE="once"
            ;;
        --check)
            MODE="check"
            ;;
        --dry-run)
            MODE="dry-run"
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            usage >&2
            exit 2
            ;;
    esac
}

main() {
    local status

    parse_arguments "$@"
    validate_scalar_configuration
    build_ssh_command

    if [[ "$MODE" == "dry-run" ]]; then
        print_command
        return 0
    fi

    validate_files_and_host_key
    check_ssh_configuration

    if [[ "$MODE" == "check" ]]; then
        log "INFO" \
            "本地配置有效：${SERVER_HOST}:${SERVER_PORT}，反向端口 127.0.0.1:${REMOTE_PORT}"
        return 0
    fi

    acquire_single_instance_lock
    trap handle_stop_signal INT TERM HUP

    if [[ "$MODE" == "once" ]]; then
        if run_ssh_session; then
            status=0
        else
            status=$?
        fi
        (( STOP_REQUESTED == 1 )) && return 0
        return "$status"
    fi

    while (( STOP_REQUESTED == 0 )); do
        if run_ssh_session; then
            status=0
        else
            status=$?
        fi
        (( STOP_REQUESTED == 1 )) && break
        log "WARN" "隧道已断开（ssh exit=${status}），${RETRY_SECONDS} 秒后重连"
        sleep "$RETRY_SECONDS" || true
    done

    log "INFO" "反向 SSH 隧道已停止"
}

main "$@"
