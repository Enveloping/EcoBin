#!/usr/bin/env bash
set -euo pipefail

script_directory="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd "${script_directory}/../../.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/ecobin-local-release-test.XXXXXX")"

cleanup() {
    case "${test_root}" in
        "${TMPDIR:-/tmp}"/ecobin-local-release-test.*)
            rm -rf -- "${test_root}"
            ;;
        *)
            printf 'refusing to remove unexpected test directory: %s\n' \
                "${test_root}" >&2
            ;;
    esac
}
trap cleanup EXIT

release_id=test-release
git_commit=0123456789abcdef0123456789abcdef01234567
bundle="${test_root}/ecobin-release-${release_id}"
fake_bin="${test_root}/fake-bin"
fake_state="${test_root}/docker-build-count"
deployment_env="${test_root}/deployment.env"
runtime_env="${test_root}/runtime.env"
compose_file="${test_root}/compose.yml"
release_store="${test_root}/release-store"
runtime_secret_root="${test_root}/runtime-secrets"

mkdir -p "${bundle}/backend" "${bundle}/web/dist" "${fake_bin}"
printf 'dummy executable jar\n' > "${bundle}/backend/app.jar"
printf '#!/usr/bin/env bash\nexit 0\n' \
    > "${bundle}/backend/backend-healthcheck.sh"
cp "${repository_root}/deploy/production/runtime-images/backend.Dockerfile" \
    "${bundle}/backend/Dockerfile"
printf '<!doctype html><title>EcoBin</title>\n' \
    > "${bundle}/web/dist/index.html"
cp "${repository_root}/deploy/production/runtime-images/web.Dockerfile" \
    "${bundle}/web/Dockerfile"
cp "${repository_root}/frontend/web/nginx.conf" \
    "${bundle}/web/nginx.conf"
cat > "${bundle}/manifest.env" <<EOF
ECOBIN_RELEASE_FORMAT_VERSION=1
ECOBIN_RELEASE_ID=${release_id}
ECOBIN_GIT_COMMIT=${git_commit}
ECOBIN_SOURCE_DIRTY=false
EOF
(
    cd "${bundle}"
    find . -type f ! -path './SHA256SUMS' -print0 \
        | LC_ALL=C sort -z \
        | xargs -0 sha256sum > SHA256SUMS
)

cat > "${fake_bin}/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

backend_id="sha256:$(printf '1%.0s' {1..64})"
web_id="sha256:$(printf '2%.0s' {1..64})"
git_commit=0123456789abcdef0123456789abcdef01234567

if [[ "${1:-}" = build ]]; then
    count=0
    [[ ! -f "${FAKE_DOCKER_STATE}" ]] \
        || count="$(cat "${FAKE_DOCKER_STATE}")"
    printf '%s\n' "$((count + 1))" > "${FAKE_DOCKER_STATE}"
    exit 0
fi

if [[ "${1:-}" = image && "${2:-}" = inspect ]]; then
    image="${!#}"
    if [[ "${image}" = ecobin-local/backend:* ]]; then
        image_id="${backend_id}"
    elif [[ "${image}" = ecobin-local/web:* ]]; then
        image_id="${web_id}"
    else
        exit 1
    fi
    case "$*" in
        *'{{.Id}}'*) printf '%s\n' "${image_id}" ;;
        *'org.opencontainers.image.version'*) printf 'test-release\n' ;;
        *'org.opencontainers.image.revision'*) printf '%s\n' "${git_commit}" ;;
        *'org.ecobin.artifact.sha256'*) printf 'a%.0s' {1..64}; printf '\n' ;;
        *) exit 1 ;;
    esac
    exit 0
fi

if [[ "${1:-}" = network && "${2:-}" = inspect ]]; then
    printf 'true\n'
    exit 0
fi

if [[ "${1:-}" = compose ]]; then
    exit 0
fi

exit 1
EOF
chmod 0755 "${fake_bin}/docker"

cat > "${deployment_env}" <<'EOF'
ECOBIN_IMAGE_MODE=local
ECOBIN_RELEASE_ID=replace-with-installed-release
ECOBIN_BACKEND_IMAGE=ecobin-local/backend:replace-with-installed-release
ECOBIN_BACKEND_IMAGE_ID=sha256:0000000000000000000000000000000000000000000000000000000000000000
ECOBIN_WEB_IMAGE=ecobin-local/web:replace-with-installed-release
ECOBIN_WEB_IMAGE_ID=sha256:0000000000000000000000000000000000000000000000000000000000000000
ECOBIN_RUNTIME_ENV_FILE=/etc/ecobin/runtime.env
ECOBIN_PUBLIC_ORIGIN=https://www.jinshoubao.com
ECOBIN_WEB_LOOPBACK_PORT=18080
ECOBIN_COMPOSE_PROJECT_NAME=ecobin-target
ECOBIN_BACKEND_CONTAINER_NAME=ecobin-target-backend
ECOBIN_WEB_CONTAINER_NAME=ecobin-target-web
ECOBIN_APP_NETWORK_NAME=ecobin-target-app
ECOBIN_DB_NETWORK_NAME=ecobin-target-db
EOF

cat > "${runtime_env}" <<'EOF'
dbUrl=jdbc:mysql://ecobin-target-mysql84:3306/ecobin?useUnicode=true&characterEncoding=utf-8&serverTimezone=UTC
dbUsername=ecobin_app
defaultPlatformAdminEnabled=false
externalMode=fake
onenetSubscriptionEnabled=false
TZ=UTC
EOF
printf 'services: {}\n' > "${compose_file}"
chmod 0600 "${deployment_env}" "${runtime_env}" "${compose_file}"

export PATH="${fake_bin}:${PATH}"
export FAKE_DOCKER_STATE="${fake_state}"
export ECOBIN_DEPLOYMENT_ENV_FILE="${deployment_env}"
export ECOBIN_RELEASE_STORE="${release_store}"
export ECOBIN_PULL_RUNTIME_BASE_IMAGES=false

bash "${repository_root}/tools/deployment/ecobin-install-local-release.sh" \
    "${bundle}" >/dev/null
[[ "$(cat "${fake_state}")" = 2 ]]
grep -qx 'ECOBIN_RELEASE_ID=test-release' "${deployment_env}"
grep -Eq '^ECOBIN_BACKEND_IMAGE_ID=sha256:1{64}$' "${deployment_env}"
grep -Eq '^ECOBIN_WEB_IMAGE_ID=sha256:2{64}$' "${deployment_env}"

# A repeated activation must reuse the recorded immutable image IDs instead
# of rebuilding the same release ID against potentially changed base tags.
bash "${repository_root}/tools/deployment/ecobin-install-local-release.sh" \
    "${bundle}" >/dev/null
[[ "$(cat "${fake_state}")" = 2 ]]

mkdir -p "${runtime_secret_root}/backend"
chown 0:10001 "${runtime_secret_root}/backend"
chmod 0750 "${runtime_secret_root}/backend"

ECOBIN_APP_COMPOSE_FILE="${compose_file}" \
ECOBIN_RUNTIME_ENV_FILE="${runtime_env}" \
ECOBIN_RUNTIME_SECRET_DIR="${runtime_secret_root}" \
bash "${repository_root}/tools/deployment/ecobin-production-preflight.sh" \
    | grep -qx 'production-preflight=PASS mode=fake'

printf 'ecobin-local-release-smoke=PASS\n'
