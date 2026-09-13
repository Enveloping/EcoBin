package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Locale;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class TargetDeviceApplicationDeliveryRecoverySqlTest {

    private static final UUID SESSION_UID = UUID.fromString(
            "ccde9147-76e0-4a24-98a5-a3a7dc06aaa6");
    private static final UUID TASK_UID = UUID.fromString(
            "c8eb6ade-8ed1-4723-b601-ae364fc592d5");
    private static final LocalDateTime NOW =
            LocalDateTime.parse("2026-09-04T16:00:00");

    @Test
    void locksEveryMutableRecoveryRootBeforeMakingTheDecision() {
        assertThat(List.of(
                TargetDeviceApplication.LOCK_DELIVERY_RECOVERY_SESSION_SQL,
                TargetDeviceApplication
                        .LOCK_DELIVERY_RECOVERY_COMMAND_TASK_SQL,
                TargetDeviceApplication
                        .LOCK_DELIVERY_RECOVERY_OCCUPANCY_SQL))
                .allSatisfy(sql -> assertThat(upper(sql))
                        .contains("FOR UPDATE"));
    }

    @Test
    void closureRequiresExpiredAuthorizationAndNoRecordedProgress() {
        assertThat(upper(
                TargetDeviceApplication
                        .CLOSE_CONFIRMED_NOT_STARTED_DELIVERY_SQL))
                .contains(
                        "STATUS = 'RESULT_PENDING_RECOVERY'",
                        "AUTHORIZATION_EXPIRES_AT <= ?",
                        "FIRST_EDGE_ACCEPTED_AT IS NULL",
                        "FIRST_PHYSICAL_PROGRESS_AT IS NULL",
                        "DEVICE_COMPLETED_AT IS NULL",
                        "ENDED_AT IS NULL",
                        "END_REASON IS NULL",
                        "LOCK_VERSION = ?",
                        "STATUS = 'PRE_OPEN_ENDED'",
                        "OPERATOR_CONFIRMED_NOT_STARTED")
                .doesNotContain(
                        "UPDATE DEV_DEVICE_COMMAND",
                        "UPDATE OPS_RELIABLE_TASK");
    }

    @Test
    void recoveryRejectsAnyTrustedPhysicalEvidenceAndReleasesOnlyExactOccupancy() {
        assertThat(upper(
                TargetDeviceApplication.LOAD_DELIVERY_RECOVERY_EVIDENCE_SQL))
                .contains(
                        "DEV_DEVICE_COMMAND_EVENT",
                        "DEV_PHYSICAL_RESULT",
                        "REC_DELIVERY_ORDER");
        assertThat(upper(
                TargetDeviceApplication
                        .RELEASE_CONFIRMED_NOT_STARTED_OCCUPANCY_SQL))
                .contains(
                        "DELETE FROM DEV_DEVICE_OCCUPANCY",
                        "ASSET_ID = ?",
                        "TENANT_ID = ?",
                        "ORGANIZATION_ID = ?",
                        "OCCUPANCY_KIND = 'DELIVERY'",
                        "DELIVERY_SESSION_ID = ?");
    }

    @Test
    void exactExpiredEvidenceFreeStateCanBeClosed() {
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session(NOW.minusMinutes(1), null, null),
                command("QUEUED", null, "BLOCKED",
                        "DEVICE_EVIDENCE_TIMEOUT", null),
                true,
                new TargetDeviceApplication.DeliveryRecoveryEvidenceRow(
                        false, false, false),
                11L,
                12L,
                13L,
                TASK_UID,
                SESSION_UID,
                NOW)).isTrue();
    }

    @Test
    void activeAuthorizationOrAnyProgressFailsClosed() {
        var command = command(
                "QUEUED", null, "BLOCKED",
                "DEVICE_EVIDENCE_TIMEOUT", null);
        var noEvidence =
                new TargetDeviceApplication.DeliveryRecoveryEvidenceRow(
                        false, false, false);

        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session(NOW.plusSeconds(1), null, null), command, true,
                noEvidence, 11L, 12L, 13L,
                TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session(NOW.minusMinutes(1), NOW.minusSeconds(30), null),
                command, true, noEvidence, 11L, 12L, 13L,
                TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session(NOW.minusMinutes(1), null, NOW.minusSeconds(20)),
                command, true, noEvidence, 11L, 12L, 13L,
                TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session(NOW.minusMinutes(1), null, null),
                command("EDGE_ACCEPTED", NOW.minusSeconds(10), "BLOCKED",
                        "DEVICE_EVIDENCE_TIMEOUT", null),
                true, noEvidence, 11L, 12L, 13L,
                TASK_UID, SESSION_UID, NOW)).isFalse();
    }

    @Test
    void mismatchedTaskOccupancyOrEvidenceFailsClosed() {
        var session = session(NOW.minusMinutes(1), null, null);
        var command = command(
                "QUEUED", null, "BLOCKED",
                "DEVICE_EVIDENCE_TIMEOUT", null);
        var noEvidence =
                new TargetDeviceApplication.DeliveryRecoveryEvidenceRow(
                        false, false, false);

        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session, command, false, noEvidence,
                11L, 12L, 13L, TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session,
                command("QUEUED", null, "BLOCKED",
                        "DEVICE_EVIDENCE_TIMEOUT", "lease"),
                true, noEvidence, 11L, 12L, 13L,
                TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session, command, true,
                new TargetDeviceApplication.DeliveryRecoveryEvidenceRow(
                        true, false, false),
                11L, 12L, 13L, TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session, command, true,
                new TargetDeviceApplication.DeliveryRecoveryEvidenceRow(
                        false, true, false),
                11L, 12L, 13L, TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(TargetDeviceApplication.canConfirmDeliveryNotStarted(
                session, command, true,
                new TargetDeviceApplication.DeliveryRecoveryEvidenceRow(
                        false, false, true),
                11L, 12L, 13L, TASK_UID, SESSION_UID, NOW)).isFalse();
    }

    private static TargetDeviceApplication.DeliveryRecoverySessionRow session(
            LocalDateTime authorizationExpiresAt,
            LocalDateTime firstEdgeAcceptedAt,
            LocalDateTime firstPhysicalProgressAt) {
        return new TargetDeviceApplication.DeliveryRecoverySessionRow(
                17L,
                SESSION_UID,
                11L,
                12L,
                13L,
                "RESULT_PENDING_RECOVERY",
                authorizationExpiresAt,
                firstEdgeAcceptedAt,
                firstPhysicalProgressAt,
                null,
                null,
                null,
                null,
                1L);
    }

    private static TargetDeviceApplication.DeliveryRecoveryCommandTaskRow
            command(
                    String physicalState,
                    LocalDateTime edgeAcceptedAt,
                    String taskState,
                    String blockedReasonCode,
                    String leaseToken) {
        return new TargetDeviceApplication.DeliveryRecoveryCommandTaskRow(
                18L,
                physicalState,
                edgeAcceptedAt,
                null,
                null,
                TASK_UID,
                "START_DELIVERY_SESSION",
                taskState,
                blockedReasonCode,
                "DELIVERY_SESSION",
                SESSION_UID.toString(),
                leaseToken);
    }

    private static String upper(String sql) {
        return sql.toUpperCase(Locale.ROOT);
    }
}
