#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "$(id -u)" != 0 ]]; then
    exec sudo -n bash "$0" "$@"
fi

audit_label=${ECOBIN_DEVICE_AUDIT_LABEL:-v30}
expected_hardware=${ECOBIN_EXPECTED_HARDWARE_RELEASE:-hardware-runtime-20260906-30}
expected_communication=${ECOBIN_EXPECTED_COMMUNICATION_RELEASE:-communication-20260906-30}
expected_updater=${ECOBIN_EXPECTED_UPDATER_RELEASE:-updater-20260906-30}
expected_download_host=ecobin-update-package-1436310712.cos.ap-beijing.myqcloud.com
expected_key_fingerprint=29a1145b54e884d4f382c5a953a7673d5e477a24eea04980180b23034e14b587
expected_test_package_bytes=53755880
reserve_bytes=$((256 * 1024 * 1024))

fail() {
    printf '%s-device-remote-readiness=FAIL: %s\n' "$audit_label" "$1" >&2
    exit 2
}

run_bounded() {
    local duration=$1
    shift
    timeout --foreground --signal=TERM --kill-after=5s "$duration" "$@"
}

[[ "$(readlink /opt/ecobin/hardware/current)" = "releases/${expected_hardware}" ]] \
    || fail 'the installed hardware runtime is not the expected release'
grep -Fqx "ECOBIN_EDGE_VERSION=${expected_hardware}" \
    /opt/ecobin/hardware/current/release.env \
    || fail 'the hardware runtime release file differs'
[[ "$(readlink /opt/ecobin/communication/current)" = \
    "releases/${expected_communication}" ]] \
    || fail 'the permanent communication-agent release differs'
[[ "$(readlink /opt/ecobin/updater/current)" = \
    "releases/${expected_updater}" ]] \
    || fail 'the permanent updater release differs'
grep -Fqx "ECOBIN_COMMUNICATION_AGENT_VERSION=${expected_communication}" \
    /usr/share/ecobin/device-management-release.env \
    || fail 'the communication-agent identity file differs'
grep -Fqx "ECOBIN_DEVICE_UPDATER_VERSION=${expected_updater}" \
    /usr/share/ecobin/device-management-release.env \
    || fail 'the updater identity file differs'

for unit in \
    ecobin-business-runtime.target \
    ecobin-communication-proxy.service \
    ecobin-updater-candidate.service; do
    run_bounded 10s systemctl is-active --quiet "$unit" \
        || fail "required daily service is not active: ${unit}"
    if systemctl is-failed --quiet "$unit"; then
        fail "required daily service is failed: ${unit}"
    fi
done

baseline_state="$(systemctl is-active ecobin-business.service || true)"
updatable_state="$(systemctl is-active \
    ecobin-business-updatable-candidate.service || true)"
if [[ "$baseline_state" = active && "$updatable_state" = active ]]; then
    fail 'both business runtime services are active'
fi
if [[ "$baseline_state" != active && "$updatable_state" != active ]]; then
    fail 'neither business runtime service is active'
fi
for unit in \
    ecobin-first-boot.service \
    ecobin-factory.target \
    ecobin-factory-portal.service \
    ecobin-hardware.service \
    ecobin-communication.service \
    ecobin-updater.service; do
    [[ "$(systemctl is-active "$unit" || true)" != active ]] \
        || fail "factory or legacy service is unexpectedly active: ${unit}"
done

communication_unit=/etc/systemd/system/ecobin-communication-proxy.service
updater_unit=/etc/systemd/system/ecobin-updater-candidate.service
grep -Eq '^ExecStart=.* --enable-remote-business-update( |$)' \
    "$communication_unit" \
    || fail 'the communication agent cannot route remote business updates'
grep -Eq '^ExecStart=.* --enable-remote-business-update( |$)' "$updater_unit" \
    || fail 'the updater cannot accept remote business updates'
grep -Fqx \
    "Environment=ECOBIN_BUSINESS_DOWNLOAD_BASE_URL=https://${expected_download_host}" \
    "$updater_unit" \
    || fail 'the updater trusted download origin differs'
grep -Fqx 'RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6' "$updater_unit" \
    || fail 'the updater does not have the required bounded network families'
grep -Fqx 'NoNewPrivileges=yes' "$updater_unit" \
    || fail 'the updater privilege boundary differs'
grep -Fqx 'CapabilityBoundingSet=' "$updater_unit" \
    || fail 'the updater capability boundary differs'
grep -Fqx 'DevicePolicy=closed' "$updater_unit" \
    || fail 'the updater device boundary differs'

public_key=/usr/share/ecobin/business-release-keys/business_2026.pem
[[ -f "$public_key" && ! -L "$public_key" ]] \
    || fail 'the formal business-release public key is absent or unsafe'
[[ "$(stat -c '%u:%g:%a:%h' -- "$public_key")" = '0:0:644:1' ]] \
    || fail 'the formal business-release public key metadata differs'
actual_key_fingerprint="$(
    openssl pkey -pubin -in "$public_key" -outform DER 2>/dev/null \
        | sha256sum | awk '{print $1}'
)"
[[ "$actual_key_fingerprint" = "$expected_key_fingerprint" ]] \
    || fail 'the formal business-release public key fingerprint differs'

updater_status="$(
    run_bounded 15s /opt/ecobin/updater/current/.venv/bin/python \
        /opt/ecobin/updater/current/app/updater_control_cli.py business-status
)"
UPDATER_STATUS="$updater_status" EXPECTED_UPDATER="$expected_updater" python3 - <<'PY' \
    || fail 'the permanent updater is not idle and ready for a remote test'
import json
import os

status = json.loads(os.environ["UPDATER_STATUS"])
business = status.get("businessUpdateCandidate", {})
mcu = status.get("mcuUpdateCandidate", {})
checks = {
    "service-ready": status.get("status") == "READY",
    "updater-version": status.get("releaseVersion") == os.environ["EXPECTED_UPDATER"],
    "candidate-active": status.get("candidateActivationState") == "ACTIVE",
    "job-gate-open": status.get("jobGateState") == "OPEN",
    "no-active-job": status.get("activeJobPermitCount") == 0,
    "maintenance-idle": status.get("maintenanceState") == "IDLE",
    "no-unresolved-action": status.get("unreconciledPhysicalActionCount") == 0,
    "business-updater-present": status.get("businessUpdateCandidateEnabled") is True,
    "remote-business-enabled": business.get("remoteTriggerEnabled") is True,
    "business-update-idle": business.get("activeUpdate") is None,
    "mcu-update-idle": mcu.get("activeUpdate") is None,
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit(",".join(failed))
print("updater-ready=PASS")
PY

run_bounded 135s env \
    COMMUNICATION_ROOT=/opt/ecobin/communication/current/app \
    EXPECTED_RELEASE="$expected_communication" \
    python3 - <<'PY' \
    || fail 'the communication agent did not reach the connected remote-update posture'
import os
import pathlib
import sys
import time

sys.path.insert(0, os.environ["COMMUNICATION_ROOT"])
from local_control import LocalControlClient  # noqa: E402

client = LocalControlClient(
    "/run/ecobin/communication/control.sock",
    protocol_name="ecobin.communication.control",
)
deadline = time.monotonic() + 120
last = None
while time.monotonic() < deadline:
    try:
        last = client.request("GET_STATUS", {})
    except Exception:
        time.sleep(2)
        continue
    if (
        last.get("status") == "READY"
        and last.get("releaseVersion") == os.environ["EXPECTED_RELEASE"]
        and last.get("onenetOwnership") == "ENABLED"
        and last.get("cloudConnectionState") == "CONNECTED"
        and last.get("businessEventIngress") == "ENABLED"
        and last.get("remoteUpdateRouting") == "BUSINESS_RUNTIME_ONLY"
    ):
        print("communication-ready=PASS")
        raise SystemExit(0)
    time.sleep(2)
raise SystemExit("communication status did not converge")
PY

run_bounded 20s env COMMUNICATION_ROOT=/opt/ecobin/communication/current/app \
    python3 - <<'PY' \
    || fail 'the business runtime did not report the permanent proxy posture'
import os
import sys

sys.path.insert(0, os.environ["COMMUNICATION_ROOT"])
from local_control import LocalControlClient  # noqa: E402

status = LocalControlClient(
    "/run/ecobin/business/control.sock",
    protocol_name="ecobin.business.control",
).request("GET_STATUS", {})
checks = {
    "component": status.get("component") == "BUSINESS_RUNTIME",
    "ready": status.get("status") == "READY",
    "local-proxy": status.get("managementArchitectureGeneration") == "LOCAL_PROXY",
    "cloud-owner": status.get("cloudConnectionOwner") == "COMMUNICATION_AGENT",
    "job-permit": status.get("jobPermitEnforced") is True,
    "maintenance-handoff": status.get("maintenanceHandoffEnabled") is True,
    "cloud-ingress": status.get("cloudProxyIngressEnabled") is True,
    "instance": isinstance(status.get("runtimeInstanceUid"), str),
    "version": isinstance(status.get("releaseVersion"), str),
    "database-size": isinstance(status.get("businessDatabaseSize"), int),
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit(",".join(failed))
print(
    "business-ready=PASS "
    f"releaseVersion={status['releaseVersion']} "
    f"databaseBytes={status['businessDatabaseSize']}"
)
PY

run_bounded 15s getent ahosts "$expected_download_host" >/dev/null \
    || fail 'DNS cannot resolve the private COS download host'

database_bytes="$(stat -c %s /var/lib/ecobin/business/edge.db)"
required_bytes=$((expected_test_package_bytes * 2 + database_bytes + reserve_bytes))
free_bytes="$(df --output=avail -B1 /var/lib/ecobin/updater | tail -n 1 | tr -d ' ')"
[[ "$free_bytes" =~ ^[0-9]+$ && "$free_bytes" -ge "$required_bytes" ]] \
    || fail 'free storage is below the signed-package staging requirement'

for root in \
    /var/lib/ecobin/updater/business-packages \
    /var/lib/ecobin/updater/staging; do
    [[ -d "$root" && ! -L "$root" ]] \
        || fail "business update directory is absent or unsafe: ${root}"
    [[ -z "$(find "$root" -mindepth 1 -print -quit)" ]] \
        || fail "business update directory is not empty: ${root}"
done

printf '%s-device-remote-readiness=PASS hardware=%s communication=%s updater=%s businessUnit=%s dns=true activeUpdate=false freeBytes=%s requiredBytes=%s\n' \
    "$audit_label" "$expected_hardware" "$expected_communication" "$expected_updater" \
    "$([[ "$baseline_state" = active ]] && printf baseline || printf updatable)" \
    "$free_bytes" "$required_bytes"
