#!/usr/bin/env bash
set -euo pipefail

state_dir="${ECOBIN_MONITOR_STATE_DIR:-/var/lib/ecobin-monitoring}"
state_file="${state_dir}/vmstat.prev"
disk_threshold_pct="${ECOBIN_DISK_THRESHOLD_PCT:-70}"
memory_threshold_kib="${ECOBIN_MEMORY_THRESHOLD_KIB:-614400}"

install -d -o root -g root -m 0750 "${state_dir}"

root_used_pct="$(df -P / | awk 'NR == 2 {gsub(/%/, "", $5); print $5}')"
memory_available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
swap_total_kib="$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)"
swap_free_kib="$(awk '/^SwapFree:/ {print $2}' /proc/meminfo)"
swap_used_kib="$((swap_total_kib - swap_free_kib))"
pswpin="$(awk '$1 == "pswpin" {print $2}' /proc/vmstat)"
pswpout="$(awk '$1 == "pswpout" {print $2}' /proc/vmstat)"

previous_pswpin="${pswpin}"
previous_pswpout="${pswpout}"
if [[ -r "${state_file}" ]]; then
    read -r previous_pswpin previous_pswpout < "${state_file}" || true
fi

swap_io_delta="$(((pswpin - previous_pswpin) + (pswpout - previous_pswpout)))"
printf '%s %s\n' "${pswpin}" "${pswpout}" > "${state_file}.tmp"
chmod 0640 "${state_file}.tmp"
mv -f "${state_file}.tmp" "${state_file}"

alerts=()
if ((root_used_pct >= disk_threshold_pct)); then
    alerts+=("root_disk=${root_used_pct}%")
fi
if ((memory_available_kib < memory_threshold_kib)); then
    alerts+=("memory_available=${memory_available_kib}KiB")
fi
if ((swap_io_delta > 0)); then
    alerts+=("swap_io_delta=${swap_io_delta}")
fi

summary="root_disk=${root_used_pct}% memory_available=${memory_available_kib}KiB swap_used=${swap_used_kib}KiB swap_io_delta=${swap_io_delta}"
if ((${#alerts[@]} > 0)); then
    logger -p daemon.warning -t ecobin-capacity "WARN ${alerts[*]} ${summary}"
    echo "status=warn ${summary}"
else
    logger -p daemon.info -t ecobin-capacity "OK ${summary}"
    echo "status=ok ${summary}"
fi
