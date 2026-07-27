#!/usr/bin/env bash
set -euo pipefail

swap_path="/swapfile"
swap_size_bytes="$((2 * 1024 * 1024 * 1024))"
swap_sysctl="/etc/sysctl.d/90-ecobin-swap.conf"

if [[ -e "${swap_path}" && ! -f "${swap_path}" ]]; then
    echo "${swap_path} exists but is not a regular file" >&2
    exit 1
fi

if [[ ! -e "${swap_path}" ]]; then
    fallocate -l "${swap_size_bytes}" "${swap_path}"
    chmod 0600 "${swap_path}"
    mkswap "${swap_path}" >/dev/null
else
    actual_size_bytes="$(stat -c '%s' "${swap_path}")"
    if [[ "${actual_size_bytes}" != "${swap_size_bytes}" ]]; then
        echo "existing ${swap_path} has unexpected size ${actual_size_bytes}" >&2
        exit 1
    fi
    chmod 0600 "${swap_path}"
    if [[ "$(blkid -p -s TYPE -o value "${swap_path}" || true)" != "swap" ]]; then
        echo "existing ${swap_path} is not initialized as swap" >&2
        exit 1
    fi
fi

if ! swapon --show=NAME --noheadings | grep -Fxq "${swap_path}"; then
    swapon "${swap_path}"
fi

if ! grep -Eq '^[[:space:]]*/swapfile[[:space:]]+none[[:space:]]+swap[[:space:]]+' /etc/fstab; then
    if [[ ! -e /etc/fstab.ecobin-phase2.bak ]]; then
        cp -a /etc/fstab /etc/fstab.ecobin-phase2.bak
    fi
    printf '\n/swapfile none swap sw 0 0\n' >> /etc/fstab
fi

if [[ -e "${swap_sysctl}" && ! -e "${swap_sysctl}.ecobin-phase2.bak" ]]; then
    cp -a "${swap_sysctl}" "${swap_sysctl}.ecobin-phase2.bak"
fi
printf 'vm.swappiness = 10\n' > "${swap_sysctl}"
chmod 0644 "${swap_sysctl}"
sysctl -q -w vm.swappiness=10

if systemctl is-active --quiet ntp.service; then
    echo "time-provider=ntp"
elif systemctl is-active --quiet chrony.service; then
    echo "time-provider=chrony"
else
    systemctl unmask systemd-timesyncd.service
    systemctl enable --now systemd-timesyncd.service
    timedatectl set-ntp true
    echo "time-provider=systemd-timesyncd"
fi

echo "swap-bytes=$(swapon --show=SIZE --bytes --noheadings "${swap_path}" | tr -d ' ')"
echo "swappiness=$(sysctl -n vm.swappiness)"
timedatectl show \
    --property=NTP \
    --property=NTPSynchronized \
    --property=Timezone
