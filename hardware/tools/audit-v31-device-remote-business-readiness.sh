#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" != 0 ]]; then
    exec sudo -n env \
        ECOBIN_DEVICE_AUDIT_LABEL=v31 \
        ECOBIN_EXPECTED_HARDWARE_RELEASE=hardware-runtime-20260906-31 \
        ECOBIN_EXPECTED_COMMUNICATION_RELEASE=communication-20260906-31 \
        ECOBIN_EXPECTED_UPDATER_RELEASE=updater-20260906-31 \
        bash "$0" "$@"
fi

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
export ECOBIN_DEVICE_AUDIT_LABEL=v31
export ECOBIN_EXPECTED_HARDWARE_RELEASE=hardware-runtime-20260906-31
export ECOBIN_EXPECTED_COMMUNICATION_RELEASE=communication-20260906-31
export ECOBIN_EXPECTED_UPDATER_RELEASE=updater-20260906-31
exec bash "$script_dir/audit-v30-device-remote-business-readiness.sh" "$@"
