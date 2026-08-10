package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class AutomaticDeviceActivationSchedulerSqlTest {

    @Test
    void retriesFactoryBaselineOnlyWhileFactoryBagStillOccupiesPort() {
        String sql = AutomaticDeviceActivationScheduler
                .FIND_INCOMPLETE_ASSET_IDS_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "JOIN REC_BAG FACTORY_BAG",
                        "JOIN REC_BAG_CURRENT_OCCUPANCY CURRENT_OCCUPANCY",
                        "CURRENT_OCCUPANCY.BAG_ID = FACTORY_BAG.ID",
                        "FACTORY_INSTALLATION.TARE_STATUS <> 'READY'")
                .doesNotContain("FROM DEV_FACTORY_INSTALLED_BAG BAG");
    }
}
