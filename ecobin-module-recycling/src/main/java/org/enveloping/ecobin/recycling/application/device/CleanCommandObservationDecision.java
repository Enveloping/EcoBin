package org.enveloping.ecobin.recycling.application.device;

import java.util.Set;

/** Pure monotonic decision for one trusted cleaning command observation. */
final class CleanCommandObservationDecision {

    private static final Set<String> TERMINAL = Set.of(
            "PRE_UNLOCK_ENDED", "COMPLETED", "ABORTED");

    private CleanCommandObservationDecision() {
    }

    static Action decide(
            String commandType,
            String operationStatus,
            boolean firstUnlockMayHaveExecuted,
            String stage,
            String errorCode) {
        if (!"START_CLEAN_OPERATION".equals(commandType)
                || TERMINAL.contains(operationStatus)) {
            return Action.NONE;
        }
        return switch (stage) {
            case "RECEIVED" -> Action.NONE;
            case "ACCEPTED" -> Action.MARK_EDGE_SAVED;
            case "MCU_ACCEPTED" -> Action.MARK_IN_PROGRESS;
            case "REJECTED", "PRE_START_FAILED" ->
                    firstUnlockMayHaveExecuted
                            ? Action.NONE
                            : Action.END_BEFORE_UNLOCK;
            case "FAILED" -> "EDGE_RESTARTED".equals(errorCode)
                    || "RECOVERY_REQUIRED".equals(operationStatus)
                    ? Action.NONE
                    : Action.REQUIRE_RECOVERY;
            default -> throw new IllegalArgumentException(
                    "unsupported clean command observation stage");
        };
    }

    enum Action {
        MARK_EDGE_SAVED,
        MARK_IN_PROGRESS,
        END_BEFORE_UNLOCK,
        REQUIRE_RECOVERY,
        NONE
    }
}
