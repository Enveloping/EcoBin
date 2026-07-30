package org.enveloping.ecobin.device.api.result;

import java.util.List;

public record PhotoStatusBusinessResult(
        Outcome outcome,
        String effectKind,
        String conflictCode,
        List<DeliveryCompletionResultReference> resultReferences) {

    public PhotoStatusBusinessResult {
        resultReferences = List.copyOf(resultReferences);
        if (outcome == Outcome.CONFLICT) {
            if (conflictCode == null
                    || !conflictCode.matches("[A-Z][A-Z0-9_]{0,63}")) {
                throw new IllegalArgumentException(
                        "conflictCode must be a stable safe code");
            }
            if (effectKind != null) {
                throw new IllegalArgumentException(
                        "conflicting photo status cannot have an effect");
            }
        } else {
            if (!List.of(
                    "CREATED",
                    "UPDATED",
                    "NO_ACTION_REQUIRED").contains(effectKind)) {
                throw new IllegalArgumentException(
                        "effectKind must use the confirmation contract");
            }
            if (conflictCode != null) {
                throw new IllegalArgumentException(
                        "applied photo status cannot have a conflict");
            }
        }
    }

    public static PhotoStatusBusinessResult applied(
            String effectKind,
            List<DeliveryCompletionResultReference> references) {
        return new PhotoStatusBusinessResult(
                Outcome.APPLIED,
                effectKind,
                null,
                references);
    }

    public static PhotoStatusBusinessResult conflict(
            String conflictCode) {
        return new PhotoStatusBusinessResult(
                Outcome.CONFLICT,
                null,
                conflictCode,
                List.of());
    }

    public enum Outcome {
        APPLIED,
        CONFLICT
    }
}
