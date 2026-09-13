package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.util.Locale;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AutomaticDeviceActivationSchedulerSqlTest {

    @Test
    void scanIncludesRecognizedProfileChangesAndProcessesEachAssetOnce() {
        var jdbc = mock(JdbcTemplate.class);
        var application = mock(TargetDeviceApplication.class);
        when(jdbc.query(eq(AutomaticDeviceActivationScheduler.FIND_INCOMPLETE_ASSET_IDS_SQL),
                org.mockito.ArgumentMatchers.<RowMapper<Long>>any())).thenReturn(List.of(1L, 2L));
        when(jdbc.query(eq(McuConfigurationProfileProvider.PROFILE_CHANGE_ASSET_IDS_SQL),
                org.mockito.ArgumentMatchers.<RowMapper<Long>>any())).thenReturn(List.of(2L, 3L));

        new AutomaticDeviceActivationScheduler(jdbc, application).reconcileIncompleteAssets();

        verify(application).reconcileAutomaticActivation(1L);
        verify(application).reconcileAutomaticActivation(2L);
        verify(application).reconcileAutomaticActivation(3L);
        verifyNoMoreInteractions(application);
    }

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
