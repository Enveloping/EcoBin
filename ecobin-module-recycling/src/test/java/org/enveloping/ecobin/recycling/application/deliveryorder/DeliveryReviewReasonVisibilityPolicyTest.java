package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class DeliveryReviewReasonVisibilityPolicyTest {

    @Test
    void exposesOnlyNonBlankHumanReviewerReasons() {
        assertThat(DeliveryReviewReasonVisibilityPolicy.userVisibleReason(
                "PLATFORM_ADMIN", "  平台管理员已核对  "))
                .isEqualTo("平台管理员已核对");
        assertThat(DeliveryReviewReasonVisibilityPolicy.userVisibleReason(
                "STAFF", "机构工作人员已现场核对"))
                .isEqualTo("机构工作人员已现场核对");

        assertThat(DeliveryReviewReasonVisibilityPolicy.userVisibleReason(
                "SYSTEM", "机构投递规则自动审核通过"))
                .isNull();
        assertThat(DeliveryReviewReasonVisibilityPolicy.userVisibleReason(
                "FUTURE_REVIEWER", "未知审核方说明"))
                .isNull();
        assertThat(DeliveryReviewReasonVisibilityPolicy.userVisibleReason(
                null, "缺少审核方说明"))
                .isNull();
        assertThat(DeliveryReviewReasonVisibilityPolicy.userVisibleReason(
                "STAFF", "   "))
                .isNull();
    }
}
