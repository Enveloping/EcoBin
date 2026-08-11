package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class AutomaticDeviceActivationSchedulerSqlTest {

    @Test
    void failedConfigurationIsNeverSelectedForAutomaticRetry() {
        String sql = AutomaticDeviceActivationScheduler
                .FIND_INCOMPLETE_ASSET_IDS_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .doesNotContain(
                        "APPLICATION.STATUS = 'FAILED'",
                        "AUTOMATIC_CONFIGURATION_RETRY",
                        "APPLICATION.LAST_FAILURE_CODE NOT LIKE");
    }

    @Test
    void selectsOnlyRetryableFactoryBaselineGenerations() {
        String sql = AutomaticDeviceActivationScheduler
                .FIND_INCOMPLETE_ASSET_IDS_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "JOIN REC_BAG FACTORY_BAG",
                        "JOIN REC_BAG_CURRENT_OCCUPANCY CURRENT_OCCUPANCY",
                        "CURRENT_OCCUPANCY.BAG_ID = FACTORY_BAG.ID",
                        "CAPACITY.CURRENT_BAG_ID = FACTORY_BAG.ID",
                        "CAPACITY.BASELINE_STATE <> 'VALID'",
                        "APPLICATION.STATUS = 'APPLIED'",
                        "ATTEMPTED.INITIATOR_KIND = 'SYSTEM'",
                        ") < 4",
                        "ACTIVE.STATUS = 'PENDING'",
                        "'DEVICE_IDENTITY_UNRESOLVED'",
                        "'PERMANENT_TECHNICAL_FAILURE'")
                .doesNotContain("FROM DEV_FACTORY_INSTALLED_BAG BAG");
    }
}
