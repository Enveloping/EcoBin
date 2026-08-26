#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
output_directory=""
release_id=""
signing_private_key=""

fail() {
    printf 'runtime-builder-launch=FAIL: %s\n' "$1" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            [[ $# -ge 2 ]] || fail "--output-dir requires a value"
            output_directory="$2"
            shift 2
            ;;
        --release-id)
            [[ $# -ge 2 ]] || fail "--release-id requires a value"
            release_id="$2"
            shift 2
            ;;
        --signing-private-key)
            [[ $# -ge 2 ]] || fail "--signing-private-key requires a value"
            signing_private_key="$2"
            shift 2
            ;;
        *)
            fail "unknown argument"
            ;;
    esac
done

for command_name in docker git python3 readlink stat; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required launcher command is missing: ${command_name}"
done
[[ -n "${output_directory}" && -n "${release_id}" \
    && -n "${signing_private_key}" ]] \
    || fail "output directory, release ID, and signing private key are required"
[[ -f "${signing_private_key}" && ! -L "${signing_private_key}" ]] \
    || fail "signing private key must be a regular non-symlink file"
[[ "$(stat -c '%h' -- "${signing_private_key}")" = 1 ]] \
    || fail "signing private key must have one hard link"
private_key_mode="$(stat -c '%a' -- "${signing_private_key}")"
[[ "${private_key_mode}" =~ ^[0-7]{3,4}$ ]] \
    || fail "signing private key permissions are unsafe"
(( (8#${private_key_mode} & 077) == 0 )) \
    || fail "signing private key permissions are unsafe"
signing_private_key="$(readlink -f -- "${signing_private_key}")"

[[ -z "$(git -C "${repository_root}" status --porcelain --untracked-files=normal)" ]] \
    || fail "formal runtime releases require a clean repository"
if [[ "${output_directory}" != /* ]]; then
    output_directory="${PWD}/${output_directory}"
fi
output_parent="$(dirname -- "${output_directory}")"
output_name="$(basename -- "${output_directory}")"
[[ -n "${output_name}" && "${output_name}" != . && "${output_name}" != .. ]] \
    || fail "output directory name is unsafe"
mkdir -p -- "${output_parent}"
output_parent="$(readlink -f -- "${output_parent}")"
[[ ! -e "${output_parent}/${output_name}" ]] \
    || fail "runtime output directory already exists"

readarray -t builder_identity < <(
    python3 - "${script_directory}/builder.lock" <<'PY'
import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
print(document["container"]["reference"])
print(document["container"]["digest"])
print(document["container"]["platform"])
print(document["sourceDateEpoch"])
PY
)
[[ "${#builder_identity[@]}" = 4 ]] || fail "builder identity is incomplete"
builder_reference="${builder_identity[0]}"
builder_digest="${builder_identity[1]}"
builder_platform="${builder_identity[2]}"
source_date_epoch="${builder_identity[3]}"

docker pull --platform "${builder_platform}" "${builder_reference}" >/dev/null
image_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' \
    "${builder_reference}")"
[[ "${image_platform}" = "${builder_platform}" ]] \
    || fail "resolved builder platform differs from builder.lock"

docker run --rm --platform "${builder_platform}" \
    --env "ECOBIN_BUILDER_DIGEST=${builder_digest}" \
    --env "SOURCE_DATE_EPOCH=${source_date_epoch}" \
    --mount "type=bind,src=${repository_root},dst=/workspace,readonly" \
    --mount "type=bind,src=${signing_private_key},dst=/runtime-input/signing-private.pem,readonly" \
    --mount "type=bind,src=${output_parent},dst=/output" \
    "${builder_reference}" \
    bash /workspace/tools/orangepi-image/bootstrap-builder.sh --runtime-release-only \
    --release-id "${release_id}" \
    --source-root /workspace/hardware \
    --output-directory "/output/${output_name}" \
    --signing-private-key /runtime-input/signing-private.pem
