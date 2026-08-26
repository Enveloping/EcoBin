package org.enveloping.ecobin.recycling.application.deliveryorder;

/** Controls which delivery review reasons may be shown to ordinary users. */
public final class DeliveryReviewReasonVisibilityPolicy {

    private static final String PLATFORM_ADMIN = "PLATFORM_ADMIN";
    private static final String STAFF = "STAFF";

    private DeliveryReviewReasonVisibilityPolicy() {
    }

    public static String userVisibleReason(
            String reviewerKind,
            String reason) {
        if ((!PLATFORM_ADMIN.equals(reviewerKind)
                && !STAFF.equals(reviewerKind))
                || reason == null
                || reason.isBlank()) {
            return null;
        }
        return reason.trim();
    }
}
