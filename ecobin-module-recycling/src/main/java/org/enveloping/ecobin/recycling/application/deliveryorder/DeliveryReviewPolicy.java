package org.enveloping.ecobin.recycling.application.deliveryorder;

import org.enveloping.ecobin.framework.web.v1.TargetApiException;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Pure review calculation. Device facts and the locked unit price are never
 * overwritten; this policy only calculates the next human-recognized
 * projection.
 */
final class DeliveryReviewPolicy {

    static final String ORIGINAL_APPROVED = "ORIGINAL_APPROVED";
    static final String MODIFIED_APPROVED = "MODIFIED_APPROVED";

    private static final Pattern TWO_DECIMAL_KG =
            Pattern.compile("-?(?:0|[1-9][0-9]*)\\.[0-9]{2}");

    private DeliveryReviewPolicy() {
    }

    static ReviewValues calculate(
            LockedDeliveryOrderRow order,
            String requestedDecision,
            String requestedFinalWeightKg) {
        String decision = normalizeDecision(requestedDecision);
        BigDecimal finalWeightKg;
        long finalAmountCent;
        if (ORIGINAL_APPROVED.equals(decision)) {
            if (requestedFinalWeightKg != null) {
                throw validation(
                        "finalWeightKg",
                        "按原始数据通过时 finalWeightKg 必须为 null");
            }
            if (!"RELIABLE".equals(order.rawCalculationStatus())
                    || order.netWeightInconsistent()
                    || order.rawWeightKg() == null
                    || order.rawAmountCent() == null) {
                throw new TargetApiException(
                        422,
                        "DELIVERY.ORIGINAL_DATA_UNRELIABLE",
                        "原始重量或金额不可靠，必须由审核人员填写最终重量");
            }
            finalWeightKg = order.rawWeightKg().setScale(2);
            requireWithinFrozenLimit(
                    finalWeightKg,
                    order.maxReviewAbsWeightGram());
            finalAmountCent = order.rawAmountCent();
        } else {
            finalWeightKg = parseModifiedWeight(requestedFinalWeightKg);
            requireWithinFrozenLimit(
                    finalWeightKg,
                    order.maxReviewAbsWeightGram());
            finalAmountCent = calculateAmount(
                    finalWeightKg,
                    order.unitPriceYuanPerKg());
        }
        return new ReviewValues(
                decision,
                finalWeightKg,
                finalAmountCent);
    }

    private static String normalizeDecision(String value) {
        if (value == null) {
            throw validation("decision", "审核决定不能为空");
        }
        String normalized = value.trim();
        if (!ORIGINAL_APPROVED.equals(normalized)
                && !MODIFIED_APPROVED.equals(normalized)) {
            throw validation(
                    "decision",
                    "审核决定只允许 ORIGINAL_APPROVED"
                            + " 或 MODIFIED_APPROVED");
        }
        return normalized;
    }

    private static BigDecimal parseModifiedWeight(String value) {
        if (value == null || !TWO_DECIMAL_KG.matcher(value).matches()) {
            throw validation(
                    "finalWeightKg",
                    "修改后通过必须填写精确两位小数的千克字符串");
        }
        try {
            BigDecimal parsed = new BigDecimal(value);
            if (parsed.signum() == 0) {
                return BigDecimal.ZERO.setScale(2);
            }
            return parsed;
        } catch (NumberFormatException exception) {
            throw validation(
                    "finalWeightKg",
                    "finalWeightKg 不是有效的十进制定点数");
        }
    }

    private static void requireWithinFrozenLimit(
            BigDecimal weightKg,
            long maxReviewAbsWeightGram) {
        BigDecimal maximumKg =
                BigDecimal.valueOf(maxReviewAbsWeightGram, 3);
        if (weightKg.abs().compareTo(maximumKg) > 0) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.FINAL_WEIGHT_OUT_OF_RANGE",
                    "最终重量超出该订单冻结的人工审核范围",
                    false,
                    Map.of(
                            "maxReviewAbsoluteWeightKg",
                            maximumKg
                                    .setScale(2, RoundingMode.DOWN)
                                    .toPlainString()));
        }
    }

    private static long calculateAmount(
            BigDecimal weightKg,
            BigDecimal unitPriceYuanPerKg) {
        /*
         * Current schema requires a positive locked price. Keeping the zero
         * rule explicit makes the application safe if an older quarantined
         * order is ever projected without a recoverable price.
         */
        if (unitPriceYuanPerKg == null) {
            if (weightKg.signum() == 0) {
                return 0L;
            }
            throw new TargetApiException(
                    422,
                    "DELIVERY.LOCKED_PRICE_UNAVAILABLE",
                    "订单锁定单价无法恢复，只能认定为 0.00 千克");
        }
        try {
            return weightKg
                    .multiply(unitPriceYuanPerKg)
                    .movePointRight(2)
                    .setScale(0, RoundingMode.HALF_UP)
                    .longValueExact();
        } catch (ArithmeticException exception) {
            throw new TargetApiException(
                    422,
                    "DELIVERY.FINAL_AMOUNT_OUT_OF_RANGE",
                    "最终金额超出系统可精确保存的范围");
        }
    }

    private static TargetApiException validation(
            String field,
            String message) {
        return new TargetApiException(
                400,
                "COMMON.VALIDATION_FAILED",
                "审核请求字段不符合接口契约",
                false,
                Map.of(field, message));
    }

    record ReviewValues(
            String decision,
            BigDecimal finalWeightKg,
            long finalAmountCent) {
    }
}
