package org.enveloping.ecobin.recycling.application.device;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RecyclingDeviceLifecycleServiceTest {
    private JdbcTemplate jdbc;
    private RecyclingDeviceLifecycleService service;

    @BeforeEach
    void setup() {
        jdbc = new JdbcTemplate(new DriverManagerDataSource(
                "jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", ""));
        service = new RecyclingDeviceLifecycleService(jdbc);
        jdbc.execute("CREATE TABLE rec_clean_operation (asset_id BIGINT, status VARCHAR(40))");
        jdbc.execute("CREATE TABLE rec_clean_bag_recovery (id BIGINT PRIMARY KEY, tenant_id BIGINT, organization_id BIGINT, status VARCHAR(40), updated_at TIMESTAMP, lock_version BIGINT DEFAULT 0)");
        jdbc.execute("CREATE TABLE rec_port_baseline_measurement (asset_id BIGINT, tenant_id BIGINT, organization_id BIGINT, clean_bag_recovery_id BIGINT, status VARCHAR(40), fault_code VARCHAR(64), completed_at TIMESTAMP, updated_at TIMESTAMP, lock_version BIGINT DEFAULT 0)");
        jdbc.execute("CREATE TABLE rec_fullness_detection (id BIGINT PRIMARY KEY, asset_id BIGINT, status VARCHAR(40), failure_code VARCHAR(64), disposition VARCHAR(40), next_sample_at TIMESTAMP, completed_at TIMESTAMP, updated_at TIMESTAMP, lock_version BIGINT DEFAULT 0)");
        jdbc.execute("CREATE TABLE rec_port_capacity_state (asset_id BIGINT, detection_gate VARCHAR(40), current_detection_id BIGINT, updated_at TIMESTAMP, lock_version BIGINT DEFAULT 0)");
    }

    @Test
    void unfinishedCleaningBlocksEvenWhenNoOccupancyRowIsAvailable() {
        jdbc.execute("INSERT INTO rec_clean_operation VALUES (1, 'RESULT_PENDING_RECOVERY')");
        assertThatThrownBy(() -> service.requireIdle(consumer -> consumer.accept(1)))
                .isInstanceOfSatisfying(TargetApiException.class,
                        error -> assertThat(error.code()).isEqualTo("DEVICE.CONTROL_BUSY_CLEAN"));
    }

    @Test
    void finishedOrAbortedCleaningHistoryDoesNotBlockDeviceControl() {
        jdbc.execute("INSERT INTO rec_clean_operation VALUES (1, 'ABORTED'), (1, 'COMPLETED'), (1, 'PRE_UNLOCK_ENDED'), (2, 'AUTHORIZED')");
        assertThatCode(() -> service.requireIdle(consumer -> consumer.accept(1))).doesNotThrowAnyException();
    }

    @ParameterizedTest
    @ValueSource(strings = {"DEVICE_DISABLED", "DEVICE_RETIRED"})
    void cancellationReleasesOnlyStoppedDevicesDetectionAndPreservesFinishedMeasurements(String reason) {
        jdbc.execute("INSERT INTO rec_clean_bag_recovery (id, tenant_id, organization_id, status) VALUES (7, 10, 20, 'BASELINE_PENDING')");
        jdbc.execute("INSERT INTO rec_port_baseline_measurement (asset_id, tenant_id, organization_id, clean_bag_recovery_id, status) VALUES (1, 10, 20, 7, 'PENDING'), (1, 10, 20, NULL, 'ADOPTED'), (2, 10, 20, NULL, 'PENDING')");
        jdbc.execute("INSERT INTO rec_fullness_detection (id, asset_id, status, next_sample_at) VALUES (10, 1, 'WAITING_RECHECK', CURRENT_TIMESTAMP), (20, 2, 'PENDING_INITIAL_SAMPLE', CURRENT_TIMESTAMP)");
        jdbc.execute("INSERT INTO rec_port_capacity_state (asset_id, detection_gate, current_detection_id) VALUES (1, 'WAITING_RECHECK', 10), (2, 'WAITING_RECHECK', 20)");

        service.cancelDeviceWork(consumer -> consumer.accept(1), reason, LocalDateTime.of(2026, 9, 12, 12, 0));

        assertThat(jdbc.queryForList("SELECT status FROM rec_port_baseline_measurement WHERE asset_id = 1", String.class))
                .containsExactlyInAnyOrder("TECHNICAL_ABORTED", "ADOPTED");
        assertThat(jdbc.queryForObject("SELECT fault_code FROM rec_port_baseline_measurement WHERE status = 'TECHNICAL_ABORTED'", String.class))
                .isEqualTo(reason);
        assertThat(jdbc.queryForObject(
                "SELECT status FROM rec_clean_bag_recovery WHERE id = 7",
                String.class)).isEqualTo("BASELINE_REQUIRED");
        assertThat(jdbc.queryForObject("SELECT status FROM rec_fullness_detection WHERE id = 10", String.class)).isEqualTo("CANCELLED");
        assertThat(jdbc.queryForObject("SELECT next_sample_at FROM rec_fullness_detection WHERE id = 10", LocalDateTime.class)).isNull();
        assertThat(jdbc.queryForObject("SELECT detection_gate FROM rec_port_capacity_state WHERE asset_id = 1", String.class)).isEqualTo("READY");
        assertThat(jdbc.queryForObject("SELECT current_detection_id FROM rec_port_capacity_state WHERE asset_id = 1", Long.class)).isNull();
        assertThat(jdbc.queryForObject("SELECT status FROM rec_fullness_detection WHERE id = 20", String.class)).isEqualTo("PENDING_INITIAL_SAMPLE");
        assertThat(jdbc.queryForObject("SELECT current_detection_id FROM rec_port_capacity_state WHERE asset_id = 2", Long.class)).isEqualTo(20);
    }
}
