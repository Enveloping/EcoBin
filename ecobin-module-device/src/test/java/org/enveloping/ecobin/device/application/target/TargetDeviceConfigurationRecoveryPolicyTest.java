package org.enveloping.ecobin.device.application.target;

import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class TargetDeviceConfigurationRecoveryPolicyTest {

    @Test
    void onlyPreDeliveryBlockedCommandsCanBeResynchronized() {
        assertThat(canResynchronize("DEVICE_IDENTITY_UNRESOLVED"))
                .isTrue();
        assertThat(canResynchronize("PERMANENT_TECHNICAL_FAILURE"))
                .isTrue();
        assertThat(canResynchronize("AUTO_RETRY_EXHAUSTED"))
                .isTrue();
    }

    @Test
    void evidenceTimeoutsAndUnknownReasonsRequireANewVersion() {
        assertThat(canResynchronize("DEVICE_EVIDENCE_TIMEOUT"))
                .isFalse();
        assertThat(canResynchronize("DEVICE_CONFIRMATION_TIMEOUT"))
                .isFalse();
        assertThat(canResynchronize("SOME_FUTURE_REASON"))
                .isFalse();
        assertThat(canResynchronize(null)).isFalse();
    }

    @Test
    void persistedOrTerminalApplicationsRequireANewVersion() {
        assertThat(TargetDeviceApplication.canResynchronizeConfiguration(
                "FAILED", null, "BLOCKED", "AUTO_RETRY_EXHAUSTED"))
                .isFalse();
        assertThat(TargetDeviceApplication.canResynchronizeConfiguration(
                "EDGE_SAVED", Instant.now(), "BLOCKED",
                "AUTO_RETRY_EXHAUSTED"))
                .isFalse();
        assertThat(TargetDeviceApplication.canResynchronizeConfiguration(
                "APPLIED", Instant.now(), "BLOCKED",
                "AUTO_RETRY_EXHAUSTED"))
                .isFalse();
        assertThat(TargetDeviceApplication.canResynchronizeConfiguration(
                "PENDING", Instant.now(), "BLOCKED",
                "AUTO_RETRY_EXHAUSTED"))
                .isFalse();
    }

    @Test
    void anExecutingTaskCannotBeWokenThroughResynchronization() {
        assertThat(TargetDeviceApplication.canResynchronizeConfiguration(
                "PENDING", null, "PENDING",
                "AUTO_RETRY_EXHAUSTED"))
                .isFalse();
    }

    @Test
    void aLateEdgeSavedFactCannotReopenFailedOrAppliedApplications() {
        assertThat(TrustedConfigurationProgressService
                .canMergeEdgeSavedInto("PENDING")).isTrue();
        assertThat(TrustedConfigurationProgressService
                .canMergeEdgeSavedInto("EDGE_SAVED")).isTrue();
        assertThat(TrustedConfigurationProgressService
                .canMergeEdgeSavedInto("FAILED")).isFalse();
        assertThat(TrustedConfigurationProgressService
                .canMergeEdgeSavedInto("APPLIED")).isFalse();
    }

    private static boolean canResynchronize(String reason) {
        return TargetDeviceApplication.canResynchronizeConfiguration(
                "PENDING", null, "BLOCKED", reason);
    }
}
