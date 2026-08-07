package org.enveloping.ecobin.device.application.startdelivery;

import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class JdbcStartDeliveryDeviceRepositorySqlTest {

    @Test
    void mutableStartRootsTakeAnUpdateLock() {
        List<String> lockSql = List.of(
                JdbcStartDeliveryDeviceRepository
                        .LOCK_ACTIVE_SESSION_SQL,
                JdbcStartDeliveryDeviceRepository.LOCK_ASSET_SQL,
                JdbcStartDeliveryDeviceRepository
                        .LOCK_TENANT_SQL,
                JdbcStartDeliveryDeviceRepository
                        .LOCK_ORGANIZATION_SQL,
                JdbcStartDeliveryDeviceRepository
                        .LOCK_TRANSPORT_PRESENCE_SQL,
                JdbcStartDeliveryDeviceRepository
                        .LOCK_OCCUPANCY_SQL,
                JdbcStartDeliveryDeviceRepository
                        .LOCK_LATEST_CONFIGURATION_SQL);

        assertThat(lockSql)
                .allSatisfy(sql ->
                        assertThat(sql.toUpperCase(Locale.ROOT))
                                .contains("FOR UPDATE"));
    }

    @Test
    void immutableStartFactsUsePlainReadsBehindMutableRootLocks() {
        List<String> immutableSql = List.of(
                JdbcStartDeliveryDeviceRepository.LOCK_PORT_SQL,
                JdbcStartDeliveryDeviceRepository
                        .LOCK_PORT_CONFIGURATION_SQL);

        assertThat(immutableSql)
                .allSatisfy(sql ->
                        assertThat(sql.toUpperCase(Locale.ROOT))
                                .doesNotContain("FOR UPDATE"));
    }

    @Test
    void startAdmissionReadsOnlyAuthoritativeOnenetPresence() {
        String presence = JdbcStartDeliveryDeviceRepository
                .LOCK_TRANSPORT_PRESENCE_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(presence)
                .contains(
                        "from dev_device_transport_state",
                        "onenet_connection_status")
                .doesNotContain(
                        "runtime",
                        "heartbeat",
                        "mcu",
                        "uart",
                        "safety",
                        "sensor");
    }

    @Test
    void writesFrozenBagIdentityQueuedCommandAndDeliveryOccupancy() {
        String session =
                JdbcStartDeliveryDeviceRepository.INSERT_SESSION_SQL
                        .toLowerCase(Locale.ROOT);
        String command =
                JdbcStartDeliveryDeviceRepository.INSERT_COMMAND_SQL
                        .toLowerCase(Locale.ROOT);
        String occupancy =
                JdbcStartDeliveryDeviceRepository.INSERT_OCCUPANCY_SQL
                        .toLowerCase(Locale.ROOT);

        assertThat(session)
                .contains(
                        "bag_id",
                        "bag_uid_snapshot",
                        "bag_code_snapshot",
                        "delivery_config_content_sha256",
                        "'authorization_queued'");
        assertThat(session.chars().filter(value -> value == '?').count())
                .isEqualTo(25);
        assertThat(command)
                .contains(
                        "'start_delivery_session'",
                        "cast(? as json)",
                        "semantic_payload_sha256",
                        "'queued'");
        assertThat(command.chars().filter(value -> value == '?').count())
                .isEqualTo(10);
        assertThat(occupancy)
                .contains("'delivery'", "delivery_session_id")
                .doesNotContain("'clean'");
        assertThat(occupancy.chars().filter(value -> value == '?').count())
                .isEqualTo(5);
    }
}
