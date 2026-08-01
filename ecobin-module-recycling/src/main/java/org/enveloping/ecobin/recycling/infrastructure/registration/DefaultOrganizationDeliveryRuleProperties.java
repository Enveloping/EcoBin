package org.enveloping.ecobin.recycling.infrastructure.registration;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * 新机构自动发布的第一版投递规则。
 */
@Component
@ConfigurationProperties(
        prefix = "ecobin.recycling.default-delivery-rule")
public final class DefaultOrganizationDeliveryRuleProperties {

    private long openBalanceFloorCent = -1_000L;
    private long maximumReviewAbsoluteWeightGram = 100_000L;

    public long getOpenBalanceFloorCent() {
        return openBalanceFloorCent;
    }

    public void setOpenBalanceFloorCent(long openBalanceFloorCent) {
        this.openBalanceFloorCent = openBalanceFloorCent;
    }

    public long getMaximumReviewAbsoluteWeightGram() {
        return maximumReviewAbsoluteWeightGram;
    }

    public void setMaximumReviewAbsoluteWeightGram(
            long maximumReviewAbsoluteWeightGram) {
        this.maximumReviewAbsoluteWeightGram =
                maximumReviewAbsoluteWeightGram;
    }

    void validate() {
        if (openBalanceFloorCent >= 0) {
            throw new IllegalStateException(
                    "default delivery open balance floor must be negative");
        }
        if (maximumReviewAbsoluteWeightGram < 1
                || maximumReviewAbsoluteWeightGram > 1_000_000L) {
            throw new IllegalStateException(
                    "default maximum review weight must be between 1 and 1000000 grams");
        }
    }
}
