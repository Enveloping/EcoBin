#!/usr/bin/env bash
set -euo pipefail

release_input="${1:-}"
deployment_env="${ECOBIN_DEPLOYMENT_ENV_FILE:-/etc/ecobin/deployment.env}"
release_store="${ECOBIN_RELEASE_STORE:-/var/lib/ecobin/releases}"
pull_base_images="${ECOBIN_PULL_RUNTIME_BASE_IMAGES:-true}"
backend_base_image="${ECOBIN_BACKEND_BASE_IMAGE:-eclipse-temurin:21-jre}"
web_base_image="${ECOBIN_WEB_BASE_IMAGE:-nginx:alpine}"
allow_dirty_release="${ECOBIN_ALLOW_DIRTY_RELEASE:-false}"
incoming_directory=""
deployment_temp=""
images_temp=""
previous_temp=""

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

cleanup() {
    if [[ -n "${incoming_directory}" && -d "${incoming_directory}" ]]; then
        case "${incoming_directory}" in
            "${release_store}"/.incoming-*)
                rm -rf -- "${incoming_directory}"
                ;;
            *)
                printf 'refusing to remove unexpected temporary directory: %s\n' \
                    "${incoming_directory}" >&2
                ;;
        esac
    fi
    if [[ -n "${deployment_temp}" && -f "${deployment_temp}" ]]; then
        rm -f -- "${deployment_temp}"
    fi
    if [[ -n "${images_temp}" && -f "${images_temp}" ]]; then
        rm -f -- "${images_temp}"
    fi
    if [[ -n "${previous_temp}" && -f "${previous_temp}" ]]; then
        rm -f -- "${previous_temp}"
    fi
}
trap cleanup EXIT

manifest_value() {
    local file="$1"
    local key="$2"
    local count
    local value

    count="$(grep -Ec "^${key}=" "${file}" || true)"
    [[ "${count}" = 1 ]] \
        || fail "${file} must contain exactly one ${key} assignment"
    value="$(sed -n "s/^${key}=//p" "${file}")"
    [[ -n "${value}" ]] || fail "${key} must not be empty"
    printf '%s' "${value}"
}

require_root_controlled_file() {
    local file="$1"
    local uid
    local mode

    [[ -f "${file}" && ! -L "${file}" ]] \
        || fail "missing protected file: ${file}"
    uid="$(stat -c '%u' "${file}")"
    mode="$(stat -c '%a' "${file}")"
    [[ "${uid}" = 0 ]] || fail "${file} must be owned by root"
    (( (0${mode} & 0022) == 0 )) \
        || fail "${file} must not be group/world writable"
    if grep -q $'\r' "${file}"; then
        fail "${file} contains CRLF line endings"
    fi
}

set_env_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    local count

    count="$(grep -Ec "^${key}=" "${file}" || true)"
    [[ "${count}" = 0 || "${count}" = 1 ]] \
        || fail "${file} contains duplicate ${key} assignments"
    if [[ "${count}" = 1 ]]; then
        sed -i "s|^${key}=.*$|${key}=${value}|" "${file}"
    else
        printf '%s=%s\n' "${key}" "${value}" >> "${file}"
    fi
}

[[ "$(id -u)" = 0 ]] || fail "local release installation must run as root"
[[ $# = 1 ]] \
    || fail "usage: ecobin-install-local-release RELEASE_DIRECTORY"
[[ -n "${release_input}" ]] || fail "release directory is required"
command -v docker >/dev/null 2>&1 || fail "docker is required"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum is required"

release_directory="$(readlink -f -- "${release_input}")"
[[ -d "${release_directory}" && ! -L "${release_input}" ]] \
    || fail "release directory is missing or is a symbolic link"
if [[ -n "$(find -P "${release_directory}" -xdev -mindepth 1 \
    ! -type d ! -type f -print -quit)" ]]; then
    fail "release directory contains a link or special file"
fi

manifest_file="${release_directory}/manifest.env"
checksum_file="${release_directory}/SHA256SUMS"
[[ -f "${manifest_file}" && ! -L "${manifest_file}" ]] \
    || fail "release manifest is missing"
[[ -f "${checksum_file}" && ! -L "${checksum_file}" ]] \
    || fail "release checksum manifest is missing"
if grep -q $'\r' "${manifest_file}" "${checksum_file}"; then
    fail "release metadata must use LF line endings"
fi

while IFS= read -r checksum_line; do
    [[ "${checksum_line}" =~ ^[0-9a-f]{64}\ \ .+ ]] \
        || fail "invalid SHA256SUMS entry"
    checksum_path="${checksum_line:66}"
    [[ "${checksum_path}" != /* \
        && "/${checksum_path}/" != *"/../"* ]] \
        || fail "unsafe SHA256SUMS path"
done < "${checksum_file}"

if ! diff --brief \
    <(awk '{print substr($0, 67)}' "${checksum_file}" \
        | sed 's|^\./||' \
        | LC_ALL=C sort) \
    <(cd "${release_directory}" \
        && find . -type f ! -path './SHA256SUMS' -printf '%P\n' \
        | LC_ALL=C sort) >/dev/null; then
    fail "SHA256SUMS must cover every release file exactly once"
fi

(
    cd "${release_directory}"
    sha256sum --check --strict SHA256SUMS >/dev/null
) || fail "release bundle checksum verification failed"

format_version="$(manifest_value "${manifest_file}" \
    ECOBIN_RELEASE_FORMAT_VERSION)"
release_id="$(manifest_value "${manifest_file}" ECOBIN_RELEASE_ID)"
git_commit="$(manifest_value "${manifest_file}" ECOBIN_GIT_COMMIT)"
source_dirty="$(manifest_value "${manifest_file}" ECOBIN_SOURCE_DIRTY)"

[[ "${format_version}" = 1 ]] || fail "unsupported release bundle format"
[[ "${release_id}" =~ ^[a-z0-9][a-z0-9._-]{0,63}$ ]] \
    || fail "invalid release ID"
[[ "${git_commit}" =~ ^[0-9a-f]{40}$ ]] \
    || fail "invalid release Git commit"
[[ "${source_dirty}" = true || "${source_dirty}" = false ]] \
    || fail "invalid source dirty marker"
if [[ "${source_dirty}" = true && "${allow_dirty_release}" != true ]]; then
    fail "dirty-source release is blocked; build from a clean commit"
fi
[[ "$(basename -- "${release_directory}")" = \
    "ecobin-release-${release_id}" ]] \
    || fail "release directory name does not match its manifest"

[[ -s "${release_directory}/backend/app.jar" ]] \
    || fail "backend app.jar is missing"
[[ -f "${release_directory}/backend/Dockerfile" ]] \
    || fail "backend runtime Dockerfile is missing"
[[ -f "${release_directory}/backend/backend-healthcheck.sh" ]] \
    || fail "backend healthcheck is missing"
[[ -s "${release_directory}/web/dist/index.html" ]] \
    || fail "Web dist/index.html is missing"
[[ -f "${release_directory}/web/Dockerfile" ]] \
    || fail "Web runtime Dockerfile is missing"
[[ -f "${release_directory}/web/nginx.conf" ]] \
    || fail "Web nginx.conf is missing"

install -d -o root -g root -m 0750 "${release_store}"
release_store="$(readlink -f -- "${release_store}")"
release_destination="${release_store}/${release_id}"
if [[ -e "${release_destination}" ]]; then
    [[ -d "${release_destination}" && ! -L "${release_destination}" ]] \
        || fail "existing release destination is not a regular directory"
    cmp --silent "${manifest_file}" \
        "${release_destination}/manifest.env" \
        || fail "existing release ID has different metadata"
    cmp --silent "${checksum_file}" \
        "${release_destination}/SHA256SUMS" \
        || fail "existing release ID has different checksums"
    (
        cd "${release_destination}"
        sha256sum --check --strict SHA256SUMS >/dev/null
    ) || fail "stored release checksum verification failed"
else
    incoming_directory="$(mktemp -d \
        "${release_store}/.incoming-${release_id}.XXXXXX")"
    cp -a -- "${release_directory}/." "${incoming_directory}/"
    chown -R root:root "${incoming_directory}"
    find "${incoming_directory}" -type d -exec chmod 0750 {} +
    find "${incoming_directory}" -type f -exec chmod 0640 {} +
    (
        cd "${incoming_directory}"
        sha256sum --check --strict SHA256SUMS >/dev/null
    ) || fail "copied release checksum verification failed"
    mv -- "${incoming_directory}" "${release_destination}"
    incoming_directory=""
fi

backend_image="ecobin-local/backend:${release_id}"
web_image="ecobin-local/web:${release_id}"
images_record="${release_destination}/images.env"
if [[ -f "${images_record}" ]]; then
    require_root_controlled_file "${images_record}"
    [[ "$(manifest_value "${images_record}" ECOBIN_IMAGE_MODE)" = local ]] \
        || fail "stored image mode mismatch"
    [[ "$(manifest_value "${images_record}" ECOBIN_RELEASE_ID)" = \
        "${release_id}" ]] || fail "stored release ID mismatch"
    [[ "$(manifest_value "${images_record}" \
        ECOBIN_RELEASE_GIT_COMMIT)" = "${git_commit}" ]] \
        || fail "stored release Git commit mismatch"
    [[ "$(manifest_value "${images_record}" ECOBIN_BACKEND_IMAGE)" = \
        "${backend_image}" ]] || fail "stored backend image tag mismatch"
    [[ "$(manifest_value "${images_record}" ECOBIN_WEB_IMAGE)" = \
        "${web_image}" ]] || fail "stored Web image tag mismatch"
    backend_image_id="$(manifest_value "${images_record}" \
        ECOBIN_BACKEND_IMAGE_ID)"
    web_image_id="$(manifest_value "${images_record}" ECOBIN_WEB_IMAGE_ID)"
    [[ "$(docker image inspect --format '{{.Id}}' \
        "${backend_image}" 2>/dev/null)" = "${backend_image_id}" ]] \
        || fail "stored backend image is missing or its tag was changed"
    [[ "$(docker image inspect --format '{{.Id}}' \
        "${web_image}" 2>/dev/null)" = "${web_image_id}" ]] \
        || fail "stored Web image is missing or its tag was changed"
else
    backend_artifact_sha="$(sha256sum \
        "${release_destination}/backend/app.jar" | awk '{print $1}')"
    web_artifact_sha="$({
        cd "${release_destination}/web/dist"
        find . -type f -print0 \
            | LC_ALL=C sort -z \
            | xargs -0 sha256sum
    } | sha256sum | awk '{print $1}')"

    case "${pull_base_images}" in
        true) build_pull_args=(--pull) ;;
        false) build_pull_args=() ;;
        *) fail "ECOBIN_PULL_RUNTIME_BASE_IMAGES must be true or false" ;;
    esac

    docker build "${build_pull_args[@]}" \
        --file "${release_destination}/backend/Dockerfile" \
        --tag "${backend_image}" \
        --build-arg "ECOBIN_BACKEND_BASE_IMAGE=${backend_base_image}" \
        --build-arg "ECOBIN_RELEASE_ID=${release_id}" \
        --build-arg "ECOBIN_GIT_COMMIT=${git_commit}" \
        --build-arg "ECOBIN_ARTIFACT_SHA256=${backend_artifact_sha}" \
        "${release_destination}/backend"

    docker build "${build_pull_args[@]}" \
        --file "${release_destination}/web/Dockerfile" \
        --tag "${web_image}" \
        --build-arg "ECOBIN_WEB_BASE_IMAGE=${web_base_image}" \
        --build-arg "ECOBIN_RELEASE_ID=${release_id}" \
        --build-arg "ECOBIN_GIT_COMMIT=${git_commit}" \
        --build-arg "ECOBIN_ARTIFACT_SHA256=${web_artifact_sha}" \
        "${release_destination}/web"

    backend_image_id="$(docker image inspect \
        --format '{{.Id}}' "${backend_image}")"
    web_image_id="$(docker image inspect \
        --format '{{.Id}}' "${web_image}")"
    [[ "${backend_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || fail "invalid backend image ID"
    [[ "${web_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || fail "invalid Web image ID"

    images_temp="$(mktemp "${release_destination}/.images.env.XXXXXX")"
    {
        printf 'ECOBIN_IMAGE_MODE=local\n'
        printf 'ECOBIN_RELEASE_ID=%s\n' "${release_id}"
        printf 'ECOBIN_RELEASE_GIT_COMMIT=%s\n' "${git_commit}"
        printf 'ECOBIN_BACKEND_IMAGE=%s\n' "${backend_image}"
        printf 'ECOBIN_BACKEND_IMAGE_ID=%s\n' "${backend_image_id}"
        printf 'ECOBIN_WEB_IMAGE=%s\n' "${web_image}"
        printf 'ECOBIN_WEB_IMAGE_ID=%s\n' "${web_image_id}"
    } > "${images_temp}"
    chown root:root "${images_temp}"
    chmod 0640 "${images_temp}"
    mv -- "${images_temp}" "${images_record}"
    images_temp=""
fi

[[ "${backend_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "invalid backend image ID"
[[ "${web_image_id}" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || fail "invalid Web image ID"

for image in "${backend_image}" "${web_image}"; do
    [[ "$(docker image inspect --format \
        '{{ index .Config.Labels "org.opencontainers.image.version" }}' \
        "${image}")" = "${release_id}" ]] \
        || fail "image release label mismatch: ${image}"
    [[ "$(docker image inspect --format \
        '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
        "${image}")" = "${git_commit}" ]] \
        || fail "image revision label mismatch: ${image}"
done

require_root_controlled_file "${deployment_env}"
deployment_directory="$(dirname -- "${deployment_env}")"
current_release_id="$(sed -n 's/^ECOBIN_RELEASE_ID=//p' \
    "${deployment_env}" | tail -n 1)"
if [[ "${current_release_id}" != "${release_id}" ]]; then
    previous_temp="$(mktemp \
        "${deployment_directory}/.deployment.env.previous.XXXXXX")"
    install -o root -g root -m 0600 \
        "${deployment_env}" "${previous_temp}"
    mv -f -- "${previous_temp}" "${deployment_env}.previous"
    previous_temp=""
fi

deployment_temp="$(mktemp \
    "${deployment_directory}/.deployment.env.XXXXXX")"
cp -- "${deployment_env}" "${deployment_temp}"
if [[ -s "${deployment_temp}" \
    && "$(tail -c 1 "${deployment_temp}" | od -An -t u1 | tr -d ' ')" \
        != 10 ]]; then
    printf '\n' >> "${deployment_temp}"
fi
set_env_value "${deployment_temp}" ECOBIN_IMAGE_MODE local
set_env_value "${deployment_temp}" ECOBIN_RELEASE_ID "${release_id}"
set_env_value "${deployment_temp}" ECOBIN_BACKEND_IMAGE "${backend_image}"
set_env_value "${deployment_temp}" ECOBIN_BACKEND_IMAGE_ID \
    "${backend_image_id}"
set_env_value "${deployment_temp}" ECOBIN_WEB_IMAGE "${web_image}"
set_env_value "${deployment_temp}" ECOBIN_WEB_IMAGE_ID "${web_image_id}"
chown root:root "${deployment_temp}"
chmod 0600 "${deployment_temp}"
mv -f -- "${deployment_temp}" "${deployment_env}"
deployment_temp=""

printf 'local release installed release_id=%s backend_id=%s web_id=%s\n' \
    "${release_id}" "${backend_image_id}" "${web_image_id}"
printf '%s\n' \
    'application was not restarted; run production preflight before activation'
