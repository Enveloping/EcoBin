package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class AutomaticDeviceActivationServiceSqlTest {

    @Test
    void locksMutableCapacityButPlainReadsImmutableBaselineFacts() {
        assertThat(upper(
                AutomaticDeviceActivationService
                        .LOCK_INITIAL_BASELINE_CAPACITY_SQL))
                .contains(
                        "FROM REC_PORT_CAPACITY_STATE",
                        "ORDER BY PORT_ID",
                        "FOR UPDATE")
                .doesNotContain(" JOIN ");
        assertThat(upper(
                AutomaticDeviceActivationService
                        .LOAD_INITIAL_BASELINE_FACTS_SQL))
                .contains(
                        "FROM DEV_PORT PORT",
                        "JOIN DEV_FACTORY_INSTALLED_BAG",
                        "JOIN REC_PORT_CAPACITY_STATE")
                .doesNotContain("FOR UPDATE");
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
