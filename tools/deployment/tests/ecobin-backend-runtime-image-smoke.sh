#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
test_root="$(mktemp -d)"
image_tag="ecobin-local/backend-runtime-permission-smoke:$$"

cleanup() {
  docker image rm --force "${image_tag}" >/dev/null 2>&1 || true
  rm -rf -- "${test_root}"
}
trap cleanup EXIT

cp "${repo_root}/deploy/production/runtime-images/backend.Dockerfile" \
  "${test_root}/Dockerfile"
cp "${repo_root}/deploy/production/backend-healthcheck.sh" \
  "${test_root}/backend-healthcheck.sh"

printf 'backend-runtime-permission-smoke\n' > "${test_root}/app.jar"
chmod 0640 "${test_root}/app.jar"

docker build \
  --tag "${image_tag}" \
  --build-arg ECOBIN_RELEASE_ID=runtime-permission-smoke \
  --build-arg ECOBIN_GIT_COMMIT=0000000000000000000000000000000000000000 \
  --build-arg ECOBIN_ARTIFACT_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
  "${test_root}" >/dev/null

docker run --rm --entrypoint /bin/sh "${image_tag}" -c '
  test "$(id -u):$(id -g)" = "10001:10001"
  test -r /app/app.jar
  test ! -w /app/app.jar
  test "$(stat -c "%u:%g:%a" /app/app.jar)" = "0:10001:440"
'

echo "ecobin-backend-runtime-image-smoke=PASS"
