#!/usr/bin/env bash
set -euo pipefail

exec 3<>/dev/tcp/127.0.0.1/8080
printf '%s\r\n' \
    'GET /actuator/health/readiness HTTP/1.1' \
    'Host: 127.0.0.1' \
    'Connection: close' \
    '' >&3

IFS=$'\r' read -r status_line <&3
[[ "${status_line}" = 'HTTP/1.1 200 '* \
    || "${status_line}" = 'HTTP/1.0 200 '* ]]
