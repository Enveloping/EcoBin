package org.enveloping.ecobin.bootstrap;

import org.junit.jupiter.api.Test;
import org.springframework.boot.env.YamlPropertySourceLoader;
import org.springframework.core.env.PropertySource;
import org.springframework.core.io.ClassPathResource;
import org.springframework.util.ClassUtils;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
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
            "V11__device_controlled_vertical.sql",
            "V12__trusted_orange_pi_runtime_facts.sql",
            "V13__separate_device_protocol_control_tasks.sql",
            "V14__delivery_happy_path_facts.sql",
            "V15__photo_terminal_facts.sql",
            "V16__organization_delivery_defaults.sql",
            "V17__delivery_fullness_normal_flow.sql",
            "V18__wallet_read_models.sql",
            "V19__delivery_configuration_management.sql",
            "V20__clean_records_without_review.sql",
            "V21__clean_normal_flow_facts.sql",
            "V22__device_tenant_allocation.sql",
            "V23__device_acceptance_reclaim_credentials.sql",
            "V24__device_transport_presence_and_dispatch_gates.sql",
            "V25__edge_reported_current_bag_fullness.sql",
            "V26__event_driven_device_presence_and_evidence.sql",
            "V27__edge_restart_abort_semantics.sql",
            "V28__organization_payout_account_bootstrap.sql",
            "V29__organization_withdrawal_defaults.sql",
            "V30__funds_channel_evidence_and_recovery.sql",
            "V31__native_request_freeze_and_wallet_adjustment.sql",
            "V32__organization_miniapp_database_secret.sql",
            "V33__operations_governance_idempotency.sql",
            "V34__operations_audit_organization_user_index.sql",
            "V35__merchant_transfer_authorization.sql",
            "V36__permanent_device_ownership.sql",
            "V37__platform_acceptance_confirmations.sql",
            "V38__simulator_neutral_device_acceptance.sql",
            "V39__shared_miniapp_multi_organization_identity.sql",
            "V40__permanent_asset_edge_event_targets.sql"
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
                false,
                property(
                        sources,
                        "ecobin.observability.diagnostic-logging.one-net.enabled"));
        assertEquals(
                false,
                property(
                        sources,
                        "ecobin.observability.diagnostic-logging.sql.enabled"));
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
        assertFalse(yaml.contains("wechatAppid"));
        assertFalse(yaml.contains("wechatSecret"));
        assertFalse(yaml.contains("miniappSecretStoreDirectory"));
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
                "cos.base-url")) {
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
        assertEquals(
                true,
                property(
                        realSources,
                        "ecobin.observability.diagnostic-logging.one-net.enabled"));
        assertEquals(
                true,
                property(
                        realSources,
                        "ecobin.observability.diagnostic-logging.sql.enabled"));
        assertEquals(
                "${ecobinLogPath:./logs}",
                property(realSources, "logging.file.path"));
    }

    @Test
    void productionProfileKeepsDiagnosticsClosedAndDisablesWeakBootstrap()
            throws IOException {
        List<PropertySource<?>> productionSources =
                new YamlPropertySourceLoader().load(
                        "production",
                        new ClassPathResource(
                                "application-production.yml"));

        assertEquals(
                false,
                property(
                        productionSources,
                        "ecobin.development.default-platform-admin.enabled"));
        assertEquals(
                false,
                property(
                        productionSources,
                        "ecobin.observability.http-request-logging.include-request-body"));
        assertEquals(
                false,
                property(
                        productionSources,
                        "ecobin.observability.diagnostic-logging.one-net.enabled"));
        assertEquals(
                false,
                property(
                        productionSources,
                        "ecobin.observability.diagnostic-logging.sql.enabled"));
        assertEquals(
                "native",
                property(
                        productionSources,
                        "server.forward-headers-strategy"));
        assertEquals(
                "graceful",
                property(productionSources, "server.shutdown"));
        assertNull(property(productionSources, "logging.file.path"));
        assertNull(property(productionSources, "ecobin.external.mode"));
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

    @Test
    void v32RevokesLegacyMiniappSessionsBeforeDroppingSecretReferences()
            throws IOException {
        String migration = Files.readString(
                moduleSource("src/main/resources/db/p0-migration/"
                        + "V32__organization_miniapp_database_secret.sql"),
                StandardCharsets.UTF_8);
        int userSessionRevocation = migration.indexOf(
                "UPDATE iam_organization_user_session");
        int staffSessionRevocation = migration.indexOf(
                "UPDATE iam_staff_login_session");
        int secretReferenceDrop = migration.indexOf(
                "DROP COLUMN secret_ref");

        assertTrue(userSessionRevocation >= 0);
        assertTrue(staffSessionRevocation >= 0);
        assertTrue(secretReferenceDrop > userSessionRevocation);
        assertTrue(secretReferenceDrop > staffSessionRevocation);
        assertTrue(migration.contains(
                "MINIAPP_CREDENTIAL_STORAGE_MIGRATED"));
    }

    @Test
    void v38KeepsSimulationMarkersDiagnosticOnly() throws IOException {
        String migration = Files.readString(
                moduleSource("src/main/resources/db/p0-migration/"
                        + "V38__simulator_neutral_device_acceptance.sql"),
                StandardCharsets.UTF_8);

        assertTrue(migration.contains(
                "ck_dev_acceptance_evidence_result_v38"));
        assertFalse(migration.contains("mcu_simulated = 0"));
        assertFalse(migration.contains("cameras_simulated = 0"));
        assertTrue(migration.contains("mcu_communication_healthy = 1"));
        assertTrue(migration.contains("cameras_capture_healthy = 1"));
        assertTrue(migration.contains("camera_upload_healthy = 1"));
    }

    private static Path moduleSource(String relativePath) {
        Path workingDirectory = Path.of("").toAbsolutePath();
        Path direct = workingDirectory.resolve(relativePath);
        if (Files.isRegularFile(direct)) {
            return direct;
        }
        Path nested = workingDirectory.resolve("ecobin-bootstrap")
                .resolve(relativePath);
        assertTrue(
                Files.isRegularFile(nested),
                () -> "missing source file " + nested);
        return nested;
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
