package org.enveloping.ecobin;

import org.enveloping.ecobin.device.api.persistence.DeviceOwnedRecyclingPortRefFactory;
import org.enveloping.ecobin.device.application.target.DeviceListStatusQuery;
import org.enveloping.ecobin.device.web.v1.DeviceModels.DeviceAssetView;
import org.enveloping.ecobin.recycling.infrastructure.device.RecyclingDeviceListFullnessAdapter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeviceListStatusQueryTest {
    private static final UUID ASSET = UUID.fromString("10000000-0000-4000-8000-000000000001");
    private static final UUID REPORT = UUID.fromString("20000000-0000-4000-8000-000000000001");
    private JdbcTemplate jdbc;
    private TransactionTemplate tx;
    private DeviceOwnedRecyclingPortRefFactory refs;
    private RecyclingDeviceListFullnessAdapter fullness;
    private DeviceListStatusQuery query;

    @BeforeEach
    void setup() {
        var source = new DriverManagerDataSource("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", "");
        jdbc = new JdbcTemplate(source);
        tx = new TransactionTemplate(new DataSourceTransactionManager(source));
        refs = new DeviceOwnedRecyclingPortRefFactory();
        fullness = new RecyclingDeviceListFullnessAdapter(jdbc);
        query = new DeviceListStatusQuery(jdbc, refs, fullness);
        jdbc.execute("CREATE TABLE dev_device_asset(id BIGINT, asset_uid VARCHAR(36), tenant_id BIGINT, organization_id BIGINT)");
        jdbc.execute("CREATE TABLE dev_port(id BIGINT, asset_id BIGINT, port_no INT)");
        jdbc.execute("CREATE TABLE dev_device_runtime_state(asset_id BIGINT, mcu_link_status VARCHAR, camera_health VARCHAR, local_storage_health VARCHAR, clock_sync_health VARCHAR, last_heartbeat_at TIMESTAMP)");
        jdbc.execute("CREATE TABLE dev_port_runtime_state(asset_id BIGINT, port_id BIGINT, reported_weight_grams BIGINT, weight_value_available BOOLEAN, weight_sensor_health VARCHAR, weight_measurement_status VARCHAR, infrared_value VARCHAR, infrared_sensor_health VARCHAR, last_observed_at TIMESTAMP, delivery_door_actuator_health VARCHAR, clean_solenoid_health VARCHAR, smoke_state VARCHAR, smoke_sensor_health VARCHAR, safety_status VARCHAR)");
        jdbc.execute("CREATE TABLE dev_config_version(id BIGINT, asset_id BIGINT, version_no BIGINT)");
        jdbc.execute("CREATE TABLE dev_port_config_snapshot(config_version_id BIGINT, port_id BIGINT, display_name VARCHAR)");
        jdbc.execute("CREATE TABLE rec_port_capacity_state(port_id BIGINT, tenant_id BIGINT, organization_id BIGINT, current_bag_id BIGINT, current_fullness_state_change_id BIGINT)");
        jdbc.execute("CREATE TABLE rec_bag_current_occupancy(port_id BIGINT, tenant_id BIGINT, organization_id BIGINT, bag_id BIGINT, occupancy_type VARCHAR)");
        jdbc.execute("CREATE TABLE rec_fullness_state_change(id BIGINT, state_change_uid VARCHAR(36), port_id BIGINT, tenant_id BIGINT, organization_id BIGINT, bag_id BIGINT, disposition VARCHAR)");
        jdbc.execute("CREATE TABLE dev_fullness_state_fact(state_change_uid VARCHAR(36), port_id BIGINT, weight_full BOOLEAN, device_occurred_at TIMESTAMP)");
        jdbc.update("INSERT INTO dev_device_asset VALUES (1, ?, 7, 9), (2, ?, 8, 10)", ASSET.toString(), UUID.randomUUID().toString());
        jdbc.update("INSERT INTO dev_port VALUES (11, 1, 1), (12, 1, 2), (21, 2, 1)");
        jdbc.update("INSERT INTO dev_device_runtime_state VALUES (1, 'DISCONNECTED', 'OK', 'OK', 'OK', TIMESTAMP '2026-09-12 08:00:00')");
        jdbc.update("INSERT INTO dev_port_runtime_state VALUES (1, 11, 1200, TRUE, 'OK', 'STABLE', 'BLOCKED', 'OK', TIMESTAMP '2026-09-12 08:00:00', 'OK', 'OK', 'NORMAL', 'OK', 'SAFE')");
        jdbc.update("INSERT INTO dev_config_version VALUES (1, 1, 1), (2, 1, 2)");
        jdbc.update("INSERT INTO dev_port_config_snapshot VALUES (1, 11, '旧投口名'), (2, 11, '纸类')");
        jdbc.update("INSERT INTO rec_port_capacity_state VALUES (11, 7, 9, 100, 1)");
        jdbc.update("INSERT INTO rec_bag_current_occupancy VALUES (11, 7, 9, 100, 'PORT_BOUND')");
        jdbc.update("INSERT INTO rec_fullness_state_change VALUES (1, ?, 11, 7, 9, 100, 'APPLIED')", REPORT.toString());
        jdbc.update("INSERT INTO dev_fullness_state_fact VALUES (?, 11, FALSE, TIMESTAMP '2026-09-12 07:00:00')", REPORT.toString());
    }

    @Test
    void pageProjectionKeepsPortsOrderedAndUnknownsDistinct() {
        var result = read();
        assertThat(result.listStatus().faults()).containsExactly("控制板通信未连接");
        assertThat(result.listStatus().ports()).hasSize(2);
        var first = result.listStatus().ports().getFirst();
        assertThat(first.portNo()).isEqualTo(1);
        assertThat(first.displayName()).isEqualTo("纸类");
        assertThat(first.reportedWeightGrams()).isEqualTo(1200);
        assertThat(first.weightFull()).isFalse();
        assertThat(first.infraredValue()).isEqualTo("BLOCKED");
        assertThat(first.fullnessObservedAt()).isBefore(first.observedAt());
        var second = result.listStatus().ports().getLast();
        assertThat(second.portNo()).isEqualTo(2);
        assertThat(second.reportedWeightGrams()).isNull();
        assertThat(second.weightFull()).isNull();
        assertThat(second.infraredValue()).isNull();
    }

    @Test
    void bagReplacementAndStaleReportsCannotLeakTheOldWeightDecision() {
        jdbc.update("UPDATE rec_bag_current_occupancy SET bag_id = 101");
        assertThat(read().listStatus().ports().getFirst().weightFull()).isNull();
        jdbc.update("UPDATE rec_port_capacity_state SET current_bag_id = 101");
        assertThat(read().listStatus().ports().getFirst().weightFull()).isNull();
        jdbc.update("UPDATE rec_fullness_state_change SET bag_id = 101, disposition = 'STALE_SEQUENCE'");
        assertThat(read().listStatus().ports().getFirst().weightFull()).isNull();
        jdbc.update("UPDATE rec_fullness_state_change SET disposition = 'NO_STATE_CHANGE'");
        assertThat(read().listStatus().ports().getFirst().weightFull()).isFalse();
    }

    @Test
    void currentPointerWinsOverAnotherNewerOrForeignPhysicalFact() {
        jdbc.update("INSERT INTO dev_fullness_state_fact VALUES (?, 11, TRUE, TIMESTAMP '2026-09-12 09:00:00')", UUID.randomUUID().toString());
        assertThat(read().listStatus().ports().getFirst().weightFull()).isFalse();
        jdbc.update("UPDATE dev_fullness_state_fact SET port_id = 21 WHERE state_change_uid = ?", REPORT.toString());
        assertThat(read().listStatus().ports().getFirst().weightFull()).isNull();
    }

    @Test
    void scopeReferencesEnforceTenantOrganizationAndSingleTransaction() {
        var token = UUID.randomUUID();
        tx.executeWithoutResult(status -> {
            assertThat(fullness.currentReports(Map.of(token, refs.issue(8, 9, 11)))).isEmpty();
            assertThat(fullness.currentReports(Map.of(token, refs.issue(7, 10, 11)))).isEmpty();
            var ref = refs.issue(7, 9, 11);
            assertThat(fullness.currentReports(Map.of(token, ref))).containsEntry(token, REPORT);
            assertThatThrownBy(() -> fullness.currentReports(Map.of(token, ref))).isInstanceOf(IllegalStateException.class);
        });
        var expired = tx.execute(status -> refs.issue(7, 9, 11));
        tx.executeWithoutResult(status -> assertThatThrownBy(() -> fullness.currentReports(Map.of(token, expired))).isInstanceOf(IllegalStateException.class));
    }

    @Test
    void emptyPageAndUnassignedAssetsNeedNoInventedPortReadings() {
        List<DeviceAssetView> empty = tx.execute(status -> query.enrich(List.of()));
        assertThat(empty).isEmpty();
        jdbc.update("DELETE FROM dev_port WHERE asset_id = 1");
        jdbc.update("UPDATE dev_device_asset SET tenant_id = NULL, organization_id = NULL WHERE id = 1");
        assertThat(read().listStatus().ports()).isEmpty();
    }

    private DeviceAssetView read() {
        var asset = new DeviceAssetView(ASSET, "device-test", "hardware-test", "model", null, 2,
                "tenant", "org", "PASSED", false, null, "NORMAL", 0,
                null, null, null, null, null, null, null, null, null, null, null, null);
        return tx.execute(status -> query.enrich(List.of(asset)).getFirst());
    }
}
