package org.enveloping.ecobin.device.application.remote;

/** Pure state decisions for reconciling database sessions with jump state. */
final class RemoteSupportReconciliationPolicy {

    private RemoteSupportReconciliationPolicy() {
    }

    static Decision decide(String state, ActualLeaseState actual) {
        if (isTerminal(state)) {
            return new Decision(
                    state, false, actual == ActualLeaseState.ABSENT, null);
        }
        if (actual == ActualLeaseState.CONFLICT) {
            return new Decision(
                    "FAILED", false, false, "SERVER_LEASE_CONFLICT");
        }
        if ("CLOSING".equals(state)) {
            return actual == ActualLeaseState.ABSENT
                    ? new Decision("CLOSED", false, true, null)
                    : new Decision("CLOSING", false, false, null);
        }
        if ("OPEN".equals(state)) {
            return actual == ActualLeaseState.ABSENT
                    ? new Decision("RECONNECTING", true, false, null)
                    : new Decision("OPEN", true, false, null);
        }
        if ("RECONNECTING".equals(state)) {
            return actual == ActualLeaseState.MATCH
                    ? new Decision("OPEN", true, false, null)
                    : new Decision("RECONNECTING", true, false, null);
        }
        if ("PREPARING".equals(state) || "CONNECTING".equals(state)) {
            return new Decision(state, true, false, null);
        }
        throw new IllegalArgumentException(
                "unsupported remote support state: " + state);
    }

    private static boolean isTerminal(String state) {
        return "CLOSED".equals(state)
                || "FAILED".equals(state)
                || "EXPIRED".equals(state);
    }

    enum ActualLeaseState {
        MATCH,
        ABSENT,
        CONFLICT
    }

    record Decision(
            String nextState,
            boolean desiredRequired,
            boolean releaseLease,
            String failureCode) {
    }
}
