#!/usr/bin/env bash
set -euo pipefail
umask 077

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../.." && pwd)"
config_directory="${script_directory}"
source_artifact=""
output_directory=""
release_id=""
version=""
software_payload=""
software_payload_sha256=""
target_media_qualification_evidence=""
validate_only=false
allow_dirty=false
staging_directory=""

fail() {
    printf 'image-build=FAIL: %s\n' "$1" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  build-image.sh --validate-only [--config-dir DIR]
  build-image.sh --source FILE --output-dir DIR --release-id ID --version X.Y.Z
                 --software-payload DIR --software-payload-sha256 HEX
                 --target-media-qualification-evidence FILE
                 [--config-dir DIR] [--allow-dirty]

The real build requires LOCKED inputs and ECOBIN_BUILDER_DIGEST equal to the
digest in builder.lock. The output directory must not already exist.
EOF
}

cleanup() {
    if [[ -n "${staging_directory}" && -d "${staging_directory}" ]]; then
        case "$(basename -- "${staging_directory}")" in
            .ecobin-orange-image.*)
                rm -rf -- "${staging_directory}"
                ;;
            *)
                printf 'refusing to clean unexpected staging path: %s\n' \
                    "${staging_directory}" >&2
                ;;
        esac
    fi
}
trap cleanup EXIT

json_value() {
    local json_file="$1"
    local dotted_path="$2"
    python3 - "${json_file}" "${dotted_path}" <<'PY'
import json
import pathlib
import sys

value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
for part in sys.argv[2].split("."):
    value = value[part]
if isinstance(value, bool):
    print(str(value).lower())
elif value is None:
    print("null")
else:
    print(value)
PY
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config-dir)
            [[ $# -ge 2 ]] || fail "--config-dir requires a value"
            config_directory="$2"
            shift 2
            ;;
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
        --validate-only)
            validate_only=true
            shift
            ;;
        --allow-dirty)
            allow_dirty=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown argument: $1"
            ;;
    esac
done

command -v python3 >/dev/null 2>&1 || fail "Python 3 is required"
validation_arguments=(--config-dir "${config_directory}" --require-locked)
if [[ -n "${target_media_qualification_evidence}" ]]; then
    validation_arguments+=(--target-media-qualification-evidence "${target_media_qualification_evidence}")
fi
python3 "${script_directory}/lib/validate_inputs.py" "${validation_arguments[@]}"

if [[ "${validate_only}" = true ]]; then
    [[ -z "${source_artifact}${output_directory}${release_id}${version}${software_payload}${software_payload_sha256}" ]] \
        || fail "--validate-only cannot be combined with build arguments"
    printf 'image-build-validation=PASS\n'
    exit 0
fi

[[ -n "${source_artifact}" && -n "${output_directory}" \
    && -n "${release_id}" && -n "${version}" \
    && -n "${software_payload}" && -n "${software_payload_sha256}" \
    && -n "${target_media_qualification_evidence}" ]] \
    || fail "source, output, identities, and controlled software payload are required"
[[ "${software_payload_sha256}" =~ ^[0-9a-f]{64}$ ]] \
    || fail "software payload SHA-256 is malformed"
[[ "${release_id}" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] \
    || fail "release ID has an invalid format"
[[ "${version}" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] \
    || fail "version must use semantic form x.y.z"
[[ "$(id -u)" = 0 ]] \
    || fail "real image build requires root for read-only post-build inspection"

for command_name in git sha256sum stat; do
    command -v "${command_name}" >/dev/null 2>&1 \
        || fail "required command is missing: ${command_name}"
done

[[ -f "${source_artifact}" && ! -L "${source_artifact}" ]] \
    || fail "source artifact must be a regular non-symlink file"
[[ -f "${target_media_qualification_evidence}" \
    && ! -L "${target_media_qualification_evidence}" \
    && "$(stat -c '%h' -- "${target_media_qualification_evidence}")" = 1 ]] \
    || fail "target-media qualification evidence must be a regular one-link file"
config_directory="$(readlink -f -- "${config_directory}")"
source_artifact="$(readlink -f -- "${source_artifact}")"
target_media_qualification_evidence="$(readlink -f -- "${target_media_qualification_evidence}")"
export ECOBIN_TARGET_MEDIA_QUALIFICATION_EVIDENCE="${target_media_qualification_evidence}"
[[ -d "${software_payload}" && ! -L "${software_payload}" ]] \
    || fail "software payload must be a regular directory"
software_payload="$(readlink -f -- "${software_payload}")"

expected_builder_digest="$(json_value "${config_directory}/builder.lock" \
    container.digest)"
[[ "${ECOBIN_BUILDER_DIGEST:-}" = "${expected_builder_digest}" ]] \
    || fail "ECOBIN_BUILDER_DIGEST does not identify the locked builder"

expected_name="$(json_value "${config_directory}/source.lock.json" \
    artifact.fileName)"
expected_download_bytes="$(json_value "${config_directory}/source.lock.json" \
    artifact.downloadBytes)"
expected_source_sha="$(json_value "${config_directory}/source.lock.json" \
    artifact.sha256)"
archive_format="$(json_value "${config_directory}/source.lock.json" \
    artifact.archiveFormat)"
image_member="$(json_value "${config_directory}/source.lock.json" \
    artifact.imageMember)"
expected_image_bytes="$(json_value "${config_directory}/source.lock.json" \
    artifact.extractedImageBytes)"
expected_image_sha="$(json_value "${config_directory}/source.lock.json" \
    artifact.extractedImageSha256)"
layout_image_bytes="$(json_value "${config_directory}/image-layout.json" \
    compactImage.fixedRawImageBytes)"
source_date_epoch="$(json_value "${config_directory}/builder.lock" \
    sourceDateEpoch)"

[[ "$(basename -- "${source_artifact}")" = "${expected_name}" ]] \
    || fail "source artifact file name does not match source.lock.json"
actual_download_bytes="$(stat -c '%s' -- "${source_artifact}")"
[[ "${actual_download_bytes}" = "${expected_download_bytes}" ]] \
    || fail "source artifact byte length does not match source.lock.json"
actual_source_sha="$(sha256sum -- "${source_artifact}" | awk '{print $1}')"
[[ "${actual_source_sha}" = "${expected_source_sha}" ]] \
    || fail "source artifact SHA-256 does not match source.lock.json"
[[ "${expected_image_bytes}" = "${layout_image_bytes}" ]] \
    || fail "source and layout raw image sizes disagree"
[[ -n "${ECOBIN_TARGET_DEB_DIRECTORY:-}" \
    && -d "${ECOBIN_TARGET_DEB_DIRECTORY}" \
    && ! -L "${ECOBIN_TARGET_DEB_DIRECTORY}" ]] \
    || fail "locked offline target deb directory is unavailable"

git_commit="$(git -c safe.directory="${repository_root}" \
    -C "${repository_root}" rev-parse HEAD)"
[[ "${git_commit}" =~ ^[0-9a-f]{40}$ ]] \
    || fail "cannot resolve repository Git commit"
python3 "${repository_root}/hardware/system/image_software_installer.py" \
    validate-payload \
    --payload "${software_payload}" \
    --payload-sha256 "${software_payload_sha256}" \
    --git-commit "${git_commit}"
dirty_output="$(git -c safe.directory="${repository_root}" \
    -C "${repository_root}" status --porcelain \
    --untracked-files=normal)"
source_dirty=false
if [[ -n "${dirty_output}" ]]; then
    source_dirty=true
    [[ "${allow_dirty}" = true ]] \
        || fail "worktree is dirty; commit it or use --allow-dirty for a trial only"
fi

if [[ "${output_directory}" != /* ]]; then
    output_directory="${PWD}/${output_directory}"
fi
output_parent="$(dirname -- "${output_directory}")"
output_name="$(basename -- "${output_directory}")"
[[ "${output_name}" != . && "${output_name}" != .. && -n "${output_name}" ]] \
    || fail "output directory has an unsafe name"
mkdir -p -- "${output_parent}"
output_parent="$(readlink -f -- "${output_parent}")"
output_directory="${output_parent}/${output_name}"
[[ ! -e "${output_directory}" ]] \
    || fail "output directory already exists and will not be overwritten"
staging_directory="$(mktemp -d \
    "${output_parent}/.ecobin-orange-image.XXXXXXXX")"

image_name="ecobin-orangepi-zero3-${version}.img"
candidate_image="${staging_directory}/${image_name}"
work_directory="${staging_directory}/.rootfs-rebuild"
mkdir -- "${work_directory}"
working_image="${work_directory}/source-working.img"
case "${archive_format}" in
    raw)
        cp --sparse=always --reflink=auto -- "${source_artifact}" \
            "${working_image}"
        ;;
    gzip)
        command -v gzip >/dev/null 2>&1 || fail "gzip is required"
        gzip -dc -- "${source_artifact}" > "${working_image}"
        ;;
    xz)
        command -v xz >/dev/null 2>&1 || fail "xz is required"
        xz -dc -- "${source_artifact}" > "${working_image}"
        ;;
    zstd)
        command -v zstd >/dev/null 2>&1 || fail "zstd is required"
        zstd -q -dc -- "${source_artifact}" > "${working_image}"
        ;;
    7z)
        command -v 7z >/dev/null 2>&1 || fail "7z is required"
        [[ "${image_member}" != null ]] \
            || fail "7z source lock must name the exact image member"
        7z x -bd -y -so -- "${source_artifact}" "${image_member}" \
            > "${working_image}"
        ;;
    *)
        fail "unsupported locked archive format: ${archive_format}"
        ;;
esac

actual_image_bytes="$(stat -c '%s' -- "${working_image}")"
[[ "${actual_image_bytes}" = "${expected_image_bytes}" ]] \
    || fail "extracted image byte length does not match locked input"
actual_image_sha="$(sha256sum -- "${working_image}" | awk '{print $1}')"
[[ "${actual_image_sha}" = "${expected_image_sha}" ]] \
    || fail "extracted raw image SHA-256 does not match source.lock.json"
[[ ! "${working_image}" -ef "${source_artifact}" ]] \
    || fail "working image must be a copied file, not the source artifact"
[[ "$(stat -c '%h' -- "${working_image}")" = 1 ]] \
    || fail "working image must not share a hard link with another path"
python3 "${script_directory}/lib/verify_raw_layout.py" \
    --image "${working_image}" \
    --layout "${config_directory}/image-layout.json" \
    --require-source-sha

bash "${script_directory}/sanitize-candidate.sh" \
    --config-dir "${config_directory}" \
    --image "${working_image}" \
    --target-deb-dir "${ECOBIN_TARGET_DEB_DIRECTORY}" \
    --software-payload "${software_payload}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --release-id "${release_id}" \
    --version "${version}" \
    --git-commit "${git_commit}"
rebuilt_partition="${work_directory}/root.partition.img"
bash "${script_directory}/rebuild-rootfs.sh" \
    --config-dir "${config_directory}" \
    --source-image "${working_image}" \
    --output-partition "${rebuilt_partition}" \
    --working-directory "${work_directory}"
python3 "${script_directory}/lib/assemble_raw_image.py" \
    --source-image "${working_image}" \
    --root-partition "${rebuilt_partition}" \
    --output "${candidate_image}" \
    --layout "${config_directory}/image-layout.json"
python3 "${script_directory}/lib/verify_raw_layout.py" \
    --image "${candidate_image}" --layout "${config_directory}/image-layout.json"
touch -d "@${source_date_epoch}" -- "${candidate_image}"

bash "${script_directory}/verify-image.sh" --candidate \
    --config-dir "${config_directory}" --image "${candidate_image}" \
    --software-payload "${software_payload}" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --release-id "${release_id}" \
    --version "${version}" \
    --git-commit "${git_commit}"

mkdir -- "${staging_directory}/locks" "${staging_directory}/schemas"
cp -- "${config_directory}/source.lock.json" \
    "${config_directory}/builder.lock" \
    "${config_directory}/apt-packages.lock" \
    "${config_directory}/image-layout.json" \
    "${staging_directory}/locks/"
cp -- "${software_payload}/software-payload.lock.json" \
    "${staging_directory}/locks/software-payload.lock.json"
cp -- "${config_directory}/schemas/image-manifest.schema.json" \
    "${config_directory}/schemas/software-payload-lock.schema.json" \
    "${config_directory}/schemas/target-media-qualification-evidence.schema.json" \
    "${staging_directory}/schemas/"
cp -- "${target_media_qualification_evidence}" \
    "${staging_directory}/target-media-qualification-evidence.json"

manifest_path="${staging_directory}/image-manifest.json"
python3 "${script_directory}/lib/generate_manifest.py" \
    --config-dir "${config_directory}" \
    --image "${candidate_image}" \
    --output "${manifest_path}" \
    --release-id "${release_id}" \
    --version "${version}" \
    --git-commit "${git_commit}" \
    --source-dirty "${source_dirty}" \
    --software-payload-lock "${software_payload}/software-payload.lock.json" \
    --software-payload-sha256 "${software_payload_sha256}" \
    --rootfs-deterministic true \
    --target-media-qualification-evidence "${target_media_qualification_evidence}"

(
    cd "${staging_directory}"
    sha256sum -- "${image_name}" > "${image_name}.sha256"
)
touch -d "@${source_date_epoch}" -- \
    "${manifest_path}" "${candidate_image}.sha256" \
    "${staging_directory}/target-media-qualification-evidence.json" \
    "${staging_directory}/locks/"* "${staging_directory}/schemas/"*
rm -rf -- "${work_directory}"

mv -- "${staging_directory}" "${output_directory}"
staging_directory=""
printf 'image-build=PASS output=%s artifact_class=UNSIGNED_NO_SECRET_CANDIDATE\n' \
    "${output_directory}"
