FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build

COPY pom.xml .
COPY ecobin-common/pom.xml ecobin-common/
COPY ecobin-framework/pom.xml ecobin-framework/
COPY ecobin-module-identity/pom.xml ecobin-module-identity/
COPY ecobin-module-device/pom.xml ecobin-module-device/
COPY ecobin-module-funds/pom.xml ecobin-module-funds/
COPY ecobin-module-recycling/pom.xml ecobin-module-recycling/
COPY ecobin-module-operations/pom.xml ecobin-module-operations/
COPY ecobin-integration/pom.xml ecobin-integration/
COPY ecobin-bootstrap/pom.xml ecobin-bootstrap/

# 预下载依赖到本地仓库。多模块项目里 go-offline 偶尔会因模块间依赖未安装而报警告，
# 故加 "|| true" 让它即使不完整也不中断（缺的依赖会在下面 package 时补下）。
RUN mvn -B dependency:go-offline || true

COPY . .
RUN mvn -B clean package -DskipTests

FROM eclipse-temurin:21-jre AS runtime
WORKDIR /app

COPY --from=build /build/ecobin-bootstrap/target/ecobin-bootstrap-*.jar /app/app.jar

EXPOSE 8080

USER 10001

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
