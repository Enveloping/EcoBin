package org.enveloping.ecobin.recycling.application.clean;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class CleanDeviceQueryServiceTest {

    @Test
    void exposesExactlyTheSixFrozenDeviceFilters() {
        assertThat(CleanDeviceQueryService.DeviceFilter.values())
                .extracting(Enum::name)
                .containsExactly(
                        "ALL",
                        "ONLINE",
                        "NO_DELIVERY_24H",
                        "NO_CLEAN_24H",
                        "FULL",
                        "FULL_TIMEOUT_2H");
    }

    @Test
    void eachActivityFilterUsesItsAuthoritativeBusinessFact() {
        assertThat(sql("ONLINE"))
                .contains("dev_device_transport_state")
                .contains("onenet_connection_status = 'online'");
        assertThat(sql("NO_DELIVERY_24H"))
                .contains("rec_delivery_order")
                .contains("backend_received_at >= :deliverythreshold")
                .contains("not exists");
        assertThat(sql("NO_CLEAN_24H"))
                .contains("rec_clean_record")
                .contains("completed_at >= :cleanthreshold")
                .contains("not exists");
        assertThat(sql("FULL"))
                .contains("rec_fullness_event")
                .contains("status = 'active'")
                .doesNotContain(":fullthreshold");
        assertThat(sql("FULL_TIMEOUT_2H"))
                .contains("rec_fullness_event")
                .contains("confirmed_at <= :fullthreshold");
        assertThat(sql("ALL")).isBlank();
    }

    @Test
    void missingOrUnknownFilterIsRejectedInsteadOfChangingMeaning() {
        assertThatThrownBy(() ->
                CleanDeviceQueryService.DeviceFilter.parse(null))
                .isInstanceOf(TargetApiException.class)
                .hasMessageContaining("filter");
        assertThatThrownBy(() ->
                CleanDeviceQueryService.DeviceFilter.parse("recent"))
                .isInstanceOf(TargetApiException.class)
                .hasMessageContaining("filter");
    }

    private static String sql(String filter) {
        StringBuilder sql = new StringBuilder();
        CleanDeviceQueryService.DeviceFilter.parse(filter).append(sql);
        return sql.toString()
                .replaceAll("\\s+", " ")
                .trim()
                .toLowerCase(Locale.ROOT);
    }
}
