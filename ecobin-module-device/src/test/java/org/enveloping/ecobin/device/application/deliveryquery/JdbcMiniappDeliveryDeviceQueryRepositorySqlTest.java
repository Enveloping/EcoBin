package org.enveloping.ecobin.device.application.deliveryquery;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class JdbcMiniappDeliveryDeviceQueryRepositorySqlTest {

    @Test
    void allMiniappReadsAreNonLockingAndAvoidBusinessTables() {
        List<String> sqlStatements = List.of(
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_CURRENT_ASSET_SQL,
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_PORTS_SQL,
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_OWNED_SESSION_SQL);

        assertThat(sqlStatements).allSatisfy(sql -> {
            String normalized = sql.toLowerCase(Locale.ROOT);
            assertThat(normalized)
                    .doesNotContain(
                            "for update",
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
    void assetAndSessionReadsAreScopedToCurrentIdentity() {
        String asset =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_CURRENT_ASSET_SQL
                        .toLowerCase(Locale.ROOT);
        String session =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_OWNED_SESSION_SQL
                        .toLowerCase(Locale.ROOT);

        assertThat(asset)
                .contains(
                        "asset.tenant_id = ?",
                        "asset.organization_id = ?",
                        "asset.device_public_code = ?",
                        "asset.lifecycle_status = 'normal'",
                        "asset.acceptance_status = 'passed'");
        assertThat(session)
                .contains(
                        "delivery_session.tenant_id = ?",
                        "delivery_session.organization_id = ?",
                        "delivery_session.organization_user_id = ?",
                        "delivery_session.session_uid = ?");
    }

    @Test
    void optionsUseReliableConfigurationProgressAndLatestConfiguration() {
        String asset =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_CURRENT_ASSET_SQL
                        .toLowerCase(Locale.ROOT);
        String ports =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_PORTS_SQL
                        .toLowerCase(Locale.ROOT);

        assertThat(asset)
                .contains(
                        "order by latest.version_no desc",
                        "trusted_runtime_edge_event_id",
                        "trusted_runtime_received_at",
                        "runtime.applied_config_version_no",
                        "runtime.applied_config_content_sha256",
                        "runtime.applied_mcu_payload_sha256")
                .doesNotContain("orange_pi_reported_config");
        assertThat(ports)
                .contains(
                        "trusted_runtime_edge_event_id",
                        "weight_measurement_status",
                        "runtime_fault_bitmap",
                        "pending_delivery_result_session_id");
    }

    @Test
    void optionsReadManagementGenerationAndBusinessAdmission() {
        String asset =
                JdbcMiniappDeliveryDeviceQueryRepository
                        .FIND_CURRENT_ASSET_SQL
                        .toLowerCase(Locale.ROOT);

        assertThat(asset).contains(
                "left join dev_device_management_profile management",
                "left join dev_device_compatibility_projection compatibility",
                "management.architecture_generation",
                "compatibility.business_admission_status");
    }
}
