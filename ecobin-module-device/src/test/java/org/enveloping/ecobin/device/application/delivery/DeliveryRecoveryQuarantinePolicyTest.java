package org.enveloping.ecobin.device.application.delivery;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.Locale;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class DeliveryRecoveryQuarantinePolicyTest {

    private static final UUID SESSION_UID = UUID.fromString(
            "fca37401-7b2e-4b42-93cf-2fc8c6d72fb2");
    private static final UUID ORIGINAL_COMMAND_UID = UUID.fromString(
            "4c73f918-d9ac-4e77-b0fa-9bb85de20e57");
    private static final UUID ORIGINAL_TASK_UID = UUID.fromString(
            "2b32dc99-5f88-401c-b1e8-eab711f660b1");
    private static final LocalDateTime NOW =
            LocalDateTime.parse("2026-09-10T10:00:00");

    @Test
    void exactUncertainDeliveryCanRequestIssueOnlyQuarantine() {
        assertThat(DeliveryRecoveryQuarantineService.canRequest(
                session("RESULT_PENDING_RECOVERY", NOW.minusMinutes(1)),
                command("DONE", "EDGE_ACCEPTED", null, null),
                true,
                new DeliveryRecoveryQuarantineService.BusinessEvidence(
                        false, false),
                11L,
                12L,
                13L,
                ORIGINAL_TASK_UID,
                SESSION_UID,
                NOW)).isTrue();
    }

    @Test
    void requestFailsClosedForWrongGenerationUnsafeTaskOrBusinessResult() {
        var session = session(
                "RESULT_PENDING_RECOVERY", NOW.minusMinutes(1));
        var command = command("DONE", "EDGE_ACCEPTED", null, null);
        var noBusinessResult =
                new DeliveryRecoveryQuarantineService.BusinessEvidence(
                        false, false);

        assertThat(DeliveryRecoveryQuarantineService.canRequest(
                session, command, false, noBusinessResult,
                11L, 12L, 13L, ORIGINAL_TASK_UID, SESSION_UID, NOW))
                .isFalse();
        assertThat(DeliveryRecoveryQuarantineService.canRequest(
                session, command("PENDING", "QUEUED", null, "lease"), true,
                noBusinessResult, 11L, 12L, 13L,
                ORIGINAL_TASK_UID, SESSION_UID, NOW)).isFalse();
        assertThat(DeliveryRecoveryQuarantineService.canRequest(
                session, command, true,
                new DeliveryRecoveryQuarantineService.BusinessEvidence(
                        true, false),
                11L, 12L, 13L, ORIGINAL_TASK_UID, SESSION_UID, NOW))
                .isFalse();
        assertThat(DeliveryRecoveryQuarantineService.canRequest(
                session, command, true,
                new DeliveryRecoveryQuarantineService.BusinessEvidence(
                        false, true),
                11L, 12L, 13L, ORIGINAL_TASK_UID, SESSION_UID, NOW))
                .isFalse();
        assertThat(DeliveryRecoveryQuarantineService.canRequest(
                session("DEVICE_ABORTED", NOW.minusMinutes(1)),
                command, true, noBusinessResult,
                11L, 12L, 13L, ORIGINAL_TASK_UID, SESSION_UID, NOW))
                .isFalse();
        assertThat(DeliveryRecoveryQuarantineService.canRequest(
                session("RESULT_PENDING_RECOVERY", NOW.plusSeconds(1)),
                command, true, noBusinessResult,
                11L, 12L, 13L, ORIGINAL_TASK_UID, SESSION_UID, NOW))
                .isFalse();
    }

    @Test
    void terminalMutationOnlyAbortsSessionReleasesExactOccupancyAndArchivesEvidence() {
        String mutationSql = upper(
                DeliveryRecoveryQuarantineService.ABORT_SESSION_SQL
                        + DeliveryRecoveryQuarantineService
                        .RELEASE_OCCUPANCY_SQL
                        + DeliveryRecoveryQuarantineService
                        .ARCHIVE_EVIDENCE_SQL);

        assertThat(mutationSql)
                .contains(
                        "STATUS = 'DEVICE_ABORTED'",
                        "REMOTE_RECOVERY_QUARANTINED",
                        "DELETE FROM DEV_DEVICE_OCCUPANCY",
                        "OCCUPANCY_KIND = 'DELIVERY'",
                        "DELIVERY_SESSION_ID = ?",
                        "UPDATE DEV_DELIVERY_RECOVERY_QUARANTINE",
                        "BUSINESS_VALUE = 'NONE'")
                .doesNotContain(
                        "REC_DELIVERY_ORDER",
                        "FIN_WALLET",
                        "WITHDRAW",
                        "REFUND",
                        "AUTO_REVIEW");
    }

    private static DeliveryRecoveryQuarantineService.SessionRow session(
            String status,
            LocalDateTime authorizationExpiresAt) {
        return new DeliveryRecoveryQuarantineService.SessionRow(
                17L,
                SESSION_UID,
                11L,
                12L,
                13L,
                status,
                authorizationExpiresAt,
                null,
                null,
                null,
                null,
                null,
                4L);
    }

    private static DeliveryRecoveryQuarantineService.CommandTaskRow command(
            String taskState,
            String physicalState,
            String blockedReasonCode,
            String leaseToken) {
        return new DeliveryRecoveryQuarantineService.CommandTaskRow(
                18L,
                ORIGINAL_COMMAND_UID,
                physicalState,
                ORIGINAL_TASK_UID,
                "START_DELIVERY_SESSION",
                taskState,
                blockedReasonCode,
                "DELIVERY_SESSION",
                SESSION_UID.toString(),
                leaseToken);
    }

    private static String upper(String value) {
        return value.toUpperCase(Locale.ROOT);
    }
}
