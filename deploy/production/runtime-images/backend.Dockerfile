ARG ECOBIN_BACKEND_BASE_IMAGE=eclipse-temurin:21-jre
FROM ${ECOBIN_BACKEND_BASE_IMAGE}

ARG ECOBIN_BACKEND_BASE_IMAGE
ARG ECOBIN_RELEASE_ID
ARG ECOBIN_GIT_COMMIT
ARG ECOBIN_ARTIFACT_SHA256

LABEL org.opencontainers.image.title="EcoBin backend" \
      org.opencontainers.image.version="${ECOBIN_RELEASE_ID}" \
      org.opencontainers.image.revision="${ECOBIN_GIT_COMMIT}" \
      org.ecobin.artifact.sha256="${ECOBIN_ARTIFACT_SHA256}" \
      org.ecobin.base.reference="${ECOBIN_BACKEND_BASE_IMAGE}"

WORKDIR /app

COPY app.jar /app/app.jar
COPY backend-healthcheck.sh /usr/local/bin/ecobin-backend-healthcheck

RUN test -n "${ECOBIN_RELEASE_ID}" \
    && test -n "${ECOBIN_GIT_COMMIT}" \
    && test -n "${ECOBIN_ARTIFACT_SHA256}" \
    && test -s /app/app.jar \
    && groupadd --gid 10001 ecobin \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin ecobin \
    && chmod 0555 /usr/local/bin/ecobin-backend-healthcheck

EXPOSE 8080

USER 10001:10001

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
