ARG ECOBIN_WEB_BASE_IMAGE=nginx:alpine
FROM ${ECOBIN_WEB_BASE_IMAGE}

ARG ECOBIN_WEB_BASE_IMAGE
ARG ECOBIN_RELEASE_ID
ARG ECOBIN_GIT_COMMIT
ARG ECOBIN_ARTIFACT_SHA256

LABEL org.opencontainers.image.title="EcoBin Web" \
      org.opencontainers.image.version="${ECOBIN_RELEASE_ID}" \
      org.opencontainers.image.revision="${ECOBIN_GIT_COMMIT}" \
      org.ecobin.artifact.sha256="${ECOBIN_ARTIFACT_SHA256}" \
      org.ecobin.base.reference="${ECOBIN_WEB_BASE_IMAGE}"

COPY dist/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf

RUN test -n "${ECOBIN_RELEASE_ID}" \
    && test -n "${ECOBIN_GIT_COMMIT}" \
    && test -n "${ECOBIN_ARTIFACT_SHA256}" \
    && test -s /usr/share/nginx/html/index.html \
    && chown -R root:root /usr/share/nginx/html \
    && find /usr/share/nginx/html -type d -exec chmod 0555 {} + \
    && find /usr/share/nginx/html -type f -exec chmod 0444 {} + \
    && chown root:root /etc/nginx/conf.d/default.conf \
    && chmod 0444 /etc/nginx/conf.d/default.conf \
    && test "$(stat -c '%u:%g:%a' /usr/share/nginx/html)" = "0:0:555" \
    && test "$(stat -c '%u:%g:%a' /usr/share/nginx/html/index.html)" = "0:0:444"

EXPOSE 80
