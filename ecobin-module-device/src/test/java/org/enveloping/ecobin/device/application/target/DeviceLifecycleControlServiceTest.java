package org.enveloping.ecobin.device.application.target;

import org.enveloping.ecobin.device.api.port.DeviceLifecycleParticipationPort;
import org.enveloping.ecobin.device.application.software.DeviceSoftwareCompatibilityService;
import org.enveloping.ecobin.framework.reliability.ReliableDeviceTaskLifecyclePort;
import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

class DeviceLifecycleControlServiceTest {
    private JdbcTemplate jdbc;
    private TransactionTemplate tx;
    private ReliableDeviceTaskLifecyclePort tasks;
    private DeviceLifecycleParticipationPort participant;
    private DeviceSoftwareCompatibilityService compatibility;
    private DeviceLifecycleControlService service;
    private final LocalDateTime now = LocalDateTime.of(2026, 9, 12, 12, 0);

    @BeforeEach
    void setup() {
        var ds = new DriverManagerDataSource("jdbc:h2:mem:" + UUID.randomUUID() + ";MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", "");
        jdbc = new JdbcTemplate(ds);
        tx = new TransactionTemplate(new DataSourceTransactionManager(ds));
        jdbc.execute("CREATE TABLE dev_device_asset (id BIGINT PRIMARY KEY, lifecycle_status VARCHAR(20), tenant_id BIGINT, acceptance_status VARCHAR(20), acceptance_generation BIGINT, accepted_at TIMESTAMP, acceptance_evidence_sha256 BINARY(32), acceptance_failure_json VARCHAR(200))");
        jdbc.execute("INSERT INTO dev_device_asset (id, lifecycle_status) VALUES (1, 'NORMAL')");
        jdbc.execute("CREATE TABLE dev_delivery_session (asset_id BIGINT, status VARCHAR(40))");
        jdbc.execute("CREATE TABLE dev_device_occupancy (asset_id BIGINT)");
        jdbc.execute("CREATE TABLE dev_remote_support_session (asset_id BIGINT, state VARCHAR(20), lease_released_at TIMESTAMP, server_lease_state VARCHAR(20))");
        for (String table : List.of("dev_mcu_firmware_deployment", "dev_edge_software_deployment")) {
            jdbc.execute("CREATE TABLE " + table + " (asset_id BIGINT, deployment_status VARCHAR(40), reliable_task_uid VARCHAR(36), command_uid VARCHAR(36), queued_at TIMESTAMP, error_code VARCHAR(64), completed_at TIMESTAMP, updated_at TIMESTAMP, lock_version BIGINT DEFAULT 0)");
        }
        jdbc.execute("CREATE TABLE dev_config_application (asset_id BIGINT, status VARCHAR(20), last_failure_code VARCHAR(64), last_failure_at TIMESTAMP, updated_at TIMESTAMP, lock_version BIGINT DEFAULT 0)");
        jdbc.execute("INSERT INTO dev_config_application (asset_id, status) VALUES (1, 'EDGE_SAVED'), (1, 'APPLIED'), (2, 'PENDING')");
        jdbc.execute("CREATE TABLE dev_factory_seal_authorization (asset_id BIGINT, acceptance_generation BIGINT, authorization_status VARCHAR(20), reliable_task_uid VARCHAR(36), cancelled_at TIMESTAMP, cancellation_reason VARCHAR(64), updated_at TIMESTAMP)");
        tasks = mock(ReliableDeviceTaskLifecyclePort.class);
        participant = mock(DeviceLifecycleParticipationPort.class);
        compatibility = mock(DeviceSoftwareCompatibilityService.class);
        service = new DeviceLifecycleControlService(jdbc, tasks, List.of(participant), compatibility);
    }

    @ParameterizedTest
    @ValueSource(strings = {"DISABLED", "RETIRED"})
    void rejectsDeliveryRecoveryWithoutChangingAssetOrTasks(String status) {
        jdbc.execute("INSERT INTO dev_delivery_session VALUES (1, 'RESULT_PENDING_RECOVERY')");
        assertThatThrownBy(() -> control(status)).isInstanceOfSatisfying(TargetApiException.class,
                error -> assertThat(error.code()).isEqualTo("DEVICE.CONTROL_BUSY_DELIVERY"));
        assertUnchanged();
    }

    @ParameterizedTest
    @ValueSource(strings = {"DISABLED", "RETIRED"})
    void rejectsCleaningParticipantAndNeverPartiallyCancels(String status) {
        doThrow(new TargetApiException(409, "DEVICE.CONTROL_BUSY_CLEAN", "清运未结束"))
                .when(participant).requireIdle(any());
        assertThatThrownBy(() -> control(status)).isInstanceOf(TargetApiException.class);
        assertUnchanged();
    }

    @ParameterizedTest
    @ValueSource(strings = {"DISABLED", "RETIRED"})
    void closedRemoteStateStillBlocksUntilServerLeaseIsReleased(String status) {
        jdbc.execute("INSERT INTO dev_remote_support_session VALUES (1, 'CLOSED', NULL, 'ACTIVE')");
        assertThatThrownBy(() -> control(status)).isInstanceOfSatisfying(TargetApiException.class,
                error -> assertThat(error.code()).isEqualTo("DEVICE.CONTROL_BUSY_REMOTE_SUPPORT"));
        assertUnchanged();
    }

    @Test
    void queuedUpgradeWithExternalMarkerBlocksEvenWithoutDeviceProgress() {
        UUID task = UUID.randomUUID();
        jdbc.update("INSERT INTO dev_mcu_firmware_deployment (asset_id, deployment_status, reliable_task_uid) VALUES (1, 'QUEUED', ?)", task.toString());
        when(tasks.externalCallMayHaveStarted(task)).thenReturn(true);
        assertThatThrownBy(() -> control("RETIRED")).isInstanceOfSatisfying(TargetApiException.class,
                error -> assertThat(error.code()).isEqualTo("DEVICE.CONTROL_BUSY_UPGRADE"));
        assertThat(jdbc.queryForObject("SELECT lifecycle_status FROM dev_device_asset", String.class)).isEqualTo("NORMAL");
        verify(tasks, never()).cancelDeviceWork(anyString());
    }

    @Test
    void cancelsUnsentWorkAndPreservesFinishedWorkAndOtherDevices() {
        jdbc.execute("INSERT INTO dev_delivery_session VALUES (1, 'DEVICE_ABORTED')");
        jdbc.execute("INSERT INTO dev_mcu_firmware_deployment (asset_id, deployment_status) VALUES (1, 'PENDING'), (1, 'SUCCEEDED')");
        jdbc.execute("INSERT INTO dev_edge_software_deployment (asset_id, deployment_status) VALUES (1, 'PLANNED')");
        control("RETIRED");
        assertThat(jdbc.queryForList("SELECT status FROM dev_config_application WHERE asset_id = 1", String.class))
                .containsExactlyInAnyOrder("CANCELLED", "APPLIED");
        assertThat(jdbc.queryForObject("SELECT status FROM dev_config_application WHERE asset_id = 2", String.class)).isEqualTo("PENDING");
        assertThat(jdbc.queryForList("SELECT deployment_status FROM dev_mcu_firmware_deployment", String.class))
                .containsExactlyInAnyOrder("LOCAL_CANCELLED", "SUCCEEDED");
        assertThat(jdbc.queryForObject("SELECT deployment_status FROM dev_edge_software_deployment", String.class)).isEqualTo("LOCAL_CANCELLED");
        verify(tasks).cancelDeviceWork("HW-1");
        verify(compatibility).reassessLatestFact(1, now);
        verify(participant).cancelDeviceWork(any(), eq("DEVICE_RETIRED"), eq(now));
    }

    @Test
    void disablingPreservesConfigurationAndUpgradeIntentsWithoutRevivingCompletedWork() {
        jdbc.execute("INSERT INTO dev_mcu_firmware_deployment (asset_id, deployment_status) VALUES (1, 'PENDING'), (1, 'SUCCEEDED')");
        jdbc.execute("INSERT INTO dev_edge_software_deployment (asset_id, deployment_status) VALUES (1, 'PLANNED')");
        control("DISABLED");
        assertThat(jdbc.queryForList("SELECT status FROM dev_config_application WHERE asset_id = 1", String.class))
                .containsExactlyInAnyOrder("EDGE_SAVED", "APPLIED");
        assertThat(jdbc.queryForList("SELECT deployment_status FROM dev_mcu_firmware_deployment", String.class))
                .containsExactlyInAnyOrder("PENDING", "SUCCEEDED");
        assertThat(jdbc.queryForObject("SELECT deployment_status FROM dev_edge_software_deployment", String.class)).isEqualTo("PLANNED");
        verify(tasks).pauseDeviceWork("HW-1");
        verify(tasks, never()).cancelDeviceWork(anyString());
        verifyNoInteractions(compatibility);
    }

    @ParameterizedTest
    @ValueSource(strings = {"dev_mcu_firmware_deployment", "dev_edge_software_deployment"})
    void restoringReplacesOnlyTheQueuedDeploymentsExpiredAuthorization(String table) {
        UUID oldTask = UUID.randomUUID();
        UUID nextTask = UUID.randomUUID();
        UUID nextCommand = UUID.randomUUID();
        jdbc.update("INSERT INTO " + table + " (asset_id, deployment_status, reliable_task_uid) VALUES (1, 'QUEUED', ?)", oldTask.toString());
        jdbc.execute("INSERT INTO " + table + " (asset_id, deployment_status) VALUES (1, 'LOCAL_CANCELLED'), (1, 'SUCCEEDED')");
        when(tasks.renewExpiredUnsentUpgradeTask("HW-1", oldTask)).thenReturn(java.util.Optional.of(
                new ReliableDeviceTaskLifecyclePort.RenewedUpgradeCommand(nextTask, nextCommand)));
        tx.executeWithoutResult(transaction -> service.resumeDeviceWork(1, "HW-1", now));
        assertThat(jdbc.queryForObject("SELECT reliable_task_uid FROM " + table + " WHERE deployment_status = 'QUEUED'", String.class))
                .isEqualTo(nextTask.toString());
        assertThat(jdbc.queryForObject("SELECT command_uid FROM " + table + " WHERE deployment_status = 'QUEUED'", String.class))
                .isEqualTo(nextCommand.toString());
        assertThat(jdbc.queryForList("SELECT deployment_status FROM " + table, String.class))
                .containsExactlyInAnyOrder("QUEUED", "LOCAL_CANCELLED", "SUCCEEDED");
        verify(tasks).resumeDeviceWork("HW-1");
    }

    @Test
    void participantFailureRollsBackStatusAndConfigurationTogether() {
        doThrow(new IllegalStateException("database write failed"))
                .when(participant).cancelDeviceWork(any(), eq("DEVICE_RETIRED"), eq(now));
        assertThatThrownBy(() -> control("RETIRED")).isInstanceOf(IllegalStateException.class);
        assertThat(jdbc.queryForObject("SELECT lifecycle_status FROM dev_device_asset", String.class)).isEqualTo("NORMAL");
        assertThat(jdbc.queryForList("SELECT status FROM dev_config_application WHERE asset_id = 1", String.class))
                .containsExactlyInAnyOrder("EDGE_SAVED", "APPLIED");
    }

    private void control(String status) {
        tx.executeWithoutResult(transaction -> {
            jdbc.queryForObject("SELECT id FROM dev_device_asset WHERE id = 1 FOR UPDATE", Long.class);
            service.requireIdle(1, "HW-1");
            jdbc.update("UPDATE dev_device_asset SET lifecycle_status = ? WHERE id = 1", status);
            service.cancelDeviceWork(1, "HW-1", status, now);
        });
    }

    private void assertUnchanged() {
        assertThat(jdbc.queryForObject("SELECT lifecycle_status FROM dev_device_asset", String.class)).isEqualTo("NORMAL");
        assertThat(jdbc.queryForList("SELECT status FROM dev_config_application WHERE asset_id = 1", String.class))
                .containsExactlyInAnyOrder("EDGE_SAVED", "APPLIED");
        verifyNoInteractions(tasks);
    }
}
