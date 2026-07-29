package org.enveloping.ecobin.bootstrap;

import org.junit.jupiter.api.Test;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.PropertySource;
import org.springframework.core.io.ClassPathResource;
import org.springframework.util.ClassUtils;

import java.io.IOException;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

class RuntimeSafetyConfigurationTest {

    private static final String[] TARGET_MIGRATIONS = {
            "V1__p0_epoch_and_iam_core.sql",
            "V2__device_inventory_and_configuration.sql",
            "V3__organization_users_and_sessions.sql",
            "V4__device_operations_and_evidence.sql",
            "V5__recycling.sql",
            "V6__funds.sql",
            "V7__operations.sql",
            "V8__cross_module_constraints.sql",
            "V9__immutability_guards.sql",
            "V10__permission_reference_data.sql",
            "V11__device_controlled_vertical.sql"
    };

    @Test
    void runtimeCannotMigrateBaselineInitializeOrAutoCreateDatabase()
            throws IOException {
        List<PropertySource<?>> sources = new YamlPropertySourceLoader()
                .load(
                        "runtime",
                        new ClassPathResource("application.yml"));

        assertNull(property(sources, "spring.flyway.enabled"));
        assertNull(property(sources, "spring.flyway.baseline-on-migrate"));
        assertNull(property(sources, "spring.flyway.locations"));
        assertEquals("never", property(sources, "spring.sql.init.mode"));
        assertEquals("${dbUrl}", property(sources, "spring.datasource.url"));
        assertEquals(
                "${dbUsername}",
                property(sources, "spring.datasource.username"));
        assertEquals(
                "${dbPassword}",
                property(sources, "spring.datasource.password"));
        assertEquals(
                "${jwtSecret}",
                property(sources, "jwt.secret"));
        assertEquals(
                "${defaultPlatformAdminEnabled:true}",
                property(
                        sources,
                        "ecobin.development.default-platform-admin.enabled"));
        assertEquals(
                "${defaultPlatformAdminLogin:admin}",
                property(
                        sources,
                        "ecobin.development.default-platform-admin.login-name"));
        assertEquals(
                "${defaultPlatformAdminPassword:admin123}",
                property(
                        sources,
                        "ecobin.development.default-platform-admin.password"));
        assertEquals(
                "optional:file:./.ecobin/application-local-secrets.yml",
                property(sources, "spring.config.import[0]"));
        assertEquals(
                "optional:configtree:/run/secrets/",
                property(sources, "spring.config.import[1]"));

        String yaml = new ClassPathResource("application.yml")
                .getContentAsString(java.nio.charset.StandardCharsets.UTF_8);
        assertFalse(yaml.contains("createDatabaseIfNotExist"));
        assertFalse(yaml.contains("dbUsername:root"));
        assertFalse(yaml.contains("StdOutImpl"));
        assertFalse(yaml.contains("optional:file:./.env"));
    }

    @Test
    void localProfilesSwitchExternalBoundaryWithoutEditingSecrets()
            throws IOException {
        List<PropertySource<?>> fakeSources = new YamlPropertySourceLoader()
                .load(
                        "local-fake",
                        new ClassPathResource(
                                "application-local-fake.yml"));

        assertEquals(
                "fake",
                property(fakeSources, "ecobin.external.mode"));
        assertEquals(
                true,
                property(
                        fakeSources,
                        "ecobin.external.fake.block-inbound"));
        assertEquals(
                true,
                property(
                        fakeSources,
                        "ecobin.development.default-platform-admin.enabled"));
        assertEquals(
                false,
                property(fakeSources, "onenet.subscription.enabled"));
        for (String property : List.of(
                "onenet.subscription.access-id",
                "onenet.subscription.secret-key",
                "onenet.subscription.subscription-name",
                "onenet.product-id",
                "onenet.access-key",
                "cos.secret-id",
                "cos.secret-key",
                "cos.region",
                "cos.bucket-name",
                "cos.base-url",
                "wechat.miniapp.appid",
                "wechat.miniapp.secret")) {
            assertEquals(
                    "",
                    property(fakeSources, property),
                    () -> "local-fake must mask " + property);
        }

        List<PropertySource<?>> realSources = new YamlPropertySourceLoader()
                .load(
                        "local-real",
                        new ClassPathResource(
                                "application-local-real.yml"));

        assertEquals(
                "real",
                property(realSources, "ecobin.external.mode"));
        assertEquals(
                true,
                property(realSources, "onenet.subscription.enabled"));
        assertEquals(
                false,
                property(
                        realSources,
                        "ecobin.development.default-platform-admin.enabled"));
    }

    @Test
    void runtimeClasspathCarriesNeitherFlywayEngineNorMigrationScripts() {
        ClassLoader classLoader = getClass().getClassLoader();

        assertFalse(ClassUtils.isPresent(
                "org.flywaydb.core.Flyway",
                classLoader));
        assertFalse(ClassUtils.isPresent(
                "org.springframework.boot.flyway.autoconfigure.FlywayAutoConfiguration",
                classLoader));
        assertNull(classLoader.getResource(
                "db/migration/V1__init_schema.sql"));
        for (String migration : TARGET_MIGRATIONS) {
            assertNull(
                    classLoader.getResource(
                            "db/p0-migration/" + migration),
                    () -> "runtime classpath contains target migration " + migration);
        }
    }

    private static Object property(
            List<PropertySource<?>> sources,
            String name) {
        for (PropertySource<?> source : sources) {
            Object value = source.getProperty(name);
            if (value != null) {
                return value;
            }
        }
        return null;
    }
}
