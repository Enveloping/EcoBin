package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.recycling.web.v1.CleanModels.CleanPortOption;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class CleanQueryServiceTest {

    @Test
    void optionAndStartUseTheSameStableBlockerSet() throws Exception {
        ResultSet row = port(
                null,
                true,
                true,
                "READY",
                "FULL");

        CleanPortOption option = CleanQueryService.portOption(
                row,
                false,
                false,
                false,
                true,
                "OFFLINE");

        assertThat(option.cleaningAllowed()).isFalse();
        assertThat(option.displayName()).isEqualTo("2号投口");
        assertThat(option.blockers()).containsExactly(
                "CLEAN_CONFIGURATION_UNAVAILABLE",
                "CONFIGURATION_NOT_APPLIED",
                "DEVICE_SOFTWARE_NOT_ACCEPTING",
                "EDGE_OFFLINE",
                "DEVICE_BUSY",
                "CLEAN_OPERATION_ACTIVE",
                "PORT_WORK_ACTIVE");
    }

    @Test
    void disabledConfiguredPortHasOnlyItsSpecificBlocker()
            throws Exception {
        ResultSet row = port(
                false,
                false,
                false,
                "READY",
                "NOT_FULL");

        CleanPortOption option = CleanQueryService.portOption(
                row,
                true,
                true,
                true,
                false,
                "ONLINE");

        assertThat(option.cleaningAllowed()).isFalse();
        assertThat(option.blockers()).containsExactly("PORT_DISABLED");
    }

    @Test
    void fullyReadyPortCanStartCleaning() throws Exception {
        ResultSet row = port(
                true,
                false,
                false,
                "READY",
                "FULL");

        CleanPortOption option = CleanQueryService.portOption(
                row,
                true,
                true,
                true,
                false,
                "ONLINE");

        assertThat(option.cleaningAllowed()).isTrue();
        assertThat(option.blockers()).isEqualTo(List.of());
        assertThat(option.fullnessStatus()).isEqualTo("FULL");
    }

    @Test
    void releasedPendingWorkStillMakesTheOriginalDeviceBusy() {
        String sql = CleanQueryService.DEVICE_BUSY_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql).contains(
                "from dev_device_occupancy",
                "from dev_delivery_session",
                "from rec_clean_operation",
                "offline_occupancy_released_at is not null",
                "ended_at is null");
    }

    private static ResultSet port(
            Boolean businessEnabled,
            boolean operationActive,
            boolean portWorkActive,
            String detectionGate,
            String fullnessState) throws Exception {
        ResultSet row = mock(ResultSet.class);
        when(row.getInt("port_no")).thenReturn(2);
        when(row.getString("display_name"))
                .thenReturn(businessEnabled == null ? null : "可回收物");
        when(row.getString("bag_code")).thenReturn("EB1-test");
        when(row.getString("detection_gate")).thenReturn(detectionGate);
        when(row.getString("confirmed_fullness_state"))
                .thenReturn(fullnessState);
        when(row.getBigDecimal("displayed_fullness_percent"))
                .thenReturn(new BigDecimal("88.50"));
        when(row.getObject("business_enabled", Boolean.class))
                .thenReturn(businessEnabled);
        when(row.getBoolean("operation_active"))
                .thenReturn(operationActive);
        when(row.getBoolean("port_work_active"))
                .thenReturn(portWorkActive);
        return row;
    }
}
