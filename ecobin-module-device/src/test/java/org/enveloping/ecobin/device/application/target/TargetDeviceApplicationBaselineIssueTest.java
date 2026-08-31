package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

class TargetDeviceApplicationBaselineIssueTest {

    @Test
    void automaticRetryDependsOnlyOnSystemGenerationBudgetAndFault() {
        assertThat(TargetDeviceApplication
                .automaticBaselineRetryAvailable(0, false)).isTrue();
        assertThat(TargetDeviceApplication
                .automaticBaselineRetryAvailable(3, false)).isTrue();
        assertThat(TargetDeviceApplication
                .automaticBaselineRetryAvailable(4, false)).isFalse();
        assertThat(TargetDeviceApplication
                .automaticBaselineRetryAvailable(1, true)).isFalse();
    }

    @Test
    void acceptedManualMeasurementPointsAtItsReliableTask() {
        UUID taskUid = UUID.fromString(
                "59ca8ff0-7d95-4b85-97dc-bdb6fc90e95c");

        assertThat(TargetDeviceApplication.baselineTaskStatusUrl(taskUid))
                .isEqualTo(
                        "/api/v1/web/platform/operations/reliable-tasks/"
                                + taskUid);
    }

    @Test
    void blockedFactorySealTaskOffersDiagnosticsWithoutBlindReplay() {
        UUID taskUid = UUID.fromString(
                "99313185-f245-43ea-a14a-5deec2ca18a8");
        var issue = TargetDeviceApplication.taskIssue(
                new TargetDeviceApplication.TechnicalTaskRow(
                        21L,
                        taskUid,
                        "AUTHORIZE_FACTORY_SEAL",
                        "BLOCKED",
                        "DEVICE_IDENTITY_UNRESOLVED",
                        "redacted task blocker",
                        LocalDateTime.parse("2026-08-31T00:00:00"),
                        null,
                        null,
                        null,
                        null,
                        200,
                        "10410",
                        "redacted OneNet diagnosis"));

        assertThat(issue.category()).isEqualTo("FACTORY_SEAL");
        assertThat(issue.state()).isEqualTo("ACTION_REQUIRED");
        assertThat(issue.taskUid()).isEqualTo(taskUid);
        assertThat(issue.blockedReasonCode())
                .isEqualTo("DEVICE_IDENTITY_UNRESOLVED");
        assertThat(issue.diagnostic())
                .isEqualTo("redacted OneNet diagnosis");
        assertThat(issue.nextActions()).containsExactly(
                "OPEN_RELIABLE_TASK",
                "RESOLVE_FACTORY_SEAL_TASK_BLOCKER");
        assertThat(issue.nextActions()).doesNotContain("REPLAY_TASK");
    }
}
