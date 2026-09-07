package org.enveloping.ecobin.device.application.software;

import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;

import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DeviceSoftwareCompatibilityReconciliationSchedulerTest {

    @Test
    void candidatesRequireAnExpiredUpdateReasonAndNoActiveDeployment() {
        String sql = DeviceSoftwareCompatibilityReconciliationScheduler
                .FIND_STALE_UPDATE_ADMISSION_ASSET_IDS_SQL
                .toUpperCase(Locale.ROOT);

        assertThat(sql)
                .contains(
                        "BUSINESS_RUNTIME_UPDATE_ACTIVE",
                        "DEV_EDGE_SOFTWARE_DEPLOYMENT",
                        "NOT EXISTS",
                        "SUCCEEDED",
                        "ROLLED_BACK",
                        "CANCELLED")
                .doesNotContain("UPDATE DEV_DEVICE_COMPATIBILITY_PROJECTION");
    }

    @Test
    void oneBrokenProjectionDoesNotBlockTheRestOfTheBatch() {
        JdbcTemplate jdbc = mock(JdbcTemplate.class);
        DeviceSoftwareCompatibilityService compatibility = mock(
                DeviceSoftwareCompatibilityService.class);
        when(jdbc.query(
                eq(DeviceSoftwareCompatibilityReconciliationScheduler
                        .FIND_STALE_UPDATE_ADMISSION_ASSET_IDS_SQL),
                any(org.springframework.jdbc.core.RowMapper.class),
                eq(100)))
                .thenReturn(List.of(11L, 12L));
        doThrow(new IllegalStateException("broken historical fact"))
                .when(compatibility).reassessLatestFact(11L);

        new DeviceSoftwareCompatibilityReconciliationScheduler(
                jdbc, compatibility).reconcileStaleUpdateAdmissions();

        verify(compatibility).reassessLatestFact(11L);
        verify(compatibility).reassessLatestFact(12L);
    }
}
