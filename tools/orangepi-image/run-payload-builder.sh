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

fail() { printf 'payload-builder-launch=FAIL: %s\n' "$1" >&2; exit 1; }
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
        *) fail "unknown or incomplete argument: $1" ;;
    esac
done
for command_name in docker git python3 readlink stat; do
    command -v "${command_name}" >/dev/null 2>&1 || fail "missing command: ${command_name}"
done
for value in "${output_directory}" "${runtime_archive}" "${runtime_sha256}" \
    "${runtime_signature}" "${runtime_signing_key_id}" "${runtime_trust_directory}" \
    "${mcu_trust_directory}" "${enrollment_env}" "${cellular_env}" \
    "${payload_id}" "${runtime_release_id}" "${enrollment_release_id}" \
    "${remote_release_id}" "${factory_release_id}" "${first_boot_release_id}"; do
    [[ -n "${value}" ]] || fail "all payload builder inputs are required"
done
[[ "${runtime_sha256}" =~ ^[0-9a-f]{64}$ ]] || fail "runtime SHA-256 is malformed"
for variable_name in runtime_archive runtime_signature enrollment_env cellular_env; do
    value="${!variable_name}"
    [[ -f "${value}" && ! -L "${value}" && "$(stat -c '%h' -- "${value}")" = 1 ]] \
        || fail "payload input file is unsafe"
    printf -v "${variable_name}" '%s' "$(readlink -f -- "${value}")"
done
for variable_name in runtime_trust_directory mcu_trust_directory; do
    value="${!variable_name}"
    [[ -d "${value}" && ! -L "${value}" ]] || fail "payload trust directory is unsafe"
    printf -v "${variable_name}" '%s' "$(readlink -f -- "${value}")"
done
if ! repository_status="$(git -C "${repository_root}" status \
    --porcelain --untracked-files=normal)"; then
    fail "repository status could not be verified"
fi
[[ -z "${repository_status}" ]] \
    || fail "formal software payloads require a clean repository"
if [[ "${output_directory}" != /* ]]; then output_directory="${PWD}/${output_directory}"; fi
output_parent="$(dirname -- "${output_directory}")"
output_name="$(basename -- "${output_directory}")"
mkdir -p -- "${output_parent}"
output_parent="$(readlink -f -- "${output_parent}")"
[[ ! -e "${output_parent}/${output_name}" ]] || fail "payload output already exists"

readarray -t identity < <(python3 - "${script_directory}/builder.lock" <<'PY'
import json, pathlib, sys
d=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(d["container"]["reference"]); print(d["container"]["digest"])
print(d["container"]["platform"]); print(d["sourceDateEpoch"])
PY
)
[[ "${#identity[@]}" = 4 ]] || fail "builder identity is incomplete"
builder_reference="${identity[0]}"
builder_digest="${identity[1]}"
builder_platform="${identity[2]}"
source_date_epoch="${identity[3]}"
docker pull --platform "${builder_platform}" "${builder_reference}" >/dev/null
image_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' \
    "${builder_reference}")"
[[ "${image_platform}" = "${builder_platform}" ]] \
    || fail "resolved builder platform differs from builder.lock"

docker run --rm --platform "${builder_platform}" \
    --env "ECOBIN_BUILDER_DIGEST=${builder_digest}" \
    --env "SOURCE_DATE_EPOCH=${source_date_epoch}" \
    --mount "type=bind,src=${repository_root},dst=/workspace,readonly" \
    --mount "type=bind,src=${runtime_archive},dst=/payload-input/runtime.tar.gz,readonly" \
    --mount "type=bind,src=${runtime_signature},dst=/payload-input/runtime.sig,readonly" \
    --mount "type=bind,src=${runtime_trust_directory},dst=/payload-input/runtime-trust,readonly" \
    --mount "type=bind,src=${mcu_trust_directory},dst=/payload-input/mcu-trust,readonly" \
    --mount "type=bind,src=${enrollment_env},dst=/payload-input/enrollment.env,readonly" \
    --mount "type=bind,src=${cellular_env},dst=/payload-input/cellular.env,readonly" \
    --mount "type=bind,src=${output_parent},dst=/output" \
    "${builder_reference}" \
    bash /workspace/tools/orangepi-image/bootstrap-builder.sh --software-payload-only \
    --output-dir "/output/${output_name}" \
    --runtime-archive /payload-input/runtime.tar.gz \
    --runtime-sha256 "${runtime_sha256}" \
    --runtime-signature /payload-input/runtime.sig \
    --runtime-signing-key-id "${runtime_signing_key_id}" \
    --runtime-trust-dir /payload-input/runtime-trust \
    --mcu-trust-dir /payload-input/mcu-trust \
    --enrollment-env /payload-input/enrollment.env \
    --cellular-env /payload-input/cellular.env \
    --payload-id "${payload_id}" \
    --runtime-release-id "${runtime_release_id}" \
    --enrollment-release-id "${enrollment_release_id}" \
    --remote-support-release-id "${remote_release_id}" \
    --factory-test-release-id "${factory_release_id}" \
    --first-boot-release-id "${first_boot_release_id}"
