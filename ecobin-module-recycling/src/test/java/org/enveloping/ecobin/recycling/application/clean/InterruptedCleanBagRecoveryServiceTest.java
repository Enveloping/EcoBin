package org.enveloping.ecobin.recycling.application.clean;

import org.junit.jupiter.api.Test;

import java.util.Locale;

import static org.assertj.core.api.Assertions.assertThat;

class InterruptedCleanBagRecoveryServiceTest {

    @Test
    void acceptsOnlyTheOriginalOrAlreadyReservedBag() {
        assertThat(InterruptedCleanBagRecoveryService.decide(
                "EB1-OLD", "EB1-NEW", "EB1-OLD"))
                .isEqualTo(InterruptedCleanBagRecoveryService.Decision
                        .RETAIN_OLD_BAG);
        assertThat(InterruptedCleanBagRecoveryService.decide(
                "EB1-OLD", "EB1-NEW", "EB1-NEW"))
                .isEqualTo(InterruptedCleanBagRecoveryService.Decision
                        .USE_RESERVED_NEW_BAG);
        assertThat(InterruptedCleanBagRecoveryService.decide(
                "EB1-OLD", "EB1-NEW", "EB1-THIRD"))
                .isNull();
    }

    @Test
    void firstInstallationCanRecoverOnlyToItsReservedNewBag() {
        assertThat(InterruptedCleanBagRecoveryService.decide(
                null, "EB1-NEW", "EB1-NEW"))
                .isEqualTo(InterruptedCleanBagRecoveryService.Decision
                        .USE_RESERVED_NEW_BAG);
        assertThat(InterruptedCleanBagRecoveryService.decide(
                null, "EB1-NEW", "EB1-UNKNOWN"))
                .isNull();
    }

    @Test
    void nativeBaselineUsesTheConfirmedFiveSecondDeadline() {
        assertThat(InterruptedCleanBagRecoveryService
                .BASELINE_MEASUREMENT_TIMEOUT_MS)
                .isEqualTo(5_000L);
    }

    @Test
    void targetLockCarriesTheFrozenAndCurrentBagBaselineFacts() {
        String sql = InterruptedCleanBagRecoveryService.LOCK_TARGET_SQL
                .toLowerCase(Locale.ROOT);

        assertThat(sql).contains(
                "operation.old_bag_id",
                "operation.old_baseline_id",
                "operation.old_baseline_weight_g",
                "operation.new_bag_id",
                "operation.cleaner_organization_user_id",
                "capacity.current_bag_id",
                "capacity.current_baseline_id",
                "capacity.current_baseline_weight_g",
                "baseline.bag_id as baseline_bag_id",
                "runtime.applied_config_version_no",
                "for update");
    }

    @Test
    void sourceFaultLookupUsesTheTerminalReasonInsteadOfLatestFailure() {
        String source = readSource();

        assertThat(source).contains("String faultCode = target.endReason()")
                .contains("AND event.error_code = ?")
                .doesNotContain("ORDER BY event.id DESC");
    }

    @Test
    void manualRecoveryAcceptsOnlyRealBagUncertaintyTerminalReasons() {
        for (String reason : new String[]{
                "MCU_RESTART_FINAL_RESULT_UNAVAILABLE",
                "MCU_COMMUNICATION_UNAVAILABLE",
                "EDGE_RESTARTED",
                "MCU_CLEAN_FINAL_WEIGHT_UNAVAILABLE",
                "MCU_WORK_CANCELLED",
                "MCU_WORK_FAILED"}) {
            assertThat(InterruptedCleanBagRecoveryService
                    .supportsManualRecoveryFault(reason)).isTrue();
        }
        assertThat(InterruptedCleanBagRecoveryService
                .supportsManualRecoveryFault(
                        "MCU_INITIAL_WEIGHT_UNAVAILABLE")).isFalse();
        assertThat(InterruptedCleanBagRecoveryService
                .supportsManualRecoveryFault("WEIGHT_TIMEOUT")).isFalse();
    }

    private static String readSource() {
        try {
            return java.nio.file.Files.readString(java.nio.file.Path.of(
                    "src/main/java/org/enveloping/ecobin/recycling/"
                            + "application/clean/"
                            + "InterruptedCleanBagRecoveryService.java"));
        } catch (java.io.IOException exception) {
            throw new AssertionError(exception);
        }
    }
}
