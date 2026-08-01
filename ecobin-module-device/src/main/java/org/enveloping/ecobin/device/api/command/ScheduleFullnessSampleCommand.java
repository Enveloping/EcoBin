package org.enveloping.ecobin.device.api.command;

import org.enveloping.ecobin.device.api.persistence.FullnessDetectionCommandRef;

import java.util.Objects;
import java.util.UUID;

public record ScheduleFullnessSampleCommand(
        FullnessDetectionCommandRef detectionRef,
        UUID detectionUid,
        int portNo,
        String sampleRole,
        String triggerType,
        String fullnessMode,
        Long currentBaselineWeightGrams,
        long configuredFullWeightGrams,
        long settleWaitMs,
        long measurementTimeoutMs,
        long configVersion,
        String configContentSha256,
        String configMcuPayloadSha256,
        UUID correlationUid,
        UUID causationUid) {

    public ScheduleFullnessSampleCommand {
        Objects.requireNonNull(detectionRef, "detectionRef");
        Objects.requireNonNull(detectionUid, "detectionUid");
        requireOneOf(
                sampleRole,
                "sampleRole",
                "INITIAL",
                "CONFIRMATION",
                "MANUAL_RECHECK");
        requireOneOf(
                triggerType,
                "triggerType",
                "DELIVERY_COMPLETE",
                "CLEAN_COMPLETE",
                "MANUAL_RECHECK");
        requireOneOf(
                fullnessMode,
                "fullnessMode",
                "INFRARED_ONLY",
                "WEIGHT_ONLY",
                "INFRARED_OR_WEIGHT");
        if (portNo < 1 || portNo > 6
                || configuredFullWeightGrams < 1
                || configuredFullWeightGrams > 4_294_967_295L
                || settleWaitMs < 0
                || settleWaitMs > 4_294_967_295L
                || measurementTimeoutMs < 1_000
                || measurementTimeoutMs > 6_000
                || configVersion <= 0) {
            throw new IllegalArgumentException(
                    "fullness sample command values are outside the contract");
        }
        if (currentBaselineWeightGrams != null
                && (currentBaselineWeightGrams < Integer.MIN_VALUE
                || currentBaselineWeightGrams > Integer.MAX_VALUE)) {
            throw new IllegalArgumentException(
                    "currentBaselineWeightGrams is outside int32");
        }
        requireDigest(configContentSha256, "configContentSha256");
        requireDigest(
                configMcuPayloadSha256,
                "configMcuPayloadSha256");
    }

    private static void requireOneOf(
            String value,
            String field,
            String... supported) {
        Objects.requireNonNull(value, field);
        for (String candidate : supported) {
            if (candidate.equals(value)) {
                return;
            }
        }
        throw new IllegalArgumentException(
                field + " is unsupported");
    }

    private static void requireDigest(String value, String field) {
        if (value == null || !value.matches("[0-9a-f]{64}")) {
            throw new IllegalArgumentException(
                    field + " must be lowercase SHA-256");
        }
    }
}
