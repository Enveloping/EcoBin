package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.Locale;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class TargetDeviceApplicationAbnormalDeliveryRetirementTest {

    private static final UUID SESSION_UID = UUID.fromString(
            "80acf8e0-8ff6-4cc3-a6a2-1b22621426cc");
    private static final UUID TASK_UID = UUID.fromString(
            "ed414b4e-05ab-45a2-a7da-b5a20aa5dd5b");
    private static final LocalDateTime NOW =
            LocalDateTime.parse("2026-09-14T02:00:00");

    @Test
    void productionShapedStartedDeliveryCanBeClosedOnlyWithRetirement() {
        assertThat(allowed(
                session("IN_PROGRESS", NOW.minusDays(4), null),
                command("PHYSICAL_STARTED", "DONE", null),
                transport("OFFLINE", NOW.minusMinutes(11)),
                true,
                false,
                evidence(false, false))).isTrue();
    }

    @Test
    void onlineOrRecentlyOfflineDeviceFailsClosed() {
        var session = session("IN_PROGRESS", NOW.minusDays(4), null);
        var command = command("PHYSICAL_STARTED", "DONE", null);
        assertThat(allowed(session, command,
                transport("ONLINE", null), true, false,
                evidence(false, false))).isFalse();
        assertThat(allowed(session, command,
                transport("OFFLINE", NOW.minusMinutes(9)), true, false,
                evidence(false, false))).isFalse();
    }

    @Test
    void activeAuthorizationCompletionOrBusinessResultFailsClosed() {
        var command = command("PHYSICAL_STARTED", "DONE", null);
        var offline = transport("OFFLINE", NOW.minusMinutes(11));
        assertThat(allowed(
                session("IN_PROGRESS", NOW.plusSeconds(1), null),
                command, offline, true, false,
                evidence(false, false))).isFalse();
        assertThat(allowed(
                session("IN_PROGRESS", NOW.minusDays(4), NOW.minusMinutes(1)),
                command, offline, true, false,
                evidence(false, false))).isFalse();
        assertThat(allowed(
                session("IN_PROGRESS", NOW.minusDays(4), null),
                command, offline, true, false,
                evidence(true, false))).isFalse();
        assertThat(allowed(
                session("IN_PROGRESS", NOW.minusDays(4), null),
                command, offline, true, false,
                evidence(false, true))).isFalse();
    }

    @Test
    void wrongOccupancyActiveRecoveryOrTaskLeaseFailsClosed() {
        var session = session(
                "RESULT_PENDING_RECOVERY", NOW.minusDays(1), null);
        var offline = transport("OFFLINE", NOW.minusHours(1));
        assertThat(allowed(session,
                command("EDGE_ACCEPTED", "BLOCKED", null),
                offline, false, false, evidence(false, false))).isFalse();
        assertThat(allowed(session,
                command("EDGE_ACCEPTED", "BLOCKED", null),
                offline, true, true, evidence(false, false))).isFalse();
        assertThat(allowed(session,
                command("EDGE_ACCEPTED", "BLOCKED", "active-lease"),
                offline, true, false, evidence(false, false))).isFalse();
    }

    @Test
    void mismatchedScopeIdentityOrTaskStateFailsClosed() {
        var session = session("IN_PROGRESS", NOW.minusDays(1), null);
        var command = command("PHYSICAL_STARTED", "DONE", null);
        var offline = transport("OFFLINE", NOW.minusHours(1));
        var noResult = evidence(false, false);

        assertThat(TargetDeviceApplication.canRetireAbnormalDelivery(
                session,
                command,
                offline,
                true,
                false,
                noResult,
                99L,
                12L,
                13L,
                TASK_UID,
                SESSION_UID,
                NOW)).isFalse();
        assertThat(TargetDeviceApplication.canRetireAbnormalDelivery(
                session,
                command,
                offline,
                true,
                false,
                noResult,
                11L,
                12L,
                13L,
                UUID.randomUUID(),
                SESSION_UID,
                NOW)).isFalse();
        assertThat(allowed(
                session,
                command("PHYSICAL_STARTED", "CANCELLED", null),
                offline,
                true,
                false,
                noResult)).isFalse();
    }

    @Test
    void updateSqlEndsOnlyTheExactSessionAndPermanentlyRetiresAsset() {
        String close = upper(TargetDeviceApplication
                .CLOSE_ABNORMAL_DELIVERY_FOR_RETIREMENT_SQL);
        String retire = upper(TargetDeviceApplication
                .RETIRE_AFTER_ABNORMAL_DELIVERY_SQL);
        assertThat(close).contains(
                "STATUS = 'DEVICE_ABORTED'",
                "OPERATOR_RETIRED_UNKNOWN_DELIVERY",
                "STATUS IN ('IN_PROGRESS', 'RESULT_PENDING_RECOVERY')",
                "DEVICE_COMPLETED_AT IS NULL",
                "LOCK_VERSION = ?");
        assertThat(retire).contains(
                "LIFECYCLE_STATUS = 'RETIRED'",
                "LIFECYCLE_STATUS IN ('NORMAL', 'DISABLED')",
                "CONTROL_VERSION = ?");
    }

    private static boolean allowed(
            TargetDeviceApplication.DeliveryRecoverySessionRow session,
            TargetDeviceApplication.DeliveryRecoveryCommandTaskRow command,
            TargetDeviceApplication.DeliveryRetirementTransportRow transport,
            boolean expectedOccupancy,
            boolean activeRecovery,
            TargetDeviceApplication.DeliveryRecoveryEvidenceRow evidence) {
        return TargetDeviceApplication.canRetireAbnormalDelivery(
                session,
                command,
                transport,
                expectedOccupancy,
                activeRecovery,
                evidence,
                11L,
                12L,
                13L,
                TASK_UID,
                SESSION_UID,
                NOW);
    }

    private static TargetDeviceApplication.DeliveryRecoverySessionRow session(
            String status,
            LocalDateTime authorizationExpiresAt,
            LocalDateTime completedAt) {
        return new TargetDeviceApplication.DeliveryRecoverySessionRow(
                17L,
                SESSION_UID,
                11L,
                12L,
                13L,
                status,
                authorizationExpiresAt,
                NOW.minusDays(4),
                NOW.minusDays(4),
                completedAt,
                null,
                null,
                null,
                2L);
    }

    private static TargetDeviceApplication.DeliveryRecoveryCommandTaskRow
            command(String physicalState, String taskState, String lease) {
        return new TargetDeviceApplication.DeliveryRecoveryCommandTaskRow(
                18L,
                physicalState,
                NOW.minusDays(4),
                NOW.minusDays(4),
                null,
                TASK_UID,
                "START_DELIVERY_SESSION",
                taskState,
                null,
                "DELIVERY_SESSION",
                SESSION_UID.toString(),
                lease);
    }

    private static TargetDeviceApplication.DeliveryRetirementTransportRow
            transport(String status, LocalDateTime offlineSinceAt) {
        return new TargetDeviceApplication.DeliveryRetirementTransportRow(
                status, offlineSinceAt);
    }

    private static TargetDeviceApplication.DeliveryRecoveryEvidenceRow evidence(
            boolean physicalResult,
            boolean order) {
        return new TargetDeviceApplication.DeliveryRecoveryEvidenceRow(
                true, physicalResult, order);
    }

    private static String upper(String value) {
        return value.toUpperCase(Locale.ROOT);
    }
}
