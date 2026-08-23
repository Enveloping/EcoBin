#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
source_artifact=""
output_directory=""
release_id=""
version=""
software_payload=""
software_payload_sha256=""
target_media_qualification_evidence=""
allow_dirty=false

fail() {
    printf 'builder-launch=FAIL: %s\n' "$1" >&2
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)
            [[ $# -ge 2 ]] || fail "--source requires a value"
            source_artifact="$2"
            shift 2
            ;;
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
        --version)
            [[ $# -ge 2 ]] || fail "--version requires a value"
            version="$2"
            shift 2
            ;;
        --software-payload)
            [[ $# -ge 2 ]] || fail "--software-payload requires a value"
            software_payload="$2"
            shift 2
            ;;
        --software-payload-sha256)
            [[ $# -ge 2 ]] || fail "--software-payload-sha256 requires a value"
            software_payload_sha256="$2"
            shift 2
            ;;
        --target-media-qualification-evidence)
            [[ $# -ge 2 ]] || fail "--target-media-qualification-evidence requires a value"
            target_media_qualification_evidence="$2"
            shift 2
            ;;
        --allow-dirty)
            allow_dirty=true
            shift
            ;;
        *)
            fail "unknown argument"
            ;;
    esac
done

for command_name in docker python3 readlink stat; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required launcher command is missing: ${command_name}"
done
python3 "${script_directory}/lib/validate_inputs.py" \
    --config-dir "${script_directory}" --require-locked \
    --target-media-qualification-evidence "${target_media_qualification_evidence}"

[[ -n "${source_artifact}" && -n "${output_directory}" \
    && -n "${release_id}" && -n "${version}" \
    && -n "${software_payload}" && -n "${software_payload_sha256}" \
    && -n "${target_media_qualification_evidence}" ]] \
    || fail "source, output, identities, and controlled software payload are required"
[[ "${software_payload_sha256}" =~ ^[0-9a-f]{64}$ ]] \
    || fail "software payload SHA-256 is malformed"
[[ -f "${source_artifact}" && ! -L "${source_artifact}" ]] \
    || fail "source artifact must be a regular non-symlink file"
source_artifact="$(readlink -f -- "${source_artifact}")"
[[ -f "${target_media_qualification_evidence}" \
    && ! -L "${target_media_qualification_evidence}" ]] \
    || fail "target-media qualification evidence must be a regular non-symlink file"
target_media_qualification_evidence="$(readlink -f -- "${target_media_qualification_evidence}")"
[[ "$(stat -c '%h' -- "${target_media_qualification_evidence}")" = 1 ]] \
    || fail "target-media qualification evidence must have one hard link"
[[ "$(stat -c '%h' -- "${source_artifact}")" = 1 ]] \
    || fail "source artifact must have one hard link"
[[ -d "${software_payload}" && ! -L "${software_payload}" ]] \
    || fail "software payload must be a regular directory"
software_payload="$(readlink -f -- "${software_payload}")"
[[ -f "${software_payload}/software-payload.lock.json" \
    && ! -L "${software_payload}/software-payload.lock.json" ]] \
    || fail "software payload lock is unavailable"

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
    || fail "output directory already exists"

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

source_name="$(
    python3 - "${script_directory}/source.lock.json" <<'PY'
import json
import pathlib
import sys
print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["artifact"]["fileName"])
PY
)"
[[ "$(basename -- "${source_artifact}")" = "${source_name}" ]] \
    || fail "source file name differs from the locked artifact"

docker pull --platform "${builder_platform}" "${builder_reference}" >/dev/null
image_platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' \
    "${builder_reference}")"
[[ "${image_platform}" = "${builder_platform}" ]] \
    || fail "resolved builder platform differs from the lock"

container_arguments=(
    run --rm --privileged
    --platform "${builder_platform}"
    --env "ECOBIN_BUILDER_DIGEST=${builder_digest}"
    --env "SOURCE_DATE_EPOCH=${source_date_epoch}"
    --mount "type=bind,src=${repository_root},dst=/workspace,readonly"
    --mount "type=bind,src=${source_artifact},dst=/input/${source_name},readonly"
    --mount "type=bind,src=${target_media_qualification_evidence},dst=/input/target-media-qualification-evidence.json,readonly"
    --mount "type=bind,src=${software_payload},dst=/software-payload,readonly"
    --mount "type=bind,src=${output_parent},dst=/output"
    "${builder_reference}"
    bash /workspace/tools/orangepi-image/bootstrap-builder.sh
    --config-dir /workspace/tools/orangepi-image
    --source "/input/${source_name}"
    --output-dir "/output/${output_name}"
    --release-id "${release_id}"
    --version "${version}"
    --software-payload /software-payload
    --software-payload-sha256 "${software_payload_sha256}"
    --target-media-qualification-evidence /input/target-media-qualification-evidence.json
)
if [[ "${allow_dirty}" = true ]]; then
    container_arguments+=(--allow-dirty)
fi
docker "${container_arguments[@]}"
