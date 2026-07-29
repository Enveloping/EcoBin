package org.enveloping.ecobin.device.api.value;

import java.util.Locale;

/**
 * Recycling-owned delivery rule identity that is frozen into a device
 * delivery session. Database keys deliberately stay outside this value.
 */
public record DeliveryRuleSnapshot(
        long version,
        String contentSha256,
        long openBalanceFloorCent,
        long maxReviewAbsWeightGrams) {

    public DeliveryRuleSnapshot {
        if (version <= 0) {
            throw new IllegalArgumentException("version must be positive");
        }
        if (contentSha256 == null
                || !contentSha256.matches("^[0-9a-fA-F]{64}$")) {
            throw new IllegalArgumentException(
                    "contentSha256 must be a SHA-256 hex digest");
        }
        contentSha256 = contentSha256.toLowerCase(Locale.ROOT);
        if (openBalanceFloorCent >= 0) {
            throw new IllegalArgumentException(
                    "openBalanceFloorCent must be negative");
        }
        if (maxReviewAbsWeightGrams < 1
                || maxReviewAbsWeightGrams > 1_000_000) {
            throw new IllegalArgumentException(
                    "maxReviewAbsWeightGrams is outside the supported range");
        }
    }
}
