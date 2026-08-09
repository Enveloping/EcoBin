package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class TrustedOrangePiRuntimeFactServiceSqlTest {

    @Test
    void baselineResultLocksMutableRootsButPlainReadsImmutableFacts() {
        assertThat(upper(
                TrustedOrangePiRuntimeFactService
                        .LOAD_BASELINE_LOCK_COORDINATES_SQL))
                .contains("FROM REC_PORT_BASELINE_MEASUREMENT")
                .doesNotContain("FOR UPDATE");

        assertSingleTableLock(
                TrustedOrangePiRuntimeFactService
                        .LOCK_BASELINE_RUNTIME_SQL,
                "FROM DEV_DEVICE_RUNTIME_STATE");
        assertSingleTableLock(
                TrustedOrangePiRuntimeFactService
                        .LOCK_BASELINE_CAPACITY_SQL,
                "FROM REC_PORT_CAPACITY_STATE");
        assertSingleTableLock(
                TrustedOrangePiRuntimeFactService
                        .LOCK_BASELINE_MEASUREMENT_SQL,
                "FROM REC_PORT_BASELINE_MEASUREMENT");
        assertSingleTableLock(
                TrustedOrangePiRuntimeFactService
                        .LOCK_BASELINE_COMMAND_SQL,
                "FROM DEV_DEVICE_COMMAND");

        assertThat(upper(
                TrustedOrangePiRuntimeFactService
                        .LOAD_BASELINE_TARGET_SQL))
                .contains(
                        "JOIN DEV_PORT PORT",
                        "JOIN REC_BAG BAG",
                        "JOIN DEV_CONFIG_VERSION VERSION",
                        "JOIN DEV_PORT_CONFIG_SNAPSHOT SNAPSHOT",
                        "JOIN DEV_FACTORY_INSTALLED_BAG FACTORY_BAG")
                .doesNotContain("FOR UPDATE");
    }

    @Test
    void immutableCommandStageUsesPlainReadBehindCommandLock() {
        String sql = TrustedOrangePiRuntimeFactService
                .LOAD_COMMAND_STAGE_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains("FROM DEV_DEVICE_COMMAND_EVENT")
                .doesNotContain("FOR UPDATE");
    }

    private static void assertSingleTableLock(
            String source,
            String expectedTable) {
        assertThat(upper(source))
                .contains(expectedTable, "FOR UPDATE")
                .doesNotContain(" JOIN ");
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
