#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
output_directory=""
runtime_archive=""
runtime_sha256=""
runtime_signature=""
runtime_signing_key_id=""
runtime_trust_directory=""
mcu_trust_directory=""
enrollment_env=""
cellular_env=""
payload_id=""
runtime_release_id=""
enrollment_release_id=""
remote_release_id=""
factory_release_id=""
first_boot_release_id=""
communication_agent_release_id=""
device_updater_release_id=""
staging_directory=""

fail() { printf 'software-payload-build=FAIL: %s\n' "$1" >&2; exit 1; }
cleanup() {
    if [[ -n "${staging_directory}" && -d "${staging_directory}" ]]; then
        case "$(basename -- "${staging_directory}")" in
            .ecobin-software-payload.*) rm -rf -- "${staging_directory}" ;;
            *) printf 'refusing to clean unexpected payload staging path\n' >&2 ;;
        esac
    fi
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir) output_directory="$2"; shift 2 ;;
        --runtime-archive) runtime_archive="$2"; shift 2 ;;
        --runtime-sha256) runtime_sha256="$2"; shift 2 ;;
        --runtime-signature) runtime_signature="$2"; shift 2 ;;
        --runtime-signing-key-id) runtime_signing_key_id="$2"; shift 2 ;;
        --runtime-trust-dir) runtime_trust_directory="$2"; shift 2 ;;
        --mcu-trust-dir) mcu_trust_directory="$2"; shift 2 ;;
        --enrollment-env) enrollment_env="$2"; shift 2 ;;
        --cellular-env) cellular_env="$2"; shift 2 ;;
        --payload-id) payload_id="$2"; shift 2 ;;
        --runtime-release-id) runtime_release_id="$2"; shift 2 ;;
        --enrollment-release-id) enrollment_release_id="$2"; shift 2 ;;
        --remote-support-release-id) remote_release_id="$2"; shift 2 ;;
        --factory-test-release-id) factory_release_id="$2"; shift 2 ;;
        --first-boot-release-id) first_boot_release_id="$2"; shift 2 ;;
        --communication-agent-release-id) communication_agent_release_id="$2"; shift 2 ;;
        --device-updater-release-id) device_updater_release_id="$2"; shift 2 ;;
        *) fail "unknown or incomplete argument: $1" ;;
    esac
done

[[ "$(id -u)" = 0 ]] || fail "payload build requires container root"
export UV_LINK_MODE=copy
export PYTHONDONTWRITEBYTECODE=1
[[ "$(uname -m)" = aarch64 || "$(uname -m)" = arm64 ]] \
    || fail "payload build requires the locked ARM64 container"
python3 -c 'import sys; assert sys.version_info[:2] == (3, 11)' \
    || fail "payload build requires Python 3.11"
for value in "${output_directory}" "${runtime_archive}" "${runtime_sha256}" \
    "${runtime_signature}" "${runtime_signing_key_id}" "${runtime_trust_directory}" \
    "${mcu_trust_directory}" "${enrollment_env}" "${cellular_env}" \
    "${payload_id}" "${runtime_release_id}" "${enrollment_release_id}" \
    "${remote_release_id}" "${factory_release_id}" "${first_boot_release_id}"; do
    [[ -n "${value}" ]] || fail "all controlled payload inputs are required"
done
for value in "${communication_agent_release_id}" "${device_updater_release_id}"; do
    [[ -n "${value}" ]] || fail "all controlled payload inputs are required"
done
[[ "${runtime_sha256}" =~ ^[0-9a-f]{64}$ ]] || fail "runtime SHA-256 is malformed"
for file in "${runtime_archive}" "${runtime_signature}" "${enrollment_env}" "${cellular_env}"; do
    [[ -f "${file}" && ! -L "${file}" ]] || fail "payload input file is unsafe"
done
for directory in "${runtime_trust_directory}" "${mcu_trust_directory}"; do
    [[ -d "${directory}" && ! -L "${directory}" ]] || fail "payload trust directory is unsafe"
done
[[ "$(sha256sum -- "${runtime_archive}" | awk '{print $1}')" = "${runtime_sha256}" ]] \
    || fail "runtime archive digest differs before staging"
expected_uv="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["tools"]["uv"])' "${script_directory}/builder.lock")"
expected_builder_digest="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container"]["digest"])' "${script_directory}/builder.lock")"
[[ "${ECOBIN_BUILDER_DIGEST:-}" = "${expected_builder_digest}" ]] \
    || fail "payload build is not running in the locked builder"
case "$(uv --version)" in
    "uv ${expected_uv}"|"uv ${expected_uv} "*) ;;
    *) fail "uv differs from builder.lock" ;;
esac
git_commit="$(git -c safe.directory="${repository_root}" \
    -C "${repository_root}" rev-parse HEAD)"
[[ "${git_commit}" =~ ^[0-9a-f]{40}$ ]] || fail "repository commit is unavailable"

if [[ "${output_directory}" != /* ]]; then output_directory="${PWD}/${output_directory}"; fi
output_parent="$(dirname -- "${output_directory}")"
output_name="$(basename -- "${output_directory}")"
mkdir -p -- "${output_parent}"
output_parent="$(readlink -f -- "${output_parent}")"
output_directory="${output_parent}/${output_name}"
[[ ! -e "${output_directory}" ]] || fail "payload output already exists"
staging_directory="$(mktemp -d "${output_parent}/.ecobin-software-payload.XXXXXXXX")"
mkdir -m 0755 -- "${staging_directory}/components" "${staging_directory}/config" \
    "${staging_directory}/trust" "${staging_directory}/evidence"
mkdir -m 0755 -- \
    "${staging_directory}/components/communication-agent" \
    "${staging_directory}/components/communication-agent/app" \
    "${staging_directory}/components/device-updater" \
    "${staging_directory}/components/device-updater/app" \
    "${staging_directory}/components/device-updater/helpers" \
    "${staging_directory}/components/device-updater/systemd"

make_locked_venv() {
    local destination="$1"
    local dependency_group="$2"
    uv venv --python /usr/bin/python3.11 --relocatable "${destination}"
    VIRTUAL_ENV="${destination}" UV_PROJECT_ENVIRONMENT="${destination}" \
        PYTHONDONTWRITEBYTECODE=1 \
        uv sync --project "${repository_root}/hardware" --active --frozen \
        --only-group "${dependency_group}" --no-install-project
    python3 "${script_directory}/lib/harden_venv.py" --venv "${destination}"
    "${destination}/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 11)'
}

# Reject a private key, certificate, unsupported name, subdirectory, symlink,
# hard link, empty/oversized key or unsafe mode before any glob, copy or
# signature-verification operation consumes either source trust directory.
python3 "${repository_root}/hardware/system/image_software_installer.py" \
    validate-trust \
    --mcu-trust "${mcu_trust_directory}" \
    --runtime-trust "${runtime_trust_directory}"

tool_venv="${staging_directory}/.verification-venv"
verification_inputs="${staging_directory}/.verification-inputs"
mkdir -m 0700 -- "${verification_inputs}" "${verification_inputs}/trust"
cp -- "${runtime_archive}" "${verification_inputs}/runtime.tar.gz"
cp -- "${runtime_signature}" "${verification_inputs}/runtime.sig"
cp -- "${runtime_trust_directory}/"*.pem "${verification_inputs}/trust/"
chmod 0600 -- "${verification_inputs}/runtime.tar.gz"
chmod 0644 -- "${verification_inputs}/runtime.sig" \
    "${verification_inputs}/trust/"*.pem
make_locked_venv "${tool_venv}" remote-support
"${tool_venv}/bin/python" "${script_directory}/lib/stage_signed_runtime_payload.py" \
    --archive "${verification_inputs}/runtime.tar.gz" \
    --expected-sha256 "${runtime_sha256}" \
    --signature "${verification_inputs}/runtime.sig" \
    --signing-key-id "${runtime_signing_key_id}" \
    --trusted-public-keys-directory "${verification_inputs}/trust" \
    --release-id "${runtime_release_id}" \
    --destination "${staging_directory}/components/hardware-runtime"
rm -rf -- "${tool_venv}" "${verification_inputs}"

# These permanent agents are image components. They deliberately remain
# outside the replaceable hardware/business runtime release.  The OneNet SDK
# lives in a communication-only environment so this rescue path never depends
# on the currently active business release.
for name in cloud_transport.py communication_agent.py communication_credentials.py \
    communication_router.py communication_store.py direct_onenet_transport.py \
    local_control.py onenet_projection_model.json onenet_wire.py trusted_clock.py; do
    install -m 0644 -- "${repository_root}/hardware/${name}" \
        "${staging_directory}/components/communication-agent/app/${name}"
done
for name in device_management_preflight.py local_control.py \
    mcu_firmware_package.py mcu_update_coordinator.py mcu_update_package.py \
    mcu_update_store.py updater_agent.py updater_control_cli.py updater_store.py; do
    install -m 0644 -- "${repository_root}/hardware/${name}" \
        "${staging_directory}/components/device-updater/app/${name}"
done
for name in __init__.py privileged_control.py business_activation_helper.py \
    business_activation_candidate_helper.py business_activation_primitives.py \
    mcu_flash_helper.py mcu_flash_candidate_helper.py mcu_flash_primitives.py \
    mcu_flash_recovery.py updater_mutation_authorizer.py; do
    install -m 0644 -- \
        "${repository_root}/hardware/device_management/helpers/${name}" \
        "${staging_directory}/components/device-updater/helpers/${name}"
done
for name in ecobin-business-activation-helper.socket \
    ecobin-business-activation-helper@.service \
    ecobin-mcu-flash-helper.socket ecobin-mcu-flash-helper@.service \
    ecobin-business-activation-candidate-helper.socket \
    ecobin-business-activation-candidate-helper@.service \
    ecobin-mcu-flash-candidate-helper.socket \
    ecobin-mcu-flash-candidate-helper@.service; do
    install -m 0644 -- \
        "${repository_root}/hardware/device_management/helpers/systemd/${name}" \
        "${staging_directory}/components/device-updater/systemd/${name}"
done

make_locked_venv "${staging_directory}/components/enrollment-venv" enrollment
make_locked_venv "${staging_directory}/components/remote-support-venv" remote-support
make_locked_venv "${staging_directory}/components/factory-test-venv" runtime
make_locked_venv "${staging_directory}/components/communication-agent/.venv" communication
make_locked_venv "${staging_directory}/components/device-updater/.venv" updater
"${staging_directory}/components/enrollment-venv/bin/python" -c 'import cryptography,requests'
"${staging_directory}/components/remote-support-venv/bin/python" -c 'import cryptography'
"${staging_directory}/components/factory-test-venv/bin/python" -c \
    'import cv2,paho.mqtt.client,serial,qcloud_cos'
"${staging_directory}/components/communication-agent/.venv/bin/python" -c \
    'import paho.mqtt.client'
"${staging_directory}/components/device-updater/.venv/bin/python" -c \
    'import cryptography'

cp -- "${enrollment_env}" "${staging_directory}/config/enrollment.env"
cp -- "${cellular_env}" "${staging_directory}/config/cellular.env"
cp -a --no-preserve=ownership -- "${mcu_trust_directory}" \
    "${staging_directory}/trust/mcu-release-keys"
cp -a --no-preserve=ownership -- "${runtime_trust_directory}" \
    "${staging_directory}/trust/runtime-release-keys"
chmod 0600 -- "${staging_directory}/config/"*.env
find "${staging_directory}/trust" -type d -exec chmod 0755 -- {} +
find "${staging_directory}/trust" -type f -exec chmod 0644 -- {} +
signature_sha256="$(sha256sum -- "${runtime_signature}" | awk '{print $1}')"
python3 - "${staging_directory}/evidence/runtime-release-verification.json" \
    "${runtime_release_id}" "${git_commit}" "${runtime_sha256}" \
    "${signature_sha256}" "${runtime_signing_key_id}" <<'PY'
import json
import pathlib
import sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    "schemaVersion": 1,
    "releaseId": sys.argv[2],
    "sourceGitCommit": sys.argv[3],
    "archiveSha256": sys.argv[4],
    "signatureSha256": sys.argv[5],
    "signingKeyId": sys.argv[6],
}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
PY
chmod 0644 -- "${staging_directory}/evidence/runtime-release-verification.json"

python3 "${script_directory}/lib/generate_software_payload_lock.py" \
    --payload-dir "${staging_directory}" --payload-id "${payload_id}" \
    --git-commit "${git_commit}" --hardware-runtime-release-id "${runtime_release_id}" \
    --enrollment-release-id "${enrollment_release_id}" \
    --remote-support-release-id "${remote_release_id}" \
    --factory-test-release-id "${factory_release_id}" \
    --first-boot-release-id "${first_boot_release_id}" \
    --communication-agent-release-id "${communication_agent_release_id}" \
    --device-updater-release-id "${device_updater_release_id}"
payload_sha256="$(sha256sum -- "${staging_directory}/software-payload.lock.json" | awk '{print $1}')"
python3 "${repository_root}/hardware/system/image_software_installer.py" validate-payload \
    --payload "${staging_directory}" --payload-sha256 "${payload_sha256}" \
    --git-commit "${git_commit}"
mv -- "${staging_directory}" "${output_directory}"
staging_directory=""
printf 'software-payload-build=PASS output=%s lock_sha256=%s\n' \
    "${output_directory}" "${payload_sha256}"
