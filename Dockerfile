FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build

COPY pom.xml .
COPY ecobin-framework/pom.xml ecobin-framework/
COPY ecobin-module-identity/pom.xml ecobin-module-identity/
COPY ecobin-module-device/pom.xml ecobin-module-device/
COPY ecobin-module-funds/pom.xml ecobin-module-funds/
COPY ecobin-module-recycling/pom.xml ecobin-module-recycling/
COPY ecobin-module-operations/pom.xml ecobin-module-operations/
COPY ecobin-integration/pom.xml ecobin-integration/
COPY ecobin-bootstrap/pom.xml ecobin-bootstrap/

COPY . .
RUN --mount=type=cache,id=ecobin-maven,target=/root/.m2 \
    mvn -B clean package -Dmaven.test.skip=true

FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

COPY --from=build /build/ecobin-bootstrap/target/ecobin-bootstrap-*.jar /app/app.jar
COPY deploy/production/backend-healthcheck.sh \
    /usr/local/bin/ecobin-backend-healthcheck

RUN apt-get update \
    && apt-get install --yes --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ecobin \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin ecobin \
    && chmod 0555 /usr/local/bin/ecobin-backend-healthcheck

EXPOSE 8080

USER 10001:10001

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
