#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
test_root="$(mktemp -d)"
image_tag="ecobin-local/web-runtime-permission-smoke:$$"
container_name="ecobin-web-runtime-permission-smoke-$$"

cleanup() {
  docker container rm --force "${container_name}" >/dev/null 2>&1 || true
  docker image rm --force "${image_tag}" >/dev/null 2>&1 || true
  rm -rf -- "${test_root}"
}
trap cleanup EXIT

mkdir -p "${test_root}/dist/assets"
cp "${repo_root}/deploy/production/runtime-images/web.Dockerfile" \
  "${test_root}/Dockerfile"
printf '%s\n' \
  'server {' \
  '    listen 80;' \
  '    server_name _;' \
  '    root /usr/share/nginx/html;' \
  '    index index.html;' \
  '    location / { try_files $uri $uri/ /index.html; }' \
  '}' > "${test_root}/nginx.conf"
printf '<!doctype html><title>runtime permission smoke</title>\n' \
  > "${test_root}/dist/index.html"
printf 'console.log("runtime permission smoke");\n' \
  > "${test_root}/dist/assets/app.js"
chmod 0750 "${test_root}/dist" "${test_root}/dist/assets"
chmod 0640 "${test_root}/dist/index.html" \
  "${test_root}/dist/assets/app.js"

docker build \
  --tag "${image_tag}" \
  --build-arg ECOBIN_RELEASE_ID=runtime-permission-smoke \
  --build-arg ECOBIN_GIT_COMMIT=0000000000000000000000000000000000000000 \
  --build-arg ECOBIN_ARTIFACT_SHA256=0000000000000000000000000000000000000000000000000000000000000000 \
  "${test_root}" >/dev/null

docker run --detach --name "${container_name}" "${image_tag}" >/dev/null

for _ in $(seq 1 15); do
  if response="$(docker exec "${container_name}" \
      wget -qO- http://127.0.0.1/)" \
      && grep -q 'runtime permission smoke' <<<"${response}"; then
    docker exec --user nginx "${container_name}" /bin/sh -c '
      test "$(stat -c "%u:%g:%a" /usr/share/nginx/html)" = "0:0:555"
      test "$(stat -c "%u:%g:%a" /usr/share/nginx/html/index.html)" = "0:0:444"
      test -r /usr/share/nginx/html/index.html
      test ! -w /usr/share/nginx/html/index.html
    '
    echo "ecobin-web-runtime-image-smoke=PASS"
    exit 0
  fi
  sleep 1
done

docker logs "${container_name}" >&2
docker exec "${container_name}" ls -laR /usr/share/nginx/html >&2
exit 1
