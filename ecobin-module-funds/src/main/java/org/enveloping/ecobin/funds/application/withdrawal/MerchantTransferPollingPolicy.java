package org.enveloping.ecobin.funds.application.withdrawal;

import java.time.Duration;
import java.time.LocalDateTime;

/** 商家转账主动查单采用分级节流，并尊重微信最近 30 天查询边界。 */
final class MerchantTransferPollingPolicy {

    private static final Duration LONG_UNSETTLED = Duration.ofMinutes(30);
    private static final Duration QUERY_WINDOW = Duration.ofDays(30);

    private MerchantTransferPollingPolicy() {
    }

    static Duration nextDelay(
            LocalDateTime channelBoundaryAt, LocalDateTime now) {
        Duration age = age(channelBoundaryAt, now);
        if (age.compareTo(Duration.ofMinutes(5)) < 0) {
            return Duration.ofSeconds(30);
        }
        if (age.compareTo(LONG_UNSETTLED) < 0) {
            return Duration.ofMinutes(2);
        }
        if (age.compareTo(Duration.ofDays(1)) < 0) {
            return Duration.ofMinutes(10);
        }
        if (age.compareTo(Duration.ofDays(7)) < 0) {
            return Duration.ofHours(1);
        }
        return Duration.ofHours(6);
    }

    static boolean isLongUnsettled(
            LocalDateTime channelBoundaryAt, LocalDateTime now) {
        return age(channelBoundaryAt, now).compareTo(LONG_UNSETTLED) >= 0;
    }

    static boolean isQueryWindowExpired(
            LocalDateTime channelBoundaryAt, LocalDateTime now) {
        return age(channelBoundaryAt, now).compareTo(QUERY_WINDOW) >= 0;
    }

    private static Duration age(
            LocalDateTime channelBoundaryAt, LocalDateTime now) {
        if (channelBoundaryAt == null || now.isBefore(channelBoundaryAt)) {
            return Duration.ZERO;
        }
        return Duration.between(channelBoundaryAt, now);
    }
}
