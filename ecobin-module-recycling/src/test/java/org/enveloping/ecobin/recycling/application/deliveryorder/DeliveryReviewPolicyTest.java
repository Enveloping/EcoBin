package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class DeliveryReviewPolicyTest {

    @Test
    void originalApprovalUsesReliableFrozenRawFacts() {
        var result = DeliveryReviewPolicy.calculate(
                order(
                        new BigDecimal("-1.25"),
                        -100L,
                        "RELIABLE"),
                "ORIGINAL_APPROVED",
                null);

        assertThat(result.finalWeightKg())
                .isEqualByComparingTo("-1.25");
        assertThat(result.finalAmountCent()).isEqualTo(-100L);
    }

    @Test
    void modifiedApprovalCalculatesSignedAmountHalfUp() {
        var result = DeliveryReviewPolicy.calculate(
                order(null, null, "INVALID"),
                "MODIFIED_APPROVED",
                "-1.25");

        assertThat(result.finalWeightKg())
                .isEqualByComparingTo("-1.25");
        assertThat(result.finalAmountCent()).isEqualTo(-100L);
    }

    @Test
    void modifiedApprovalRequiresExactlyTwoDecimals() {
        assertThatThrownBy(() -> DeliveryReviewPolicy.calculate(
                order(null, null, "INVALID"),
                "MODIFIED_APPROVED",
                "1.2"))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> {
                            assertThat(failure.status()).isEqualTo(400);
                            assertThat(failure.code())
                                    .isEqualTo(
                                            "COMMON.VALIDATION_FAILED");
                        });
    }

    @Test
    void originalApprovalRejectsUnreliableFacts() {
        assertThatThrownBy(() -> DeliveryReviewPolicy.calculate(
                order(null, null, "INVALID"),
                "ORIGINAL_APPROVED",
                null))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code())
                                 .isEqualTo(
                                         "DELIVERY.ORIGINAL_DATA_UNRELIABLE"));
    }

    @Test
    void originalApprovalRejectsNetWeightMismatch() {
        assertThatThrownBy(() -> DeliveryReviewPolicy.calculate(
                order(
                        new BigDecimal("1.25"),
                        100L,
                        "RELIABLE",
                        true),
                "ORIGINAL_APPROVED",
                null))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code())
                                .isEqualTo(
                                        "DELIVERY.ORIGINAL_DATA_UNRELIABLE"));
    }

    @Test
    void modifiedApprovalAllowsHumanWeightForNetWeightMismatch() {
        var result = DeliveryReviewPolicy.calculate(
                order(
                        new BigDecimal("1.25"),
                        100L,
                        "RELIABLE",
                        true),
                "MODIFIED_APPROVED",
                "2.00");

        assertThat(result.finalWeightKg())
                .isEqualByComparingTo("2.00");
        assertThat(result.finalAmountCent()).isEqualTo(160L);
    }

    @Test
    void frozenAbsoluteWeightLimitAppliesToEveryDecision() {
        assertThatThrownBy(() -> DeliveryReviewPolicy.calculate(
                order(null, null, "INVALID"),
                "MODIFIED_APPROVED",
                "100.01"))
                .isInstanceOfSatisfying(
                        TargetApiException.class,
                        failure -> assertThat(failure.code())
                                 .isEqualTo(
                                         "DELIVERY.FINAL_WEIGHT_OUT_OF_RANGE"));
    }

    @Test
    void negativeZeroIsCanonicalized() {
        var result = DeliveryReviewPolicy.calculate(
                order(null, null, "INVALID"),
                "MODIFIED_APPROVED",
                "-0.00");

        assertThat(result.finalWeightKg().toPlainString())
                .isEqualTo("0.00");
        assertThat(result.finalAmountCent()).isZero();
    }

    private static LockedDeliveryOrderRow order(
            BigDecimal rawWeight,
            Long rawAmount,
            String rawStatus) {
        return order(rawWeight, rawAmount, rawStatus, false);
    }

    private static LockedDeliveryOrderRow order(
            BigDecimal rawWeight,
            Long rawAmount,
            String rawStatus,
            boolean netWeightInconsistent) {
        return new LockedDeliveryOrderRow(
                10L,
                "DO202607290001",
                30L,
                new BigDecimal("0.8000"),
                100_000L,
                rawWeight,
                rawAmount,
                rawStatus,
                netWeightInconsistent,
                "PENDING",
                0L,
                null,
                null,
                null,
                null);
    }
}
