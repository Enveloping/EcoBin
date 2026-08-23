package org.enveloping.ecobin.device.api.result;

import java.util.Objects;

/** Authoritative result of the last database check before a seal command call. */
public record FactorySealDispatchDecision(
        Outcome outcome,
        String reasonCode) {

    public FactorySealDispatchDecision {
        Objects.requireNonNull(outcome, "outcome");
        if (outcome == Outcome.ALLOW) {
            if (reasonCode != null) {
                throw new IllegalArgumentException(
                        "an allowed seal dispatch cannot carry a reason");
            }
        } else if (reasonCode == null
                || !reasonCode.matches("[A-Z][A-Z0-9_]{0,63}")) {
            throw new IllegalArgumentException(
                    "a skipped seal dispatch needs a stable reason");
        }
    }

    public enum Outcome {
        ALLOW,
        ALREADY_ACKNOWLEDGED,
        CANCEL_STALE
    }
}
