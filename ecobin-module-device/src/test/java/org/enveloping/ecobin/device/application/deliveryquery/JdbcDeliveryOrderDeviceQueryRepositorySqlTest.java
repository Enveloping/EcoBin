package org.enveloping.ecobin.device.application.deliveryquery;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class JdbcDeliveryOrderDeviceQueryRepositorySqlTest {

    @Test
    void factLookupUsesOnlyDeviceTablesAndTheCompleteScopedTuple() {
        String sql = JdbcDeliveryOrderDeviceQueryRepository
                .factsSql(2)
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ");

        assertThat(sql)
                .doesNotContain(
                        " for update",
                        "rec_",
                        "iam_")
                .contains(
                        "from dev_physical_result physical_result",
                        "join dev_edge_event edge_event",
                        "join dev_delivery_session delivery_session",
                        "join dev_device_asset asset",
                        "join dev_port port",
                        "physical_result.tenant_id = ?",
                        "physical_result.organization_id = ?",
                        "physical_result.result_type = 'delivery'",
                        "physical_result.edge_event_type = "
                                + "'delivery_complete'",
                        "edge_event.tenant_id = "
                                + "physical_result.tenant_id",
                        "edge_event.organization_id = "
                                + "physical_result.organization_id",
                        "edge_event.asset_id = "
                                + "physical_result.asset_id",
                        "edge_event.id = physical_result.edge_event_id",
                        "edge_event.event_type = "
                                + "physical_result.edge_event_type",
                        "delivery_session.port_id = "
                                + "physical_result.port_id",
                        "delivery_session.id = "
                                + "physical_result.delivery_session_id",
                        "physical_result.asset_id,",
                        "physical_result.port_id,",
                        "physical_result.delivery_session_id,",
                        "physical_result.id");
        assertThat(count(sql, '?')).isEqualTo(10);
    }

    @Test
    void filterResolutionIsOrganizationScopedAndPortScoped() {
        String sql = JdbcDeliveryOrderDeviceQueryRepository
                .RESOLVE_ASSET_FILTER_SQL
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ");

        assertThat(sql)
                .doesNotContain(
                        " for update",
                        "rec_",
                        "iam_")
                .contains(
                        "from dev_device_asset asset",
                        "left join dev_port port",
                        "port.tenant_id = asset.tenant_id",
                        "port.organization_id = "
                                + "asset.organization_id",
                        "port.asset_id = asset.id",
                        "port.port_no = ?",
                        "asset.tenant_id = ?",
                        "asset.organization_id = ?",
                        "asset.device_public_code = ?");
    }

    @Test
    void portOnlyFilterResolvesAllMatchesInsideOneOrganization() {
        String sql = JdbcDeliveryOrderDeviceQueryRepository
                .FIND_PORT_FILTER_KEYS_SQL
                .toLowerCase(Locale.ROOT)
                .replaceAll("\\s+", " ");

        assertThat(sql)
                .doesNotContain(
                        " for update",
                        "rec_",
                        "iam_",
                        "dev_device_asset")
                .contains(
                        "from dev_port port",
                        "port.tenant_id = ?",
                        "port.organization_id = ?",
                        "port.port_no = ?",
                        "order by port.id");
    }

    @Test
    void factSqlRejectsAnEmptyBatch() {
        assertThatThrownBy(() ->
                JdbcDeliveryOrderDeviceQueryRepository.factsSql(0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("positive");
    }

    private static long count(String value, char character) {
        return value.chars()
                .filter(candidate -> candidate == character)
                .count();
    }
}
