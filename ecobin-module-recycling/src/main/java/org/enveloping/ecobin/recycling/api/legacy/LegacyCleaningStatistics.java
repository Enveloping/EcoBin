package org.enveloping.ecobin.recycling.api.legacy;

/**
 * operations 旧概览所需的清运聚合。
 */
public record LegacyCleaningStatistics(
        long monthCount,
        double monthWeight) {
}
