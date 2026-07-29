package org.enveloping.ecobin.device.application.delivery;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class TrustedDeliveryCompletionServiceSqlTest {

    @Test
    void locksPortRuntimeButPlainReadsImmutablePortFacts() {
        assertThat(upper(
                TrustedDeliveryCompletionService.LOCK_PORT_RUNTIME_SQL))
                .contains(
                        "FROM DEV_PORT_RUNTIME_STATE",
                        "FOR UPDATE");
        assertThat(upper(
                TrustedDeliveryCompletionService.LOAD_PORT_SQL))
                .contains("FROM DEV_PORT")
                .doesNotContain("FOR UPDATE");
        assertThat(upper(
                TrustedDeliveryCompletionService
                        .LOAD_PORT_CONFIGURATION_SQL))
                .contains(
                        "FROM DEV_PORT_CONFIG_SNAPSHOT",
                        "JOIN DEV_PORT")
                .doesNotContain("FOR UPDATE");
    }

    @Test
    void collisionReadReliesOnDeploymentSerializationAndUniqueKeys() {
        assertThat(upper(
                TrustedDeliveryCompletionService
                        .FIND_EDGE_COLLISIONS_SQL))
                .contains(
                        "FROM DEV_EDGE_EVENT",
                        "EVENT_UID = ?",
                        "EDGE_EVENT_SEQUENCE = ?",
                        "SOURCE_INBOX_ID = ?")
                .doesNotContain("FOR UPDATE");
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
