package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

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
}
