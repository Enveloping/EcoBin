package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class TrustedOrangePiRuntimeFactServiceSqlTest {

    @Test
    void immutableCommandStageUsesPlainReadBehindCommandLock() {
        String sql = TrustedOrangePiRuntimeFactService
                .LOAD_COMMAND_STAGE_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains("FROM DEV_DEVICE_COMMAND_EVENT")
                .doesNotContain("FOR UPDATE");
    }
}
