package org.enveloping.ecobin.bootstrap.database;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class DeviceEnrollmentRemoteSupportMigrationTest {

    private static final Path V52 = Path.of(
            "src/main/resources/db/p0-migration",
            "V52__device_enrollment_factory_support_and_remote_access.sql");

    @Test
    void addsEnrollmentFactoryIdentityAndBoundedRemoteSupportFacts()
            throws Exception {
        String sql = Files.readString(resolve(), StandardCharsets.UTF_8)
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "CREATE TABLE DEV_DEVICE_ENROLLMENT_CHALLENGE",
                        "CREATE TABLE DEV_DEVICE_ENROLLMENT",
                        "CREATE TABLE DEV_DEVICE_MAINTENANCE_IDENTITY",
                        "CREATE TABLE IAM_PLATFORM_ADMIN_MAINTENANCE_SSH_KEY",
                        "CREATE TABLE IAM_FACTORY_OPERATOR",
                        "CREATE TABLE IAM_FACTORY_OPERATOR_BINDING_INTENT",
                        "CREATE TABLE IAM_FACTORY_OPERATOR_MINIAPP_BINDING",
                        "CREATE TABLE IAM_FACTORY_OPERATOR_MINIAPP_SESSION",
                        "ACTOR_KIND = 'FACTORY_OPERATOR'",
                        "'MINIAPP_FACTORY'",
                        "CREATE TABLE REC_BAG_LABEL_CLAIM",
                        "CREATE TABLE DEV_REMOTE_SUPPORT_SESSION",
                        "LEGACY_GRANDFATHERED",
                        "FACTORY_BAG_REVISION",
                        "FACTORY_BAG_SET_SHA256",
                        "LEASE_RELEASED_AT",
                        "'RECONNECTING'",
                        "OPEN_REMOTE_SUPPORT_TUNNEL",
                        "CLOSE_REMOTE_SUPPORT_TUNNEL",
                        "REMOTE_SUPPORT_TUNNEL_STATUS");
        assertThat(sql)
                .contains(
                        "CASE WHEN LEASE_RELEASED_AT IS NULL",
                        "FACTORY_BAG_REVISION BIGINT",
                        "FACTORY_BAG_SET_SHA256 BINARY(32)",
                        "CONSTRAINT UQ_DEV_FACTORY_BAG_LABEL_V52 UNIQUE (LABEL_ITEM_ID)",
                        "CONSTRAINT UQ_REC_BAG_LABEL_CLAIM_ACTIVE_LABEL",
                        "UNIQUE (ACTIVE_LABEL_ITEM_ID)");
        assertThat(sql)
                .contains("(22011, 1", "(22014, 1")
                .doesNotContain(
                        "IAM_PLATFORM_MINIAPP_BINDING",
                        "ONENET_SEC_KEY",
                        "CA_PRIVATE_KEY");
    }

    private static Path resolve() {
        if (Files.isRegularFile(V52)) {
            return V52;
        }
        return Path.of("ecobin-bootstrap").resolve(V52);
    }
}
