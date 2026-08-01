package org.enveloping.ecobin.device.application.deliveryquery;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class JdbcMiniappDeliveryDeviceQueryRepositorySqlTest {

    @Test
    void allMiniappReadsAreNonLockingAndStayInsideDeviceTables() {
        List<String> sqlStatements = List.of(
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_CURRENT_DEPLOYMENT_SQL,
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_PORTS_SQL,
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_OWNED_SESSION_SQL);

        assertThat(sqlStatements).allSatisfy(sql -> {
            String normalized = sql.toLowerCase(Locale.ROOT);
            assertThat(normalized)
                    .doesNotContain(
                            "for update",
                            "iam_",
                            "rec_",
                            "mcu_link_status",
                            "mcu_firmware_version",
                            "mcu_boot_id",
                            "uart_state",
                            "uart_protocol_major",
                            "uart_protocol_minor",
                            "capability_bitmap_hex");
        });
    }

    @Test
    void deploymentAndSessionReadsAreScopedToCurrentIdentity() {
        String deployment =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_CURRENT_DEPLOYMENT_SQL
                        .toLowerCase(Locale.ROOT);
        String session =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_OWNED_SESSION_SQL
                        .toLowerCase(Locale.ROOT);

        assertThat(deployment)
                .contains(
                        "deployment.tenant_id = ?",
                        "deployment.organization_id = ?",
                        "deployment.public_code = ?",
                        "dev_asset_active_deployment");
        assertThat(session)
                .contains(
                        "delivery_session.tenant_id = ?",
                        "delivery_session.organization_id = ?",
                        "delivery_session.organization_user_id = ?",
                        "delivery_session.session_uid = ?");
    }

    @Test
    void optionsUseTrustedOrangePiProjectionAndLatestConfiguration() {
        String deployment =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_CURRENT_DEPLOYMENT_SQL
                        .toLowerCase(Locale.ROOT);
        String ports =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_PORTS_SQL
                        .toLowerCase(Locale.ROOT);

        assertThat(deployment)
                .contains(
                        "order by latest.version_no desc",
                        "trusted_runtime_edge_event_id",
                        "trusted_runtime_received_at",
                        "orange_pi_reported_config_version_no");
        assertThat(ports)
                .contains(
                        "trusted_runtime_edge_event_id",
                        "weight_measurement_status",
                        "runtime_fault_bitmap",
                        "pending_delivery_result_session_id");
    }
}
