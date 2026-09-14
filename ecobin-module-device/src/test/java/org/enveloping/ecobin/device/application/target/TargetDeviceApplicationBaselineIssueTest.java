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
                        false,
                        false,
                        false,
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

    @Test
    void uncertainDeliveryOffersOnlyTheExplicitOnsiteClosureWhenEligible() {
        UUID taskUid = UUID.fromString(
                "c8eb6ade-8ed1-4723-b601-ae364fc592d5");
        UUID sessionUid = UUID.fromString(
                "ccde9147-76e0-4a24-98a5-a3a7dc06aaa6");
        var issue = TargetDeviceApplication.taskIssue(
                new TargetDeviceApplication.TechnicalTaskRow(
                        22L,
                        taskUid,
                        "START_DELIVERY_SESSION",
                        "BLOCKED",
                        "DEVICE_EVIDENCE_TIMEOUT",
                        "OneNet accepted but no trusted edge evidence arrived",
                        LocalDateTime.parse("2026-09-04T15:14:48"),
                        "RESULT_PENDING_RECOVERY",
                        sessionUid,
                        3L,
                        true,
                        true,
                        false,
                        null,
                        null,
                        null,
                        200,
                        null,
                        "accepted without device evidence"));

        assertThat(issue.deliverySessionUid()).isEqualTo(sessionUid);
        assertThat(issue.deliverySessionVersion()).isEqualTo(3L);
        assertThat(issue.nextActions()).containsExactly(
                "CONFIRM_DELIVERY_NOT_STARTED");
        assertThat(issue.nextActions()).doesNotContain("RESUME", "REPLAY_TASK");
    }

    @Test
    void uncertainDeliveryOffersRemoteIssueOnlyQuarantineWhenEligible() {
        var issue = TargetDeviceApplication.taskIssue(
                new TargetDeviceApplication.TechnicalTaskRow(
                        23L,
                        UUID.fromString(
                                "f5499548-df41-4aba-9ba1-9bd9d6714f0d"),
                        "START_DELIVERY_SESSION",
                        "BLOCKED",
                        "DEVICE_EVIDENCE_TIMEOUT",
                        "device might have started physical work",
                        LocalDateTime.parse("2026-09-04T15:15:48"),
                        "RESULT_PENDING_RECOVERY",
                        UUID.fromString(
                                "77f35bc9-7d56-4898-8897-247cd03d2e0f"),
                        4L,
                        false,
                        true,
                        false,
                        null,
                        null,
                        null,
                        200,
                        null,
                        "physical result remains uncertain"));

        assertThat(issue.nextActions()).containsExactly(
                "QUARANTINE_DELIVERY_RECOVERY");
        assertThat(issue.description()).contains(
                "不会创建投递订单",
                "不会增加余额",
                "不会触发自动提现");
    }

    @Test
    void permanentlyOfflineStartedDeliveryOffersIndependentTermination() {
        var issue = TargetDeviceApplication.taskIssue(
                new TargetDeviceApplication.TechnicalTaskRow(
                        24L,
                        UUID.fromString(
                                "ed414b4e-05ab-45a2-a7da-b5a20aa5dd5b"),
                        "START_DELIVERY_SESSION",
                        "DONE",
                        null,
                        null,
                        LocalDateTime.parse("2026-09-09T13:47:00"),
                        "IN_PROGRESS",
                        UUID.fromString(
                                "80acf8e0-8ff6-4cc3-a6a2-1b22621426cc"),
                        2L,
                        false,
                        false,
                        true,
                        null,
                        null,
                        null,
                        null,
                        null,
                        null));

        assertThat(issue.title()).isEqualTo("投递物理结果无法确认");
        assertThat(issue.description()).contains(
                "连续离线", "没有生成订单", "保持禁用",
                "单独选择恢复或报废");
        assertThat(issue.nextActions()).containsExactly(
                "END_ABNORMAL_DELIVERY");
    }

    @Test
    void confirmedUnstartedDeliveryExplainsThatUserMustStartAgain() {
        UUID taskUid = UUID.fromString(
                "c8eb6ade-8ed1-4723-b601-ae364fc592d5");
        UUID sessionUid = UUID.fromString(
                "ccde9147-76e0-4a24-98a5-a3a7dc06aaa6");
        var issue = TargetDeviceApplication.taskIssue(
                new TargetDeviceApplication.TechnicalTaskRow(
                        22L,
                        taskUid,
                        "START_DELIVERY_SESSION",
                        "BLOCKED",
                        "DEVICE_EVIDENCE_TIMEOUT",
                        "OneNet accepted but no trusted edge evidence arrived",
                        LocalDateTime.parse("2026-09-04T15:14:48"),
                        "PRE_OPEN_ENDED",
                        sessionUid,
                        4L,
                        false,
                        false,
                        false,
                        null,
                        null,
                        null,
                        200,
                        null,
                        "accepted without device evidence"));

        assertThat(issue.title()).isEqualTo("原投递已安全结束");
        assertThat(issue.description())
                .contains("不会生成投递订单", "重新扫码");
        assertThat(issue.nextActions()).containsExactly(
                "USER_RESTART_REQUIRED");
    }
}
